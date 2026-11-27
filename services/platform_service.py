"""
Serviços da plataforma V4.0 (Ondas 3 e 4):
- Geração e persistência de Kit de Publicação SEO IA
- Exportação segura em lote (.zip) dos cortes renderizados de um vídeo
- Job assíncrono de Highlight Reel (xfade + acrossfade)
- Agendamento e disparo de publicações multiplataforma
"""

import io
import logging
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Tuple
import zipfile

from fastapi import HTTPException

from core import media_utils
from core.ai_apis import generate_seo_publish_kit
from core.highlight_reel import compile_highlight_reel
from core.logging_config import request_id_var
from core.media_renderer import JobCancelledError
from core.publishers import dispatch_publish, validate_publish_media
from database import platform_repo
from database.db_manager import get_corte, get_video, list_cortes, update_corte
from services.job_manager import JobManager, job_manager
from services.media_service import _load_transcript_segments

logger = logging.getLogger("easykut_platform_service")


def generate_and_save_seo_kit(
    *,
    corte_id: int,
    provider: str = "gemini",
    cache_dir: Path,
    temp_dir: Path,
    manager: JobManager = job_manager,
    seo_fn: Callable[..., Dict[str, Any]] = generate_seo_publish_kit,
) -> Dict[str, Any]:
    """Gera títulos A/B, descrição e hashtags para um corte e persiste em cortes.seo_kit_json."""
    corte = get_corte(corte_id)
    if not corte:
        raise HTTPException(status_code=404, detail=f"Corte ID {corte_id} não encontrado.")

    segments = _load_transcript_segments(
        int(corte["video_id"]),
        cache_dir=cache_dir,
        temp_dir=temp_dir,
        manager=manager,
    )
    s_time = float(corte.get("start_time", 0.0))
    e_time = float(corte.get("end_time", s_time + 1.0))
    excerpt = " ".join(
        str(seg.get("text", "")).strip()
        for seg in segments
        if float(seg.get("end", 0.0)) > s_time and float(seg.get("start", 0.0)) < e_time
    ).strip()

    kit = seo_fn(
        cut_title=str(corte.get("title") or "Corte Viral"),
        transcript_excerpt=excerpt,
        justification=str(corte.get("justification") or ""),
        provider=provider,
    )
    update_corte(corte_id, seo_kit=kit)
    return {"corte_id": corte_id, "seo_kit": kit}


def build_video_cuts_zip(video_id: int) -> Tuple[bytes, str]:
    """
    Empacota em memória (.zip) todos os arquivos MP4 renderizados de um vídeo,
    verificando confinamento estrito em DATA_DIR.
    """
    video = get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail=f"Vídeo ID {video_id} não encontrado.")

    cortes = list_cortes(video_id=video_id)
    valid_files: List[Tuple[Path, str]] = []
    seen_names: set[str] = set()

    for idx, c in enumerate(cortes):
        out_p = c.get("output_path") or ""
        if not out_p:
            continue
        p = Path(out_p).resolve()
        if media_utils.is_path_within(p, media_utils.DATA_DIR_RESOLVED) and p.exists() and p.is_file():
            safe_title = re.sub(r"[^a-zA-Z0-9_-]", "_", str(c.get("title") or f"corte_{idx + 1}"))[:50]
            arc_name = f"{idx + 1:02d}_{safe_title}{p.suffix or '.mp4'}"
            while arc_name in seen_names:
                arc_name = f"{idx + 1:02d}_{p.name}"
            seen_names.add(arc_name)
            valid_files.append((p, arc_name))

    if not valid_files:
        raise HTTPException(status_code=400, detail="Nenhum corte renderizado em disco disponível para exportar em ZIP.")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path, arc_name in valid_files:
            zf.write(str(file_path), arcname=arc_name)

    safe_vid = re.sub(r"[^a-zA-Z0-9_-]", "_", str(video.get("title") or f"video_{video_id}"))[:40]
    return buf.getvalue(), f"easykut_{video_id}_{safe_vid}.zip"


