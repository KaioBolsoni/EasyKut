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
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from core import media_utils
from core.audio_mastering import build_audio_mastering_filters, resolve_confined_music_path
from core.brand_overlay import build_brand_overlay_filters, resolve_confined_logo_path
from core.downloader import get_direct_stream_url
from core.hw_accel import build_video_encoder_args
from core.jump_cuts import (
    build_jump_cut_filter_graph,
    compute_speech_intervals_from_transcript,
    detect_speech_intervals_ffmpeg,
    remap_timestamps_for_intervals,
)
from core.layout_filters import build_layout_ffmpeg_args, normalize_effective_mode
from core.subtitles import (
    format_ffmpeg_ass_filter,
    format_ffmpeg_subtitles_filter,
    generate_ass_karaoke_for_segment,
    generate_srt_for_segment,
)

logger = logging.getLogger(__name__)


class JobCancelledError(RuntimeError):
    """Exceção dedicada disparada quando um job FFmpeg é cancelado pelo usuário."""


def run_cancellable_ffmpeg(
    cmd: List[str],
    *,
    timeout: int = 300,
    job_id: Optional[str] = None,
    cancel_check: Optional[Any] = None,
    register_proc: Optional[Any] = None,
    unregister_proc: Optional[Any] = None,
) -> Tuple[int, str]:
    """
    Executa FFmpeg via subprocess.Popen com suporte a cancelamento cooperativo em tempo real,
    sem deadlock de buffer de pipe no Windows (redireciona stderr para arquivo temporário seguro).
    """
    if cancel_check and cancel_check():
        raise JobCancelledError(f"Job '{job_id}' cancelado antes de iniciar FFmpeg.")

    with tempfile.TemporaryFile(mode="w+b") as stderr_file:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
        )  # nosec B603
        if register_proc:
            register_proc(proc)

        deadline = time.monotonic() + float(timeout)
        try:
            while proc.poll() is None:
                if cancel_check and cancel_check():
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    raise JobCancelledError(f"Job '{job_id}' cancelado pelo usuário.")
                if time.monotonic() > deadline:
                    proc.kill()
                    raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)
                time.sleep(0.15)
            proc.wait(timeout=5)
            stderr_file.seek(0)
            stderr_text = stderr_file.read().decode("utf-8", errors="replace")
            return int(proc.returncode or 0), stderr_text
        finally:
            if unregister_proc:
                unregister_proc(proc)


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


def _build_subtitle_filter_for_cut(
    *,
    burn_subtitles: bool,
    segments: Optional[List[Dict[str, Any]]],
    words: Optional[List[Dict[str, Any]]],
    start_time: float,
    end_time: float,
    effective_mode: str,
    target_width: int,
    target_height: int,
    karaoke_subtitles: bool,
    subtitle_preset: str,
    prefer_segments: bool,
) -> Optional[str]:
    if not burn_subtitles or (not segments and not words):
        return None

    is_vertical = effective_mode not in ("horizontal_16_9",)
    if karaoke_subtitles:
        ass_path = generate_ass_karaoke_for_segment(
            segments=segments,
            words=words,
            start_time=start_time,
            end_time=end_time,
            preset=subtitle_preset,
            is_vertical=is_vertical,
            play_res_x=target_width,
            play_res_y=target_height,
            prefer_segments=prefer_segments,
        )
        if ass_path:
            return format_ffmpeg_ass_filter(ass_path)

    srt_path = generate_srt_for_segment(segments or [], start_time, end_time)
    return format_ffmpeg_subtitles_filter(srt_path, is_vertical=is_vertical) if srt_path else None


