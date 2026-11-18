"""
Módulo de Download de Vídeo e Áudio (EasyKut)
Utiliza a biblioteca yt-dlp para extrair metadados e baixar mídias
de forma segura e assíncrona/otimizada.
"""

import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import yt_dlp

from core.config import DOWNLOADS_DIR

logger = logging.getLogger(__name__)


def is_valid_video_url(url: str) -> bool:
    """
    Valida se a URL fornecida tem padrão de plataforma de vídeo suportada (ex: YouTube, Shorts, etc.).
    """
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    youtube_pattern = r"^(https?://)?(www\.|m\.)?(youtube\.com/(watch\?v=|shorts/|embed/)|youtu\.be/)[a-zA-Z0-9_-]{11}"
    generic_url_pattern = r"^https?://[^\s/$.?#].[^\s]*$"
    return bool(re.match(youtube_pattern, url) or re.match(generic_url_pattern, url))


def sanitize_filename(name: str) -> str:
    """
    Remove caracteres proibidos pelo sistema de arquivos do Windows/Linux.
    """
    clean = re.sub(r'[\\/*?:"<>|]', "", name)
    clean = re.sub(r"\s+", "_", clean).strip("_")
    return clean[:80] if clean else "video"


def extract_video_info(url: str) -> Dict[str, Any]:
    """
    Extrai informações e metadados essenciais do vídeo sem realizar o download.
    """
    if not is_valid_video_url(url):
        raise ValueError(f"URL de vídeo inválida: {url}")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise ValueError("Não foi possível extrair informações da URL.")

            return {
                "id": info.get("id", ""),
                "title": info.get("title", "Sem Título"),
                "duration": float(info.get("duration", 0.0) or 0.0),
                "thumbnail": info.get("thumbnail", ""),
                "uploader": info.get("uploader", ""),
                "view_count": info.get("view_count", 0),
            }
        except Exception as exc:
            logger.error("Erro ao extrair metadados da URL %s: %s", url, exc)
            raise


def download_audio(
    url: str,
    output_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None
) -> str:
    """
    Baixa e extrai apenas a faixa de áudio em formato MP3 (64k) para otimizar
    o envio e processamento na API de transcrição Whisper da Groq.
    Retorna o caminho absoluto do arquivo de áudio gerado.
    """
    target_dir = output_dir or DOWNLOADS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    def _hook(d):
        if progress_callback and d.get("status") == "downloading":
            progress_callback(d)

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(target_dir / "%(id)s_audio.%(ext)s"),
        "postprocessor_args": {
            "FFmpegExtractAudio": ["-ac", "1", "-ar", "16000"]
        },
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "48",
        }],
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [_hook] if progress_callback else [],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        video_id = info.get("id", "audio")
        audio_path = target_dir / f"{video_id}_audio.mp3"

        if not audio_path.exists():
            # Fallback para caso tenha sido salvo com outra extensão
            matches = list(target_dir.glob(f"{video_id}_audio.*"))
            if matches:
                audio_path = matches[0]

        logger.info("Áudio baixado com sucesso em: %s", audio_path)
        return str(audio_path.resolve())


def download_video(
    url: str,
    output_dir: Optional[Path] = None,
    max_resolution: int = 1080,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None
) -> str:
    """
    Baixa o arquivo de vídeo completo em formato MP4 compatível até a resolução desejada.
    Retorna o caminho absoluto do arquivo de vídeo gerado.
    """
    target_dir = output_dir or DOWNLOADS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    def _hook(d):
        if progress_callback and d.get("status") == "downloading":
            progress_callback(d)

    format_str = f"bestvideo[ext=mp4][height<={max_resolution}]+bestaudio[ext=m4a]/best[ext=mp4]/best"

    ydl_opts = {
        "format": format_str,
        "outtmpl": str(target_dir / "%(id)s_video.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [_hook] if progress_callback else [],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        video_id = info.get("id", "video")
        video_path = target_dir / f"{video_id}_video.mp4"

        if not video_path.exists():
            matches = list(target_dir.glob(f"{video_id}_video.*"))
            if matches:
                video_path = matches[0]

        logger.info("Vídeo baixado com sucesso em: %s", video_path)
        return str(video_path.resolve())
