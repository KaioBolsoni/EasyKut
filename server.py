"""
Servidor Backend FastAPI do EasyKut Studio (V3.0)
Camada HTTP fina responsável por rotas REST, contratos Pydantic/OpenAPI,
hardening de segurança local, observabilidade (X-Request-ID, /api/health) e arquivos estáticos.
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

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from core.ai_apis import detect_viral_moments, transcribe_audio_groq
from core.config import (
    CACHE_DIR,
    DATA_DIR,
    FFMPEG_AVAILABLE,
    validate_config,
)
from core.downloader import (
    download_audio_with_metadata,
    download_video,
    is_valid_video_url,
)
from core.logging_config import configure_logging, request_id_var
from core.media_utils import normalize_virality_score
from core.video_engine import (
    cleanup_temporary_files,
    cut_video,
    extract_preview_clip,
    extract_preview_frame,
)
from database.db_manager import (
    delete_corte,
    delete_video,
    get_connection,
    get_corte,
    get_video,
    init_db,
    list_videos_with_cortes,
    update_corte,
)
from services.job_manager import job_manager
from services.media_service import (
    resolve_trusted_video_source as _service_resolve_source,
    run_analyze_job as _service_run_analyze_job,
    run_render_job as _service_run_render_job,
)

# Configuração de Logging Rotativo + Correlation ID
configure_logging()
logger = logging.getLogger("easykut_server")

# Inicializa banco de dados SQLite (migrations v1 -> v3)
init_db()

# Pastas de dados e interface web
TEMP_DIR = (BASE_DIR / "data" / "temp").resolve()
CUTS_DIR = (BASE_DIR / "data" / "cortes").resolve()
DOWNLOADS_DIR = (BASE_DIR / "data" / "downloads").resolve()
WEB_DIR = (BASE_DIR / "web").resolve()

for _d in (TEMP_DIR, CUTS_DIR, DOWNLOADS_DIR, CACHE_DIR, WEB_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _migrate_legacy_transcripts_to_cache() -> None:
    """
    Move arquivos transcript_*.json legados de data/temp/ (exposto via StaticFiles)
    para data/cache/ (privado, não servido via HTTP).
    """
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
# GC automático de arquivos temporários com mais de 24h no startup
cleanup_temporary_files(temp_dir=TEMP_DIR, remove_raw_downloads=False, max_age_hours=24.0)

# Instância FastAPI
app = FastAPI(
    title="EasyKut Studio API",
    version="3.1.0",
    description="Backend local REST de alta performance para curadoria, enquadramento e renderização de cortes virais.",
)

# Política estrita de CORS e origens locais permitidas
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
    rastreamento por X-Request-ID e injeção de cabeçalhos defensivos padrão.
    """
    incoming_req_id = (request.headers.get("x-request-id") or "").strip()
    if incoming_req_id and re.fullmatch(r"[A-Za-z0-9_.-]{4,64}", incoming_req_id):
        req_id = incoming_req_id
    else:
        req_id = uuid.uuid4().hex[:12]

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
    job_manager.update_job(
        job_id=job_id,
        status=status,
        step=step,
        progress=progress,
        result=result,
        error=error,
    )


# ==========================================
# MODELOS PYDANTIC (REQUEST & RESPONSE SCHEMAS)
# ==========================================
AllowedLayoutMode = Literal[
    "horizontal_16_9",
    "vertical_blur",
    "manual_crop",
    "split_screen",
    "face_tracking_9_16",
]


class AnalyzeRequest(BaseModel):
    url: str
    provider: Literal["gemini", "groq"] = "gemini"
    max_cuts: int = Field(default=5, ge=1, le=20)


class FrameRequest(BaseModel):
    video_id: Optional[int] = None
    source: Optional[str] = None
    timestamp_sec: float = Field(default=1.0, ge=0.0)


class PreviewClipRequest(BaseModel):
    video_id: Optional[int] = None
    source: Optional[str] = None
    start_time: float = Field(default=0.0, ge=0.0)
    end_time: float = Field(default=30.0, gt=0.0)


class UpdateCorteRequest(BaseModel):
    title: Optional[str] = None
    start_time: Optional[float] = Field(default=None, ge=0.0)
    end_time: Optional[float] = Field(default=None, gt=0.0)
    tipo: Optional[Literal["short", "longo"]] = None
    layout_mode: Optional[AllowedLayoutMode] = None
    layout_params: Optional[Dict[str, Any]] = None
    burn_subtitles: Optional[bool] = None

    @model_validator(mode="after")
    def validate_time_order(self):
        if self.start_time is not None and self.end_time is not None and self.end_time <= self.start_time:
            raise ValueError("end_time deve ser maior que start_time.")
        return self


