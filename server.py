"""
EasyKut Studio (v3.0) - Servidor Web Local FastAPI
Substitui a interface Streamlit por uma API REST robusta e veloz,
mantendo 100% dos motores Python existentes (yt-dlp, Groq Whisper, Gemini/Groq LLMs, OpenCV e FFmpeg).
"""

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional
import uuid

# Garante inclusão do diretório raiz no sys.path e define BASE_DIR absoluto
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.config import (
    FFMPEG_AVAILABLE,
    GEMINI_API_KEY,
    GROQ_API_KEY,
    validate_config,
)
from core.downloader import (
    download_audio,
    download_audio_with_metadata,
    download_video,
    extract_video_info,
    get_direct_stream_url,
    is_valid_video_url,
)
from core.ai_apis import detect_viral_moments, transcribe_audio_groq
from core.video_engine import (
    cleanup_temporary_files,
    cut_video,
    draw_framing_preview,
    extract_preview_clip,
    extract_preview_frame,
    get_video_metadata,
)
from database.db_manager import (
    add_corte,
    add_video,
    delete_video,
    get_video,
    init_db,
    list_cortes,
    list_videos,
    save_analysis,
    update_corte_status,
    update_video_status,
)

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("easykut_server")

# Inicializa o banco de dados SQLite
init_db()

# Garante que as pastas de dados e web sejam criadas com caminhos absolutos baseados em BASE_DIR
TEMP_DIR = BASE_DIR / "data" / "temp"
CUTS_DIR = BASE_DIR / "data" / "cortes"
DOWNLOADS_DIR = BASE_DIR / "data" / "downloads"
WEB_DIR = BASE_DIR / "web"

TEMP_DIR.mkdir(parents=True, exist_ok=True)
CUTS_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
WEB_DIR.mkdir(parents=True, exist_ok=True)

# Instância FastAPI
app = FastAPI(
    title="EasyKut Studio API",
    version="3.0.0",
    description="Backend local REST de alta performance para curadoria, enquadramento e renderização de cortes virais."
)

