"""
Módulo de Compilação de Melhores Momentos (Highlight Reel — Onda 4).
Concatena múltiplos cortes renderizados em um vídeo único com transições suaves
FFmpeg `xfade` (vídeo) + `acrossfade` (áudio) e confinamento estrito em DATA_DIR.
"""

import logging
from pathlib import Path
from typing import Any, List, Optional
import uuid

from core import media_utils
from core.hw_accel import build_video_encoder_args
from core.media_renderer import get_video_metadata, run_cancellable_ffmpeg

logger = logging.getLogger(__name__)

ALLOWED_XFADE_TRANSITIONS = {"fade", "wipeleft", "slideright", "dissolve", "smoothleft"}


def build_xfade_filter_complex(
    durations: List[float],
    *,
    transition: str = "fade",
    transition_duration: float = 0.5,
    target_width: int = 1080,
    target_height: int = 1920,
) -> str:
    """
    Constrói o grafo `-filter_complex` normalizando resolução/fps/sample-rate de N clipes
    e encadeando `xfade` + `acrossfade` com offsets cumulativos precisos.
    """
    if len(durations) < 2:
        raise ValueError("Highlight Reel requer ao menos 2 clipes.")

    safe_trans = transition if transition in ALLOWED_XFADE_TRANSITIONS else "fade"
    t_dur = max(0.2, min(2.0, float(transition_duration)))
    w = max(2, int(target_width) - (int(target_width) % 2))
    h = max(2, int(target_height) - (int(target_height) % 2))

    parts: List[str] = []
    for idx in range(len(durations)):
        parts.append(
            f"[{idx}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p[v{idx}]"
        )
        parts.append(f"[{idx}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{idx}]")

    cur_v = "[v0]"
    cur_a = "[a0]"
    cumulative_dur = max(0.5, float(durations[0]))

    for idx in range(1, len(durations)):
        clip_dur = max(0.5, float(durations[idx]))
        eff_trans = min(t_dur, max(0.15, min(cumulative_dur, clip_dur) * 0.4))
        offset = max(0.05, round(cumulative_dur - eff_trans, 3))
        out_v = "[vout]" if idx == len(durations) - 1 else f"[vx{idx}]"
        out_a = "[aout]" if idx == len(durations) - 1 else f"[ax{idx}]"

        parts.append(
            f"{cur_v}[v{idx}]xfade=transition={safe_trans}:duration={eff_trans:.2f}:offset={offset:.3f}{out_v}"
        )
        parts.append(f"{cur_a}[a{idx}]acrossfade=d={eff_trans:.2f}{out_a}")

        cur_v = out_v
        cur_a = out_a
        cumulative_dur = max(0.5, cumulative_dur + clip_dur - eff_trans)

    return ";".join(parts)


def compile_highlight_reel(
    clip_paths: List[str],
    *,
    output_path: Optional[str] = None,
    transition: str = "fade",
    transition_duration: float = 0.5,
    target_width: int = 1080,
    target_height: int = 1920,
    use_gpu: bool = False,
    job_id: Optional[str] = None,
    cancel_check: Optional[Any] = None,
    register_proc: Optional[Any] = None,
    unregister_proc: Optional[Any] = None,
) -> str:
    """Compila múltiplos clipes confinados em DATA_DIR em um Highlight Reel MP4 com xfade."""
    media_utils.check_ffmpeg()
    if len(clip_paths) < 2:
        raise ValueError("Selecione pelo menos 2 cortes renderizados para compilar o Highlight Reel.")

    safe_clips: List[str] = []
    durations: List[float] = []
    for cp in clip_paths:
        resolved = media_utils.validate_ffmpeg_input_target(cp, require_within_data_dir=True)
        meta = get_video_metadata(resolved)
        safe_clips.append(resolved)
        durations.append(max(0.5, float(meta.get("duration", 5.0))))

    if output_path:
        dest = Path(output_path).resolve()
        if not media_utils.is_path_within(dest, media_utils.DATA_DIR_RESOLVED):
            raise PermissionError("Destino do Highlight Reel fora de DATA_DIR.")
    else:
        dest = (media_utils.CUTS_DIR / f"highlight_reel_{uuid.uuid4().hex[:8]}.mp4").resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)

    fc = build_xfade_filter_complex(
        durations,
        transition=transition,
        transition_duration=transition_duration,
        target_width=target_width,
        target_height=target_height,
    )

    cmd_base = ["ffmpeg", "-nostdin", "-y"]
    for sc in safe_clips:
        cmd_base.extend(["-i", sc])
    cmd_base.extend(["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"])

    v_args, used_enc = build_video_encoder_args(use_gpu=use_gpu)
    cmd = list(cmd_base) + v_args + ["-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest)]

    rc, stderr = run_cancellable_ffmpeg(
        cmd,
        timeout=300,
        job_id=job_id,
        cancel_check=cancel_check,
        register_proc=register_proc,
        unregister_proc=unregister_proc,
    )
    if rc != 0 and use_gpu and used_enc != "libx264":
        logger.warning("GPU encoder '%s' falhou no Highlight Reel; fallback libx264.", used_enc)
        v_cpu, _ = build_video_encoder_args(use_gpu=False)
        cmd_cpu = list(cmd_base) + v_cpu + ["-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest)]
        rc, stderr = run_cancellable_ffmpeg(
            cmd_cpu,
            timeout=300,
            job_id=job_id,
            cancel_check=cancel_check,
            register_proc=register_proc,
            unregister_proc=unregister_proc,
        )

    if rc != 0:
        raise RuntimeError(f"Erro ao compilar Highlight Reel: {stderr[:350]}")

    logger.info("Highlight Reel compilado com sucesso (%d clipes, xfade=%s): %s", len(safe_clips), transition, dest.name)
    return str(dest)
