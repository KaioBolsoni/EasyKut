"""
Fachada retrocompatível do Motor de Vídeo (EasyKut Studio).
Agrega os submódulos especializados sem acoplamento circular:
- core.media_utils: validação FFmpeg, caminhos e normalização.
- core.subtitles: geração e escape de legendas .srt.
- core.face_tracker: OpenCV Haar/YuNet ONNX (com SHA-256) + média móvel.
- core.media_renderer: ffprobe, prévias, cortes FFmpeg e GC de temporários.
"""

import socket

import cv2

from core.config import CUTS_DIR, DATA_DIR, DOWNLOADS_DIR
from core.face_tracker import (
    MODELS_DIR,
    YUNET_DOWNLOAD_URL,
    YUNET_MODEL_NAME,
    YUNET_MODEL_PATH,
    YUNET_MODEL_SHA256,
    _FaceDetectorWrapper,
    _ensure_yunet_model,
    detect_smooth_face_crop_x,
    verify_file_sha256,
    verify_sha256_bytes,
)
from core.media_renderer import (
    cleanup_temporary_files,
    cut_video,
    draw_framing_preview,
    extract_frame_thumbnail,
    extract_preview_clip,
    extract_preview_frame,
    get_video_metadata,
)
from core.media_utils import (
    DATA_DIR_RESOLVED,
    FFMPEG_ALLOWED_PROTOCOLS,
    TEMP_DIR,
    check_ffmpeg,
    is_path_within as _is_path_within,
    validate_ffmpeg_input_target as _validate_ffmpeg_input_target,
)
from core.subtitles import (
    _format_ffmpeg_subtitles_filter,
    format_ffmpeg_subtitles_filter,
    format_srt_time,
    generate_srt_for_segment,
)

__all__ = [
    "CUTS_DIR",
    "DATA_DIR",
    "DATA_DIR_RESOLVED",
    "DOWNLOADS_DIR",
    "FFMPEG_ALLOWED_PROTOCOLS",
    "MODELS_DIR",
    "TEMP_DIR",
    "YUNET_DOWNLOAD_URL",
    "YUNET_MODEL_NAME",
    "YUNET_MODEL_PATH",
    "YUNET_MODEL_SHA256",
    "_FaceDetectorWrapper",
    "_ensure_yunet_model",
    "_format_ffmpeg_subtitles_filter",
    "_is_path_within",
    "_validate_ffmpeg_input_target",
    "check_ffmpeg",
    "cleanup_temporary_files",
    "cut_video",
    "cv2",
    "detect_smooth_face_crop_x",
    "draw_framing_preview",
    "extract_frame_thumbnail",
    "extract_preview_clip",
    "extract_preview_frame",
    "format_ffmpeg_subtitles_filter",
    "format_srt_time",
    "generate_srt_for_segment",
    "get_video_metadata",
    "socket",
    "verify_file_sha256",
    "verify_sha256_bytes",
]