# Habilita CORS para origens locais e acesso direto
origins = [
    "http://127.0.0.1:8502",
    "http://localhost:8502",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1",
    "http://localhost",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# GERENCIAMENTO DE JOBS EM SEGUNDO PLANO
# ==========================================
jobs_lock = threading.Lock()
jobs: Dict[str, Dict[str, Any]] = {}
analyses_cache: Dict[int, Dict[str, Any]] = {}


def update_job(
    job_id: str,
    status: Optional[str] = None,
    step: Optional[str] = None,
    progress: Optional[int] = None,
    result: Optional[Any] = None,
    error: Optional[str] = None
) -> None:
    """Atualiza o estado de um job de processamento de forma thread-safe."""
    with jobs_lock:
        if job_id not in jobs:
            return
        if status is not None:
            jobs[job_id]["status"] = status
        if step is not None:
            jobs[job_id]["step"] = step
        if progress is not None:
            jobs[job_id]["progress"] = progress
        if result is not None:
            jobs[job_id]["result"] = result
        if error is not None:
            jobs[job_id]["error"] = error
        jobs[job_id]["updated_at"] = time.time()


# ==========================================
# MODELOS PYDANTIC (SCHEMAS)
# ==========================================
class AnalyzeRequest(BaseModel):
    url: str
    provider: Optional[str] = "gemini"
    max_cuts: Optional[int] = 5


class FrameRequest(BaseModel):
    source: str
    timestamp_sec: float = 1.0


class PreviewClipRequest(BaseModel):
    video_id: Optional[int] = None
    source: str
    start_time: float = 0.0
    end_time: float = 30.0


class FramePreviewRequest(BaseModel):
    frame_path: str
    mode: str = "manual_crop"
    params: Optional[Dict[str, Any]] = None


class RenderCutItem(BaseModel):
    id: Optional[int] = None
    title: Optional[str] = None
    titulo: Optional[str] = None
    start_time: float
    end_time: float
    layout_mode: Optional[str] = "vertical_blur"
    layout_params: Optional[Dict[str, Any]] = None
    burn_subtitles: Optional[bool] = True

    def get_effective_title(self, fallback: str = "Corte") -> str:
        return self.titulo or self.title or fallback


class RenderRequest(BaseModel):
    video_id: int
    cuts: List[RenderCutItem]
    max_resolution: Optional[int] = 1080


# ==========================================
# ROTAS DA API REST (/api)
# ==========================================
@app.get("/api/config")
def get_config():
    """
    Retorna o status de conexão das chaves de API (Groq, Gemini),
    disponibilidade do FFmpeg, presença de cookies.txt e provedor padrão recomendado.
    """
    is_ready, details = validate_config()
    return {
        "ready": is_ready,
        "keys": {
            "GROQ_API_KEY": bool(details.get("GROQ_API_KEY")),
            "GEMINI_API_KEY": bool(details.get("GEMINI_API_KEY")),
            "FFMPEG_AVAILABLE": bool(details.get("FFMPEG")),
            "COOKIES_TXT": bool(details.get("COOKIES_TXT")),
        },
        "default_provider": "gemini" if details.get("GEMINI_API_KEY") else "groq",
        "status_details": details,
    }


def _run_analyze_job(job_id: str, url: str, provider: str, max_cuts: int) -> None:
    """Thread de execução em background da esteira de ingestão e curadoria IA (1 única chamada yt-dlp)."""
    try:
        # 1. Ingestão unificada: Metadados + Download de Áudio + Stream URL em 1 única chamada yt-dlp
        update_job(job_id, step="[1/4] Extraindo metadados e baixando áudio leve (1 chamada yt-dlp)...", progress=15)
        ingest = download_audio_with_metadata(url)
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
            status="transcribing"
        )

        # 2. Transcrição com Groq Whisper
        update_job(job_id, step="[2/4] Transcrevendo áudio com Groq Whisper-large-v3...", progress=45)
        transcript_data = transcribe_audio_groq(audio_file)

        # Salva cache de transcrição em disco para reutilização na renderização
        transcript_cache_path = TEMP_DIR / f"transcript_{video_id}.json"
        try:
            with open(transcript_cache_path, "w", encoding="utf-8") as f_cache:
                json.dump(transcript_data, f_cache, ensure_ascii=False)
        except Exception as exc_cache:
            logger.warning("Falha ao salvar cache de transcrição: %s", exc_cache)

        # 3. Detecção de momentos virais com IA
        prov_clean = (provider or "gemini").lower().strip()
        update_job(job_id, step=f"[3/4] Analisando momentos de retenção com {prov_clean.upper()}...", progress=70)
        cortes_detectados = detect_viral_moments(transcript_data, provider=prov_clean)

        if not cortes_detectados:
            update_video_status(video_id, status="completed_no_cuts")
            update_job(
                job_id,
                status="completed",
                step="Análise finalizada: Nenhum corte viral detectado pela IA.",
                progress=100,
                result={
                    "video_id": video_id,
                    "url": url,
                    "title": video_title,
                    "duration": video_duration,
                    "thumbnail": video_thumbnail,
                    "uploader": video_uploader,
                    "cortes": []
                }
            )
            return

        # Ordena cortes por virality_score decrescente (0-100)
        def _get_score(c):
            val = float(c.get("virality_score", 0.0) or 0.0)
            return val * 10.0 if (0.0 < val <= 10.0) else val

        cortes_detectados.sort(key=_get_score, reverse=True)

        # 4. Captura automática de frames de prévia reaproveitando o stream já obtido (zero novas chamadas yt-dlp)
        update_job(job_id, step=f"[4/4] Capturando frames reais de prévia para {len(cortes_detectados)} cortes...", progress=85)

        cortes_formatados: List[Dict[str, Any]] = []
        for idx, c in enumerate(cortes_detectados):
            c_start = float(c.get("start_time", 0.0))
            c_end = float(c.get("end_time", 0.0))
            c_dur = max(1.0, c_end - c_start)
            c_tipo = str(c.get("tipo", "short")).lower().strip()

            # Modo padrão inicial: horizontal para vídeos longos (7+ min), senão vertical_blur
            if c_tipo == "longo" or c_dur >= 180.0:
                modo_inicial = "horizontal_16_9"
            else:
                modo_inicial = "vertical_blur"

            # Captura frame real via FFmpeg; se falhar, utiliza thumbnail do YouTube como fallback visual garantido
            ts_frame = max(0.0, c_start + min(1.5, c_dur / 2.0))
            frame_path = ""
            frame_url = ""
            try:
                frame_path = extract_preview_frame(
                    source=url,
                    timestamp_sec=ts_frame,
                    direct_stream_url=direct_stream_url
                )
                if frame_path and os.path.exists(frame_path):
                    frame_url = f"/data/temp/{Path(frame_path).name}"
                else:
                    frame_path = ""
                    frame_url = ""
            except Exception as exc_frame:
                logger.warning("Falha ao extrair frame de preview do corte %d: %s", idx, exc_frame)
                frame_path = ""
                frame_url = ""

            # Fallback resiliente: se a captura do frame falhar, usa a thumbnail oficial para manter a prévia ativa
            if not frame_url and video_thumbnail:
                frame_url = video_thumbnail

            raw_sc = float(c.get("virality_score", 80.0) or 80.0)
            norm_sc = raw_sc * 10.0 if (0.0 < raw_sc <= 10.0) else raw_sc

            # Filtra segmentos da transcrição pertencentes a este corte para legenda ao vivo e prévia textual
            all_segs = transcript_data.get("segments", []) or []
            cut_segs = [
                {
                    "start": round(float(s.get("start", 0.0)), 2),
                    "end": round(float(s.get("end", 0.0)), 2),
                    "text": str(s.get("text", "")).strip(),
                }
                for s in all_segs
                if float(s.get("end", 0.0)) > c_start and float(s.get("start", 0.0)) < c_end and str(s.get("text", "")).strip()
            ]

            cortes_formatados.append({
                "idx": idx,
                "selected": (idx < max_cuts),
                "title": str(c.get("title", f"Corte #{idx+1}")),
                "start_time": c_start,
                "end_time": c_end,
                "duration": round(c_dur, 1),
                "virality_score": round(min(100.0, max(0.0, norm_sc)), 0),
                "tipo": c_tipo,
                "justification": str(c.get("justification", "")),
                "layout_mode": modo_inicial,
                "layout_params": {"zoom_factor": 1.0} if modo_inicial == "vertical_blur" else {},
                "burn_subtitles": True,
                "frame_path": frame_path,
                "frame_url": frame_url,
                "segments": cut_segs,
            })

        # Salva análise no banco SQLite e vincula IDs primários aos cortes formatados
        inserted_ids = save_analysis(video_id=video_id, transcript=transcript_data, analysis_data=cortes_detectados)
        for idx, c_fmt in enumerate(cortes_formatados):
            if idx < len(inserted_ids):
                c_fmt["id"] = inserted_ids[idx]

        # Cache em memória (inclui direct_stream_url e audio_path para /api/frame e /api/preview-clip)
        analyses_cache[video_id] = {
            "transcript_data": transcript_data,
            "url": url,
            "title": video_title,
            "duration": video_duration,
            "thumbnail": video_thumbnail,
            "uploader": video_uploader,
            "direct_stream_url": direct_stream_url,
            "audio_path": audio_file,
        }

        # Conclui o job
        update_job(
            job_id,
            status="completed",
            step="Análise e extração de prévias concluídas com sucesso!",
            progress=100,
            result={
                "video_id": video_id,
                "url": url,
                "title": video_title,
                "duration": video_duration,
                "thumbnail": video_thumbnail,
                "uploader": video_uploader,
                "cortes": cortes_formatados,
                "transcript_segments": transcript_data.get("segments", []) or [],
            }
        )
    except Exception as exc:
        logger.exception("Erro crítico na esteira de análise (job %s): %s", job_id, exc)
        if "video_id" in locals():
            try:
                update_video_status(video_id, status="failed")
            except Exception:
                pass
        update_job(
            job_id,
            status="failed",
            step="Erro durante o processamento da análise",
            progress=100,
            error=str(exc)
        )


