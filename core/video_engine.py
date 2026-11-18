"""
Módulo de Edição e Renderização de Vídeo (EasyKut - Fase 3)
Executa cortes precisos, conversão para proporção vertical 9:16 (Shorts com Face Tracking OpenCV)
e horizontal 16:9 (vídeos longos), com sincronização e queima de legendas (.srt) via FFmpeg.
"""

from collections import deque
import json
import logging
from pathlib import Path
import shutil
import subprocess
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from core.config import CUTS_DIR, DATA_DIR, DOWNLOADS_DIR, FFMPEG_AVAILABLE

logger = logging.getLogger(__name__)

TEMP_DIR = DATA_DIR / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)


def check_ffmpeg() -> None:
    """Verifica se o FFmpeg está disponível no PATH do sistema."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError(
            "FFmpeg ou FFprobe não foram encontrados no PATH. "
            "Certifique-se de que o executável está instalado (ex: C:\\ffmpeg\\bin)."
        )


def get_video_metadata(video_path: str) -> Dict[str, Any]:
    """
    Obtém metadados técnicos do vídeo (duração, dimensões, fps) via ffprobe.
    """
    check_ffmpeg()
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Vídeo não encontrado: {video_path}")

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    probe_data = json.loads(result.stdout)

    video_stream = next(
        (s for s in probe_data.get("streams", []) if s.get("codec_type") == "video"),
        {}
    )
    format_info = probe_data.get("format", {})

    width = int(video_stream.get("width", 1920))
    height = int(video_stream.get("height", 1080))
    duration = float(format_info.get("duration", video_stream.get("duration", 0.0)))

    # Extração de FPS
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


def format_srt_time(seconds: float) -> str:
    """
    Converte segundos em formato de timestamp padrão SRT: HH:MM:SS,mmm
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
    output_srt_path: Optional[str] = None
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

        # Descarta segmentos que estão totalmente fora do intervalo do corte
        if seg_end <= start_time or seg_start >= end_time or not text:
            continue

        # Ajusta os tempos relativos ao início do corte (começando em 0.0s)
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
        dest_srt = TEMP_DIR / f"sub_{int(start_time)}_{int(end_time)}.srt"

    dest_srt.parent.mkdir(parents=True, exist_ok=True)
    dest_srt.write_text("\n".join(srt_entries), encoding="utf-8")
    logger.info("Legenda SRT sincronizada gerada com sucesso: %s", dest_srt)
    return str(dest_srt.resolve())


def detect_smooth_face_crop_x(
    video_path: str,
    start_time: float,
    end_time: float,
    orig_w: int,
    orig_h: int,
    crop_w: int,
    sample_rate_sec: float = 0.25,
    window_size: int = 10
) -> Optional[int]:
    """
    Detecta rostos nos frames do trecho utilizando OpenCV Haar Cascade.
    Calcula a coordenada X com Média Móvel (Moving Average nos últimos 10 frames)
    para estabilizar o enquadramento 9:16 e evitar tremor visual (jitter).
    Retorna a coordenada X suavizada ou None caso nenhum rosto seja detectado (fallback para center crop).
    """
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    face_cascade = cv2.CascadeClassifier(cascade_path)
    if face_cascade.empty():
        logger.warning("Classificador facial Haar Cascade não carregou. Fallback para center crop.")
        return None

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.warning("Não foi possível abrir o vídeo para detecção facial: %s", video_path)
        return None

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_step = max(1, int(fps * sample_rate_sec))

    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, start_time * 1000.0))

    history_x: deque = deque(maxlen=window_size)
    smoothed_x_list: List[float] = []

    current_sec = start_time
    max_crop_x = max(0, orig_w - crop_w)

    while current_sec < end_time:
        ret, frame = cap.read()
        if not ret:
            break

        # Reduz resolução para aceleração da detecção facial
        scale_factor = 0.5
        small_frame = cv2.resize(frame, (0, 0), fx=scale_factor, fy=scale_factor)
        gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)

        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=4,
            minSize=(int(40 * scale_factor), int(40 * scale_factor))
        )

        if len(faces) > 0:
            # Prioriza o rosto de maior área (falante em primeiro plano mais próximo da câmera)
            largest_face = max(faces, key=lambda f: f[2] * f[3])
            fx, fy, fw, fh = [int(v / scale_factor) for v in largest_face]
            face_center_x = fx + (fw / 2.0)

            # Posição X da janela 9:16 para centralizar o rosto
            target_x = face_center_x - (crop_w / 2.0)
            target_x = max(0.0, min(float(max_crop_x), target_x))

            # Estabilidade em múltiplos rostos: se houver salto brusco entre falantes,
            # limita o passo máximo por amostra para garantir transição suave (damping)
            if history_x:
                last_pos = history_x[-1]
                max_step = float(crop_w * 0.25)
                if abs(target_x - last_pos) > max_step:
                    if target_x > last_pos:
                        target_x = last_pos + max_step
                    else:
                        target_x = last_pos - max_step

            # Atualiza Média Móvel nos últimos 10 frames
            history_x.append(target_x)
            moving_avg = sum(history_x) / len(history_x)
            smoothed_x_list.append(moving_avg)

        # Salta para o próximo intervalo de amostragem
        current_sec += sample_rate_sec
        current_frame_pos = int(current_sec * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, current_frame_pos)

    cap.release()

    if not smoothed_x_list:
        logger.info("Nenhum rosto detectado no trecho %.2fs -> %.2fs. Fallback para center crop.", start_time, end_time)
        return None

    # Mediana das posições médias suavizadas para máxima estabilidade no corte
    final_x = int(round(float(np.median(smoothed_x_list))))
    final_x = max(0, min(max_crop_x, final_x))

    # Garante número par para compatibilidade com FFmpeg libx264
    if final_x % 2 != 0:
        final_x = max(0, final_x - 1)

    logger.info("Face tracking OpenCV definiu X=%d suavizado (Janela: %dx%d em %dx%d).", final_x, crop_w, orig_h, orig_w, orig_h)
    return final_x


