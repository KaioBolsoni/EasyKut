"""
Módulo de Edição e Renderização de Vídeo (EasyKut)
Executa cortes precisos, conversão para proporção vertical 9:16 (Shorts/Reels/TikTok)
com fundo desfocado ou corte centralizado, utilizando FFmpeg e OpenCV.
"""

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import CUTS_DIR, FFMPEG_AVAILABLE

logger = logging.getLogger(__name__)


def check_ffmpeg() -> None:
    """Verifica se o FFmpeg está disponível no PATH do sistema."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError(
            "FFmpeg ou FFprobe não foram encontrados no PATH. "
            "Certifique-se de que o executável está instalado (ex: C:\\ffmpeg\\bin)."
        )


def get_video_metadata(video_path: str) -> Dict[str, Any]:
    """
    Obtém metadados técnicos do vídeo (duração, dimensões, fps) via ffprobe.
    """
    check_ffmpeg()
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Vídeo não encontrado: {video_path}")

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    probe_data = json.loads(result.stdout)

    video_stream = next(
        (s for s in probe_data.get("streams", []) if s.get("codec_type") == "video"),
        {}
    )
    format_info = probe_data.get("format", {})

    width = int(video_stream.get("width", 1920))
    height = int(video_stream.get("height", 1080))
    duration = float(format_info.get("duration", video_stream.get("duration", 0.0)))

    return {
        "width": width,
        "height": height,
        "duration": duration,
        "aspect_ratio": width / height if height > 0 else 1.77,
    }


def cut_video(
    input_path: str,
    start_time: float,
    end_time: float,
    output_path: Optional[str] = None,
    mode: str = "blur_background",
    target_width: int = 1080,
    target_height: int = 1920
) -> str:
    """
    Recorta um trecho do vídeo e formata para proporção 9:16 vertical.

    Modos disponíveis:
    - 'blur_background': Fundo ampliado e desfocado com vídeo original nítido ao centro (Padrão viral).
    - 'crop_center': Recorte direto das bordas laterais focado no centro (9:16).
    - 'original': Mantém a proporção e resolução original do vídeo de entrada.

    Retorna o caminho do arquivo de vídeo gerado.
    """
    check_ffmpeg()
    src = Path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"Arquivo de origem não encontrado: {input_path}")

    duration = end_time - start_time
    if duration <= 0:
        raise ValueError(f"Intervalo de corte inválido: {start_time}s -> {end_time}s")

    if output_path:
        dest = Path(output_path)
    else:
        dest_filename = f"{src.stem}_cut_{int(start_time)}_{int(end_time)}.mp4"
        dest = CUTS_DIR / dest_filename

    dest.parent.mkdir(parents=True, exist_ok=True)

    # Base dos argumentos: corte com início e duração exatos
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(round(start_time, 2)),
        "-i", str(src),
        "-t", str(round(duration, 2)),
    ]

    # Configuração dos filtros de vídeo
    if mode == "blur_background":
        # Cria fundo 9:16 desfocado com vídeo original centralizado
        filter_complex = (
            f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
            f"crop={target_width}:{target_height},boxblur=25:10[bg];"
            f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[outv]"
        )
        cmd.extend(["-filter_complex", filter_complex, "-map", "[outv]", "-map", "0:a?"])
    elif mode == "crop_center":
        # Recorte 9:16 central direto
        video_filter = f"crop=ih*(9/16):ih,scale={target_width}:{target_height}"
        cmd.extend(["-vf", video_filter])
    else:
        # Modo original
        cmd.extend(["-vf", f"scale=-2:{min(target_width, 1080)}"])

    # Codecs otimizados para alta compatibilidade mobile e web (Reels/TikTok/Shorts)
    cmd.extend([
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(dest)
    ])

    logger.info("Executando corte FFmpeg (%s -> %ss, modo: %s): %s", start_time, end_time, mode, dest.name)

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        logger.error("Falha no FFmpeg: %s", proc.stderr)
        raise RuntimeError(f"Erro ao processar corte de vídeo: {proc.stderr[:300]}")

    logger.info("Corte finalizado com sucesso: %s", dest)
    return str(dest.resolve())


def extract_frame_thumbnail(video_path: str, timestamp: float = 1.0, output_path: Optional[str] = None) -> str:
    """
    Extrai um frame estático de preview do vídeo como imagem JPEG.
    """
    check_ffmpeg()
    src = Path(video_path)
    dest = Path(output_path) if output_path else src.with_suffix(".jpg")

    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(max(0.0, timestamp)),
        "-i", str(src),
        "-vframes", "1",
        "-q:v", "2",
        str(dest)
    ]

    subprocess.run(cmd, capture_output=True, check=True)
    return str(dest.resolve())