@app.post("/api/analyze")
def start_analyze(req: AnalyzeRequest):
    """
    Inicia a esteira de análise assíncrona para uma URL de vídeo.
    Retorna imediatamente um job_id para acompanhamento pelo frontend.
    """
    target_url = (req.url or "").strip()
    if not target_url.startswith("http://") and not target_url.startswith("https://"):
        target_url = "https://" + target_url

    if not is_valid_video_url(target_url):
        raise HTTPException(status_code=400, detail="URL de vídeo inválida ou não suportada.")

    job_id = f"job_analyze_{uuid.uuid4().hex[:10]}"
    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id,
            "type": "analyze",
            "status": "running",
            "step": "Iniciando esteira de análise...",
            "progress": 5,
            "result": None,
            "error": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    threading.Thread(
        target=_run_analyze_job,
        args=(job_id, target_url, req.provider or "gemini", req.max_cuts or 5),
        daemon=True
    ).start()

    return {
        "job_id": job_id,
        "message": "Análise de vídeo iniciada com sucesso em segundo plano."
    }


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    """
    Retorna o status atual, progresso em tempo real e resultado ou erro de um job.
    """
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' não encontrado.")
        return job


@app.post("/api/frame")
def get_frame(req: FrameRequest):
    """
    Extrai 1 único frame JPEG (.jpg) no segundo especificado a partir de uma URL ou arquivo local.
    Reaproveita a URL direta de stream já em cache para evitar chamadas redundantes ao yt-dlp.
    """
    try:
        cached_stream = None
        for entry in analyses_cache.values():
            if entry.get("url") == req.source and entry.get("direct_stream_url"):
                cached_stream = entry["direct_stream_url"]
                break

        frame_path = extract_preview_frame(
            source=req.source,
            timestamp_sec=req.timestamp_sec,
            direct_stream_url=cached_stream
        )
        if not frame_path or not os.path.exists(frame_path):
            if cached_stream:
                logger.info("Tentando extrair frame novamente sem o stream URL em cache...")
                frame_path = extract_preview_frame(
                    source=req.source,
                    timestamp_sec=req.timestamp_sec,
                    direct_stream_url=None
                )

        if not frame_path or not os.path.exists(frame_path):
            raise RuntimeError("Não foi possível gerar o frame de prévia para este timestamp.")
        frame_url = f"/data/temp/{Path(frame_path).name}"
        return {
            "success": True,
            "frame_path": frame_path,
            "frame_url": frame_url,
            "timestamp_sec": req.timestamp_sec,
        }
    except Exception as exc:
        logger.error("Erro ao extrair frame no tempo %s: %s", req.timestamp_sec, exc)
        raise HTTPException(status_code=500, detail=f"Erro ao extrair frame: {exc}")