def _format_ffmpeg_subtitles_filter(srt_path: str, is_vertical: bool = True) -> str:
    """
    Formata e escapa o caminho do arquivo .srt para o filtro subtitles do FFmpeg no Windows/Linux,
    aplicando tipografia profissional para Shorts (9:16) ou formato horizontal (16:9).
    """
    p = Path(srt_path).resolve()
    clean_path = str(p).replace("\\", "/")

    # No Windows, o dois pontos do drive precisa ser escapado como '\:' no filtro FFmpeg
    if len(clean_path) > 1 and clean_path[1] == ":":
        drive = clean_path[0]
        rest = clean_path[2:]
        clean_path = f"{drive}\\:{rest}"

    if is_vertical:
        # Estilo para Shorts/Reels/TikTok: texto centralizado, destaque em caixa ou borda nítida
        style = (
            "FontName=Arial,FontSize=16,Bold=1,"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,"
            "Alignment=2,MarginV=55"
        )
    else:
        # Estilo para vídeos longos 16:9
        style = (
            "FontName=Arial,FontSize=18,Bold=1,"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,"
            "Alignment=2,MarginV=25"
        )

    return f"subtitles='{clean_path}':force_style='{style}'"


def cut_video(
    input_path: str,
    start_time: float,
    end_time: float,
    output_path: Optional[str] = None,
    mode: str = "blur_background",
    target_width: int = 1080,
    target_height: int = 1920,
    segments: Optional[List[Dict[str, Any]]] = None,
    burn_subtitles: bool = False,
    face_tracking: bool = False,
) -> str:
    """
    Recorta um trecho do vídeo e formata de acordo com a proporção desejada.

    Modos disponíveis:
    - 'face_tracking_9_16' / 'vertical_face': Corte vertical 9:16 com Face Tracking OpenCV e média móvel.
    - 'blur_background': Fundo ampliado e desfocado com vídeo original nítido ao centro (Padrão viral 9:16).
    - 'crop_center': Recorte direto das bordas laterais focado no centro (9:16).
    - 'horizontal_16_9' / 'long_16_9' / 'original': Mantém proporção horizontal 16:9 para vídeos longos.

    Suporta queima automática de legendas (.srt sincronizadas a partir de 00:00:00,000).
    """
    check_ffmpeg()
    src = Path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"Arquivo de origem não encontrado: {input_path}")

    duration = end_time - start_time
    if duration <= 0:
        raise ValueError(f"Intervalo de corte inválido: {start_time}s -> {end_time}s")

    if output_path:
        dest = Path(output_path)
    else:
        dest_filename = f"{src.stem}_cut_{int(start_time)}_{int(end_time)}.mp4"
        dest = CUTS_DIR / dest_filename

    dest.parent.mkdir(parents=True, exist_ok=True)

    # 1. Metadados do vídeo de entrada
    meta = get_video_metadata(str(src))
    orig_w = meta["width"]
    orig_h = meta["height"]

    is_horizontal_mode = mode in ("horizontal_16_9", "long_16_9", "original")

    # 2. Geração da legenda temporária (.srt) se solicitado
    srt_file_path = None
    if burn_subtitles and segments:
        srt_file_path = generate_srt_for_segment(segments, start_time, end_time)

    # 3. Construção dos filtros de vídeo
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(round(start_time, 2)),
        "-i", str(src),
        "-t", str(round(duration, 2)),
    ]

    # Flags e filtros
    use_face_tracking = face_tracking or mode in ("face_tracking_9_16", "vertical_face")

    if is_horizontal_mode:
        # Modo Horizontal 16:9 (Vídeos longos)
        video_filter = f"scale={min(orig_w, 1920)}:-2"
        if srt_file_path:
            sub_filter = _format_ffmpeg_subtitles_filter(srt_file_path, is_vertical=False)
            video_filter = f"{video_filter},{sub_filter}"
        cmd.extend(["-vf", video_filter])

    elif use_face_tracking:
        # Modo Vertical 9:16 com Face Tracking e Suavização
        crop_w = int(orig_h * (9.0 / 16.0))
        if crop_w % 2 != 0:
            crop_w -= 1

        # Tenta calcular a posição com OpenCV
        face_x = detect_smooth_face_crop_x(
            video_path=str(src),
            start_time=start_time,
            end_time=end_time,
            orig_w=orig_w,
            orig_h=orig_h,
            crop_w=crop_w
        )

        # Fallback para center crop caso não detecte rosto
        if face_x is None:
            face_x = max(0, (orig_w - crop_w) // 2)
            if face_x % 2 != 0:
                face_x -= 1

        video_filter = f"crop={crop_w}:{orig_h}:{face_x}:0,scale={target_width}:{target_height}"
        if srt_file_path:
            sub_filter = _format_ffmpeg_subtitles_filter(srt_file_path, is_vertical=True)
            video_filter = f"{video_filter},{sub_filter}"
        cmd.extend(["-vf", video_filter])

    elif mode == "crop_center":
        # Recorte 9:16 central direto
        video_filter = f"crop=ih*(9/16):ih,scale={target_width}:{target_height}"
        if srt_file_path:
            sub_filter = _format_ffmpeg_subtitles_filter(srt_file_path, is_vertical=True)
            video_filter = f"{video_filter},{sub_filter}"
        cmd.extend(["-vf", video_filter])

    else:
        # Modo Padrão: blur_background 9:16
        filter_complex = (
            f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
            f"crop={target_width}:{target_height},boxblur=25:10[bg];"
            f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[merged]"
        )
        if srt_file_path:
            sub_filter = _format_ffmpeg_subtitles_filter(srt_file_path, is_vertical=True)
            filter_complex += f";[merged]{sub_filter}[outv]"
            cmd.extend(["-filter_complex", filter_complex, "-map", "[outv]", "-map", "0:a?"])
        else:
            filter_complex += f"[merged]"
            cmd.extend(["-filter_complex", filter_complex, "-map", "[merged]", "-map", "0:a?"])

    # 4. Codecs otimizados para compatibilidade web/mobile
    cmd.extend([
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(dest)
    ])

    logger.info("Executando corte FFmpeg (%s -> %ss, modo: %s, legendas: %s): %s",
                start_time, end_time, mode, bool(srt_file_path), dest.name)

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        logger.error("Falha no FFmpeg: %s", proc.stderr)
        raise RuntimeError(f"Erro ao processar corte de vídeo: {proc.stderr[:350]}")

    logger.info("Corte finalizado com sucesso: %s", dest)
    return str(dest.resolve())


def extract_frame_thumbnail(video_path: str, timestamp: float = 1.0, output_path: Optional[str] = None) -> str:
    """
    Extrai um frame estático de preview do vídeo como imagem JPEG.
    """
    check_ffmpeg()
    src = Path(video_path)
    dest = Path(output_path) if output_path else src.with_suffix(".jpg")

    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(max(0.0, timestamp)),
        "-i", str(src),
        "-vframes", "1",
        "-q:v", "2",
        str(dest)
    ]

    subprocess.run(cmd, capture_output=True, check=True)
    return str(dest.resolve())


