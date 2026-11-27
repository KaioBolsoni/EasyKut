"""
Módulo de renderização e extração de mídia via FFmpeg/OpenCV (EasyKut Studio).
Responsável por metadados (ffprobe), extração de frames/clipes de prévia,
desenho de enquadramento, renderização final nos 4 layouts e limpeza de temporários.
"""

import json
import logging
from pathlib import Path
import re
import subprocess  # nosec B404
import time
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from core import media_utils
from core.downloader import get_direct_stream_url
from core.layout_filters import build_layout_ffmpeg_args, normalize_effective_mode
from core.subtitles import format_ffmpeg_subtitles_filter, generate_srt_for_segment

logger = logging.getLogger(__name__)


def get_video_metadata(video_path: str) -> Dict[str, Any]:
    """Obtém metadados técnicos do vídeo (duração, dimensões, fps) via ffprobe."""
    media_utils.check_ffmpeg()
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Vídeo não encontrado: {video_path}")

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, check=True)  # nosec B603
    probe_data = json.loads(result.stdout)

    video_stream = next(
        (s for s in probe_data.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    format_info = probe_data.get("format", {})

    width = int(video_stream.get("width", 1920))
    height = int(video_stream.get("height", 1080))
    duration = float(format_info.get("duration", video_stream.get("duration", 0.0)))

    fps_str = video_stream.get("r_frame_rate", "30/1")
    try:
        if "/" in fps_str:
            num, den = fps_str.split("/")
            fps = float(num) / float(den) if float(den) != 0 else 30.0
        else:
            fps = float(fps_str)
    except Exception:
        fps = 30.0

    return {
        "width": width,
        "height": height,
        "duration": duration,
        "fps": fps,
        "aspect_ratio": width / height if height > 0 else 1.77,
    }


def extract_preview_frame(
    source: str,
    timestamp_sec: float = 1.0,
    output_path: Optional[str] = None,
    direct_stream_url: Optional[str] = None,
) -> Optional[str]:
    """
    Extrai 1 único frame (.jpg) no tempo informado usando FFmpeg com fast input seek.
    Valida rigorosamente o alvo de entrada e aplica -protocol_whitelist.
    """
    try:
        media_utils.check_ffmpeg()
    except Exception as exc:
        logger.warning("FFmpeg não disponível para extração de frame: %s", exc)
        return None

    try:
        src_str = str(source).strip()

        if output_path:
            dest = Path(output_path)
        else:
            clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", Path(src_str).stem)[:25]
            dest = media_utils.TEMP_DIR / f"frame_{clean_id}_{int(timestamp_sec * 1000)}.jpg"

        dest.parent.mkdir(parents=True, exist_ok=True)

        is_remote = src_str.startswith(("http://", "https://"))
        input_target = src_str

        if direct_stream_url:
            input_target = direct_stream_url
            logger.debug("Usando stream URL pré-resolvido para extração de frame: %s...", input_target[:60])
        elif is_remote:
            try:
                input_target = get_direct_stream_url(src_str)
                logger.info("Stream URL direto obtido para extração de frame: %s...", input_target[:60])
            except Exception as exc:
                logger.warning("Falha ao obter stream URL direto com yt-dlp: %s. Tentando URL original.", exc)
                input_target = src_str
        else:
            local_p = Path(src_str).resolve()
            if not media_utils.is_path_within(local_p, media_utils.DATA_DIR_RESOLVED) or not local_p.exists() or not local_p.is_file():
                logger.warning("Arquivo fonte inválido ou fora de DATA_DIR para extração de frame: %s", src_str)
                return None
            input_target = str(local_p)

        ua_header = "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36\r\n"

        def _run_ffmpeg_extract(target: str) -> bool:
            try:
                safe_target = media_utils.validate_ffmpeg_input_target(target, require_within_data_dir=True)
            except Exception as val_exc:
                logger.warning("Alvo bloqueado na extração de frame: %s", val_exc)
                return False

            cmd = [
                "ffmpeg", "-nostdin", "-y",
                "-protocol_whitelist", media_utils.FFMPEG_ALLOWED_PROTOCOLS,
            ]
            if safe_target.startswith(("http://", "https://")):
                cmd.extend(["-headers", ua_header])
            cmd.extend([
                "-ss", str(max(0.0, float(timestamp_sec))),
                "-i", safe_target,
                "-vframes", "1",
                "-q:v", "2",
                str(dest),
            ])
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)  # nosec B603
                return proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0
            except Exception as exc_cmd:
                logger.debug("Erro na execução do FFmpeg para frame: %s", exc_cmd)
                return False

        success = _run_ffmpeg_extract(input_target)

        if not success and is_remote:
            try:
                fresh_url = get_direct_stream_url(src_str)
                if fresh_url and fresh_url != input_target:
                    logger.info("Tentando extração de frame com stream URL atualizado...")
                    success = _run_ffmpeg_extract(fresh_url)
            except Exception as exc_fresh:
                logger.warning("Falha ao resolver stream URL atualizado: %s", exc_fresh)

        if not success or not dest.exists() or dest.stat().st_size == 0:
            logger.warning("Arquivo de frame não foi gerado ou está vazio: %s", dest)
            return None

        logger.info("Frame de preview extraído com sucesso: %s", dest)
        return str(dest.resolve())

    except Exception as e:
        logger.warning("Erro inesperado na extração de frame de preview: %s", e)
        return None


def extract_preview_clip(
    source: str,
    start_time: float,
    end_time: float,
    output_path: Optional[str] = None,
    direct_stream_url: Optional[str] = None,
    local_audio_path: Optional[str] = None,
    max_duration: float = 120.0,
) -> Optional[str]:
    """
    Extrai um clipe de vídeo leve (.mp4 720p ultrafast com áudio) do intervalo [start_time, end_time]
    para reprodução ao vivo dentro do Canvas 60fps do Estúdio antes da renderização final.
    """
    try:
        media_utils.check_ffmpeg()
    except Exception as exc:
        logger.warning("FFmpeg não disponível para extração de clipe de prévia: %s", exc)
        return None

    try:
        src_str = str(source).strip()
        s_time = max(0.0, float(start_time))
        e_time = max(s_time + 1.0, float(end_time))
        duration = min(max_duration, e_time - s_time)

        if output_path:
            dest = Path(output_path)
        else:
            clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", Path(src_str).stem)[:25]
            dest = media_utils.TEMP_DIR / f"preview_clip_{clean_id}_{int(s_time * 10)}_{int(e_time * 10)}.mp4"

        dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.exists() and dest.stat().st_size > 2048:
            logger.info("Clipe de prévia reutilizado do cache: %s", dest.name)
            return str(dest.resolve())

        is_remote = src_str.startswith(("http://", "https://"))
        input_target = src_str

        if direct_stream_url:
            input_target = direct_stream_url
        elif is_remote:
            try:
                input_target = get_direct_stream_url(src_str, max_resolution=720)
            except Exception as exc:
                logger.warning("Falha ao obter stream URL para clipe de prévia: %s", exc)
                input_target = src_str
        else:
            local_p = Path(src_str).resolve()
            if not media_utils.is_path_within(local_p, media_utils.DATA_DIR_RESOLVED) or not local_p.exists() or not local_p.is_file():
                logger.warning("Arquivo fonte inválido ou fora de DATA_DIR para clipe de prévia: %s", src_str)
                return None
            input_target = str(local_p)

        safe_local_audio: Optional[str] = None
        if local_audio_path:
            audio_p = Path(local_audio_path).resolve()
            if media_utils.is_path_within(audio_p, media_utils.DATA_DIR_RESOLVED) and audio_p.exists() and audio_p.is_file():
                safe_local_audio = str(audio_p)

        ua_header = "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36\r\n"

        def _run_ffmpeg_clip(target: str) -> bool:
            try:
                safe_target = media_utils.validate_ffmpeg_input_target(target, require_within_data_dir=True)
            except Exception as val_exc:
                logger.warning("Alvo bloqueado na extração de clipe de prévia: %s", val_exc)
                return False

            cmd = [
                "ffmpeg", "-nostdin", "-y",
                "-protocol_whitelist", media_utils.FFMPEG_ALLOWED_PROTOCOLS,
            ]
            if safe_target.startswith(("http://", "https://")):
                cmd.extend(["-headers", ua_header])
            cmd.extend([
                "-ss", str(round(s_time, 2)),
                "-t", str(round(duration, 2)),
                "-i", safe_target,
            ])

            if safe_local_audio:
                cmd.extend([
                    "-ss", str(round(s_time, 2)),
                    "-t", str(round(duration, 2)),
                    "-i", safe_local_audio,
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                ])
            else:
                cmd.extend([
                    "-map", "0:v:0",
                    "-map", "0:a:0?",
                ])

            cmd.extend([
                "-vf", "scale=-2:720",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "27",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "128k",
                "-shortest",
                "-movflags", "+faststart",
                str(dest),
            ])
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)  # nosec B603
                return proc.returncode == 0 and dest.exists() and dest.stat().st_size > 2048
            except Exception as exc_cmd:
                logger.debug("Erro na execução do FFmpeg para clipe de prévia: %s", exc_cmd)
                return False

        success = _run_ffmpeg_clip(input_target)

        if not success and is_remote:
            try:
                fresh_url = get_direct_stream_url(src_str, max_resolution=720)
                if fresh_url and fresh_url != input_target:
                    logger.info("Retentando extração de clipe de prévia com stream URL atualizado...")
                    success = _run_ffmpeg_clip(fresh_url)
            except Exception as exc_fresh:
                logger.warning("Falha ao atualizar stream URL para clipe de prévia: %s", exc_fresh)

        if not success or not dest.exists() or dest.stat().st_size <= 2048:
            logger.warning("Não foi possível gerar o clipe de prévia em: %s", dest)
            return None

        logger.info("Clipe de prévia gerado com sucesso: %s", dest.name)
        return str(dest.resolve())

    except Exception as exc:
        logger.warning("Erro inesperado ao gerar clipe de prévia: %s", exc)
        return None


