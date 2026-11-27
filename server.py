"""
Servidor Backend FastAPI do EasyKut Studio (V3.2)
Camada HTTP fina responsável por rotas REST, middlewares de segurança/correlação,
upload seguro de vídeos locais, reabertura de workspace e arquivos estáticos.
"""

import logging
import os
from pathlib import Path
import re
import shutil
import subprocess  # nosec B404
import sys
import threading
from typing import Any, Dict, List, Literal, Optional
import uuid

# Garante inclusão do diretório raiz no sys.path e define BASE_DIR absoluto
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from core.ai_apis import detect_viral_moments, generate_seo_publish_kit, transcribe_audio_groq
from core.broll_engine import suggest_broll_assets
from core.config import (
    BRANDS_DIR,
    BROLL_DIR,
    CACHE_DIR,
    DATA_DIR,
    FFMPEG_AVAILABLE,
    MUSIC_DIR,
    validate_config,
)
from core.downloader import (
    download_audio_with_metadata,
    download_video,
    is_valid_video_url,
)
from core.hw_accel import detect_gpu_encoder
from core.logging_config import configure_logging, request_id_var
from core.media_utils import normalize_virality_score
from core.publishers import get_publish_providers_status
from core.subtitles import SUBTITLE_PRESETS
from core.video_engine import (
    cleanup_temporary_files,
    cut_video,
    extract_preview_clip,
    extract_preview_frame,
    get_video_metadata,
)
from database import platform_repo
from database.db_manager import (
    add_corte,
    delete_corte,
    delete_video,
    get_connection,
    get_corte,
    get_video,
    init_db,
    list_videos_with_cortes,
    update_corte,
)
from schemas.api_schemas import (
    AllowedLayoutMode,
    AnalyzeRequest,
    BrandKitCreateRequest,
    BrollSuggestRequest,
    CleanupRequest,
    CleanupResponse,
    ConfigResponse,
    CorteMutationResponse,
    CreateCorteRequest,
    FrameRequest,
    FrameResponse,
    HealthResponse,
    HighlightReelRequest,
    JobAcceptedResponse,
    MessageResponse,
    PreviewClipRequest,
    PreviewClipResponse,
    PublishScheduleRequest,
    RenderCutItem,
    RenderRequest,
    SeoKitRequest,
    UpdateCorteRequest,
    WorkspaceResponse,
)
from services.job_manager import job_manager
from services.media_service import (
    load_video_workspace as _service_load_workspace,
    resolve_trusted_video_source as _service_resolve_source,
    run_analyze_job as _service_run_analyze_job,
    run_local_video_analyze_job as _service_run_local_analyze_job,
    run_render_job as _service_run_render_job,
)
from services.platform_service import (
    build_video_cuts_zip as _service_build_zip,
    generate_and_save_seo_kit as _service_generate_seo,
    run_highlight_reel_job as _service_run_reel_job,
    schedule_or_publish_corte as _service_schedule_post,
)

# Configuração de Logging Rotativo + Correlation ID
configure_logging()
logger = logging.getLogger("easykut_server")

# Inicializa banco de dados SQLite (migrations v1 -> v4) e recupera jobs órfãos
init_db()
job_manager.recover_orphaned_jobs()

# Pastas de dados e interface web
TEMP_DIR = (BASE_DIR / "data" / "temp").resolve()
CUTS_DIR = (BASE_DIR / "data" / "cortes").resolve()
DOWNLOADS_DIR = (BASE_DIR / "data" / "downloads").resolve()
WEB_DIR = (BASE_DIR / "web").resolve()

for _d in (TEMP_DIR, CUTS_DIR, DOWNLOADS_DIR, CACHE_DIR, BRANDS_DIR, MUSIC_DIR, BROLL_DIR, WEB_DIR):
    _d.mkdir(parents=True, exist_ok=True)

ALLOWED_UPLOAD_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
ALLOWED_LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_MUSIC_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1 GB
MAX_LOGO_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_MUSIC_BYTES = 30 * 1024 * 1024  # 30 MB
UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MB


def _is_valid_image_magic_bytes(header: bytes, ext: str) -> bool:
    if len(header) < 8:
        return False
    if ext == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in (".jpg", ".jpeg"):
        return header.startswith(b"\xff\xd8\xff")
    if ext == ".webp":
        return header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    return False


def _is_valid_audio_magic_bytes(header: bytes, ext: str) -> bool:
    if len(header) < 4:
        return False
    if ext == ".mp3":
        return header.startswith(b"ID3") or (header[0] == 0xFF and (header[1] & 0xE0) == 0xE0)
    if ext == ".wav":
        return header.startswith(b"RIFF") and header[8:12] == b"WAVE"
    if ext == ".ogg":
        return header.startswith(b"OggS")
    if ext in (".m4a", ".aac"):
        return b"ftyp" in header[:16] or (header[0] == 0xFF and (header[1] & 0xF0) == 0xF0)
    return False


