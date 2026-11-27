"""
Módulo de Aceleração por Hardware (GPU) no FFmpeg (Onda 3).
Detecta encoders disponíveis (h264_nvenc, h264_qsv, h264_amf) com cache em memória
e fornece fallback automático seguro para libx264 (CPU).
"""

import logging
import subprocess  # nosec B404
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

GPU_ENCODER_PRIORITY = ("h264_nvenc", "h264_qsv", "h264_amf")
_PROBE_DONE = False
_CACHED_GPU_ENCODER: Optional[str] = None


def detect_gpu_encoder(force_refresh: bool = False) -> Optional[str]:
    """
    Inspeciona `ffmpeg -hide_banner -encoders` e retorna o melhor encoder H.264 GPU
    suportado (NVIDIA NVENC > Intel QSV > AMD AMF), ou None se indisponível.
    """
    global _PROBE_DONE, _CACHED_GPU_ENCODER
    if _PROBE_DONE and not force_refresh:
        return _CACHED_GPU_ENCODER

    detected: Optional[str] = None
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )  # nosec B603, B607
        if proc.returncode == 0 and proc.stdout:
            output = proc.stdout
            for enc in GPU_ENCODER_PRIORITY:
                if f" {enc} " in output:
                    detected = enc
                    break
    except Exception as exc:
        logger.debug("Probe de encoders GPU FFmpeg falhou: %s", exc)
        detected = None

    _CACHED_GPU_ENCODER = detected
    _PROBE_DONE = True
    if detected:
        logger.info("Encoder GPU FFmpeg detectado: %s", detected)
    return detected


def reset_gpu_probe_cache() -> None:
    """Limpa o cache de detecção de GPU (útil para testes unitários)."""
    global _PROBE_DONE, _CACHED_GPU_ENCODER
    _PROBE_DONE = False
    _CACHED_GPU_ENCODER = None


def build_video_encoder_args(
    use_gpu: bool = False,
    gpu_encoder: Optional[str] = None,
) -> Tuple[List[str], str]:
    """
    Retorna `(argumentos_ffmpeg_video, nome_encoder)` escolhendo GPU quando solicitado
    e disponível, ou `libx264` em CPU.
    """
    selected = (gpu_encoder or (detect_gpu_encoder() if use_gpu else None)) if use_gpu else None

    if selected == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "22", "-pix_fmt", "yuv420p"], "h264_nvenc"
    if selected == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "fast", "-global_quality", "23", "-pix_fmt", "yuv420p"], "h264_qsv"
    if selected == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "balanced", "-qp_i", "22", "-pix_fmt", "yuv420p"], "h264_amf"

    return ["-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p"], "libx264"