class RenderCutItem(BaseModel):
    id: Optional[int] = None
    title: Optional[str] = None
    titulo: Optional[str] = None
    start_time: float = Field(..., ge=0.0)
    end_time: float = Field(..., gt=0.0)
    layout_mode: AllowedLayoutMode = "vertical_blur"
    layout_params: Optional[Dict[str, Any]] = None
    burn_subtitles: Optional[bool] = True

    @model_validator(mode="after")
    def validate_time_order(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time deve ser maior que start_time.")
        return self

    def get_effective_title(self, fallback: str = "Corte") -> str:
        return self.titulo or self.title or fallback


class RenderRequest(BaseModel):
    video_id: int
    cuts: List[RenderCutItem]
    max_resolution: Literal[720, 1080] = 1080


class CleanupRequest(BaseModel):
    remove_raw_downloads: bool = False
    max_age_hours: Optional[float] = Field(default=None, ge=0.0)


class JobAcceptedResponse(BaseModel):
    job_id: str
    message: str


class ConfigResponse(BaseModel):
    ready: bool
    keys: Dict[str, bool]
    default_provider: Literal["gemini", "groq"]
    status_details: Dict[str, Any]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    db_ok: bool
    ffmpeg_available: bool
    disk_free_mb: float
    active_jobs: Dict[str, int]


class FrameResponse(BaseModel):
    success: bool
    frame_url: str
    timestamp_sec: float


class PreviewClipResponse(BaseModel):
    success: bool
    clip_url: str
    start_time: float
    end_time: float


class CleanupResponse(BaseModel):
    success: bool
    deleted_count: int
    freed_mb: float
    pruned_jobs: int
    pruned_cache: int


class MessageResponse(BaseModel):
    success: bool
    message: str


class CorteMutationResponse(BaseModel):
    success: bool
    corte: Dict[str, Any]


# ==========================================
# WRAPPERS INTERNOS DE SERVIÇO
# ==========================================
def _resolve_trusted_video_source(
    video_id: Optional[int],
    source: Optional[str],
) -> tuple[str, Optional[str], Optional[str]]:
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


def _run_render_job(job_id: str, video_id: int, cuts: List[RenderCutItem], max_resolution: int) -> None:
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
    is_healthy = db_ok
    if not is_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="ok" if is_healthy else "degraded",
        version=app.version,
        db_ok=db_ok,
        ffmpeg_available=bool(FFMPEG_AVAILABLE),
        disk_free_mb=disk_free_mb,
        active_jobs=counts,
    )


