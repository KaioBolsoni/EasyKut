"""
Módulo de Masterização de Áudio & Auto-Ducking de Trilha Sonora (Onda 3).
Suporta redução de ruído (afftdn), normalização broadcast -14 LUFS (loudnorm)
e mixagem automática de música de fundo com sidechaincompress durante a fala.
"""

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from core import media_utils

logger = logging.getLogger(__name__)

ALLOWED_MUSIC_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}


def resolve_confined_music_path(music_path: Optional[str]) -> Optional[str]:
    """Valida que a trilha de áudio existe e está estritamente confinada em DATA_DIR."""
    if not music_path:
        return None
    p = Path(music_path).resolve()
    if (
        p.suffix.lower() in ALLOWED_MUSIC_EXTENSIONS
        and media_utils.is_path_within(p, media_utils.DATA_DIR_RESOLVED)
        and p.exists()
        and p.is_file()
    ):
        return str(p)
    return None


def build_audio_mastering_filters(
    *,
    normalize_audio: bool = False,
    denoise_audio: bool = False,
    music_input_index: Optional[int] = None,
    music_volume: float = 0.18,
    input_audio_label: str = "[0:a]",
) -> Tuple[List[str], str]:
    """
    Constrói cadeia de filtros de áudio FFmpeg (afftdn + loudnorm -14 LUFS + auto-ducking).
    Retorna `(lista_de_filtros, label_audio_final)`.
    """
    if not normalize_audio and not denoise_audio and music_input_index is None:
        return [], input_audio_label

    filters: List[str] = []
    speech_chain: List[str] = []
    if denoise_audio:
        speech_chain.append("afftdn=nf=-25")
    if normalize_audio:
        speech_chain.append("loudnorm=I=-14:TP=-1.5:LRA=11")

    cur_speech = input_audio_label
    if speech_chain:
        filters.append(f"{cur_speech}{','.join(speech_chain)}[a_speech]")
        cur_speech = "[a_speech]"

    if music_input_index is not None:
        vol = max(0.02, min(0.60, float(music_volume)))
        filters.append(f"{cur_speech}asplit=2[a_main][a_sc]")
        filters.append(f"[{music_input_index}:a]volume={vol:.2f}[a_bgm_low]")
        filters.append(
            "[a_bgm_low][a_sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=250[a_bgm_ducked]"
        )
        filters.append("[a_main][a_bgm_ducked]amix=inputs=2:duration=first:dropout_transition=2[a_master]")
        return filters, "[a_master]"

    return filters, cur_speech
