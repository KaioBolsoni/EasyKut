"""
Módulo de Remoção Automática de Silêncios (Jump Cuts Inteligentes — Onda 2).
Identifica pausas longas via timestamps do Whisper ou FFmpeg silencedetect, gera
o grafo de concatenação sem silêncios e remapeia timestamps de legendas com precisão.
"""

import logging
import re
import subprocess  # nosec B404
from typing import Any, Dict, List, Optional, Tuple

from core import media_utils

logger = logging.getLogger(__name__)


def compute_speech_intervals_from_transcript(
    segments: Optional[List[Dict[str, Any]]],
    words: Optional[List[Dict[str, Any]]],
    start_time: float,
    end_time: float,
    min_silence_sec: float = 0.6,
    pad_sec: float = 0.08,
) -> List[Tuple[float, float]]:
    """
    Calcula os intervalos de fala relativos [0, duration] a manter, removendo pausas >= min_silence_sec.
    Prioriza timestamps palavra-a-palavra quando disponíveis; caso contrário usa segmentos.
    """
    clip_dur = max(0.0, float(end_time) - float(start_time))
    if clip_dur <= 0.5:
        return []

    source_items = words if words else segments
    if not source_items:
        return []

    raw_spans: List[Tuple[float, float]] = []
    for item in source_items:
        s = float(item.get("start", 0.0))
        e = float(item.get("end", s))
        if e <= start_time or s >= end_time:
            continue
        rs = max(0.0, s - start_time - pad_sec)
        re_t = min(clip_dur, e - start_time + pad_sec)
        if re_t > rs:
            raw_spans.append((rs, re_t))

    if not raw_spans:
        return []

    raw_spans.sort(key=lambda pair: pair[0])
    merged: List[Tuple[float, float]] = [raw_spans[0]]
    gap_thresh = max(0.2, float(min_silence_sec))

    for cur_s, cur_e in raw_spans[1:]:
        prev_s, prev_e = merged[-1]
        if (cur_s - prev_e) < gap_thresh:
            merged[-1] = (prev_s, max(prev_e, cur_e))
        else:
            merged.append((cur_s, cur_e))

    # Só aplica Jump Cut se realmente houver economia perceptível (>= 0.4s) e ao menos 1 intervalo válido
    kept_dur = sum(e - s for s, e in merged)
    if (clip_dur - kept_dur) < 0.4 or kept_dur < 1.0:
        return []
    return [(round(s, 3), round(e, 3)) for s, e in merged if (e - s) >= 0.12]


def detect_speech_intervals_ffmpeg(
    video_path: str,
    start_time: float,
    end_time: float,
    min_silence_sec: float = 0.6,
    noise_db: float = -32.0,
) -> List[Tuple[float, float]]:
    """Fallback acústico via FFmpeg silencedetect quando não houver timestamps de transcrição."""
    clip_dur = max(0.0, float(end_time) - float(start_time))
    if clip_dur <= 1.0:
        return []
    try:
        safe_src = media_utils.validate_ffmpeg_input_target(video_path, require_within_data_dir=True)
        cmd = [
            "ffmpeg", "-nostdin", "-hide_banner",
            "-ss", str(round(start_time, 2)),
            "-t", str(round(clip_dur, 2)),
            "-i", safe_src,
            "-af", f"silencedetect=noise={noise_db}dB:d={max(0.25, float(min_silence_sec)):.2f}",
            "-f", "null", "-",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)  # nosec B603
        stderr = proc.stderr or ""
    except Exception as exc:
        logger.warning("FFmpeg silencedetect falhou: %s", exc)
        return []

    starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", stderr)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", stderr)]
    if not starts:
        return []

    silences: List[Tuple[float, float]] = []
    for idx, s_val in enumerate(starts):
        e_val = ends[idx] if idx < len(ends) else clip_dur
        if e_val > s_val:
            silences.append((max(0.0, s_val), min(clip_dur, e_val)))

    keep: List[Tuple[float, float]] = []
    cursor = 0.0
    for sil_s, sil_e in silences:
        if sil_s - cursor >= 0.15:
            keep.append((round(cursor, 3), round(sil_s, 3)))
        cursor = max(cursor, sil_e)
    if clip_dur - cursor >= 0.15:
        keep.append((round(cursor, 3), round(clip_dur, 3)))

    kept_dur = sum(e - s for s, e in keep)
    if not keep or (clip_dur - kept_dur) < 0.4 or kept_dur < 1.0:
        return []
    return keep


def remap_timestamps_for_intervals(
    items: List[Dict[str, Any]],
    intervals: List[Tuple[float, float]],
    base_start: float = 0.0,
) -> List[Dict[str, Any]]:
    """
    Remapeia timestamps absolutos de segmentos ou palavras para a linha do tempo
    compactada pós-Jump Cut, preservando sincronismo exato das legendas.
    """
    if not items or not intervals:
        return items

    remapped: List[Dict[str, Any]] = []
    for item in items:
        abs_s = float(item.get("start", 0.0)) - base_start
        abs_e = float(item.get("end", abs_s)) - base_start
        if abs_e <= 0.0:
            continue

        offset = 0.0
        for iv_s, iv_e in intervals:
            ov_s = max(abs_s, iv_s)
            ov_e = min(abs_e, iv_e)
            if ov_e > ov_s:
                new_item = dict(item)
                new_item["start"] = round(offset + (ov_s - iv_s), 3)
                new_item["end"] = round(offset + (ov_e - iv_s), 3)
                remapped.append(new_item)
                break
            offset += (iv_e - iv_s)

    return remapped


def build_jump_cut_filter_graph(
    intervals: List[Tuple[float, float]],
    video_in: str = "[0:v]",
    audio_in: str = "[0:a]",
) -> Tuple[str, str, str]:
    """
    Gera filtro FFmpeg trim/atrim + concat para cortar silêncios de vídeo e áudio sincronizados.
    Retorna `(filter_fragment, out_v_label, out_a_label)`.
    """
    if not intervals:
        return "", video_in, audio_in

    v_parts: List[str] = []
    a_parts: List[str] = []
    concat_inputs: List[str] = []

    for idx, (s, e) in enumerate(intervals):
        v_parts.append(f"{video_in}trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[jcv{idx}]")
        a_parts.append(f"{audio_in}atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[jca{idx}]")
        concat_inputs.append(f"[jcv{idx}][jca{idx}]")

    n = len(intervals)
    concat_expr = f"{''.join(concat_inputs)}concat=n={n}:v=1:a=1[jcv_out][jca_out]"
    full = ";".join(v_parts + a_parts + [concat_expr])
    return full, "[jcv_out]", "[jca_out]"
