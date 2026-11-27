"""
Serviços de domínio e orquestração das esteiras de análise IA (URL e arquivo local),
reabertura de workspace e renderização FFmpeg.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
from pathlib import Path
import subprocess  # nosec B404
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException

from core import media_utils
from core.ai_apis import detect_viral_moments, transcribe_audio_groq
from core.config import CACHE_DIR
from core.downloader import download_audio_with_metadata, download_video, is_valid_video_url
from core.logging_config import request_id_var
from core.media_utils import normalize_virality_score
from core.video_engine import JobCancelledError, cut_video, extract_preview_frame, get_video_metadata
from database import platform_repo
from database.db_manager import (
    add_corte,
    add_video,
    get_corte,
    get_video,
    list_cortes,
    list_videos,
    save_analysis,
    update_corte,
    update_corte_status,
    update_video_status,
)
from services.job_manager import JobManager, job_manager

logger = logging.getLogger("easykut_media_service")


def _resolve_confined_local_file(file_path: Optional[str]) -> Optional[str]:
    """Retorna o caminho resolvido de um arquivo local somente se existir e estiver em DATA_DIR."""
    if not file_path:
        return None
    p = Path(file_path).resolve()
    if media_utils.is_path_within(p, media_utils.DATA_DIR_RESOLVED) and p.exists() and p.is_file():
        return str(p)
    return None


def resolve_trusted_video_source(
    video_id: Optional[int],
    source: Optional[str],
    manager: JobManager = job_manager,
) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Resolve a fonte de vídeo exclusivamente a partir de registros confiáveis já cadastrados
    (analyses_cache ou tabela SQLite 'videos'), prevenindo LFI e SSRF.
    Suporta URLs públicas autorizadas e vídeos locais confinados em DATA_DIR (uploads).
    """
    cached_stream: Optional[str] = None
    local_audio: Optional[str] = None
    trusted_url: Optional[str] = None
    local_video_file: Optional[str] = None

    if video_id is not None:
        entry = manager.get_analysis_cache(video_id)
        if entry:
            trusted_url = entry.get("url")
            cached_stream = entry.get("direct_stream_url")
            local_audio = entry.get("audio_path")
            local_video_file = _resolve_confined_local_file(entry.get("file_path"))

        if not trusted_url or not local_audio or not local_video_file:
            video_row = get_video(video_id)
            if video_row:
                trusted_url = trusted_url or video_row.get("url")
                local_audio = local_audio or video_row.get("audio_path")
                local_video_file = local_video_file or _resolve_confined_local_file(video_row.get("file_path"))

        if local_video_file and (not trusted_url or str(trusted_url).startswith("local://")):
            return local_video_file, None, local_audio

    if not trusted_url and source:
        clean_src = source.strip()
        if not is_valid_video_url(clean_src):
            raise HTTPException(
                status_code=400,
                detail="Fonte de vídeo inválida ou não permitida. Informe um video_id cadastrado.",
            )
        entry_by_url = manager.find_analysis_cache_by_url(clean_src)
        if entry_by_url:
            trusted_url = clean_src
            cached_stream = cached_stream or entry_by_url.get("direct_stream_url")
            local_audio = local_audio or entry_by_url.get("audio_path")
        if not trusted_url:
            for row in list_videos(search=clean_src):
                if row.get("url") == clean_src:
                    trusted_url = clean_src
                    local_audio = local_audio or row.get("audio_path")
                    break

    if not trusted_url or not is_valid_video_url(trusted_url):
        raise HTTPException(
            status_code=400,
            detail="Vídeo não encontrado ou fonte não autorizada. Informe um video_id válido.",
        )

    return trusted_url, cached_stream, local_audio