@app.post("/api/preview-clip")
def get_preview_clip(req: PreviewClipRequest):
    """
    Gera e retorna um clipe de vídeo leve (.mp4 com áudio) do intervalo [start_time, end_time]
    para reprodução em tempo real a 60fps no Canvas do Estúdio e no Mockup 9:16 antes da renderização.
    """
    try:
        cached_stream = None
        local_audio = None

        if req.video_id and req.video_id in analyses_cache:
            entry = analyses_cache[req.video_id]
            cached_stream = entry.get("direct_stream_url")
            local_audio = entry.get("audio_path")

        if not cached_stream or not local_audio:
            for entry in analyses_cache.values():
                if entry.get("url") == req.source:
                    cached_stream = cached_stream or entry.get("direct_stream_url")
                    local_audio = local_audio or entry.get("audio_path")
                    break

        if not local_audio and req.video_id:
            video_row = get_video(req.video_id)
            if video_row and video_row.get("audio_path"):
                local_audio = video_row["audio_path"]

        clip_path = extract_preview_clip(
            source=req.source,
            start_time=req.start_time,
            end_time=req.end_time,
            direct_stream_url=cached_stream,
            local_audio_path=local_audio
        )
        if not clip_path or not os.path.exists(clip_path):
            raise RuntimeError("Não foi possível gerar o clipe de prévia em vídeo para este corte.")

        clip_url = f"/data/temp/{Path(clip_path).name}"
        return {
            "success": True,
            "clip_path": clip_path,
            "clip_url": clip_url,
            "start_time": req.start_time,
            "end_time": req.end_time,
        }
    except Exception as exc:
        logger.error("Erro ao gerar clipe de prévia (%ss -> %ss): %s", req.start_time, req.end_time, exc)
        raise HTTPException(status_code=500, detail=f"Erro ao gerar clipe de prévia: {exc}")