def run_highlight_reel_job(
    *,
    job_id: str,
    video_id: int,
    corte_ids: List[int],
    transition: str,
    transition_duration: float,
    title: str,
    use_gpu: bool = False,
    manager: JobManager = job_manager,
    compile_fn: Callable[..., str] = compile_highlight_reel,
) -> None:
    """Executa job assíncrono de compilação Highlight Reel (xfade + acrossfade)."""
    token = request_id_var.set(job_id)
    try:
        manager.update_job(job_id, step=f"Validando {len(corte_ids)} cortes para Highlight Reel...", progress=20)
        clip_paths: List[str] = []
        for cid in corte_ids:
            corte = get_corte(int(cid))
            if not corte or int(corte["video_id"]) != int(video_id):
                raise ValueError(f"Corte ID {cid} não pertence ao vídeo {video_id}.")
            out_p = corte.get("output_path") or ""
            p = Path(out_p).resolve() if out_p else None
            if not p or not media_utils.is_path_within(p, media_utils.DATA_DIR_RESOLVED) or not p.exists():
                raise FileNotFoundError(f"Corte '{corte.get('title', cid)}' precisa estar renderizado antes da compilação.")
            clip_paths.append(str(p))

        manager.update_job(job_id, step=f"Compilando Highlight Reel com transição '{transition}'...", progress=55)
        reel_path = compile_fn(
            clip_paths,
            transition=transition,
            transition_duration=transition_duration,
            use_gpu=use_gpu,
            job_id=job_id,
            cancel_check=lambda: manager.is_cancelled(job_id),
            register_proc=lambda pr: manager.register_process(job_id, pr),
            unregister_proc=lambda pr: manager.unregister_process(job_id, pr),
        )
        manager.update_job(
            job_id,
            status="completed",
            step="Highlight Reel compilado com sucesso!",
            progress=100,
            result={
                "video_id": video_id,
                "title": title,
                "transition": transition,
                "clip_count": len(clip_paths),
                "video_url": f"/data/cortes/{Path(reel_path).name}",
            },
        )
    except JobCancelledError as cancel_exc:
        manager.update_job(job_id, status="cancelled", step="Compilação cancelada pelo usuário.", error=str(cancel_exc))
    except Exception as exc:
        logger.exception("Erro ao compilar Highlight Reel (job %s): %s", job_id, exc)
        manager.mark_job_error(job_id=job_id, job_type="reel", step="Erro ao compilar Highlight Reel", error=str(exc))
    finally:
        request_id_var.reset(token)


def schedule_or_publish_corte(
    *,
    corte_id: int,
    platform: str,
    title: str,
    caption: str,
    hashtags: str,
    scheduled_at: str,
    publish_now: bool = False,
) -> Dict[str, Any]:
    """Valida mídia do corte, dispara publicação se publish_now=True e persiste em scheduled_posts."""
    corte = get_corte(corte_id)
    if not corte:
        raise HTTPException(status_code=404, detail=f"Corte ID {corte_id} não encontrado.")

    try:
        media_file = validate_publish_media(str(corte.get("output_path") or ""))
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    post_status = "scheduled"
    ext_id = ""
    err_msg = ""
    dispatch_msg = "Publicação agendada com sucesso."

    if publish_now:
        res = dispatch_publish(
            platform=platform,
            video_path=str(media_file),
            title=title,
            caption=caption,
            hashtags=hashtags,
        )
        post_status = res.get("status", "scheduled")
        ext_id = res.get("external_id", "")
        err_msg = res.get("error", "")
        dispatch_msg = res.get("message", dispatch_msg)

    post = platform_repo.create_scheduled_post(
        corte_id=corte_id,
        platform=platform,
        title=title,
        caption=caption,
        hashtags=hashtags,
        scheduled_at=scheduled_at,
        status=post_status,
        external_id=ext_id,
        error=err_msg,
    )
    return {"success": True, "message": dispatch_msg, "post": post}