@app.get("/api/config", response_model=ConfigResponse)
def get_config():
    """Retorna o status das chaves de API, disponibilidade do FFmpeg, cookies e provedor padrão."""
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
    """Inicia a esteira assíncrona de análise IA para uma URL suportada (retorna 202 Accepted)."""
    target_url = (req.url or "").strip()
    if not target_url.startswith(("http://", "https://")):
        target_url = "https://" + target_url

    if not is_valid_video_url(target_url):
        raise HTTPException(status_code=400, detail="URL de vídeo inválida ou não suportada.")

    job_id = job_manager.create_job(
        job_type="analyze",
        step="Iniciando esteira de análise...",
        progress=5,
    )
    if job_id is None:
        raise HTTPException(
            status_code=429,
            detail="Limite de análises simultâneas atingido. Aguarde a conclusão do job atual.",
        )

    threading.Thread(
        target=_run_analyze_job,
        args=(job_id, target_url, req.provider or "gemini", req.max_cuts or 5),
        daemon=True,
    ).start()

    return JobAcceptedResponse(
        job_id=job_id,
        message="Análise de vídeo iniciada com sucesso em segundo plano.",
    )


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    """Retorna cópia profunda segura do estado atual, progresso e resultado/erro de um job."""
    snapshot = job_manager.get_job_snapshot(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' não encontrado.")
    return snapshot


@app.post("/api/frame", response_model=FrameResponse)
def get_frame(req: FrameRequest):
    """Extrai 1 frame JPEG no timestamp informado a partir de vídeo previamente cadastrado."""
    trusted_url, cached_stream, _ = _resolve_trusted_video_source(req.video_id, req.source)

    acquired = preview_ffmpeg_semaphore.acquire(timeout=1.5)
    if not acquired:
        raise HTTPException(
            status_code=429,
            detail="Muitas extrações de prévia em andamento. Tente novamente em instantes.",
        )

    try:
        frame_path = extract_preview_frame(
            source=trusted_url,
            timestamp_sec=req.timestamp_sec,
            direct_stream_url=cached_stream,
        )
        if not frame_path or not os.path.exists(frame_path):
            if cached_stream:
                logger.info("Tentando extrair frame novamente sem o stream URL em cache...")
                frame_path = extract_preview_frame(
                    source=trusted_url,
                    timestamp_sec=req.timestamp_sec,
                    direct_stream_url=None,
                )
        if not frame_path or not os.path.exists(frame_path):
            raise HTTPException(status_code=500, detail="Não foi possível extrair o frame solicitado via FFmpeg.")

        return FrameResponse(
            success=True,
            frame_url=f"/data/temp/{Path(frame_path).name}",
            timestamp_sec=req.timestamp_sec,
        )
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

    acquired = preview_ffmpeg_semaphore.acquire(timeout=1.5)
    if not acquired:
        raise HTTPException(
            status_code=429,
            detail="Muitas prévias em vídeo sendo geradas simultaneamente. Aguarde alguns segundos.",
        )

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
    """Inicia a renderização assíncrona em background dos cortes selecionados (retorna 202 Accepted)."""
    if not req.cuts:
        raise HTTPException(status_code=400, detail="Nenhum corte selecionado para renderização.")

    video = get_video(req.video_id)
    if not video:
        raise HTTPException(status_code=404, detail=f"Vídeo ID {req.video_id} não encontrado.")

    job_id = job_manager.create_job(
        job_type="render",
        step=f"Iniciando renderização de {len(req.cuts)} corte(s)...",
        progress=5,
    )
    if job_id is None:
        raise HTTPException(
            status_code=429,
            detail="Limite de renderizações simultâneas atingido. Aguarde o job em andamento terminar.",
        )

    threading.Thread(
        target=_run_render_job,
        args=(job_id, req.video_id, req.cuts, req.max_resolution or 1080),
        daemon=True,
    ).start()

    return JobAcceptedResponse(
        job_id=job_id,
        message="Processo de renderização iniciado em segundo plano.",
    )


@app.get("/api/history")
def get_history(
    response: Response,
    search: Optional[str] = Query(None, description="Busca textual por título ou URL"),
    min_score: Optional[float] = Query(0.0, ge=0.0, le=100.0, description="Nota mínima de viralidade (0 a 100)"),
    limit: int = Query(50, ge=1, le=200, description="Máximo de vídeos por página"),
    offset: int = Query(0, ge=0, description="Deslocamento de paginação"),
):
    """
    Retorna vídeos paginados com seus cortes em consulta em lote (sem N+1).
    Expõe o total de vídeos correspondentes no cabeçalho X-Total-Count.
    """
    videos, total_count = list_videos_with_cortes(search=search, limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(total_count)

    existing_cut_names = (
        {p.name for p in CUTS_DIR.iterdir() if p.is_file()}
        if CUTS_DIR.exists()
        else set()
    )

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

    return CorteMutationResponse(
        success=True,
        corte=get_corte(corte_id) or {},
    )


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
    """
    Executa limpeza sob demanda de arquivos temporários (data/temp/, opcionalmente downloads brutos)
    e poda entradas expiradas de jobs/cache em memória, preservando data/cortes/.
    """
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
        return MessageResponse(
            success=True,
            message="Pasta data/cortes/ aberta no Explorador de Arquivos.",
        )
    except Exception as exc:
        logger.error("Erro ao abrir pasta data/cortes/: %s", exc)
        raise HTTPException(status_code=500, detail="Erro interno ao abrir pasta de cortes no Explorador.")


# ==========================================
# ROTAS ESTÁTICAS PARA MÍDIA E INTERFACE WEB
# ==========================================
app.mount("/data/temp", StaticFiles(directory=str(BASE_DIR / "data" / "temp")), name="data_temp")
app.mount("/data/cortes", StaticFiles(directory=str(BASE_DIR / "data" / "cortes")), name="data_cortes")
app.mount("/", StaticFiles(directory=str(BASE_DIR / "web"), html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8502, reload=False)