def _compose_pro_filter_complex(
    *,
    layout_args: List[str],
    intervals: List[Tuple[float, float]],
    brand_kit: Optional[Dict[str, Any]],
    hook_title: str,
    effective_duration: float,
    target_width: int,
    target_height: int,
    sub_filter: Optional[str],
    punch_in_zoom: bool,
    logo_input_index: Optional[int],
    music_input_index: Optional[int],
    normalize_audio: bool,
    denoise_audio: bool,
    music_volume: float,
) -> List[str]:
    """Compõe um único -filter_complex integrando Jump Cuts, Layout, Punch-in Zoom, Legendas, Brand Kit e Áudio Pro."""
    parts: List[str] = []
    v_in, a_in = "[0:v]", "[0:a]"

    if intervals:
        jc_graph, v_in, a_in = build_jump_cut_filter_graph(intervals, video_in=v_in, audio_in=a_in)
        if jc_graph:
            parts.append(jc_graph)

    if layout_args and layout_args[0] == "-filter_complex":
        raw_fc = layout_args[1]
        if v_in != "[0:v]":
            raw_fc = raw_fc.replace("[0:v]", v_in)
        parts.append(raw_fc)
        v_cur = layout_args[3] if len(layout_args) > 3 else "[merged]"
    elif layout_args and layout_args[0] == "-vf":
        parts.append(f"{v_in}{layout_args[1]}[v_lay]")
        v_cur = "[v_lay]"
    else:
        parts.append(f"{v_in}scale=trunc(iw/2)*2:trunc(ih/2)*2[v_lay]")
        v_cur = "[v_lay]"

    if punch_in_zoom:
        # Efeito de retenção suave: alterna micro-zoom 1.00x -> 1.07x a cada 6 segundos mantendo dimensões finais
        parts.append(
            f"{v_cur}scale='iw*(1+0.07*gt(mod(t,12),6))':'ih*(1+0.07*gt(mod(t,12),6))':eval=frame,"
            f"crop={target_width}:{target_height}[v_punch]"
        )
        v_cur = "[v_punch]"

    if sub_filter:
        parts.append(f"{v_cur}{sub_filter}[v_sub]")
        v_cur = "[v_sub]"

    if brand_kit:
        b_filters, v_cur, _ = build_brand_overlay_filters(
            brand_kit=brand_kit,
            duration=effective_duration,
            target_width=target_width,
            target_height=target_height,
            hook_title=hook_title,
            input_video_label=v_cur,
            logo_input_index=logo_input_index,
        )
        parts.extend(b_filters)

    a_filters, a_cur = build_audio_mastering_filters(
        normalize_audio=normalize_audio,
        denoise_audio=denoise_audio,
        music_input_index=music_input_index,
        music_volume=music_volume,
        input_audio_label=a_in,
    )
    parts.extend(a_filters)
    a_map = a_cur if a_cur != "[0:a]" else "0:a?"
    return ["-filter_complex", ";".join(parts), "-map", v_cur, "-map", a_map]