def _is_valid_video_magic_bytes(header: bytes, ext: str) -> bool:
    """Valida assinatura binária (magic bytes) básica de contêineres MP4/MOV/MKV/WebM."""
    if len(header) < 8:
        return False
    if ext in (".mp4", ".mov", ".m4v"):
        return b"ftyp" in header[:16] or header[4:8] in (b"moov", b"mdat", b"wide", b"free")
    if ext in (".mkv", ".webm"):
        return header.startswith(b"\x1a\x45\xdf\xa3")
    return False


def _migrate_legacy_transcripts_to_cache() -> None:
    """Move arquivos transcript_*.json legados de data/temp/ para data/cache/."""
    for legacy_file in TEMP_DIR.glob("transcript_*.json"):
        try:
            dest = CACHE_DIR / legacy_file.name
            if not dest.exists():
                shutil.move(str(legacy_file), str(dest))
            else:
                legacy_file.unlink()
        except Exception as exc:
            logger.warning("Não foi possível migrar transcrição legada '%s': %s", legacy_file.name, exc)


_migrate_legacy_transcripts_to_cache()
cleanup_temporary_files(temp_dir=TEMP_DIR, remove_raw_downloads=False, max_age_hours=24.0)

# Instância FastAPI
app = FastAPI(
    title="EasyKut Studio API",
    version="4.0.0",
    description="Plataforma local completa de curadoria IA, edição viral, Brand Kit, GPU, Highlight Reel e publicação.",
)

ALLOWED_ORIGINS = [
    "http://127.0.0.1:8502",
    "http://localhost:8502",
]
ALLOWED_HOSTS = {
    "127.0.0.1:8502",
    "localhost:8502",
    "testserver",
    "testserver:80",
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Accept", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-Total-Count"],
)


@app.middleware("http")
async def local_security_middleware(request: Request, call_next):
    """
    Proteção contra DNS rebinding, CSRF cross-origin em rotas mutáveis /api/*,
    rastreamento por X-Request-ID e cabeçalhos defensivos padrão.
    """
    incoming_req_id = (request.headers.get("x-request-id") or "").strip()
    req_id = incoming_req_id if (incoming_req_id and re.fullmatch(r"[A-Za-z0-9_.-]{4,64}", incoming_req_id)) else uuid.uuid4().hex[:12]

    token = request_id_var.set(req_id)
    try:
        host_header = (request.headers.get("host") or "").strip().lower()
        if host_header and host_header not in ALLOWED_HOSTS:
            logger.warning("Requisição bloqueada por Host não permitido: %s", host_header)
            resp = JSONResponse(status_code=403, content={"detail": "Host não autorizado."})
            resp.headers["X-Request-ID"] = req_id
            return resp

        if request.url.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = (request.headers.get("origin") or "").strip()
            if origin and origin not in ALLOWED_ORIGINS:
                logger.warning("Requisição mutável bloqueada por Origin não permitido: %s", origin)
                resp = JSONResponse(status_code=403, content={"detail": "Origem não autorizada."})
                resp.headers["X-Request-ID"] = req_id
                return resp

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers["X-Request-ID"] = req_id
        return response
    finally:
        request_id_var.reset(token)


# ==========================================
# ALIASES DE ESTADO (DELEGADOS AO JobManager)
# ==========================================
MAX_CONCURRENT_ANALYZE_JOBS = job_manager.max_concurrent_analyze
MAX_CONCURRENT_RENDER_JOBS = job_manager.max_concurrent_render
preview_ffmpeg_semaphore = job_manager.preview_ffmpeg_semaphore
jobs_lock = job_manager.lock
jobs = job_manager.jobs
analyses_cache = job_manager.analyses_cache


def update_job(
    job_id: str,
    status: Optional[str] = None,
    step: Optional[str] = None,
    progress: Optional[int] = None,
    result: Optional[Any] = None,
    error: Optional[str] = None,
) -> None:
    job_manager.update_job(job_id=job_id, status=status, step=step, progress=progress, result=result, error=error)


# ==========================================
# WRAPPERS INTERNOS DE SERVIÇO
# ==========================================
def _resolve_trusted_video_source(video_id: Optional[int], source: Optional[str]) -> tuple[str, Optional[str], Optional[str]]:
    return _service_resolve_source(video_id=video_id, source=source, manager=job_manager)


def _run_analyze_job(job_id: str, url: str, provider: str, max_cuts: int) -> None:
    _service_run_analyze_job(
        job_id=job_id,
        url=url,
        provider=provider,
        max_cuts=max_cuts,
        manager=job_manager,
        cache_dir=CACHE_DIR,
        download_audio_fn=download_audio_with_metadata,
        transcribe_fn=transcribe_audio_groq,
        detect_fn=detect_viral_moments,
        extract_frame_fn=extract_preview_frame,
    )


