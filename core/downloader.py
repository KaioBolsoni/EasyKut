"""
Módulo de Download de Vídeo e Áudio (EasyKut)
Utiliza a biblioteca yt-dlp para extrair metadados e baixar mídias
de forma segura e assíncrona/otimizada.
"""

import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import yt_dlp

from core.config import BASE_DIR, DOWNLOADS_DIR, get_cookies_file

logger = logging.getLogger(__name__)


def _apply_common_ydl_opts(opts: Dict[str, Any]) -> Dict[str, Any]:
    """
    Injeta opções comuns de resiliência e autenticação via cookies.txt (formato Netscape)
    caso o arquivo esteja presente no projeto.
    Evita --cookies-from-browser para não falhar com banco de cookies criptografado/bloqueado no Windows.
    """
    opts["no_color"] = True
    opts["retries"] = 3
    opts["fragment_retries"] = 3
    opts["extractor_retries"] = 3
    opts["remote_components"] = ["ejs:github"]

    cookies_file = get_cookies_file()
    if cookies_file:
        opts["cookiefile"] = str(cookies_file)
        logger.debug("Usando arquivo de cookies autenticado: %s", cookies_file)
        # Remove qualquer extractor_args forçando player_client mobile (android/ios) incompatível com cookies de sessão web
        if "extractor_args" in opts and isinstance(opts["extractor_args"], dict):
            yt_args = opts["extractor_args"].get("youtube", {})
            if isinstance(yt_args, dict) and "player_client" in yt_args:
                yt_args.pop("player_client", None)

    return opts


def _raise_formatted_ytdlp_error(exc: Exception, url: str) -> None:
    """
    Formata erros de bloqueio antibot do YouTube com instruções claras sobre o cookies.txt.
    """
    msg = str(exc)
    if "Sign in to confirm you're not a bot" in msg or "confirm you’re not a bot" in msg:
        cookies_file = get_cookies_file()
        expected_path = BASE_DIR / "cookies.txt"
        if not cookies_file:
            raise RuntimeError(
                "O YouTube solicitou verificação de sessão ('Sign in to confirm you're not a bot'). "
                f"Exporte os cookies do YouTube no formato Netscape e salve o arquivo em: {expected_path}"
            ) from exc
        else:
            raise RuntimeError(
                f"O YouTube rejeitou ou expirou a sessão do arquivo de cookies atual ({cookies_file}). "
                "Exporte um novo cookies.txt (formato Netscape) a partir de uma janela anônima logada no YouTube."
            ) from exc
    raise exc


def is_valid_video_url(url: str) -> bool:
    """
    Valida se a URL fornecida tem padrão de plataforma de vídeo suportada (ex: YouTube, Shorts, etc.).
    """
    if not url or not isinstance(url, str):
        return False
    u = url.strip()
    if not u.startswith("http://") and not u.startswith("https://"):
        u = "https://" + u
    youtube_pattern = r"(youtube\.com/(watch\?.*v=|shorts/|embed/|live/)|youtu\.be/)[a-zA-Z0-9_-]{11}"
    generic_url_pattern = r"^https?://[^\s]+"
    return bool(re.search(youtube_pattern, u) or re.match(generic_url_pattern, u))


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

    ydl_opts = _apply_common_ydl_opts({
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    })

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
            _raise_formatted_ytdlp_error(exc, url)


def get_direct_stream_url(url: str, max_resolution: int = 1080) -> str:
    """
    Obtém a URL HTTP direta do stream de vídeo via yt-dlp sem baixar o arquivo.
    Permite ao FFmpeg extrair frames remotos diretamente em segundos via streaming.
    """
    if not is_valid_video_url(url):
        raise ValueError(f"URL de vídeo inválida: {url}")

    ydl_opts = _apply_common_ydl_opts({
        "format": f"bestvideo[height<={max_resolution}]+bestaudio/best[height<={max_resolution}]/best",
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    })

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as exc:
            _raise_formatted_ytdlp_error(exc, url)

        stream_url = info.get("url")
        if not stream_url:
            formats = info.get("formats", []) or []
            for f in reversed(formats):
                if f.get("url") and f.get("vcodec") != "none":
                    stream_url = f.get("url")
                    break

        if not stream_url:
            raise ValueError(f"Não foi possível obter a URL direta do stream para: {url}")

        return stream_url


def extract_preview_frame(
    source: str,
    timestamp_sec: float = 1.0,
    output_path: Optional[str] = None,
    direct_stream_url: Optional[str] = None
) -> Optional[str]:
    """
    Fachada para extração de 1 único frame (.jpg) a partir de arquivo local ou URL do YouTube.
    """
    from core.video_engine import extract_preview_frame as _extract
    return _extract(
        source=source,
        timestamp_sec=timestamp_sec,
        output_path=output_path,
        direct_stream_url=direct_stream_url
    )


def _cleanup_stale_part_files(target_dir: Path) -> None:
    """
    Remove arquivos .part e .ytdl órfãos deixados por downloads interrompidos no Windows,
    evitando conflitos de renomeação [WinError 2] ou bloqueios em execuções futuras.
    """
    if not target_dir.exists():
        return
    for pattern in ("*.part", "*.ytdl"):
        for stale in target_dir.glob(pattern):
            try:
                if stale.is_file():
                    stale.unlink()
            except Exception as exc:
                logger.debug("Não foi possível remover arquivo residual %s: %s", stale.name, exc)


