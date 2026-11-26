"""
Serviços de domínio e orquestração das esteiras de análise IA e renderização FFmpeg.
Isola regras de negócio da camada HTTP FastAPI (server.py).
"""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException

from core.ai_apis import detect_viral_moments, transcribe_audio_groq
from core.config import CACHE_DIR
from core.downloader import download_audio_with_metadata, download_video, is_valid_video_url
from core.logging_config import request_id_var
from core.media_utils import TEMP_DIR, normalize_virality_score
from core.video_engine import cut_video, extract_preview_frame
from database.db_manager import (
    add_corte,
    add_video,
    get_corte,
    get_video,
    list_videos,
    save_analysis,
    update_corte,
    update_corte_status,
    update_video_status,
)
from services.job_manager import JobManager, job_manager

logger = logging.getLogger("easykut_media_service")


def resolve_trusted_video_source(
    video_id: Optional[int],
    source: Optional[str],
    manager: JobManager = job_manager,
) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Resolve a fonte de vídeo exclusivamente a partir de registros confiáveis já cadastrados
    (analyses_cache ou tabela SQLite 'videos'), prevenindo LFI e SSRF.
    Retorna (trusted_source_url, cached_stream_url, local_audio_path).
    """
    cached_stream: Optional[str] = None
    local_audio: Optional[str] = None
    trusted_url: Optional[str] = None

    if video_id is not None:
        entry = manager.get_analysis_cache(video_id)
        if entry:
            trusted_url = entry.get("url")
            cached_stream = entry.get("direct_stream_url")
            local_audio = entry.get("audio_path")

        if not trusted_url or not local_audio:
            video_row = get_video(video_id)
            if video_row:
                trusted_url = trusted_url or video_row.get("url")
                local_audio = local_audio or video_row.get("audio_path")

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
    """
    Executa a esteira assíncrona de ingestão, transcrição Whisper, curadoria IA
    e extração paralela (2 workers) dos frames de prévia.
    """
    token = request_id_var.set(job_id)
    target_cache_dir = cache_dir or CACHE_DIR
    target_cache_dir.mkdir(parents=True, exist_ok=True)
    video_id: Optional[int] = None

    try:
        # 1. Ingestão unificada: Metadados + Download de Áudio + Stream URL em 1 única chamada yt-dlp
        manager.update_job(job_id, step="[1/4] Extraindo metadados e baixando áudio leve (1 chamada yt-dlp)...", progress=15)
        ingest = download_audio_fn(url)
        info = ingest["info"]
        audio_file = ingest["audio_path"]
        direct_stream_url = ingest.get("direct_stream_url")

        video_title = info.get("title", "Vídeo Sem Título")
        video_duration = float(info.get("duration", 0.0))
        video_thumbnail = info.get("thumbnail", "")
        video_uploader = info.get("uploader", "")

        video_id = add_video(
            url=url,
            title=video_title,
            duration=video_duration,
            audio_path=audio_file,
            status="transcribing",
        )
        if not isinstance(video_id, int) or video_id <= 0 or not get_video(video_id):
            raise RuntimeError(f"Falha ao confirmar registro do vídeo na tabela 'videos' (video_id={video_id!r}).")

        # 2. Transcrição com Groq Whisper
        manager.update_job(job_id, step="[2/4] Transcrevendo áudio com Groq Whisper-large-v3...", progress=45)
        transcript_data = transcribe_fn(audio_file)

        # Salva cache de transcrição em data/cache/ (fora de StaticFiles)
        transcript_cache_path = target_cache_dir / f"transcript_{video_id}.json"
        try:
            with open(transcript_cache_path, "w", encoding="utf-8") as f_cache:
                json.dump(transcript_data, f_cache, ensure_ascii=False)
        except Exception as exc_cache:
            logger.warning("Não foi possível salvar cache de transcrição: %s", exc_cache)

        # 3. Curadoria Viral com IA
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

        # 4. Extração paralela e limitada (2 workers) dos frames de prévia
        manager.update_job(job_id, step="[4/4] Capturando frames de prévia de alta velocidade...", progress=85)

        all_segments = transcript_data.get("segments", []) or []

        def _extract_cut_frame(c_item: Dict[str, Any]) -> Optional[str]:
            s_time = max(0.0, float(c_item.get("start_time", 0.0)))
            ts_frame = min(video_duration - 1.0, s_time + 1.0) if video_duration > 2.0 else s_time
            try:
                return extract_frame_fn(
                    source=url,
                    timestamp_sec=max(0.0, ts_frame),
                    direct_stream_url=direct_stream_url,
                )
            except Exception as exc_fr:
                logger.warning("Falha não-crítica ao extrair frame do corte '%s': %s", c_item.get("title"), exc_fr)
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            extracted_frames = list(pool.map(_extract_cut_frame, cortes_detectados))

        cortes_formatados: List[Dict[str, Any]] = []
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

            cortes_formatados.append({
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

        if not isinstance(video_id, int) or video_id <= 0 or not get_video(video_id):
            logger.warning("video_id %r ausente em 'videos' antes de save_analysis; recriando registro pai.", video_id)
            video_id = add_video(
                url=url,
                title=video_title,
                duration=video_duration,
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
                "cortes": cortes_formatados,
                "transcript_segments": all_segments,
            },
        )
    except Exception as exc:
        logger.exception("Erro crítico na esteira de análise (job %s): %s", job_id, exc)
        if isinstance(video_id, int) and video_id > 0:
            try:
                update_video_status(video_id, status="failed")
            except Exception:
                pass
        manager.mark_job_error(
            job_id=job_id,
            job_type="analyze",
            step="Erro durante o processamento da análise",
            error=str(exc),
        )
    finally:
        request_id_var.reset(token)


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
) -> None:
    """Executa a esteira assíncrona de download do vídeo completo e renderização FFmpeg."""
    token = request_id_var.set(job_id)
    target_cache_dir = cache_dir or CACHE_DIR
    target_temp_dir = temp_dir or TEMP_DIR

    try:
        video = get_video(video_id)
        if not video:
            raise ValueError(f"Vídeo ID {video_id} não encontrado no banco de dados.")

        video_file = video.get("file_path", "")
        if not video_file or not os.path.exists(video_file):
            manager.update_job(job_id, step=f"Baixando vídeo completo em alta qualidade ({max_resolution}p)...", progress=15)
            video_file = download_video_fn(video["url"], max_resolution=max_resolution)
            update_video_status(video_id, status="cutting", file_path=video_file)

        segments = []
        transcript_cache_path = target_cache_dir / f"transcript_{video_id}.json"
        if not transcript_cache_path.exists():
            transcript_cache_path = target_temp_dir / f"transcript_{video_id}.json"
        if transcript_cache_path.exists():
            try:
                with open(transcript_cache_path, "r", encoding="utf-8") as f_tr:
                    tr_data = json.load(f_tr)
                    segments = tr_data.get("segments", [])
            except Exception as exc_tr:
                logger.warning("Erro ao ler transcrição em cache: %s", exc_tr)
        else:
            cached_entry = manager.get_analysis_cache(video_id)
            if cached_entry:
                segments = cached_entry.get("transcript_data", {}).get("segments", [])

        rendered_results: List[Dict[str, Any]] = []
        total_cuts = len(cuts)

        for idx, c in enumerate(cuts):
            cut_title = c.get_effective_title(fallback=f"Corte_{idx + 1}")
            pct = 25 + int((idx / max(1, total_cuts)) * 70)
            manager.update_job(
                job_id,
                step=f"Renderizando corte {idx + 1}/{total_cuts}: '{cut_title}' ({c.layout_mode})...",
                progress=pct,
            )

            corte_db_id = c.id
            if not corte_db_id or not get_corte(corte_db_id):
                corte_db_id = add_corte(
                    video_id=video_id,
                    start_time=c.start_time,
                    end_time=c.end_time,
                    title=cut_title,
                    layout_mode=c.layout_mode,
                    layout_params=c.layout_params,
                    burn_subtitles=bool(c.burn_subtitles),
                    status="rendering",
                )
            else:
                update_corte(
                    corte_db_id,
                    title=cut_title,
                    start_time=c.start_time,
                    end_time=c.end_time,
                    layout_mode=c.layout_mode,
                    layout_params=c.layout_params,
                    burn_subtitles=bool(c.burn_subtitles),
                    status="rendering",
                )

            output_clip = cut_video_fn(
                input_path=video_file,
                start_time=c.start_time,
                end_time=c.end_time,
                layout_mode=c.layout_mode,
                layout_params=c.layout_params or {},
                segments=segments if c.burn_subtitles else None,
                burn_subtitles=bool(c.burn_subtitles),
            )

            update_corte_status(corte_db_id, status="completed", output_path=output_clip)

            rendered_results.append({
                "id": corte_db_id,
                "title": cut_title,
                "layout_mode": c.layout_mode,
                "output_path": output_clip,
                "video_url": f"/data/cortes/{Path(output_clip).name}",
                "start_time": c.start_time,
                "end_time": c.end_time,
            })

        update_video_status(video_id, status="completed", file_path=video_file)

        manager.update_job(
            job_id,
            status="completed",
            step=f"{total_cuts} corte(s) renderizado(s) com sucesso!",
            progress=100,
            result={
                "video_id": video_id,
                "rendered_cuts": rendered_results,
            },
        )
    except Exception as exc:
        logger.exception("Erro crítico na esteira de renderização (job %s): %s", job_id, exc)
        if isinstance(video_id, int) and video_id > 0:
            try:
                update_video_status(video_id, status="failed")
            except Exception:
                pass
        manager.mark_job_error(
            job_id=job_id,
            job_type="render",
            step="Erro durante a renderização com FFmpeg",
            error=str(exc),
        )
    finally:
        request_id_var.reset(token)