def _extract_cut_frames_parallel(
    *,
    cortes_detectados: List[Dict[str, Any]],
    frame_source: str,
    video_duration: float,
    direct_stream_url: Optional[str],
    extract_frame_fn: Callable[..., Optional[str]],
) -> List[Optional[str]]:
    def _extract_one(c_item: Dict[str, Any]) -> Optional[str]:
        s_time = max(0.0, float(c_item.get("start_time", 0.0)))
        ts_frame = min(video_duration - 1.0, s_time + 1.0) if video_duration > 2.0 else s_time
        try:
            return extract_frame_fn(
                source=frame_source,
                timestamp_sec=max(0.0, ts_frame),
                direct_stream_url=direct_stream_url,
            )
        except Exception as exc_fr:
            logger.warning("Falha não-crítica ao extrair frame do corte '%s': %s", c_item.get("title"), exc_fr)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(_extract_one, cortes_detectados))


def _format_suggested_cuts(
    cortes_detectados: List[Dict[str, Any]],
    extracted_frames: List[Optional[str]],
    all_segments: List[Dict[str, Any]],
    max_cuts: int,
) -> List[Dict[str, Any]]:
    formatted: List[Dict[str, Any]] = []
    for idx, c in enumerate(cortes_detectados):
        s_time = max(0.0, float(c.get("start_time", 0.0)))
        e_time = max(s_time + 1.0, float(c.get("end_time", s_time + 30.0)))
        dur = round(e_time - s_time, 1)
        norm_score = normalize_virality_score(c.get("virality_score", 80.0), default=80.0)

        tipo_val = str(c.get("tipo", "short" if dur <= 180.0 else "longo")).strip().lower()
        default_mode = "horizontal_16_9" if (tipo_val == "longo" or dur >= 180.0) else "vertical_blur"
        default_params: Dict[str, Any] = {"zoom_factor": 1.0} if default_mode == "vertical_blur" else {}

        frame_path = extracted_frames[idx] if idx < len(extracted_frames) else None
        frame_url = f"/data/temp/{Path(frame_path).name}" if (frame_path and os.path.exists(frame_path)) else ""

        cut_segs = [
            seg for seg in all_segments
            if float(seg.get("end", 0.0)) > s_time and float(seg.get("start", 0.0)) < e_time
        ]

        formatted.append({
            "index": idx,
            "id": None,
            "title": str(c.get("title", f"Corte #{idx + 1}")),
            "start_time": s_time,
            "end_time": e_time,
            "duration": dur,
            "virality_score": round(norm_score, 1),
            "tipo": tipo_val,
            "justification": str(c.get("justification", "")),
            "frame_path": frame_path or "",
            "frame_url": frame_url,
            "selected": idx < max_cuts,
            "layout_mode": default_mode,
            "layout_params": default_params,
            "burn_subtitles": True,
            "segments": cut_segs,
        })
    return formatted