@app.post("/api/frame/preview")
def get_frame_preview(req: FramePreviewRequest):
    """
    Aplica o enquadramento escolhido (manual_crop, split_screen, vertical_blur)
    sobre o frame original gerando a miniatura visual ao vivo.
    """
    try:
        preview_path = draw_framing_preview(
            frame_path=req.frame_path,
            mode=req.mode,
            params=req.params
        )
        preview_url = f"/data/temp/{Path(preview_path).name}"
        return {
            "success": True,
            "preview_path": preview_path,
            "preview_url": preview_url,
            "mode": req.mode,
        }
    except Exception as exc:
        logger.error("Erro ao desenhar prévia de enquadramento: %s", exc)
        raise HTTPException(status_code=500, detail=f"Erro ao gerar prévia de enquadramento: {exc}")


def _run_render_job(job_id: str, video_id: int, cuts: List[RenderCutItem], max_resolution: int) -> None:
    """Thread de execução em background da renderização FFmpeg com os 4 modos."""
    try:
        video = get_video(video_id)
        if not video:
            raise ValueError(f"Vídeo ID {video_id} não encontrado no banco de dados.")

        update_job(job_id, step="[1/2] Verificando vídeo fonte em alta resolução...", progress=10)

        # 1. Download do vídeo em alta definição (se ainda não existir no disco)
        video_file = video.get("file_path", "")
        if not video_file or not os.path.exists(video_file):
            update_job(job_id, step=f"[1/2] Baixando vídeo fonte em {max_resolution}p via yt-dlp...", progress=20)
            video_file = download_video(video["url"], max_resolution=max_resolution)
            update_video_status(video_id, status="cutting", file_path=video_file)

        # 2. Carrega transcrição para legendas sincronizadas
        segments = []
        transcript_cache_path = TEMP_DIR / f"transcript_{video_id}.json"
        if transcript_cache_path.exists():
            try:
                with open(transcript_cache_path, "r", encoding="utf-8") as f_tr:
                    t_data = json.load(f_tr)
                    segments = t_data.get("segments", [])
            except Exception:
                pass
        elif video_id in analyses_cache:
            segments = analyses_cache[video_id].get("transcript_data", {}).get("segments", [])

        # 3. Renderização de cada corte selecionado
        rendered_cuts: List[Dict[str, Any]] = []
        total_cuts = len(cuts)

        for idx, c in enumerate(cuts):
            pct = 30 + int((idx / total_cuts) * 65)
            cut_title = c.get_effective_title(f"Corte #{idx+1}")
            update_job(
                job_id,
                step=f"[2/2] Renderizando corte {idx+1}/{total_cuts}: '{cut_title}' no modo '{c.layout_mode}'...",
                progress=pct
            )

            # Insere ou atualiza o corte no banco SQLite
            corte_db_id = c.id
            if not corte_db_id:
                corte_db_id = add_corte(
                    video_id=video_id,
                    start_time=c.start_time,
                    end_time=c.end_time,
                    title=cut_title,
                    status="rendering"
                )
            else:
                update_corte_status(corte_db_id, status="rendering")

            # Renderiza via FFmpeg com o layout e parâmetros exatos
            output_clip = cut_video(
                input_path=video_file,
                start_time=c.start_time,
                end_time=c.end_time,
                layout_mode=c.layout_mode,
                layout_params=c.layout_params,
                burn_subtitles=bool(c.burn_subtitles),
                segments=segments
            )

            update_corte_status(corte_db_id, status="completed", output_path=output_clip)

            rendered_cuts.append({
                "id": corte_db_id,
                "title": cut_title,
                "start_time": c.start_time,
                "end_time": c.end_time,
                "duration": round(c.end_time - c.start_time, 1),
                "layout_mode": c.layout_mode,
                "output_path": output_clip,
                "video_url": f"/data/cortes/{Path(output_clip).name}",
            })

        update_video_status(video_id, status="completed")

        update_job(
            job_id,
            status="completed",
            step=f"Renderização concluída! {len(rendered_cuts)} corte(s) prontos para download.",
            progress=100,
            result={
                "video_id": video_id,
                "rendered_cuts": rendered_cuts,
            }
        )
    except Exception as exc:
        logger.exception("Erro crítico na esteira de renderização (job %s): %s", job_id, exc)
        if "video_id" in locals():
            try:
                update_video_status(video_id, status="failed")
            except Exception:
                pass
        update_job(
            job_id,
            status="failed",
            step="Erro durante a renderização com FFmpeg",
            progress=100,
            error=str(exc)
        )


