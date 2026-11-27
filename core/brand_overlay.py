"""
Módulo de Brand Kit (Identidade Visual Reutilizável — Onda 2).
Gera filtros FFmpeg seguros para logo/watermark PNG confinada em DATA_DIR,
barra de progresso animada na borda do vídeo e Hook Banner nos primeiros segundos.
"""

import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

from core import media_utils

logger = logging.getLogger(__name__)


def hex_to_ffmpeg_color(hex_color: str, alpha: float = 0.95) -> str:
    """Converte cor hexadecimal #RRGGBB em literal seguro 0xRRGGBB@alpha para drawbox/drawtext."""
    raw = str(hex_color or "#6366F1").strip().lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", raw):
        raw = "6366F1"
    a = max(0.0, min(1.0, float(alpha)))
    return f"0x{raw.upper()}@{a:.2f}"


def escape_drawtext_value(text: str) -> str:
    """Sanitiza texto livre para uso seguro no filtro drawtext do FFmpeg (sem quebra ou injeção)."""
    cleaned = re.sub(r"[\r\n\t]+", " ", str(text or "")).strip()[:72]
    cleaned = (
        cleaned.replace("\\", "")
        .replace("'", "")
        .replace(":", " -")
        .replace("%", "%%")
        .replace("[", "(")
        .replace("]", ")")
        .replace(";", ",")
    )
    return cleaned


def resolve_confined_logo_path(logo_path: Optional[str]) -> Optional[str]:
    """Valida que a logo do Brand Kit existe e está estritamente confinada em DATA_DIR."""
    if not logo_path:
        return None
    p = Path(logo_path).resolve()
    if media_utils.is_path_within(p, media_utils.DATA_DIR_RESOLVED) and p.exists() and p.is_file():
        return str(p)
    return None


def build_brand_overlay_filters(
    *,
    brand_kit: Optional[Dict[str, Any]],
    duration: float,
    target_width: int,
    target_height: int,
    hook_title: str = "",
    input_video_label: str = "[vbase]",
    logo_input_index: Optional[int] = None,
) -> Tuple[List[str], str, Optional[str]]:
    """
    Constrói cadeia de filtros FFmpeg de identidade visual (logo + barra de progresso + Hook Banner).
    Retorna `(lista_de_filtros, label_video_final, caminho_logo_validado_ou_None)`.
    """
    if not brand_kit:
        return [], input_video_label, None

    filters: List[str] = []
    cur_label = input_video_label
    safe_dur = max(0.5, float(duration))

    # 1. Logo / Watermark PNG
    valid_logo = resolve_confined_logo_path(brand_kit.get("logo_path"))
    if valid_logo and logo_input_index is not None:
        logo_scale = max(0.05, min(0.40, float(brand_kit.get("logo_scale", 0.14))))
        logo_opacity = max(0.05, min(1.0, float(brand_kit.get("logo_opacity", 0.85))))
        logo_w = max(32, int(round(target_width * logo_scale)))
        logo_w -= logo_w % 2

        pos = str(brand_kit.get("logo_position", "top_right"))
        margin = max(20, int(round(target_width * 0.03)))
        pos_map = {
            "top_left": f"{margin}:{margin}",
            "top_right": f"W-w-{margin}:{margin}",
            "bottom_left": f"{margin}:H-h-{margin + 24}",
            "bottom_right": f"W-w-{margin}:H-h-{margin + 24}",
        }
        xy = pos_map.get(pos, pos_map["top_right"])
        filters.append(
            f"[{logo_input_index}:v]scale={logo_w}:-1,format=rgba,colorchannelmixer=aa={logo_opacity:.2f}[brand_logo]"
        )
        filters.append(f"{cur_label}[brand_logo]overlay={xy}[v_logo]")
        cur_label = "[v_logo]"

    # 2. Barra de progresso animada na borda inferior
    if brand_kit.get("progress_bar_enabled"):
        bar_h = max(8, int(round(target_height * 0.006)))
        bar_color = hex_to_ffmpeg_color(brand_kit.get("progress_bar_color", "#6366F1"), alpha=0.95)
        filters.append(
            f"{cur_label}drawbox=x=0:y=ih-{bar_h}:w='iw*min(1,t/{safe_dur:.2f})':h={bar_h}:color={bar_color}:t=fill[v_prog]"
        )
        cur_label = "[v_prog]"

    # 3. Hook Banner nos primeiros segundos
    safe_hook = escape_drawtext_value(hook_title)
    if brand_kit.get("hook_banner_enabled") and safe_hook:
        hook_dur = max(0.5, min(safe_dur, float(brand_kit.get("hook_banner_duration", 3.0))))
        banner_h = max(72, int(round(target_height * 0.055)))
        banner_y = max(48, int(round(target_height * 0.06)))
        font_sz = max(24, int(round(target_width * 0.034)))
        filters.append(
            f"{cur_label}drawbox=x=40:y={banner_y}:w=iw-80:h={banner_h}:color=0x0F172A@0.82:t=fill:enable='between(t,0,{hook_dur:.2f})',"
            f"drawtext=text='{safe_hook}':fontcolor=white:fontsize={font_sz}:"
            f"x=(w-text_w)/2:y={banner_y}+({banner_h}-text_h)/2:enable='between(t,0,{hook_dur:.2f})'[v_hook]"
        )
        cur_label = "[v_hook]"

    return filters, cur_label, valid_logo