def _pick_best_video_stream_url(info: Dict[str, Any], max_resolution: int = 1080) -> Optional[str]:
    """
    Seleciona em memória a melhor URL de stream de vídeo (<= max_resolution) a partir
    dos metadados já retornados pelo yt-dlp, sem fazer nenhuma nova requisição ao YouTube.
    """
    formats = info.get("formats") or []
    candidates = []
    for f in formats:
        f_url = f.get("url")
        vcodec = f.get("vcodec")
        if not f_url or not vcodec or vcodec == "none":
            continue
        height = int(f.get("height") or 0)
        if height > max_resolution:
            continue
        protocol = str(f.get("protocol") or "").lower()
        is_direct_http = protocol in ("https", "http")
        is_mp4 = str(f.get("ext") or "").lower() == "mp4"
        candidates.append((1 if is_direct_http else 0, 1 if is_mp4 else 0, height, f_url))

    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return candidates[0][3]

    fallback_url = info.get("url")
    return fallback_url if isinstance(fallback_url, str) and fallback_url.startswith("http") else None


def download_audio_with_metadata(
    url: str,
    output_dir: Optional[Path] = None,
    max_resolution: int = 1080,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """
    Executa UMA ÚNICA chamada ao yt-dlp para extrair metadados, baixar o áudio MP3 leve
    e obter a URL direta de stream em memória (evitando múltiplas requisições ao YouTube
    e prevenindo bloqueios antibot por excesso de chamadas no mesmo IP).
    """
    if not is_valid_video_url(url):
        raise ValueError(f"URL de vídeo inválida: {url}")

    target_dir = output_dir or DOWNLOADS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_part_files(target_dir)

    def _hook(d):
        if progress_callback and d.get("status") == "downloading":
            progress_callback(d)

    suffix = uuid.uuid4().hex[:6]

    ydl_opts = _apply_common_ydl_opts({
        "format": "bestaudio/best",
        "outtmpl": str(target_dir / f"%(id)s_{suffix}_audio.%(ext)s"),
        "nopart": True,
        "overwrites": True,
        "windowsfilenames": True,
        "updatetime": False,
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
    })

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
        except Exception as exc:
            _raise_formatted_ytdlp_error(exc, url)

        if not info:
            raise ValueError("Não foi possível extrair informações ou baixar o áudio da URL.")

        video_id = info.get("id", "audio")
        audio_path = target_dir / f"{video_id}_{suffix}_audio.mp3"

        if not audio_path.exists():
            matches = list(target_dir.glob(f"{video_id}_{suffix}_audio.*"))
            if matches:
                audio_path = matches[0]

        if not audio_path.exists():
            raise FileNotFoundError(f"Arquivo de áudio não encontrado após download: {audio_path}")

        direct_stream_url = _pick_best_video_stream_url(info, max_resolution=max_resolution)

        metadata = {
            "id": video_id,
            "title": info.get("title", "Sem Título"),
            "duration": float(info.get("duration", 0.0) or 0.0),
            "thumbnail": info.get("thumbnail", ""),
            "uploader": info.get("uploader", ""),
            "view_count": info.get("view_count", 0),
        }

        logger.info("Ingestão unificada concluída em 1 chamada yt-dlp: %s (%s)", metadata["title"], audio_path.name)
        return {
            "info": metadata,
            "audio_path": str(audio_path.resolve()),
            "direct_stream_url": direct_stream_url,
        }


def download_audio(
    url: str,
    output_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None
) -> str:
    """
    Baixa e extrai apenas a faixa de áudio em formato MP3 (48k mono 16kHz) para otimizar
    o envio e processamento na API de transcrição Whisper da Groq.
    Retorna o caminho absoluto do arquivo de áudio gerado.
    """
    res = download_audio_with_metadata(url=url, output_dir=output_dir, progress_callback=progress_callback)
    return res["audio_path"]


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
    _cleanup_stale_part_files(target_dir)

    def _hook(d):
        if progress_callback and d.get("status") == "downloading":
            progress_callback(d)

    format_str = f"bestvideo[height<={max_resolution}]+bestaudio/best[height<={max_resolution}]/best"

    ydl_opts = _apply_common_ydl_opts({
        "format": format_str,
        "outtmpl": str(target_dir / "%(id)s_video.%(ext)s"),
        "merge_output_format": "mp4",
        "nopart": True,
        "overwrites": True,
        "windowsfilenames": True,
        "updatetime": False,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [_hook] if progress_callback else [],
    })

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
        except Exception as exc:
            _raise_formatted_ytdlp_error(exc, url)

        video_id = info.get("id", "video")
        video_path = target_dir / f"{video_id}_video.mp4"

        if not video_path.exists():
            matches = list(target_dir.glob(f"{video_id}_video.*"))
            if matches:
                video_path = matches[0]

        logger.info("Vídeo baixado com sucesso em: %s", video_path)
        return str(video_path.resolve())