def _load_transcript_payload(
    video_id: int,
    *,
    cache_dir: Path,
    temp_dir: Path,
    manager: JobManager,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    transcript_cache_path = cache_dir / f"transcript_{video_id}.json"
    if not transcript_cache_path.exists():
        transcript_cache_path = temp_dir / f"transcript_{video_id}.json"
    if transcript_cache_path.exists():
        try:
            with open(transcript_cache_path, "r", encoding="utf-8") as f_tr:
                tr_data = json.load(f_tr)
                return tr_data.get("segments", []) or [], tr_data.get("words", []) or []
        except Exception as exc_tr:
            logger.warning("Erro ao ler transcrição em cache do vídeo %d: %s", video_id, exc_tr)

    cached_entry = manager.get_analysis_cache(video_id)
    if cached_entry:
        td = cached_entry.get("transcript_data", {}) or {}
        return td.get("segments", []) or [], td.get("words", []) or []
    return [], []


def _load_transcript_segments(
    video_id: int,
    *,
    cache_dir: Path,
    temp_dir: Path,
    manager: JobManager,
) -> List[Dict[str, Any]]:
    segs, _ = _load_transcript_payload(video_id, cache_dir=cache_dir, temp_dir=temp_dir, manager=manager)
    return segs


def _extract_audio_from_local_video(video_path: str, temp_dir: Path) -> str:
    """Extrai faixa de áudio MP3 leve (mono 16kHz 48k) de um vídeo local para transcrição Groq Whisper."""
    media_utils.check_ffmpeg()
    safe_src = media_utils.validate_ffmpeg_input_target(video_path, require_within_data_dir=True)
    audio_dest = (temp_dir / f"{Path(safe_src).stem}_audio.mp3").resolve()
    audio_dest.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-nostdin", "-y",
        "-protocol_whitelist", media_utils.FFMPEG_ALLOWED_PROTOCOLS,
        "-i", safe_src,
        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k",
        str(audio_dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)  # nosec B603
    if proc.returncode != 0 or not audio_dest.exists() or audio_dest.stat().st_size == 0:
        raise RuntimeError(f"Falha ao extrair áudio do vídeo local: {proc.stderr[:300]}")
    return str(audio_dest)


def _finalize_analysis_pipeline(
    *,
    job_id: str,
    video_id: int,
    url: str,
    frame_source: str,
    video_title: str,
    video_duration: float,
    video_thumbnail: str,
    video_uploader: str,
    audio_file: str,
    file_path: str,
    direct_stream_url: Optional[str],
    provider: str,
    max_cuts: int,
    manager: JobManager,
    cache_dir: Path,
    transcribe_fn: Callable[[str], Dict[str, Any]],
    detect_fn: Callable[..., List[Dict[str, Any]]],
    extract_frame_fn: Callable[..., Optional[str]],
) -> None:
    """Executa etapas compartilhadas 2–4: transcrição, curadoria IA, frames paralelos e persistência."""
    manager.update_job(job_id, step="[2/4] Transcrevendo áudio com Groq Whisper-large-v3...", progress=45)
    transcript_data = transcribe_fn(audio_file)

    transcript_cache_path = cache_dir / f"transcript_{video_id}.json"
    try:
        with open(transcript_cache_path, "w", encoding="utf-8") as f_cache:
            json.dump(transcript_data, f_cache, ensure_ascii=False)
    except Exception as exc_cache:
        logger.warning("Não foi possível salvar cache de transcrição: %s", exc_cache)

    manager.update_job(job_id, step=f"[3/4] Curadoria de momentos virais com {provider.upper()}...", progress=70)
    cortes_detectados = detect_fn(transcript_data, provider=provider)
    if not cortes_detectados:
        fallback_end = min(60.0, video_duration) if video_duration > 5 else 30.0
        cortes_detectados = [{
            "title": f"Destaque: {video_title[:45]}",
            "start_time": 0.0,
            "end_time": fallback_end,
            "virality_score": 80.0,
            "tipo": "short",
            "justification": "Trecho inicial sugerido automaticamente.",
        }]

    manager.update_job(job_id, step="[4/4] Capturando frames de prévia de alta velocidade...", progress=85)
    all_segments = transcript_data.get("segments", []) or []
    extracted_frames = _extract_cut_frames_parallel(
        cortes_detectados=cortes_detectados,
        frame_source=frame_source,
        video_duration=video_duration,
        direct_stream_url=direct_stream_url,
        extract_frame_fn=extract_frame_fn,
    )
    cortes_formatados = _format_suggested_cuts(cortes_detectados, extracted_frames, all_segments, max_cuts)

    if not isinstance(video_id, int) or video_id <= 0 or not get_video(video_id):
        logger.warning("video_id %r ausente em 'videos' antes de save_analysis; recriando registro pai.", video_id)
        video_id = add_video(
            url=url,
            title=video_title,
            duration=video_duration,
            file_path=file_path,
            audio_path=audio_file,
            status="analyzed",
        )

    inserted_ids = save_analysis(
        video_id=video_id,
        transcript=transcript_data,
        analysis_data=cortes_detectados,
        url=url,
        title=video_title,
        duration=video_duration,
        file_path=file_path,
        audio_path=audio_file,
    )
    for idx, c_fmt in enumerate(cortes_formatados):
        if idx < len(inserted_ids):
            c_fmt["id"] = inserted_ids[idx]

    manager.set_analysis_cache(
        video_id,
        {
            "video_id": video_id,
            "url": url,
            "title": video_title,
            "duration": video_duration,
            "thumbnail": video_thumbnail,
            "uploader": video_uploader,
            "file_path": file_path,
            "audio_path": audio_file,
            "direct_stream_url": direct_stream_url,
            "transcript_data": transcript_data,
            "cortes": cortes_formatados,
        },
    )

    manager.update_job(
        job_id,
        status="completed",
        step="Análise concluída com sucesso!",
        progress=100,
        result={
            "video_id": video_id,
            "url": url,
            "title": video_title,
            "duration": video_duration,
            "thumbnail": video_thumbnail,
            "uploader": video_uploader,
            "is_local": str(url).startswith("local://"),
            "cortes": cortes_formatados,
            "transcript_segments": all_segments,
        },
    )


def run_analyze_job(
    job_id: str,
    url: str,
    provider: str,
    max_cuts: int,
    *,
    manager: JobManager = job_manager,
    cache_dir: Optional[Path] = None,
    download_audio_fn: Callable[[str], Dict[str, Any]] = download_audio_with_metadata,
    transcribe_fn: Callable[[str], Dict[str, Any]] = transcribe_audio_groq,
    detect_fn: Callable[..., List[Dict[str, Any]]] = detect_viral_moments,
    extract_frame_fn: Callable[..., Optional[str]] = extract_preview_frame,
) -> None:
    """Executa a esteira assíncrona de análise IA para uma URL remota."""
    token = request_id_var.set(job_id)
    target_cache_dir = cache_dir or CACHE_DIR
    target_cache_dir.mkdir(parents=True, exist_ok=True)
    video_id: Optional[int] = None

    try:
        manager.update_job(job_id, step="[1/4] Extraindo metadados e baixando áudio leve (1 chamada yt-dlp)...", progress=15)
        ingest = download_audio_fn(url)
        info = ingest["info"]
        audio_file = ingest["audio_path"]
        direct_stream_url = ingest.get("direct_stream_url")

        video_title = info.get("title", "Vídeo Sem Título")
        video_duration = float(info.get("duration", 0.0))

        video_id = add_video(
            url=url,
            title=video_title,
            duration=video_duration,
            audio_path=audio_file,
            status="transcribing",
        )
        if not isinstance(video_id, int) or video_id <= 0 or not get_video(video_id):
            raise RuntimeError(f"Falha ao confirmar registro do vídeo na tabela 'videos' (video_id={video_id!r}).")

        _finalize_analysis_pipeline(
            job_id=job_id,
            video_id=video_id,
            url=url,
            frame_source=url,
            video_title=video_title,
            video_duration=video_duration,
            video_thumbnail=info.get("thumbnail", ""),
            video_uploader=info.get("uploader", ""),
            audio_file=audio_file,
            file_path="",
            direct_stream_url=direct_stream_url,
            provider=provider,
            max_cuts=max_cuts,
            manager=manager,
            cache_dir=target_cache_dir,
            transcribe_fn=transcribe_fn,
            detect_fn=detect_fn,
            extract_frame_fn=extract_frame_fn,
        )
    except Exception as exc:
        logger.exception("Erro crítico na esteira de análise (job %s): %s", job_id, exc)
        if isinstance(video_id, int) and video_id > 0:
            try:
                update_video_status(video_id, status="failed")
            except Exception:
                pass
        manager.mark_job_error(job_id=job_id, job_type="analyze", step="Erro durante o processamento da análise", error=str(exc))
    finally:
        request_id_var.reset(token)


def run_local_video_analyze_job(
    job_id: str,
    video_file_path: str,
    original_title: str,
    provider: str,
    max_cuts: int,
    *,
    manager: JobManager = job_manager,
    cache_dir: Optional[Path] = None,
    temp_dir: Optional[Path] = None,
    metadata_fn: Callable[[str], Dict[str, Any]] = get_video_metadata,
    extract_audio_fn: Callable[[str, Path], str] = _extract_audio_from_local_video,
    transcribe_fn: Callable[[str], Dict[str, Any]] = transcribe_audio_groq,
    detect_fn: Callable[..., List[Dict[str, Any]]] = detect_viral_moments,
    extract_frame_fn: Callable[..., Optional[str]] = extract_preview_frame,
) -> None:
    """Executa a esteira assíncrona de análise IA para um arquivo de vídeo local enviado via upload."""
    token = request_id_var.set(job_id)
    target_cache_dir = cache_dir or CACHE_DIR
    target_temp_dir = temp_dir or media_utils.TEMP_DIR
    target_cache_dir.mkdir(parents=True, exist_ok=True)
    target_temp_dir.mkdir(parents=True, exist_ok=True)
    video_id: Optional[int] = None

    try:
        manager.update_job(job_id, step="[1/4] Lendo metadados e extraindo áudio do vídeo local...", progress=15)
        safe_video_path = media_utils.validate_ffmpeg_input_target(video_file_path, require_within_data_dir=True)
        meta = metadata_fn(safe_video_path)
        video_duration = max(1.0, float(meta.get("duration", 0.0)))
        audio_file = extract_audio_fn(safe_video_path, target_temp_dir)
        local_uri = f"local://{Path(safe_video_path).name}"
        clean_title = (original_title or Path(safe_video_path).stem).strip()[:120]

        video_id = add_video(
            url=local_uri,
            title=clean_title,
            duration=video_duration,
            file_path=safe_video_path,
            audio_path=audio_file,
            status="transcribing",
        )
        if not isinstance(video_id, int) or video_id <= 0 or not get_video(video_id):
            raise RuntimeError(f"Falha ao registrar vídeo local na tabela 'videos' (video_id={video_id!r}).")

        _finalize_analysis_pipeline(
            job_id=job_id,
            video_id=video_id,
            url=local_uri,
            frame_source=safe_video_path,
            video_title=clean_title,
            video_duration=video_duration,
            video_thumbnail="",
            video_uploader="Upload Local",
            audio_file=audio_file,
            file_path=safe_video_path,
            direct_stream_url=None,
            provider=provider,
            max_cuts=max_cuts,
            manager=manager,
            cache_dir=target_cache_dir,
            transcribe_fn=transcribe_fn,
            detect_fn=detect_fn,
            extract_frame_fn=extract_frame_fn,
        )
    except Exception as exc:
        logger.exception("Erro crítico na análise de vídeo local (job %s): %s", job_id, exc)
        if isinstance(video_id, int) and video_id > 0:
            try:
                update_video_status(video_id, status="failed")
            except Exception:
                pass
        manager.mark_job_error(job_id=job_id, job_type="analyze", step="Erro durante a análise do vídeo local", error=str(exc))
    finally:
        request_id_var.reset(token)


def load_video_workspace(
    video_id: int,
    *,
    manager: JobManager = job_manager,
    cache_dir: Optional[Path] = None,
    temp_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """
    Carrega vídeo, cortes persistidos e segmentos de transcrição do SQLite/cache
    para reabertura direta na aba Estúdio.
    """
    video = get_video(video_id)
    if not video:
        return None

    target_cache_dir = cache_dir or CACHE_DIR
    target_temp_dir = temp_dir or media_utils.TEMP_DIR
    all_segments, all_words = _load_transcript_payload(
        video_id,
        cache_dir=target_cache_dir,
        temp_dir=target_temp_dir,
        manager=manager,
    )
    cached_entry = manager.get_analysis_cache(video_id) or {}
    cached_frames_by_id = {
        c.get("id"): c.get("frame_url", "")
        for c in cached_entry.get("cortes", [])
        if c.get("id")
    }

    db_cuts = list_cortes(video_id=video_id)
    cortes_out: List[Dict[str, Any]] = []
    for idx, c in enumerate(db_cuts):
        s_time = float(c.get("start_time", 0.0))
        e_time = float(c.get("end_time", s_time + 1.0))
        dur = round(max(0.5, e_time - s_time), 1)
        out_p = c.get("output_path", "")
        video_url = f"/data/cortes/{Path(out_p).name}" if (out_p and os.path.exists(out_p)) else None
        cut_segs = [
            seg for seg in all_segments
            if float(seg.get("end", 0.0)) > s_time and float(seg.get("start", 0.0)) < e_time
        ]
        cortes_out.append({
            "index": idx,
            "id": int(c["id"]),
            "title": str(c.get("title") or f"Corte #{idx + 1}"),
            "start_time": s_time,
            "end_time": e_time,
            "duration": dur,
            "virality_score": round(normalize_virality_score(c.get("virality_score", 80.0), default=80.0), 1),
            "tipo": str(c.get("tipo") or ("short" if dur <= 180.0 else "longo")),
            "justification": str(c.get("justification") or ""),
            "frame_url": cached_frames_by_id.get(int(c["id"]), ""),
            "selected": True,
            "layout_mode": str(c.get("layout_mode") or "vertical_blur"),
            "layout_params": c.get("layout_params") if isinstance(c.get("layout_params"), dict) else {},
            "burn_subtitles": bool(c.get("burn_subtitles", True)),
            "status": str(c.get("status") or "pending"),
            "video_url": video_url,
            "seo_kit": c.get("seo_kit"),
            "segments": cut_segs,
        })

    # Reaquece o cache em memória para prévias instantâneas
    manager.set_analysis_cache(
        video_id,
        {
            "video_id": video_id,
            "url": video.get("url", ""),
            "title": video.get("title", ""),
            "duration": float(video.get("duration", 0.0)),
            "thumbnail": cached_entry.get("thumbnail", ""),
            "uploader": cached_entry.get("uploader", "Upload Local" if str(video.get("url", "")).startswith("local://") else ""),
            "file_path": video.get("file_path", ""),
            "audio_path": video.get("audio_path", ""),
            "direct_stream_url": cached_entry.get("direct_stream_url"),
            "transcript_data": {"segments": all_segments, "words": all_words},
            "cortes": cortes_out,
        },
    )

    return {
        "video_id": video_id,
        "url": str(video.get("url", "")),
        "title": str(video.get("title") or video.get("url") or f"Vídeo #{video_id}"),
        "duration": float(video.get("duration", 0.0)),
        "thumbnail": str(cached_entry.get("thumbnail", "")),
        "uploader": str(cached_entry.get("uploader", "")),
        "is_local": str(video.get("url", "")).startswith("local://"),
        "cortes": cortes_out,
        "transcript_segments": all_segments,
        "transcript_words": all_words,
    }


def _render_single_cut(
    *,
    job_id: str,
    video_id: int,
    video_file: str,
    cut_item: Any,
    idx: int,
    segments: List[Dict[str, Any]],
    words: List[Dict[str, Any]],
    cut_video_fn: Callable[..., str],
    manager: JobManager,
    brand_kit: Optional[Dict[str, Any]] = None,
    use_gpu: bool = False,
    normalize_audio: bool = False,
    denoise_audio: bool = False,
    music_path: Optional[str] = None,
    music_volume: float = 0.18,
    export_formats: Optional[List[str]] = None,
) -> Dict[str, Any]:
    cut_title = cut_item.get_effective_title(fallback=f"Corte_{idx + 1}")
    corte_db_id = cut_item.id
    if not corte_db_id or not get_corte(corte_db_id):
        corte_db_id = add_corte(
            video_id=video_id,
            start_time=cut_item.start_time,
            end_time=cut_item.end_time,
            title=cut_title,
            layout_mode=cut_item.layout_mode,
            layout_params=cut_item.layout_params,
            burn_subtitles=bool(cut_item.burn_subtitles),
            status="rendering",
        )
    else:
        update_corte(
            corte_db_id,
            title=cut_title,
            start_time=cut_item.start_time,
            end_time=cut_item.end_time,
            layout_mode=cut_item.layout_mode,
            layout_params=cut_item.layout_params,
            burn_subtitles=bool(cut_item.burn_subtitles),
            status="rendering",
        )

    lp = dict(cut_item.layout_params or {})
    inline_overrides = getattr(cut_item, "subtitle_overrides", None) or lp.get("subtitle_overrides")
    eff_segments = inline_overrides if inline_overrides else segments
    prefer_segs = bool(inline_overrides)

    common_kwargs: Dict[str, Any] = {
        "input_path": video_file,
        "start_time": cut_item.start_time,
        "end_time": cut_item.end_time,
        "layout_mode": cut_item.layout_mode,
        "layout_params": lp,
        "segments": eff_segments if cut_item.burn_subtitles else None,
        "words": words if (cut_item.burn_subtitles and not prefer_segs) else None,
        "burn_subtitles": bool(cut_item.burn_subtitles),
        "subtitle_preset": getattr(cut_item, "subtitle_preset", "hormozi"),
        "karaoke_subtitles": bool(getattr(cut_item, "karaoke_subtitles", True)),
        "prefer_segment_subtitles": prefer_segs,
        "dynamic_face_tracking": bool(getattr(cut_item, "dynamic_face_tracking", True)),
        "remove_silences": bool(getattr(cut_item, "remove_silences", False)),
        "min_silence_sec": float(getattr(cut_item, "min_silence_sec", 0.6)),
        "brand_kit": brand_kit,
        "hook_title": cut_title,
        "punch_in_zoom": bool(getattr(cut_item, "punch_in_zoom", False)),
        "use_gpu": use_gpu,
        "normalize_audio": normalize_audio,
        "denoise_audio": denoise_audio,
        "music_path": music_path,
        "music_volume": music_volume,
        "job_id": job_id,
        "cancel_check": lambda: manager.is_cancelled(job_id),
        "register_proc": lambda p: manager.register_process(job_id, p),
        "unregister_proc": lambda p: manager.unregister_process(job_id, p),
    }

    output_clip = cut_video_fn(**common_kwargs)
    update_corte_status(corte_db_id, status="completed", output_path=output_clip)

    # Multi-Export (Onda 3): renderiza formatos adicionais (1:1, 16:9, 9:16) se solicitados
    extra_exports: List[Dict[str, str]] = []
    fmt_map = {"9:16": "vertical_blur", "1:1": "square_1_1", "16:9": "horizontal_16_9"}
    for fmt in export_formats or []:
        target_mode = fmt_map.get(fmt)
        if not target_mode or target_mode == cut_item.layout_mode:
            continue
        if manager.is_cancelled(job_id):
            raise JobCancelledError(f"Job '{job_id}' cancelado durante multi-export.")
        suffix = fmt.replace(":", "x")
        extra_dest = str(Path(output_clip).with_name(f"{Path(output_clip).stem}_{suffix}.mp4"))
        extra_kwargs = dict(common_kwargs)
        extra_kwargs["layout_mode"] = target_mode
        extra_kwargs["output_path"] = extra_dest
        extra_out = cut_video_fn(**extra_kwargs)
        extra_exports.append({"format": fmt, "video_url": f"/data/cortes/{Path(extra_out).name}"})

    return {
        "id": corte_db_id,
        "title": cut_title,
        "layout_mode": cut_item.layout_mode,
        "output_path": output_clip,
        "video_url": f"/data/cortes/{Path(output_clip).name}",
        "start_time": cut_item.start_time,
        "end_time": cut_item.end_time,
        "extra_exports": extra_exports,
    }


def run_render_job(
    job_id: str,
    video_id: int,
    cuts: List[Any],
    max_resolution: int,
    *,
    manager: JobManager = job_manager,
    cache_dir: Optional[Path] = None,
    temp_dir: Optional[Path] = None,
    download_video_fn: Callable[..., str] = download_video,
    cut_video_fn: Callable[..., str] = cut_video,
    use_gpu: bool = False,
    brand_kit_id: Optional[int] = None,
    normalize_audio: bool = False,
    denoise_audio: bool = False,
    music_path: Optional[str] = None,
    music_volume: float = 0.18,
    export_formats: Optional[List[str]] = None,
) -> None:
    """Executa a esteira assíncrona de download (se remoto) e renderização FFmpeg V4.0."""
    token = request_id_var.set(job_id)
    target_cache_dir = cache_dir or CACHE_DIR
    target_temp_dir = temp_dir or media_utils.TEMP_DIR

    try:
        video = get_video(video_id)
        if not video:
            raise ValueError(f"Vídeo ID {video_id} não encontrado no banco de dados.")

        brand_kit = platform_repo.get_brand_kit(brand_kit_id) if brand_kit_id else None

        video_file = video.get("file_path", "")
        if not video_file or not os.path.exists(video_file):
            if str(video.get("url", "")).startswith("local://"):
                raise FileNotFoundError("Arquivo de vídeo local enviado não foi encontrado em data/downloads/.")
            manager.update_job(job_id, step=f"Baixando vídeo completo em alta qualidade ({max_resolution}p)...", progress=15)
            video_file = download_video_fn(video["url"], max_resolution=max_resolution)
            update_video_status(video_id, status="cutting", file_path=video_file)

        segments, words = _load_transcript_payload(
            video_id,
            cache_dir=target_cache_dir,
            temp_dir=target_temp_dir,
            manager=manager,
        )

        rendered_results: List[Dict[str, Any]] = []
        total_cuts = len(cuts)
        for idx, c in enumerate(cuts):
            if manager.is_cancelled(job_id):
                raise JobCancelledError(f"Job '{job_id}' cancelado pelo usuário.")
            cut_title = c.get_effective_title(fallback=f"Corte_{idx + 1}")
            pct = 25 + int((idx / max(1, total_cuts)) * 70)
            manager.update_job(
                job_id,
                step=f"Renderizando corte {idx + 1}/{total_cuts}: '{cut_title}' ({c.layout_mode})...",
                progress=pct,
            )
            rendered_results.append(
                _render_single_cut(
                    job_id=job_id,
                    video_id=video_id,
                    video_file=video_file,
                    cut_item=c,
                    idx=idx,
                    segments=segments,
                    words=words,
                    cut_video_fn=cut_video_fn,
                    manager=manager,
                    brand_kit=brand_kit,
                    use_gpu=use_gpu,
                    normalize_audio=normalize_audio,
                    denoise_audio=denoise_audio,
                    music_path=music_path,
                    music_volume=music_volume,
                    export_formats=export_formats,
                )
            )

        update_video_status(video_id, status="completed", file_path=video_file)
        manager.update_job(
            job_id,
            status="completed",
            step=f"{total_cuts} corte(s) renderizado(s) com sucesso!",
            progress=100,
            result={"video_id": video_id, "rendered_cuts": rendered_results},
        )
    except JobCancelledError as cancel_exc:
        logger.info("Renderização cancelada (job %s): %s", job_id, cancel_exc)
        manager.update_job(job_id, status="cancelled", step="Renderização cancelada pelo usuário.", error=str(cancel_exc))
    except Exception as exc:
        logger.exception("Erro crítico na esteira de renderização (job %s): %s", job_id, exc)
        if isinstance(video_id, int) and video_id > 0:
            try:
                update_video_status(video_id, status="failed")
            except Exception:
                pass
        manager.mark_job_error(job_id=job_id, job_type="render", step="Erro durante a renderização com FFmpeg", error=str(exc))
    finally:
        request_id_var.reset(token)
