"""
Módulo de geração, sincronização temporal e formatação FFmpeg de legendas (.srt).
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

from core import media_utils

logger = logging.getLogger(__name__)


def format_srt_time(seconds: float) -> str:
    """
    Converte segundos em formato de timestamp padrão SRT: HH:MM:SS,mmm.
    """
    clamped_sec = max(0.0, seconds)
    millis = int(round((clamped_sec - int(clamped_sec)) * 1000))
    if millis >= 1000:
        millis = 999
    total_seconds = int(clamped_sec)
    secs = total_seconds % 60
    total_minutes = total_seconds // 60
    mins = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def generate_srt_for_segment(
    segments: List[Dict[str, Any]],
    start_time: float,
    end_time: float,
    output_srt_path: Optional[str] = None,
    temp_dir: Optional[Path] = None,
) -> Optional[str]:
    """
    Recebe os segmentos com timestamps do Whisper e o intervalo do corte (start_time, end_time),
    ajusta os tempos para iniciarem em 00:00:00,000 e salva um arquivo .srt em data/temp/.
    Retorna o caminho do arquivo .srt ou None se não houver legendas no trecho.
    """
    if not segments:
        return None

    srt_entries = []
    counter = 1

    for seg in segments:
        seg_start = float(seg.get("start", 0.0))
        seg_end = float(seg.get("end", 0.0))
        text = str(seg.get("text", "")).strip()

        if seg_end <= start_time or seg_start >= end_time or not text:
            continue

        rel_start = max(0.0, seg_start - start_time)
        rel_end = min(end_time - start_time, seg_end - start_time)

        if rel_end > rel_start:
            srt_entries.append(
                f"{counter}\n"
                f"{format_srt_time(rel_start)} --> {format_srt_time(rel_end)}\n"
                f"{text}\n"
            )
            counter += 1

    if not srt_entries:
        logger.info("Nenhuma fala encontrada no intervalo %.2fs -> %.2fs para legenda.", start_time, end_time)
        return None

    if output_srt_path:
        dest_srt = Path(output_srt_path)
    else:
        base_temp = temp_dir or media_utils.TEMP_DIR
        dest_srt = base_temp / f"sub_{int(start_time)}_{int(end_time)}_{uuid.uuid4().hex[:6]}.srt"

    dest_srt.parent.mkdir(parents=True, exist_ok=True)
    dest_srt.write_text("\n".join(srt_entries), encoding="utf-8")
    logger.info("Legenda SRT sincronizada gerada com sucesso: %s", dest_srt)
    return str(dest_srt.resolve())


def format_ffmpeg_subtitles_filter(srt_path: str, is_vertical: bool = True) -> str:
    """
    Formata e escapa o caminho do arquivo .srt para o filtro subtitles do FFmpeg (libavfilter)
    no Windows/Linux, aplicando tipografia para Shorts (9:16) ou horizontal (16:9).
    """
    p = Path(srt_path).resolve()
    clean_path = str(p).replace("\\", "/")

    if len(clean_path) > 1 and clean_path[1] == ":":
        drive = clean_path[0]
        rest = clean_path[2:]
        clean_path = f"{drive}\\:{rest}"

    clean_path = (
        clean_path.replace("'", "\\'")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )

    if is_vertical:
        style = (
            "FontName=Arial,FontSize=16,Bold=1,"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,"
            "Alignment=2,MarginV=55"
        )
    else:
        style = (
            "FontName=Arial,FontSize=18,Bold=1,"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,"
            "Alignment=2,MarginV=25"
        )

    return f"subtitles='{clean_path}':force_style='{style}'"


_format_ffmpeg_subtitles_filter = format_ffmpeg_subtitles_filter