@app.post("/api/render")
def start_render(req: RenderRequest):
    """
    Inicia a renderização em background dos cortes selecionados pelo usuário,
    aplicando os parâmetros de enquadramento customizados (4 modos) e legendas.
    """
    if not req.cuts:
        raise HTTPException(status_code=400, detail="Nenhum corte selecionado para renderização.")

    video = get_video(req.video_id)
    if not video:
        raise HTTPException(status_code=404, detail=f"Vídeo ID {req.video_id} não encontrado.")

    job_id = f"job_render_{uuid.uuid4().hex[:10]}"
    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id,
            "type": "render",
            "status": "running",
            "step": f"Iniciando renderização de {len(req.cuts)} corte(s)...",
            "progress": 5,
            "result": None,
            "error": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    threading.Thread(
        target=_run_render_job,
        args=(job_id, req.video_id, req.cuts, req.max_resolution or 1080),
        daemon=True
    ).start()

    return {
        "job_id": job_id,
        "message": "Processo de renderização iniciado em segundo plano."
    }


@app.get("/api/history")
def get_history(
    search: Optional[str] = Query(None, description="Busca textual por título ou URL"),
    min_score: Optional[float] = Query(0.0, description="Nota mínima de viralidade (0 a 100)")
):
    """
    Retorna a lista de vídeos cadastrados no SQLite com seus respectivos cortes renderizados.
    Compatível com filtros de busca textual e nota mínima.
    """
    videos = list_videos(search=search)
    history_items: List[Dict[str, Any]] = []

    for v in videos:
        all_cuts = list_cortes(video_id=v["id"])
        filtered_cuts: List[Dict[str, Any]] = []

        for c in all_cuts:
            raw_score = float(c.get("virality_score") or 0.0)
            norm_score = raw_score * 10.0 if (0.0 < raw_score <= 10.0) else raw_score

            if min_score is None or norm_score >= min_score:
                out_p = c.get("output_path", "")
                video_url = None
                file_exists = False
                if out_p and os.path.exists(out_p):
                    video_url = f"/data/cortes/{Path(out_p).name}"
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


@app.delete("/api/history/{video_id}")
def delete_history_item(
    video_id: int,
    delete_files: bool = Query(False, description="Apagar também os arquivos .mp4/.mp3 físicos do disco")
):
    """
    Remove o vídeo e seus cortes associados do SQLite, com opção de excluir também os arquivos físicos.
    """
    success = delete_video(video_id=video_id, delete_files=delete_files)
    if not success:
        raise HTTPException(status_code=404, detail=f"Vídeo ID {video_id} não encontrado.")
    return {
        "success": True,
        "message": f"Vídeo ID {video_id} {'e arquivos físicos ' if delete_files else ''}removido com sucesso."
    }


@app.post("/api/open-folder")
def open_folder():
    """
    Abre a pasta data/cortes/ diretamente no Explorador de Arquivos do Windows via os.startfile.
    """
    try:
        cuts_abs = str(CUTS_DIR.resolve())
        if hasattr(os, "startfile"):
            os.startfile(cuts_abs)
        else:
            subprocess.Popen(["explorer", cuts_abs])
        return {
            "success": True,
            "message": f"Pasta aberta no Explorador: {cuts_abs}"
        }
    except Exception as exc:
        logger.error("Erro ao abrir pasta data/cortes/: %s", exc)
        raise HTTPException(status_code=500, detail=f"Erro ao abrir pasta no Explorador: {exc}")


# ==========================================
# ROTAS ESTÁTICAS PARA MÍDIA E INTERFACE WEB
# ==========================================
app.mount("/data/temp", StaticFiles(directory=str(BASE_DIR / "data" / "temp")), name="data_temp")
app.mount("/data/cortes", StaticFiles(directory=str(BASE_DIR / "data" / "cortes")), name="data_cortes")
app.mount("/", StaticFiles(directory=str(BASE_DIR / "web"), html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8502, reload=False)
