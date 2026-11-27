"""
Builders de filtros FFmpeg por modo de layout (Strategy Pattern — V4.0).
Suporta horizontal_16_9, vertical_blur, manual_crop, face_tracking_9_16 (estático e dinâmico),
split_screen e square_1_1 (multi-export 1080x1080).
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.face_tracker import (
    build_ffmpeg_dynamic_crop_x_expr,
    detect_dynamic_face_crop_keyframes,
    detect_smooth_face_crop_x,
)

logger = logging.getLogger(__name__)


def normalize_effective_mode(
    mode: str,
    layout_mode: Optional[str],
    face_tracking: bool,
) -> Tuple[str, bool]:
    """Normaliza aliases legados de layout para um dos 5 builders canônicos."""
    effective_mode = (layout_mode or mode).lower().strip()
    if effective_mode in ("blur_background", "blur"):
        return "vertical_blur", face_tracking
    if effective_mode in ("crop_center", "crop"):
        return "manual_crop", face_tracking
    if effective_mode in ("long_16_9", "original"):
        return "horizontal_16_9", face_tracking
    if effective_mode in ("face_tracking_9_16", "vertical_face"):
        return "manual_crop", True
    if effective_mode in ("square_1_1", "square", "1:1"):
        return "square_1_1", face_tracking
    return effective_mode, face_tracking


def build_horizontal_16_9_args(
    *,
    sub_filter: Optional[str],
    **_: Any,
) -> Tuple[List[str], bool]:
    """Retorna argumentos FFmpeg para modo widescreen 16:9 (stream copy se sem legendas)."""
    if not sub_filter:
        return ["-c", "copy", "-movflags", "+faststart"], True
    video_filter = f"scale=trunc(iw/2)*2:trunc(ih/2)*2,{sub_filter}"
    return ["-vf", video_filter], False


def build_vertical_blur_args(
    *,
    cfg: Dict[str, Any],
    target_width: int,
    target_height: int,
    sub_filter: Optional[str],
    input_video_label: str = "[0:v]",
    **_: Any,
) -> Tuple[List[str], bool]:
    """Retorna argumentos FFmpeg para canvas 9:16 com fundo desfocado e zoom opcional."""
    zoom_factor = max(1.0, min(1.3, float(cfg.get("zoom_factor", 1.0))))
    fg_w = int(round(target_width * zoom_factor))
    if fg_w % 2 != 0:
        fg_w += 1

    bg_filter = (
        f"{input_video_label}scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
        f"crop={target_width}:{target_height},boxblur=20:5,eq=brightness=-0.15[bg]"
    )
    if zoom_factor <= 1.0:
        fg_filter = f"{input_video_label}scale={target_width}:-2:force_original_aspect_ratio=decrease[fg]"
    else:
        fg_filter = (
            f"{input_video_label}scale={fg_w}:-2:force_original_aspect_ratio=decrease,"
            f"crop=min({target_width},iw):min({target_height},ih)[fg]"
        )

    filter_complex = f"{bg_filter};{fg_filter};[bg][fg]overlay=(W-w)/2:(H-h)/2[merged]"
    if sub_filter:
        filter_complex += f";[merged]{sub_filter}[outv]"
        return ["-filter_complex", filter_complex, "-map", "[outv]", "-map", "0:a?"], False
    return ["-filter_complex", filter_complex, "-map", "[merged]", "-map", "0:a?"], False


def build_manual_crop_args(
    *,
    src_path: str,
    start_time: float,
    end_time: float,
    orig_w: int,
    orig_h: int,
    target_width: int,
    target_height: int,
    cfg: Dict[str, Any],
    face_tracking: bool,
    sub_filter: Optional[str],
    dynamic_face_tracking: bool = False,
    **_: Any,
) -> Tuple[List[str], bool]:
    """Retorna argumentos FFmpeg para recorte 9:16 manual ou guiado por Face Tracking (estático/dinâmico)."""
    def_crop_w = int(orig_h * (9.0 / 16.0))
    if def_crop_w % 2 != 0:
        def_crop_w -= 1

    dynamic_x_expr: Optional[str] = None

    if face_tracking:
        max_crop_x = max(0, orig_w - def_crop_w)
        if dynamic_face_tracking:
            try:
                keyframes = detect_dynamic_face_crop_keyframes(
                    video_path=src_path,
                    start_time=start_time,
                    end_time=end_time,
                    orig_w=orig_w,
                    orig_h=orig_h,
                    crop_w=def_crop_w,
                )
                if keyframes:
                    expr = build_ffmpeg_dynamic_crop_x_expr(keyframes, max_crop_x=max_crop_x)
                    if not expr.isdigit():
                        dynamic_x_expr = expr
                        crop_x, crop_y, crop_w, crop_h = 0, 0, def_crop_w, orig_h
                    else:
                        crop_x, crop_y, crop_w, crop_h = int(expr), 0, def_crop_w, orig_h
                else:
                    face_x = detect_smooth_face_crop_x(
                        video_path=src_path,
                        start_time=start_time,
                        end_time=end_time,
                        orig_w=orig_w,
                        orig_h=orig_h,
                        crop_w=def_crop_w,
                    )
                    if face_x is None:
                        face_x = max(0, (orig_w - def_crop_w) // 2)
                        face_x -= face_x % 2
                    crop_x, crop_y, crop_w, crop_h = face_x, 0, def_crop_w, orig_h
            except Exception as exc:
                logger.warning("Face tracking dinâmico falhou: %s. Fallback central.", exc)
                fallback_x = max(0, (orig_w - def_crop_w) // 2)
                fallback_x -= fallback_x % 2
                crop_x, crop_y, crop_w, crop_h = fallback_x, 0, def_crop_w, orig_h
        else:
            face_x = None
            try:
                face_x = detect_smooth_face_crop_x(
                    video_path=src_path,
                    start_time=start_time,
                    end_time=end_time,
                    orig_w=orig_w,
                    orig_h=orig_h,
                    crop_w=def_crop_w,
                )
            except Exception as exc:
                logger.warning("Preset Face Tracking falhou: %s. Utilizando fallback central.", exc)
                face_x = None

            if face_x is None:
                face_x = max(0, (orig_w - def_crop_w) // 2)
                face_x -= face_x % 2
            crop_x, crop_y, crop_w, crop_h = face_x, 0, def_crop_w, orig_h
    else:
        ref_w = float(cfg.get("ref_w", 0) or 0)
        ref_h = float(cfg.get("ref_h", 0) or 0)
        sx = (orig_w / ref_w) if ref_w > 0 else 1.0
        sy = (orig_h / ref_h) if ref_h > 0 else 1.0

        raw_w = cfg.get("crop_w", cfg.get("w"))
        raw_h = cfg.get("crop_h", cfg.get("h"))
        raw_x = cfg.get("crop_x", cfg.get("x"))
        raw_y = cfg.get("crop_y", cfg.get("y"))

        crop_w = int(round(float(raw_w) * sx)) if raw_w is not None else def_crop_w
        crop_h = int(round(float(raw_h) * sy)) if raw_h is not None else orig_h
        crop_x = int(round(float(raw_x) * sx)) if raw_x is not None else (orig_w - crop_w) // 2
        crop_y = int(round(float(raw_y) * sy)) if raw_y is not None else 0

    target_ar = target_width / target_height
    crop_w = max(2, min(orig_w, crop_w))
    crop_h = max(2, min(orig_h, crop_h))
    if crop_w / crop_h > target_ar:
        crop_w = int(round(crop_h * target_ar))
    else:
        crop_h = int(round(crop_w / target_ar))
    crop_w = max(2, min(orig_w, crop_w))
    crop_h = max(2, min(orig_h, crop_h))
    crop_x = max(0, min(orig_w - crop_w, crop_x))
    crop_y = max(0, min(orig_h - crop_h, crop_y))

    crop_x -= crop_x % 2
    crop_y -= crop_y % 2
    crop_w = max(2, crop_w - (crop_w % 2))
    crop_h = max(2, crop_h - (crop_h % 2))

    if dynamic_x_expr:
        video_filter = f"crop={crop_w}:{crop_h}:x='{dynamic_x_expr}':y={crop_y},scale={target_width}:{target_height}"
    else:
        video_filter = f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={target_width}:{target_height}"

    if sub_filter:
        video_filter += f",{sub_filter}"
    return ["-vf", video_filter], False


def build_square_1_1_args(
    *,
    orig_w: int,
    orig_h: int,
    target_width: int,
    cfg: Dict[str, Any],
    sub_filter: Optional[str],
    **_: Any,
) -> Tuple[List[str], bool]:
    """Retorna argumentos FFmpeg para formato quadrado 1:1 (1080x1080 — Instagram/LinkedIn Feed)."""
    side = min(orig_w, orig_h)
    side = max(2, side - (side % 2))
    out_side = max(2, target_width - (target_width % 2))

    ref_w = float(cfg.get("ref_w", 0) or 0)
    sx = (orig_w / ref_w) if ref_w > 0 else 1.0
    raw_x = cfg.get("crop_x", cfg.get("x"))
    crop_x = int(round(float(raw_x) * sx)) if raw_x is not None else (orig_w - side) // 2
    crop_x = max(0, min(orig_w - side, crop_x))
    crop_x -= crop_x % 2
    crop_y = max(0, (orig_h - side) // 2)
    crop_y -= crop_y % 2

    video_filter = f"crop={side}:{side}:{crop_x}:{crop_y},scale={out_side}:{out_side}"
    if sub_filter:
        video_filter += f",{sub_filter}"
    return ["-vf", video_filter], False


def _fit_split_box(
    box: Dict[str, Any],
    def_x: int,
    def_y: int,
    def_w: int,
    def_h: int,
    sx: float,
    sy: float,
    orig_w: int,
    orig_h: int,
    target_ar: float,
) -> Tuple[int, int, int, int]:
    bx = int(round(float(box["x"]) * sx)) if "x" in box else def_x
    by = int(round(float(box["y"]) * sy)) if "y" in box else def_y
    bw = int(round(float(box["w"]) * sx)) if "w" in box else def_w
    bh = int(round(float(box["h"]) * sy)) if "h" in box else def_h

    bw = max(2, min(orig_w, bw))
    bh = max(2, min(orig_h, bh))
    if bw / bh > target_ar:
        bw = int(round(bh * target_ar))
    else:
        bh = int(round(bw / target_ar))
    bw = max(2, min(orig_w, bw))
    bh = max(2, min(orig_h, bh))
    bx = max(0, min(orig_w - bw, bx))
    by = max(0, min(orig_h - bh, by))
    return (bx - (bx % 2), by - (by % 2), max(2, bw - (bw % 2)), max(2, bh - (bh % 2)))


def build_split_screen_args(
    *,
    orig_w: int,
    orig_h: int,
    target_width: int,
    target_height: int,
    cfg: Dict[str, Any],
    sub_filter: Optional[str],
    input_video_label: str = "[0:v]",
    **_: Any,
) -> Tuple[List[str], bool]:
    """Retorna argumentos FFmpeg para empilhamento vertical 2x (1080x960 cada)."""
    half_h = target_height // 2
    target_ar = target_width / half_h

    ref_w = float(cfg.get("ref_w", 0) or 0)
    ref_h = float(cfg.get("ref_h", 0) or 0)
    sx = (orig_w / ref_w) if ref_w > 0 else 1.0
    sy = (orig_h / ref_h) if ref_h > 0 else 1.0

    def_box_w = min(orig_w // 2, int(orig_h * target_ar))
    def_box_h = int(def_box_w / target_ar)

    top_x, top_y, top_w, top_h = _fit_split_box(
        cfg.get("box_top") or {},
        max(0, (orig_w // 4) - (def_box_w // 2)),
        max(0, (orig_h - def_box_h) // 2),
        def_box_w,
        def_box_h,
        sx,
        sy,
        orig_w,
        orig_h,
        target_ar,
    )
    bot_x, bot_y, bot_w, bot_h = _fit_split_box(
        cfg.get("box_bottom") or {},
        min(orig_w - def_box_w, (3 * orig_w // 4) - (def_box_w // 2)),
        max(0, (orig_h - def_box_h) // 2),
        def_box_w,
        def_box_h,
        sx,
        sy,
        orig_w,
        orig_h,
        target_ar,
    )

    filter_complex = (
        f"{input_video_label}crop={top_w}:{top_h}:{top_x}:{top_y},scale={target_width}:{half_h}[top];"
        f"{input_video_label}crop={bot_w}:{bot_h}:{bot_x}:{bot_y},scale={target_width}:{half_h}[bot];"
        f"[top][bot]vstack=inputs=2[stacked]"
    )
    if sub_filter:
        filter_complex += f";[stacked]{sub_filter}[outv]"
        return ["-filter_complex", filter_complex, "-map", "[outv]", "-map", "0:a?"], False
    return ["-filter_complex", filter_complex, "-map", "[stacked]", "-map", "0:a?"], False


LAYOUT_FILTER_BUILDERS: Dict[str, Callable[..., Tuple[List[str], bool]]] = {
    "horizontal_16_9": build_horizontal_16_9_args,
    "vertical_blur": build_vertical_blur_args,
    "manual_crop": build_manual_crop_args,
    "crop": build_manual_crop_args,
    "split_screen": build_split_screen_args,
    "split": build_split_screen_args,
    "square_1_1": build_square_1_1_args,
}


def build_layout_ffmpeg_args(
    *,
    effective_mode: str,
    src_path: str,
    start_time: float,
    end_time: float,
    orig_w: int,
    orig_h: int,
    target_width: int,
    target_height: int,
    cfg: Dict[str, Any],
    face_tracking: bool,
    sub_filter: Optional[str],
    dynamic_face_tracking: bool = False,
    input_video_label: str = "[0:v]",
) -> Tuple[List[str], bool]:
    """Despacha a construção dos argumentos FFmpeg para o builder do layout selecionado."""
    builder = LAYOUT_FILTER_BUILDERS.get(effective_mode, build_vertical_blur_args)
    return builder(
        src_path=src_path,
        start_time=start_time,
        end_time=end_time,
        orig_w=orig_w,
        orig_h=orig_h,
        target_width=target_width,
        target_height=target_height,
        cfg=cfg,
        face_tracking=face_tracking,
        sub_filter=sub_filter,
        dynamic_face_tracking=dynamic_face_tracking,
        input_video_label=input_video_label,
    )