def cut_video(
    input_path: str,
    start_time: float,
    end_time: float,
    output_path: Optional[str] = None,
    mode: str = "vertical_blur",
    target_width: int = 1080,
    target_height: int = 1920,
    segments: Optional[List[Dict[str, Any]]] = None,
    words: Optional[List[Dict[str, Any]]] = None,
    burn_subtitles: bool = False,
    subtitle_preset: str = "hormozi",
    karaoke_subtitles: bool = True,
    prefer_segment_subtitles: bool = False,
    face_tracking: bool = False,
    dynamic_face_tracking: bool = True,
    layout_mode: Optional[str] = None,
    layout_params: Optional[Dict[str, Any]] = None,
    remove_silences: bool = False,
    min_silence_sec: float = 0.6,
    brand_kit: Optional[Dict[str, Any]] = None,
    hook_title: str = "",
    punch_in_zoom: bool = False,
    use_gpu: bool = False,
    normalize_audio: bool = False,
    denoise_audio: bool = False,
    music_path: Optional[str] = None,
    music_volume: float = 0.18,
    job_id: Optional[str] = None,
    cancel_check: Optional[Any] = None,
    register_proc: Optional[Any] = None,
    unregister_proc: Optional[Any] = None,
) -> str:
    """
    Recorta um trecho do vídeo e renderiza com suporte completo a:
    - 5 layouts (horizontal_16_9, vertical_blur, manual_crop, face_tracking_9_16 dinâmico, split_screen, square_1_1)
    - Legendas Karaoke .ass (5 presets) ou .srt sincronizadas (inclusive pós-Jump Cut)
    - Remoção automática de silêncios (Jump Cuts), Brand Kit (logo/barra/hook), Punch-in Zoom
    - GPU (NVENC/QSV/AMF com fallback libx264), Masterização de áudio (-14 LUFS + Auto-Ducking) e cancelamento.
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

    if effective_mode == "square_1_1":
        target_height = target_width
    elif effective_mode == "horizontal_16_9":
        target_width, target_height = orig_w, orig_h

    target_width = max(2, target_width - (target_width % 2))
    target_height = max(2, target_height - (target_height % 2))

    # Jump Cuts (Onda 2): calcula intervalos de fala e remapeia timestamps de legendas
    intervals: List[Tuple[float, float]] = []
    sub_segments = segments
    sub_words = words
    sub_start, sub_end = start_time, end_time
    effective_duration = duration

    if remove_silences:
        intervals = compute_speech_intervals_from_transcript(
            segments=segments,
            words=words,
            start_time=start_time,
            end_time=end_time,
            min_silence_sec=min_silence_sec,
        )
        if not intervals:
            intervals = detect_speech_intervals_ffmpeg(
                video_path=str(src),
                start_time=start_time,
                end_time=end_time,
                min_silence_sec=min_silence_sec,
            )
        if intervals:
            effective_duration = sum(e - s for s, e in intervals)
            sub_segments = remap_timestamps_for_intervals(segments or [], intervals, base_start=start_time)
            sub_words = remap_timestamps_for_intervals(words or [], intervals, base_start=start_time)
            sub_start, sub_end = 0.0, effective_duration

    effective_preset = subtitle_preset or (brand_kit or {}).get("subtitle_preset") or "hormozi"
    sub_filter = _build_subtitle_filter_for_cut(
        burn_subtitles=burn_subtitles,
        segments=sub_segments,
        words=sub_words,
        start_time=sub_start,
        end_time=sub_end,
        effective_mode=effective_mode,
        target_width=target_width,
        target_height=target_height,
        karaoke_subtitles=karaoke_subtitles,
        subtitle_preset=effective_preset,
        prefer_segments=prefer_segment_subtitles,
    )

    valid_logo = resolve_confined_logo_path((brand_kit or {}).get("logo_path")) if brand_kit else None
    valid_music = resolve_confined_music_path(music_path)
    has_brand_fx = bool(brand_kit and (valid_logo or brand_kit.get("progress_bar_enabled") or brand_kit.get("hook_banner_enabled")))
    has_pro_pipeline = bool(intervals or has_brand_fx or punch_in_zoom or valid_music or normalize_audio or denoise_audio)

    cmd_prefix = [
        "ffmpeg", "-nostdin", "-y",
        "-ss", str(round(start_time, 2)),
        "-i", str(src),
        "-t", str(round(duration, 2)),
    ]

    next_input_idx = 1
    logo_input_idx: Optional[int] = None
    music_input_idx: Optional[int] = None
    if has_pro_pipeline and valid_logo:
        cmd_prefix.extend(["-i", valid_logo])
        logo_input_idx = next_input_idx
        next_input_idx += 1
    if has_pro_pipeline and valid_music:
        cmd_prefix.extend(["-stream_loop", "-1", "-i", valid_music])
        music_input_idx = next_input_idx
        next_input_idx += 1

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
        sub_filter=None if has_pro_pipeline else sub_filter,
        dynamic_face_tracking=dynamic_face_tracking,
    )

    if has_pro_pipeline:
        is_stream_copy = False
        filter_args = _compose_pro_filter_complex(
            layout_args=layout_args,
            intervals=intervals,
            brand_kit=brand_kit if has_brand_fx else None,
            hook_title=hook_title,
            effective_duration=effective_duration,
            target_width=target_width,
            target_height=target_height,
            sub_filter=sub_filter,
            punch_in_zoom=punch_in_zoom,
            logo_input_index=logo_input_idx,
            music_input_index=music_input_idx,
            normalize_audio=normalize_audio,
            denoise_audio=denoise_audio,
            music_volume=music_volume,
        )
    else:
        filter_args = layout_args

    def _run_with_encoder(try_gpu: bool) -> Tuple[int, str, str]:
        cmd = list(cmd_prefix) + list(filter_args)
        if is_stream_copy:
            cmd.append(str(dest))
            enc_name = "copy"
        else:
            v_args, enc_name = build_video_encoder_args(use_gpu=try_gpu)
            cmd.extend(v_args)
            cmd.extend(["-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest)])

        logger.info(
            "Executando corte FFmpeg (%s -> %ss, layout=%s, enc=%s, jump_cuts=%s, brand=%s): %s",
            start_time, end_time, effective_mode, enc_name, bool(intervals), has_brand_fx, dest.name,
        )
        rc, stderr = run_cancellable_ffmpeg(
            cmd,
            timeout=300,
            job_id=job_id,
            cancel_check=cancel_check,
            register_proc=register_proc,
            unregister_proc=unregister_proc,
        )
        return rc, stderr, enc_name

    rc, stderr, used_enc = _run_with_encoder(try_gpu=use_gpu)
    if rc != 0 and use_gpu and used_enc != "libx264":
        logger.warning("Encoder GPU '%s' falhou; acionando fallback automático para libx264 CPU.", used_enc)
        rc, stderr, _ = _run_with_encoder(try_gpu=False)

    if rc != 0:
        logger.error("Falha no FFmpeg: %s", stderr)
        raise RuntimeError(f"Erro ao processar corte de vídeo: {stderr[:350]}")

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