def draw_framing_preview(
    frame_path: str,
    mode: str = "manual_crop",
    params: Optional[Dict[str, Any]] = None,
    output_path: Optional[str] = None,
) -> str:
    """
    Gera uma imagem de prévia visual de enquadramento confinada estritamente a DATA_DIR.
    """
    p = Path(frame_path).resolve()
    if not media_utils.is_path_within(p, media_utils.DATA_DIR_RESOLVED):
        raise PermissionError("Acesso negado: frame_path fora do diretório de dados permitido.")
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"Frame não encontrado para preview: {p.name}")

    img = cv2.imread(str(p))
    if img is None:
        raise ValueError(f"Não foi possível decodificar o arquivo de imagem: {p.name}")

    H, W = img.shape[:2]
    cfg = params or {}

    mode_clean = str(mode).lower().strip()
    safe_mode = re.sub(r"[^a-zA-Z0-9_-]", "", mode_clean)[:32] or "preview"
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]", "_", p.stem)[:40] or "frame"

    if output_path:
        dest = Path(output_path).resolve()
        if not media_utils.is_path_within(dest, media_utils.DATA_DIR_RESOLVED):
            raise PermissionError("Acesso negado: output_path fora do diretório de dados permitido.")
    else:
        dest = (media_utils.TEMP_DIR / f"framing_{safe_stem}_{safe_mode}.jpg").resolve()
        if not media_utils.is_path_within(dest, media_utils.TEMP_DIR):
            raise PermissionError("Caminho de destino inválido para preview.")
    dest.parent.mkdir(parents=True, exist_ok=True)

    if mode_clean in ("manual_crop", "crop_center", "crop", "face_tracking_9_16", "face_tracking"):
        crop_w = int(cfg.get("crop_w", cfg.get("w", int(H * (9.0 / 16.0)))))
        crop_h = int(cfg.get("crop_h", cfg.get("h", H)))
        crop_x = int(cfg.get("crop_x", cfg.get("x", (W - crop_w) // 2)))
        crop_y = int(cfg.get("crop_y", cfg.get("y", 0)))

        crop_x = max(0, min(W - 2, crop_x))
        crop_y = max(0, min(H - 2, crop_y))
        crop_w = max(2, min(W - crop_x, crop_w))
        crop_h = max(2, min(H - crop_y, crop_h))

        overlay = (img.copy() * 0.45).astype(np.uint8)
        overlay[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w] = img[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w]
        border_color = (0, 255, 128)
        cv2.rectangle(overlay, (crop_x, crop_y), (crop_x + crop_w, crop_y + crop_h), border_color, 4)
        cv2.putText(
            overlay,
            f"Corte 9:16 ({crop_w}x{crop_h})",
            (crop_x + 10, max(30, crop_y + 35)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            border_color,
            2,
            cv2.LINE_AA,
        )
        result_img = overlay

    elif mode_clean in ("split_screen", "split"):
        box_top = cfg.get("box_top") or {}
        box_bottom = cfg.get("box_bottom") or {}
        target_ar = 1080.0 / 960.0

        tw = int(box_top.get("w", min(W // 2, int(H * target_ar))))
        th = int(box_top.get("h", int(tw / target_ar)))
        tx = int(box_top.get("x", max(0, (W // 4) - (tw // 2))))
        ty = int(box_top.get("y", max(0, (H - th) // 2)))

        bw = int(box_bottom.get("w", min(W // 2, int(H * target_ar))))
        bh = int(box_bottom.get("h", int(bw / target_ar)))
        bx = int(box_bottom.get("x", min(W - bw, (3 * W // 4) - (bw // 2))))
        by = int(box_bottom.get("y", max(0, (H - bh) // 2)))

        tx, ty = max(0, min(W - 2, tx)), max(0, min(H - 2, ty))
        tw, th = max(2, min(W - tx, tw)), max(2, min(H - ty, th))
        bx, by = max(0, min(W - 2, bx)), max(0, min(H - 2, by))
        bw, bh = max(2, min(W - bx, bw)), max(2, min(H - by, bh))

        overlay = (img.copy() * 0.45).astype(np.uint8)
        overlay[ty:ty + th, tx:tx + tw] = img[ty:ty + th, tx:tx + tw]
        overlay[by:by + bh, bx:bx + bw] = img[by:by + bh, bx:bx + bw]

        color_top = (255, 230, 0)
        cv2.rectangle(overlay, (tx, ty), (tx + tw, ty + th), color_top, 4)
        cv2.putText(overlay, "TOPO (1080x960)", (tx + 10, max(30, ty + 35)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color_top, 2, cv2.LINE_AA)

        color_bot = (0, 165, 255)
        cv2.rectangle(overlay, (bx, by), (bx + bw, by + bh), color_bot, 4)
        cv2.putText(overlay, "BASE (1080x960)", (bx + 10, max(30, by + 35)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color_bot, 2, cv2.LINE_AA)
        result_img = overlay

    elif mode_clean in ("vertical_blur", "blur_background", "blur"):
        canvas_w, canvas_h = 1080, 1920
        bg_scale = max(canvas_w / W, canvas_h / H)
        bg_w = int(round(W * bg_scale))
        bg_h = int(round(H * bg_scale))
        bg_resized = cv2.resize(img, (bg_w, bg_h))
        bg_crop_x = (bg_w - canvas_w) // 2
        bg_crop_y = (bg_h - canvas_h) // 2
        bg = bg_resized[bg_crop_y:bg_crop_y + canvas_h, bg_crop_x:bg_crop_x + canvas_w]

        bg_blurred = cv2.GaussianBlur(bg, (51, 51), 0)
        bg_darkened = np.clip(bg_blurred.astype(np.float32) * 0.82 - 15.0, 0, 255).astype(np.uint8)

        zoom_factor = max(1.0, min(1.3, float(cfg.get("zoom_factor", 1.0))))
        fg_scale = (canvas_w / W) * zoom_factor
        fg_w = int(round(W * fg_scale))
        fg_h = int(round(H * fg_scale))
        fg_resized = cv2.resize(img, (fg_w, fg_h))

        if fg_w > canvas_w:
            x_off = (fg_w - canvas_w) // 2
            fg_resized = fg_resized[:, x_off:x_off + canvas_w]
            fg_w = canvas_w

        pos_y = (canvas_h - fg_h) // 2
        pos_x = (canvas_w - fg_w) // 2
        if pos_y < 0:
            y_crop = abs(pos_y)
            fg_resized = fg_resized[y_crop:y_crop + canvas_h, :]
            pos_y = 0

        bg_darkened[pos_y:pos_y + fg_resized.shape[0], pos_x:pos_x + fg_resized.shape[1]] = fg_resized
        result_img = bg_darkened
    else:
        result_img = img

    cv2.imwrite(str(dest.resolve()), result_img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    logger.info("Prévia de enquadramento '%s' gerada em: %s", mode, dest)
    return str(dest.resolve())


def cut_video(
    input_path: str,
    start_time: float,
    end_time: float,
    output_path: Optional[str] = None,
    mode: str = "vertical_blur",
    target_width: int = 1080,
    target_height: int = 1920,
    segments: Optional[List[Dict[str, Any]]] = None,
    burn_subtitles: bool = False,
    face_tracking: bool = False,
    layout_mode: Optional[str] = None,
    layout_params: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Recorta um trecho do vídeo e renderiza nos modos suportados (horizontal_16_9,
    vertical_blur, manual_crop, split_screen e face_tracking_9_16) via builders de layout.
    """
    media_utils.check_ffmpeg()
    src = Path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"Arquivo de origem não encontrado: {input_path}")

    duration = end_time - start_time
    if duration <= 0:
        raise ValueError(f"Intervalo de corte inválido: {start_time}s -> {end_time}s")

    dest = Path(output_path) if output_path else (media_utils.CUTS_DIR / f"{src.stem}_cut_{int(start_time)}_{int(end_time)}.mp4")
    dest.parent.mkdir(parents=True, exist_ok=True)

    meta = get_video_metadata(str(src))
    orig_w, orig_h = int(meta["width"]), int(meta["height"])
    effective_mode, face_tracking = normalize_effective_mode(mode, layout_mode, face_tracking)

    target_width -= target_width % 2
    target_height -= target_height % 2

    srt_file_path = generate_srt_for_segment(segments, start_time, end_time) if (burn_subtitles and segments) else None
    sub_filter = (
        format_ffmpeg_subtitles_filter(srt_file_path, is_vertical=(effective_mode != "horizontal_16_9"))
        if srt_file_path
        else None
    )

    cmd = [
        "ffmpeg", "-nostdin", "-y",
        "-ss", str(round(start_time, 2)),
        "-i", str(src),
        "-t", str(round(duration, 2)),
    ]

    layout_args, is_stream_copy = build_layout_ffmpeg_args(
        effective_mode=effective_mode,
        src_path=str(src),
        start_time=start_time,
        end_time=end_time,
        orig_w=orig_w,
        orig_h=orig_h,
        target_width=target_width,
        target_height=target_height,
        cfg=layout_params or {},
        face_tracking=face_tracking,
        sub_filter=sub_filter,
    )
    cmd.extend(layout_args)

    if is_stream_copy:
        cmd.append(str(dest))
        logger.info("Executando corte horizontal 16:9 via stream copy (sem reencode): %s", dest.name)
    else:
        cmd.extend([
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(dest),
        ])
        logger.info(
            "Executando corte FFmpeg (%s -> %ss, layout: %s, legendas: %s): %s",
            start_time, end_time, effective_mode, bool(srt_file_path), dest.name,
        )

    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)  # nosec B603
    if proc.returncode != 0:
        logger.error("Falha no FFmpeg: %s", proc.stderr)
        raise RuntimeError(f"Erro ao processar corte de vídeo: {proc.stderr[:350]}")

    logger.info("Corte finalizado com sucesso: %s", dest)
    return str(dest.resolve())


def extract_frame_thumbnail(video_path: str, timestamp: float = 1.0, output_path: Optional[str] = None) -> Optional[str]:
    """Extrai um frame estático de preview do vídeo como imagem JPEG (retrocompatibilidade)."""
    return extract_preview_frame(source=video_path, timestamp_sec=timestamp, output_path=output_path)


def cleanup_temporary_files(
    temp_dir: Optional[Path] = None,
    downloads_dir: Optional[Path] = None,
    remove_raw_downloads: bool = False,
    max_age_hours: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Remove arquivos temporários (.srt, .mp3, prévias) em data/temp/ e opcionalmente downloads brutos,
    preservando rigorosamente data/cortes/ e arquivos .gitkeep.
    Quando `max_age_hours` é informado (> 0), remove apenas arquivos mais antigos que o limite (GC seguro).
    """
    target_temp = temp_dir or media_utils.TEMP_DIR
    target_dl = downloads_dir or media_utils.DOWNLOADS_DIR

    cutoff_ts: Optional[float] = None
    if max_age_hours is not None and max_age_hours > 0:
        cutoff_ts = time.time() - (float(max_age_hours) * 3600.0)

    deleted_count = 0
    freed_bytes = 0

    def _sweep_dir(directory: Path, label: str) -> None:
        nonlocal deleted_count, freed_bytes
        if not directory.exists():
            return
        for f in directory.iterdir():
            if not f.is_file() or f.name == ".gitkeep":
                continue
            try:
                st = f.stat()
                if cutoff_ts is not None and st.st_mtime > cutoff_ts:
                    continue
                size = st.st_size
                f.unlink()
                deleted_count += 1
                freed_bytes += size
            except Exception as exc:
                logger.warning("Falha ao remover %s '%s': %s", label, f.name, exc)

    _sweep_dir(target_temp, "arquivo temporário")
    if remove_raw_downloads:
        _sweep_dir(target_dl, "download bruto")

    freed_mb = round(freed_bytes / (1024 * 1024), 2)
    logger.info(
        "Limpeza de temporários (max_age_hours=%s): %d arquivos removidos, %.2f MB liberados.",
        max_age_hours, deleted_count, freed_mb,
    )
    return {
        "deleted_count": deleted_count,
        "freed_mb": freed_mb,
    }