def cleanup_temporary_files(
    temp_dir: Optional[Path] = None,
    downloads_dir: Optional[Path] = None,
    remove_raw_downloads: bool = False
) -> Dict[str, Any]:
    """
    Remove arquivos temporários pesados (.srt, .mp3, mídias intermediárias) em data/temp/.
    Se remove_raw_downloads=True, remove também arquivos brutos de data/downloads/,
    preservando rigorosamente todos os clipes finais renderizados em data/cortes/.
    Retorna a contagem de arquivos excluídos e o total de MB liberados.
    """
    target_temp = temp_dir or TEMP_DIR
    target_dl = downloads_dir or DOWNLOADS_DIR

    deleted_count = 0
    freed_bytes = 0

    # 1. Limpeza da pasta temporária data/temp/
    if target_temp.exists():
        for f in target_temp.iterdir():
            if f.is_file() and f.name != ".gitkeep":
                try:
                    size = f.stat().st_size
                    f.unlink()
                    deleted_count += 1
                    freed_bytes += size
                except Exception as exc:
                    logger.warning("Falha ao remover arquivo temporário %s: %s", f.name, exc)

    # 2. Limpeza opcional de downloads brutos (vídeos e áudios baixados já cortados)
    if remove_raw_downloads and target_dl.exists():
        for f in target_dl.iterdir():
            if f.is_file() and f.name != ".gitkeep":
                try:
                    size = f.stat().st_size
                    f.unlink()
                    deleted_count += 1
                    freed_bytes += size
                except Exception as exc:
                    logger.warning("Falha ao remover download bruto %s: %s", f.name, exc)

    freed_mb = round(freed_bytes / (1024 * 1024), 2)
    logger.info("Limpeza de temporários: %d arquivos removidos, %.2f MB liberados.", deleted_count, freed_mb)

    return {
        "deleted_count": deleted_count,
        "freed_mb": freed_mb
    }