def _run_local_analyze_job(job_id: str, video_file_path: str, original_title: str, provider: str, max_cuts: int) -> None:
    _service_run_local_analyze_job(
        job_id=job_id,
        video_file_path=video_file_path,
        original_title=original_title,
        provider=provider,
        max_cuts=max_cuts,
        manager=job_manager,
        cache_dir=CACHE_DIR,
        temp_dir=TEMP_DIR,
        metadata_fn=get_video_metadata,
        transcribe_fn=transcribe_audio_groq,
        detect_fn=detect_viral_moments,
        extract_frame_fn=extract_preview_frame,
    )


def _run_render_job(
    job_id: str,
    video_id: int,
    cuts: List[RenderCutItem],
    max_resolution: int,
    *,
    use_gpu: bool = False,
    brand_kit_id: Optional[int] = None,
    normalize_audio: bool = False,
    denoise_audio: bool = False,
    music_path: Optional[str] = None,
    music_volume: float = 0.18,
    export_formats: Optional[List[str]] = None,
) -> None:
    _service_run_render_job(
        job_id=job_id,
        video_id=video_id,
        cuts=cuts,
        max_resolution=max_resolution,
        manager=job_manager,
        cache_dir=CACHE_DIR,
        temp_dir=TEMP_DIR,
        download_video_fn=download_video,
        cut_video_fn=cut_video,
        use_gpu=use_gpu,
        brand_kit_id=brand_kit_id,
        normalize_audio=normalize_audio,
        denoise_audio=denoise_audio,
        music_path=music_path,
        music_volume=music_volume,
        export_formats=export_formats,
    )


# ==========================================
# ROTAS DA API REST (/api)
# ==========================================
@app.get("/api/health", response_model=HealthResponse)
def health_check(response: Response):
    """Healthcheck operacional: verifica SQLite, FFmpeg, espaço em disco e fila de jobs."""
    db_ok = False
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1;").fetchone()
        db_ok = True
    except Exception as exc:
        logger.error("Healthcheck SQLite falhou: %s", exc)

    try:
        disk_usage = shutil.disk_usage(str(DATA_DIR))
        disk_free_mb = round(disk_usage.free / (1024 * 1024), 1)
    except Exception:
        disk_free_mb = -1.0

    counts = job_manager.active_counts()
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="ok" if db_ok else "degraded",
        version=app.version,
        db_ok=db_ok,
        ffmpeg_available=bool(FFMPEG_AVAILABLE),
        disk_free_mb=disk_free_mb,
        active_jobs=counts,
        gpu_encoder=detect_gpu_encoder() if FFMPEG_AVAILABLE else None,
    )


@app.get("/api/config", response_model=ConfigResponse)
def get_config():
    """Retorna status das chaves de API, disponibilidade do FFmpeg, cookies e provedor padrão."""
    is_ready, details = validate_config()
    return ConfigResponse(
        ready=is_ready,
        keys={
            "GROQ_API_KEY": bool(details.get("GROQ_API_KEY")),
            "GEMINI_API_KEY": bool(details.get("GEMINI_API_KEY")),
            "FFMPEG_AVAILABLE": bool(details.get("FFMPEG")),
            "COOKIES_TXT": bool(details.get("COOKIES_TXT")),
        },
        default_provider="gemini" if details.get("GEMINI_API_KEY") else "groq",
        status_details=details,
    )


@app.post("/api/analyze", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse)
def start_analyze(req: AnalyzeRequest):
    """Inicia a esteira assíncrona de análise IA para uma URL pública suportada (202 Accepted)."""
    target_url = (req.url or "").strip()
    if not target_url.startswith(("http://", "https://")):
        target_url = "https://" + target_url

    if not is_valid_video_url(target_url):
        raise HTTPException(status_code=400, detail="URL de vídeo inválida ou não suportada.")

    job_id = job_manager.create_job(job_type="analyze", step="Iniciando esteira de análise...", progress=5)
    if job_id is None:
        raise HTTPException(status_code=429, detail="Limite de análises simultâneas atingido. Aguarde a conclusão do job atual.")

    threading.Thread(
        target=_run_analyze_job,
        args=(job_id, target_url, req.provider or "gemini", req.max_cuts or 5),
        daemon=True,
    ).start()

    return JobAcceptedResponse(job_id=job_id, message="Análise de vídeo iniciada com sucesso em segundo plano.")


@app.post("/api/upload", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse)
async def upload_local_video(
    file: UploadFile = File(...),
    provider: Literal["gemini", "groq"] = Form("gemini"),
    max_cuts: int = Form(5, ge=1, le=20),
):
    """
    Recebe upload de arquivo de vídeo local (.mp4/.mov/.mkv/.webm/.m4v), valida extensão,
    magic bytes e tamanho máximo em streaming seguro dentro de data/downloads/ e inicia a análise IA.
    """
    orig_name = Path(file.filename or "video.mp4").name
    ext = Path(orig_name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Formato não suportado ('{ext or 'sem extensão'}'). Use: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}.",
        )

    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest_path = (DOWNLOADS_DIR / f"local_{uuid.uuid4().hex[:12]}{ext}").resolve()

    total_bytes = 0
    first_chunk = b""
    try:
        with open(dest_path, "wb") as out_f:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                if not first_chunk:
                    first_chunk = chunk[:32]
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Arquivo excede o limite máximo permitido de 1 GB.")
                out_f.write(chunk)
    except HTTPException:
        dest_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        dest_path.unlink(missing_ok=True)
        logger.error("Erro ao receber upload local: %s", exc)
        raise HTTPException(status_code=500, detail="Erro interno ao salvar vídeo enviado.")
    finally:
        await file.close()

    if total_bytes == 0 or not _is_valid_video_magic_bytes(first_chunk, ext):
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Arquivo enviado está vazio ou não possui assinatura válida de vídeo.")

    job_id = job_manager.create_job(job_type="analyze", step="Vídeo recebido! Iniciando análise local...", progress=5)
    if job_id is None:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=429, detail="Limite de análises simultâneas atingido. Aguarde a conclusão do job atual.")

    title = Path(orig_name).stem or "Vídeo Local"
    threading.Thread(
        target=_run_local_analyze_job,
        args=(job_id, str(dest_path), title, provider, max_cuts),
        daemon=True,
    ).start()

    return JobAcceptedResponse(job_id=job_id, message="Upload recebido e análise iniciada em segundo plano.")


@app.get("/api/videos/{video_id}/workspace", response_model=WorkspaceResponse)
def get_video_workspace(video_id: int):
    """Carrega vídeo, cortes salvos e transcrição em cache para reabertura direta no Estúdio."""
    workspace = _service_load_workspace(
        video_id=video_id,
        manager=job_manager,
        cache_dir=CACHE_DIR,
        temp_dir=TEMP_DIR,
    )
    if not workspace:
        raise HTTPException(status_code=404, detail=f"Vídeo ID {video_id} não encontrado.")
    return WorkspaceResponse(**workspace)


@app.get("/api/jobs")
def list_jobs(limit: int = Query(30, ge=1, le=100)):
    """Lista jobs recentes em execução e histórico persistido no SQLite."""
    return {"jobs": job_manager.list_jobs(limit=limit)}


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    """Retorna cópia profunda segura do estado atual, progresso e resultado/erro de um job."""
    snapshot = job_manager.get_job_snapshot(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' não encontrado.")
    return snapshot


@app.delete("/api/jobs/{job_id}")
def cancel_job(job_id: str):
    """Cancela um job em andamento e encerra com segurança qualquer processo FFmpeg associado."""
    cancelled = job_manager.cancel_job(job_id)
    if cancelled is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' não encontrado.")
    return {"success": True, "job": cancelled}


@app.post("/api/frame", response_model=FrameResponse)
def get_frame(req: FrameRequest):
    """Extrai 1 frame JPEG no timestamp informado a partir de vídeo previamente cadastrado."""
    trusted_url, cached_stream, _ = _resolve_trusted_video_source(req.video_id, req.source)

    if not preview_ffmpeg_semaphore.acquire(timeout=1.5):
        raise HTTPException(status_code=429, detail="Muitas extrações de prévia em andamento. Tente novamente em instantes.")

    try:
        frame_path = extract_preview_frame(source=trusted_url, timestamp_sec=req.timestamp_sec, direct_stream_url=cached_stream)
        if (not frame_path or not os.path.exists(frame_path)) and cached_stream:
            frame_path = extract_preview_frame(source=trusted_url, timestamp_sec=req.timestamp_sec, direct_stream_url=None)
        if not frame_path or not os.path.exists(frame_path):
            raise HTTPException(status_code=500, detail="Não foi possível extrair o frame solicitado via FFmpeg.")

        return FrameResponse(success=True, frame_url=f"/data/temp/{Path(frame_path).name}", timestamp_sec=req.timestamp_sec)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Erro ao extrair frame no tempo %s: %s", req.timestamp_sec, exc)
        raise HTTPException(status_code=500, detail="Erro interno ao extrair frame de prévia.")
    finally:
        preview_ffmpeg_semaphore.release()


@app.post("/api/preview-clip", response_model=PreviewClipResponse)
def get_preview_clip(req: PreviewClipRequest):
    """Gera e retorna clipe leve (.mp4 com áudio) para prévia em tempo real no Canvas."""
    if req.end_time <= req.start_time:
        raise HTTPException(status_code=400, detail="end_time deve ser maior que start_time.")

    trusted_url, cached_stream, local_audio = _resolve_trusted_video_source(req.video_id, req.source)

    if not preview_ffmpeg_semaphore.acquire(timeout=1.5):
        raise HTTPException(status_code=429, detail="Muitas prévias em vídeo sendo geradas simultaneamente. Aguarde alguns segundos.")

    try:
        clip_path = extract_preview_clip(
            source=trusted_url,
            start_time=req.start_time,
            end_time=req.end_time,
            direct_stream_url=cached_stream,
            local_audio_path=local_audio,
        )
        if not clip_path or not os.path.exists(clip_path):
            raise HTTPException(status_code=500, detail="Não foi possível gerar o vídeo de prévia para este corte.")

        return PreviewClipResponse(
            success=True,
            clip_url=f"/data/temp/{Path(clip_path).name}",
            start_time=req.start_time,
            end_time=req.end_time,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Erro ao gerar clipe de prévia (%ss -> %ss): %s", req.start_time, req.end_time, exc)
        raise HTTPException(status_code=500, detail="Erro interno ao gerar clipe de prévia.")
    finally:
        preview_ffmpeg_semaphore.release()


@app.post("/api/render", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse)
def start_render(req: RenderRequest):
    """Inicia a renderização assíncrona em background dos cortes selecionados (202 Accepted)."""
    if not req.cuts:
        raise HTTPException(status_code=400, detail="Nenhum corte selecionado para renderização.")

    video = get_video(req.video_id)
    if not video:
        raise HTTPException(status_code=404, detail=f"Vídeo ID {req.video_id} não encontrado.")

    resolved_music_path: Optional[str] = None
    if req.music_filename:
        safe_music_name = Path(req.music_filename).name
        cand = (MUSIC_DIR / safe_music_name).resolve()
        if cand.exists() and cand.is_file() and cand.is_relative_to(MUSIC_DIR.resolve()):
            resolved_music_path = str(cand)

    job_id = job_manager.create_job(job_type="render", step=f"Iniciando renderização de {len(req.cuts)} corte(s)...", progress=5)
    if job_id is None:
        raise HTTPException(status_code=429, detail="Limite de renderizações simultâneas atingido. Aguarde o job em andamento terminar.")

    threading.Thread(
        target=_run_render_job,
        args=(job_id, req.video_id, req.cuts, req.max_resolution or 1080),
        kwargs={
            "use_gpu": bool(req.use_gpu),
            "brand_kit_id": req.brand_kit_id,
            "normalize_audio": bool(req.normalize_audio),
            "denoise_audio": bool(req.denoise_audio),
            "music_path": resolved_music_path,
            "music_volume": float(req.music_volume),
            "export_formats": req.export_formats,
        },
        daemon=True,
    ).start()

    return JobAcceptedResponse(job_id=job_id, message="Processo de renderização iniciado em segundo plano.")


@app.get("/api/history")
def get_history(
    response: Response,
    search: Optional[str] = Query(None, description="Busca textual por título ou URL"),
    min_score: Optional[float] = Query(0.0, ge=0.0, le=100.0, description="Nota mínima de viralidade (0 a 100)"),
    limit: int = Query(50, ge=1, le=200, description="Máximo de vídeos por página"),
    offset: int = Query(0, ge=0, description="Deslocamento de paginação"),
):
    """Retorna vídeos paginados com seus cortes em consulta em lote (sem N+1) + X-Total-Count."""
    videos, total_count = list_videos_with_cortes(search=search, limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(total_count)

    existing_cut_names = {p.name for p in CUTS_DIR.iterdir() if p.is_file()} if CUTS_DIR.exists() else set()

    history_items: List[Dict[str, Any]] = []
    for v in videos:
        filtered_cuts: List[Dict[str, Any]] = []
        for c in v.get("cortes", []):
            norm_score = normalize_virality_score(c.get("virality_score", 0.0), default=0.0)
            if min_score is None or norm_score >= min_score:
                out_p = c.get("output_path", "")
                video_url = None
                file_exists = False
                if out_p:
                    out_path_obj = Path(out_p)
                    if out_path_obj.name in existing_cut_names or os.path.exists(out_p):
                        video_url = f"/data/cortes/{out_path_obj.name}"
                        file_exists = True

                c_item = dict(c)
                c_item["virality_score"] = round(norm_score, 0)
                c_item["video_url"] = video_url
                c_item["file_exists"] = file_exists
                filtered_cuts.append(c_item)

        v_dict = dict(v)
        v_dict["cortes"] = filtered_cuts
        history_items.append(v_dict)

    return history_items


@app.delete("/api/history/{video_id}", response_model=MessageResponse)
def delete_history_item(
    video_id: int,
    delete_files: bool = Query(False, description="Apagar também os arquivos .mp4/.mp3 físicos do disco"),
):
    """Remove vídeo e cortes associados do SQLite, com opção segura de exclusão física."""
    success = delete_video(video_id=video_id, delete_files=delete_files)
    if not success:
        raise HTTPException(status_code=404, detail=f"Vídeo ID {video_id} não encontrado.")
    job_manager.pop_analysis_cache(video_id)
    return MessageResponse(
        success=True,
        message=f"Vídeo ID {video_id} {'e arquivos físicos ' if delete_files else ''}removido com sucesso.",
    )


@app.post("/api/cortes", status_code=status.HTTP_201_CREATED, response_model=CorteMutationResponse)
def create_corte_item(req: CreateCorteRequest):
    """Cria manualmente um novo corte associado a um vídeo existente (201 Created)."""
    if not get_video(req.video_id):
        raise HTTPException(status_code=404, detail=f"Vídeo ID {req.video_id} não encontrado.")

    dur = req.end_time - req.start_time
    tipo = req.tipo or ("short" if dur <= 180.0 else "longo")
    layout_params = req.layout_params if req.layout_params is not None else (
        {"zoom_factor": 1.0} if req.layout_mode == "vertical_blur" else {}
    )
    try:
        new_id = add_corte(
            video_id=req.video_id,
            title=req.title.strip(),
            start_time=req.start_time,
            end_time=req.end_time,
            virality_score=req.virality_score,
            justification=req.justification,
            tipo=tipo,
            layout_mode=req.layout_mode,
            layout_params=layout_params,
            burn_subtitles=req.burn_subtitles,
            status="pending",
        )
    except ValueError as val_exc:
        raise HTTPException(status_code=400, detail=str(val_exc))

    return CorteMutationResponse(success=True, corte=get_corte(new_id) or {})


@app.get("/api/cortes/{corte_id}")
def get_corte_item(corte_id: int):
    """Retorna metadados completos de um corte individual pelo ID."""
    corte = get_corte(corte_id)
    if not corte:
        raise HTTPException(status_code=404, detail=f"Corte ID {corte_id} não encontrado.")
    out_p = corte.get("output_path", "")
    corte["video_url"] = f"/data/cortes/{Path(out_p).name}" if (out_p and os.path.exists(out_p)) else None
    return corte


@app.patch("/api/cortes/{corte_id}", response_model=CorteMutationResponse)
def patch_corte_item(corte_id: int, req: UpdateCorteRequest):
    """Atualiza parcialmente título, tempos, layout e legendas de um corte no SQLite."""
    try:
        updated = update_corte(
            corte_id=corte_id,
            title=req.title,
            start_time=req.start_time,
            end_time=req.end_time,
            tipo=req.tipo,
            layout_mode=req.layout_mode,
            layout_params=req.layout_params,
            burn_subtitles=req.burn_subtitles,
        )
    except ValueError as val_exc:
        raise HTTPException(status_code=400, detail=str(val_exc))

    if not updated:
        raise HTTPException(status_code=404, detail=f"Corte ID {corte_id} não encontrado.")

    return CorteMutationResponse(success=True, corte=get_corte(corte_id) or {})


@app.delete("/api/cortes/{corte_id}", response_model=MessageResponse)
def delete_corte_item(
    corte_id: int,
    delete_file: bool = Query(False, description="Apagar também o arquivo .mp4 renderizado do disco"),
):
    """Remove um corte individual do SQLite com opção de exclusão física segura."""
    success = delete_corte(corte_id=corte_id, delete_file=delete_file)
    if not success:
        raise HTTPException(status_code=404, detail=f"Corte ID {corte_id} não encontrado.")
    return MessageResponse(
        success=True,
        message=f"Corte ID {corte_id} {'e arquivo físico ' if delete_file else ''}removido com sucesso.",
    )


@app.post("/api/cleanup", response_model=CleanupResponse)
def cleanup_resources(req: Optional[CleanupRequest] = None):
    """Executa limpeza sob demanda de data/temp/ (e opcionalmente downloads) + poda TTL/LRU."""
    payload = req or CleanupRequest()
    disk_res = cleanup_temporary_files(
        temp_dir=TEMP_DIR,
        downloads_dir=DOWNLOADS_DIR,
        remove_raw_downloads=payload.remove_raw_downloads,
        max_age_hours=payload.max_age_hours,
    )
    mem_res = job_manager.prune()
    return CleanupResponse(
        success=True,
        deleted_count=int(disk_res["deleted_count"]),
        freed_mb=float(disk_res["freed_mb"]),
        pruned_jobs=int(mem_res["pruned_jobs"]),
        pruned_cache=int(mem_res["pruned_cache"]),
    )


@app.post("/api/open-folder", response_model=MessageResponse)
def open_folder():
    """Abre a pasta data/cortes/ diretamente no Explorador de Arquivos do Windows."""
    try:
        cuts_abs = str(CUTS_DIR.resolve())
        if hasattr(os, "startfile"):
            os.startfile(cuts_abs)  # nosec B606
        else:
            subprocess.Popen(["explorer", cuts_abs])  # nosec B603, B607
        return MessageResponse(success=True, message="Pasta data/cortes/ aberta no Explorador de Arquivos.")
    except Exception as exc:
        logger.error("Erro ao abrir pasta data/cortes/: %s", exc)
        raise HTTPException(status_code=500, detail="Erro interno ao abrir pasta de cortes no Explorador.")


# ==========================================
# ROTAS PRO V4.0 (ONDAS 2, 3 E 4)
# ==========================================
@app.get("/api/subtitle-presets")
def list_subtitle_presets():
    """Retorna catálogo de presets de legendas dinâmicas Karaoke (.ass)."""
    return {
        "presets": [
            {"id": key, "label": cfg["label"], "uppercase": cfg["uppercase"]}
            for key, cfg in SUBTITLE_PRESETS.items()
        ]
    }


@app.get("/api/brand-kits")
def get_brand_kits():
    return {"brand_kits": platform_repo.list_brand_kits()}


@app.post("/api/brand-kits", status_code=status.HTTP_201_CREATED)
def save_brand_kit(req: BrandKitCreateRequest):
    safe_logo = ""
    if req.logo_path:
        cand = Path(req.logo_path).resolve()
        if cand.is_relative_to(BRANDS_DIR.resolve()) and cand.exists():
            safe_logo = str(cand)
        else:
            by_name = (BRANDS_DIR / Path(req.logo_path).name).resolve()
            if by_name.is_relative_to(BRANDS_DIR.resolve()) and by_name.exists():
                safe_logo = str(by_name)
    try:
        kit = platform_repo.upsert_brand_kit(
            name=req.name,
            logo_path=safe_logo,
            logo_position=req.logo_position,
            logo_opacity=req.logo_opacity,
            logo_scale=req.logo_scale,
            subtitle_preset=req.subtitle_preset,
            progress_bar_enabled=req.progress_bar_enabled,
            progress_bar_color=req.progress_bar_color,
            hook_banner_enabled=req.hook_banner_enabled,
            hook_banner_duration=req.hook_banner_duration,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"success": True, "brand_kit": kit}


@app.delete("/api/brand-kits/{kit_id}", response_model=MessageResponse)
def remove_brand_kit(kit_id: int):
    if not platform_repo.delete_brand_kit(kit_id):
        raise HTTPException(status_code=404, detail=f"Brand Kit ID {kit_id} não encontrado.")
    return MessageResponse(success=True, message=f"Brand Kit ID {kit_id} removido.")


@app.post("/api/brand-kits/upload-logo", status_code=status.HTTP_201_CREATED)
async def upload_brand_logo(file: UploadFile = File(...)):
    orig_name = Path(file.filename or "logo.png").name
    ext = Path(orig_name).suffix.lower()
    if ext not in ALLOWED_LOGO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Logo deve ser PNG, JPG ou WebP.")
    BRANDS_DIR.mkdir(parents=True, exist_ok=True)
    content = await file.read(MAX_LOGO_BYTES + 1)
    await file.close()
    if not content or len(content) > MAX_LOGO_BYTES:
        raise HTTPException(status_code=413 if len(content) > MAX_LOGO_BYTES else 400, detail="Logo inválida ou acima de 5 MB.")
    if not _is_valid_image_magic_bytes(content[:16], ext):
        raise HTTPException(status_code=400, detail="Assinatura binária de imagem inválida.")
    dest = (BRANDS_DIR / f"logo_{uuid.uuid4().hex[:10]}{ext}").resolve()
    dest.write_bytes(content)
    return {"success": True, "logo_path": dest.name, "logo_url": f"/data/brands/{dest.name}"}


@app.get("/api/music")
def list_music_tracks():
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    tracks = [
        {"filename": f.name, "size_kb": round(f.stat().st_size / 1024, 1)}
        for f in sorted(MUSIC_DIR.iterdir())
        if f.is_file() and f.suffix.lower() in ALLOWED_MUSIC_EXTENSIONS
    ]
    return {"tracks": tracks}


@app.post("/api/music/upload", status_code=status.HTTP_201_CREATED)
async def upload_music_track(file: UploadFile = File(...)):
    orig_name = Path(file.filename or "bgm.mp3").name
    ext = Path(orig_name).suffix.lower()
    if ext not in ALLOWED_MUSIC_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Formato de trilha inválido. Use MP3, WAV, M4A, AAC ou OGG.")
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    content = await file.read(MAX_MUSIC_BYTES + 1)
    await file.close()
    if not content or len(content) > MAX_MUSIC_BYTES:
        raise HTTPException(status_code=413 if len(content) > MAX_MUSIC_BYTES else 400, detail="Trilha vazia ou acima de 30 MB.")
    if not _is_valid_audio_magic_bytes(content[:16], ext):
        raise HTTPException(status_code=400, detail="Assinatura binária de áudio inválida.")
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]", "_", Path(orig_name).stem)[:40] or "track"
    dest = (MUSIC_DIR / f"{safe_stem}_{uuid.uuid4().hex[:6]}{ext}").resolve()
    dest.write_bytes(content)
    return {"success": True, "filename": dest.name}


@app.delete("/api/music/{filename}", response_model=MessageResponse)
def delete_music_track(filename: str):
    safe_name = Path(filename).name
    target = (MUSIC_DIR / safe_name).resolve()
    if not target.is_relative_to(MUSIC_DIR.resolve()) or not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Trilha de áudio não encontrada.")
    target.unlink()
    return MessageResponse(success=True, message=f"Trilha '{safe_name}' removida.")


@app.post("/api/cortes/{corte_id}/seo-kit")
def create_seo_kit(corte_id: int, req: Optional[SeoKitRequest] = None):
    payload = req or SeoKitRequest()
    return _service_generate_seo(
        corte_id=corte_id,
        provider=payload.provider,
        cache_dir=CACHE_DIR,
        temp_dir=TEMP_DIR,
        manager=job_manager,
        seo_fn=generate_seo_publish_kit,
    )


@app.get("/api/videos/{video_id}/export-zip")
def export_video_cuts_zip(video_id: int):
    zip_bytes, filename = _service_build_zip(video_id)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/videos/{video_id}/highlight-reel", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse)
def start_highlight_reel(video_id: int, req: HighlightReelRequest):
    if not get_video(video_id):
        raise HTTPException(status_code=404, detail=f"Vídeo ID {video_id} não encontrado.")
    job_id = job_manager.create_job(job_type="reel", step="Iniciando compilação de Highlight Reel...", progress=5)
    if job_id is None:
        raise HTTPException(status_code=429, detail="Limite de compilações simultâneas atingido.")
    threading.Thread(
        target=_service_run_reel_job,
        kwargs={
            "job_id": job_id,
            "video_id": video_id,
            "corte_ids": req.corte_ids,
            "transition": req.transition,
            "transition_duration": req.transition_duration,
            "title": req.title,
            "use_gpu": req.use_gpu,
            "manager": job_manager,
        },
        daemon=True,
    ).start()
    return JobAcceptedResponse(job_id=job_id, message="Compilação de Highlight Reel iniciada.")


@app.post("/api/broll/suggest")
def broll_suggest(req: BrollSuggestRequest):
    return suggest_broll_assets(
        query=req.query,
        transcript_excerpt=req.transcript_excerpt,
        orientation=req.orientation,
        max_results=req.max_results,
        broll_dir=BROLL_DIR,
    )


@app.get("/api/publish/providers")
def list_publish_providers():
    return {"providers": get_publish_providers_status()}


@app.get("/api/publish/schedule")
def list_publish_schedule(corte_id: Optional[int] = Query(None), status_filter: Optional[str] = Query(None, alias="status")):
    return {"posts": platform_repo.list_scheduled_posts(corte_id=corte_id, status=status_filter)}


@app.post("/api/publish/schedule", status_code=status.HTTP_201_CREATED)
def schedule_publish_post(req: PublishScheduleRequest):
    return _service_schedule_post(
        corte_id=req.corte_id,
        platform=req.platform,
        title=req.title,
        caption=req.caption,
        hashtags=req.hashtags,
        scheduled_at=req.scheduled_at,
        publish_now=req.publish_now,
    )


@app.delete("/api/publish/schedule/{post_id}", response_model=MessageResponse)
def cancel_scheduled_post(post_id: int):
    if not platform_repo.delete_scheduled_post(post_id):
        raise HTTPException(status_code=404, detail=f"Agendamento ID {post_id} não encontrado.")
    return MessageResponse(success=True, message=f"Agendamento ID {post_id} removido.")


# ==========================================
# ROTAS ESTÁTICAS PARA MÍDIA E INTERFACE WEB
# ==========================================
app.mount("/data/temp", StaticFiles(directory=str(BASE_DIR / "data" / "temp")), name="data_temp")
app.mount("/data/cortes", StaticFiles(directory=str(BASE_DIR / "data" / "cortes")), name="data_cortes")
app.mount("/data/brands", StaticFiles(directory=str(BASE_DIR / "data" / "brands")), name="data_brands")
app.mount("/data/broll", StaticFiles(directory=str(BASE_DIR / "data" / "broll")), name="data_broll")
app.mount("/", StaticFiles(directory=str(BASE_DIR / "web"), html=True), name="web")

__all__ = ["AllowedLayoutMode", "RenderCutItem", "app"]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8502, reload=False)
