"""
Módulo de Edição e Renderização de Vídeo (EasyKut - Fase 3)
Executa cortes precisos, conversão para proporção vertical 9:16 (Shorts com Face Tracking OpenCV)
e horizontal 16:9 (vídeos longos), com sincronização e queima de legendas (.srt) via FFmpeg.
"""

from collections import deque
import json
import logging
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple
import uuid

import cv2
import numpy as np

from core.config import CUTS_DIR, DATA_DIR, DOWNLOADS_DIR, FFMPEG_AVAILABLE

logger = logging.getLogger(__name__)

TEMP_DIR = DATA_DIR / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

MODELS_DIR = DATA_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
YUNET_MODEL_NAME = "face_detection_yunet_2023mar.onnx"
YUNET_MODEL_PATH = MODELS_DIR / YUNET_MODEL_NAME
YUNET_DOWNLOAD_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
)


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

    result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)
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
        dest_srt = TEMP_DIR / f"sub_{int(start_time)}_{int(end_time)}_{uuid.uuid4().hex[:6]}.srt"

    dest_srt.parent.mkdir(parents=True, exist_ok=True)
    dest_srt.write_text("\n".join(srt_entries), encoding="utf-8")
    logger.info("Legenda SRT sincronizada gerada com sucesso: %s", dest_srt)
    return str(dest_srt.resolve())


def _ensure_yunet_model() -> Optional[Path]:
    """
    Localiza ou tenta baixar o modelo ONNX YuNet para cv2.FaceDetectorYN.
    Retorna o Path para o modelo caso disponível, ou None em caso de falha.
    """
    if YUNET_MODEL_PATH.exists() and YUNET_MODEL_PATH.stat().st_size > 5000:
        return YUNET_MODEL_PATH

    alt_paths = [
        Path(__file__).resolve().parent / "models" / YUNET_MODEL_NAME,
        Path(__file__).resolve().parent.parent / "models" / YUNET_MODEL_NAME,
    ]
    for p in alt_paths:
        if p.exists() and p.stat().st_size > 5000:
            return p

    try:
        import urllib.request
        logger.info("Baixando modelo facial YuNet do OpenCV Zoo para %s...", YUNET_MODEL_PATH)
        req = urllib.request.Request(
            YUNET_DOWNLOAD_URL,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            content = response.read()
            if len(content) > 5000:
                YUNET_MODEL_PATH.write_bytes(content)
                logger.info("Modelo YuNet baixado com sucesso (%d bytes).", len(content))
                return YUNET_MODEL_PATH
    except Exception as exc:
        logger.warning("Não foi possível baixar o modelo YuNet automaticamente: %s", exc)

    return None


class _FaceDetectorWrapper:
    """
    Detector facial unificado compatível com OpenCV 4.x e OpenCV 5.x.
    - Se cv2.CascadeClassifier existir (OpenCV 4.x), utiliza Haar Cascade.
    - Se não existir (OpenCV 5.x), utiliza cv2.FaceDetectorYN (YuNet) ou cv2.dnn.
    - Mantém a priorização do maior rosto e Média Móvel.
    - Garante fallback seguro caso nenhum modelo ou detector esteja disponível.
    """
    def __init__(self, target_size: Tuple[int, int] = (640, 360)):
        self.detector_type = "none"
        self.backend = None
        self.target_size = target_size

        # 1. Verifica dinamicamente se cv2.CascadeClassifier existe antes de usá-lo (OpenCV 4.x)
        if hasattr(cv2, "CascadeClassifier"):
            try:
                cascade_dir = getattr(cv2.data, "haarcascades", "") if hasattr(cv2, "data") else ""
                cascade_path = Path(cascade_dir) / "haarcascade_frontalface_default.xml"
                if cascade_path.exists():
                    cc = cv2.CascadeClassifier(str(cascade_path))
                    if not cc.empty():
                        self.backend = cc
                        self.detector_type = "cascade"
                        logger.info("Detector facial ativo: cv2.CascadeClassifier (Haar Cascade).")
            except Exception as exc:
                logger.debug("CascadeClassifier falhou ao inicializar: %s", exc)

        # 2. Se CascadeClassifier não existir ou não carregou (OpenCV 5.x), utiliza FaceDetectorYN
        if self.detector_type == "none" and hasattr(cv2, "FaceDetectorYN"):
            model_path = _ensure_yunet_model()
            if model_path:
                try:
                    self.backend = cv2.FaceDetectorYN.create(
                        model=str(model_path),
                        config="",
                        input_size=self.target_size,
                        score_threshold=0.6,
                        nms_threshold=0.3,
                        top_k=5000
                    )
                    self.detector_type = "facedetectoryn"
                    logger.info("Detector facial ativo: cv2.FaceDetectorYN (YuNet ONNX).")
                except Exception as exc:
                    logger.warning("Falha ao inicializar cv2.FaceDetectorYN: %s", exc)

        # 3. Fallback adicional para cv2.dnn
        if self.detector_type == "none" and hasattr(cv2, "dnn"):
            caffe_proto = MODELS_DIR / "deploy.prototxt"
            caffe_model = MODELS_DIR / "res10_300x300_ssd_iter_140000.caffemodel"
            if caffe_proto.exists() and caffe_model.exists():
                try:
                    self.backend = cv2.dnn.readNetFromCaffe(str(caffe_proto), str(caffe_model))
                    self.detector_type = "dnn_caffe"
                    logger.info("Detector facial ativo: cv2.dnn (Caffe SSD).")
                except Exception as exc:
                    logger.warning("Falha ao inicializar modelo Caffe no cv2.dnn: %s", exc)

        if self.detector_type == "none":
            logger.warning(
                "Nenhum detector facial compatível/modelo disponível no OpenCV %s. "
                "Enquadramento 9:16 utilizará fallback seguro para Center Crop.",
                getattr(cv2, "__version__", "desconhecido")
            )

    def is_available(self) -> bool:
        return self.detector_type != "none" and self.backend is not None

    def detect_largest_face(self, frame: np.ndarray, scale_factor: float = 0.5) -> Optional[Tuple[int, int, int, int]]:
        """
        Detecta o maior rosto no frame (priorização por maior área / falante em primeiro plano).
        Retorna (fx, fy, fw, fh) na resolução original do frame, ou None se nenhum rosto for encontrado.
        """
        if not self.is_available():
            return None

        h, w = frame.shape[:2]
        small_w = max(1, int(w * scale_factor))
        small_h = max(1, int(h * scale_factor))
        small_frame = cv2.resize(frame, (small_w, small_h))

        try:
            if self.detector_type == "cascade":
                gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
                faces = self.backend.detectMultiScale(
                    gray,
                    scaleFactor=1.1,
                    minNeighbors=4,
                    minSize=(int(40 * scale_factor), int(40 * scale_factor))
                )
                if len(faces) > 0:
                    largest = max(faces, key=lambda f: f[2] * f[3])
                    return (
                        int(largest[0] / scale_factor),
                        int(largest[1] / scale_factor),
                        int(largest[2] / scale_factor),
                        int(largest[3] / scale_factor),
                    )

            elif self.detector_type == "facedetectoryn":
                self.backend.setInputSize((small_w, small_h))
                _, faces = self.backend.detect(small_frame)
                if faces is not None and len(faces) > 0:
                    largest = max(faces, key=lambda f: float(f[2]) * float(f[3]))
                    return (
                        int(float(largest[0]) / scale_factor),
                        int(float(largest[1]) / scale_factor),
                        int(float(largest[2]) / scale_factor),
                        int(float(largest[3]) / scale_factor),
                    )

            elif self.detector_type == "dnn_caffe":
                blob = cv2.dnn.blobFromImage(
                    cv2.resize(small_frame, (300, 300)), 1.0, (300, 300), (104.0, 177.0, 123.0)
                )
                self.backend.setInput(blob)
                detections = self.backend.forward()
                best_face = None
                max_area = 0.0
                for i in range(detections.shape2 if hasattr(detections, "shape2") else detections.shape[2]):
                    confidence = float(detections[0, 0, i, 2])
                    if confidence > 0.6:
                        box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
                        bx1, by1, bx2, by2 = box.astype("int")
                        bw = bx2 - bx1
                        bh = by2 - by1
                        area = bw * bh
                        if area > max_area and bw > 20 and bh > 20:
                            max_area = area
                            best_face = (bx1, by1, bw, bh)
                return best_face

        except Exception as exc:
            logger.debug("Erro na detecção de rosto: %s", exc)
            return None

        return None


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
    Detecta rostos nos frames do trecho utilizando OpenCV (FaceDetectorYN / CascadeClassifier / dnn).
    Calcula a coordenada X com Média Móvel (Moving Average nos últimos 10 frames)
    para estabilizar o enquadramento 9:16 e evitar tremor visual (jitter).
    Prioriza o maior rosto em caso de múltiplos interlocutores.
    Retorna a coordenada X suavizada ou None caso nenhum detector/modelo esteja disponível
    ou nenhum rosto seja detectado (fallback seguro para center crop sem quebrar a execução).
    """
    try:
        detector = _FaceDetectorWrapper(target_size=(int(orig_w * 0.5), int(orig_h * 0.5)))
        if not detector.is_available():
            logger.info("Detector facial não disponível. Fallback para center crop.")
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
        scale_factor = 0.5

        while current_sec < end_time:
            ret, frame = cap.read()
            if not ret:
                break

            face = detector.detect_largest_face(frame, scale_factor=scale_factor)

            if face is not None:
                fx, fy, fw, fh = face
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

            # Salta frames intermediários com grab rápido (otimização de decodificação sequencial)
            for _ in range(frame_step - 1):
                if not cap.grab():
                    break
            current_sec += sample_rate_sec

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

    except Exception as exc:
        logger.warning("Falha durante o face tracking: %s. Fallback seguro para center crop.", exc)
        return None


def _format_ffmpeg_subtitles_filter(srt_path: str, is_vertical: bool = True) -> str:
    """
    Formata e escapa o caminho do arquivo .srt para o filtro subtitles do FFmpeg no Windows/Linux,
    aplicando tipografia profissional para Shorts (9:16) ou formato horizontal (16:9).
    Escapa adequadamente caracteres especiais (:, ', [, ], ,) para garantir que o filtro FFmpeg
    não falhe com caminhos de diretório complexos.
    """
    p = Path(srt_path).resolve()
    clean_path = str(p).replace("\\", "/")

    # No Windows, o dois pontos do drive precisa ser escapado como '\:' no filtro FFmpeg
    if len(clean_path) > 1 and clean_path[1] == ":":
        drive = clean_path[0]
        rest = clean_path[2:]
        clean_path = f"{drive}\\:{rest}"

    # Escapa caracteres especiais do parser de filtros do FFmpeg: ', [, ], ,, ;
    clean_path = (
        clean_path.replace("'", "'\\''")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )

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


def extract_preview_frame(
    source: str,
    timestamp_sec: float = 1.0,
    output_path: Optional[str] = None,
    direct_stream_url: Optional[str] = None
) -> Optional[str]:
    """
    Extrai 1 único frame (.jpg) no tempo informado usando FFmpeg com fast input seek.
    Se direct_stream_url for fornecido, reaproveita a URL direta de streaming já resolvida.
    Se source for uma URL web/YouTube e direct_stream_url não for passado, obtém a URL direta via yt-dlp.
    Usa -ss <timestamp> estritamente ANTES de -i <source> com -nostdin -y e timeout=15s.
    Em caso de falha ou timeout, registra warning e retorna None (resiliência).
    """
    try:
        check_ffmpeg()
    except Exception as exc:
        logger.warning("FFmpeg não disponível para extração de frame: %s", exc)
        return None

    try:
        src_str = str(source).strip()

        if output_path:
            dest = Path(output_path)
        else:
            clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", Path(src_str).stem)[:25]
            dest = TEMP_DIR / f"frame_{clean_id}_{int(timestamp_sec * 1000)}.jpg"

        dest.parent.mkdir(parents=True, exist_ok=True)

        is_remote = src_str.startswith("http://") or src_str.startswith("https://")
        input_target = src_str

        if direct_stream_url:
            input_target = direct_stream_url
            logger.debug("Usando stream URL pré-resolvido para extração de frame: %s...", input_target[:60])
        elif is_remote:
            try:
                from core.downloader import get_direct_stream_url
                input_target = get_direct_stream_url(src_str)
                logger.info("Stream URL direto obtido para extração de frame: %s...", input_target[:60])
            except Exception as exc:
                logger.warning("Falha ao obter stream URL direto com yt-dlp: %s. Tentando URL original.", exc)
                input_target = src_str
        else:
            local_p = Path(src_str)
            if not local_p.exists():
                logger.warning("Arquivo fonte não encontrado para extração de frame: %s", src_str)
                return None
            input_target = str(local_p.resolve())

        # Fast input seeking: -ss DEVE vir ANTES de -i para streams HLS remotos
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-ss", str(max(0.0, float(timestamp_sec))),
            "-i", input_target,
            "-vframes", "1",
            "-q:v", "2",
            str(dest)
        ]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if proc.returncode != 0:
                logger.warning("Falha no FFmpeg ao extrair frame (code %s): %s", proc.returncode, proc.stderr[:300] if proc.stderr else "")
                return None
        except subprocess.TimeoutExpired:
            logger.warning("Timeout (15s) excedido ao extrair frame via FFmpeg no tempo %ss", timestamp_sec)
            return None

        if not dest.exists() or dest.stat().st_size == 0:
            logger.warning("Arquivo de frame não foi gerado ou está vazio: %s", dest)
            return None

        logger.info("Frame de preview extraído com sucesso: %s", dest)
        return str(dest.resolve())

    except Exception as e:
        logger.warning("Erro inesperado na extração de frame de preview: %s", e)
        return None


def draw_framing_preview(
    frame_path: str,
    mode: str = "manual_crop",
    params: Optional[Dict[str, Any]] = None,
    output_path: Optional[str] = None
) -> str:
    """
    Gera uma imagem de prévia visual mostrando exatamente como o corte ficará no enquadramento.
    Suporta:
    - manual_crop: desenha o retângulo da área selecionada (9:16) sobre o frame original com destaque visual.
    - split_screen: desenha os dois retângulos (Topo: Azul/Verde e Base: Laranja/Amarelo) sobre o frame.
    - vertical_blur: gera o canvas 9:16 completo com fundo desfocado + escurecido e vídeo nítido centralizado.
    - horizontal_16_9: devolve o frame original em formato widescreen.
    Retorna o caminho absoluto da imagem JPG resultante.
    """
    p = Path(frame_path)
    if not p.exists():
        raise FileNotFoundError(f"Frame não encontrado para preview: {frame_path}")

    img = cv2.imread(str(p.resolve()))
    if img is None:
        raise ValueError(f"Não foi possível decodificar o arquivo de imagem: {frame_path}")

    H, W = img.shape[:2]
    cfg = params or {}

    if output_path:
        dest = Path(output_path)
    else:
        dest = TEMP_DIR / f"framing_{p.stem}_{mode}.jpg"
    dest.parent.mkdir(parents=True, exist_ok=True)

    mode_clean = str(mode).lower().strip()

    if mode_clean in ("manual_crop", "crop_center", "crop", "face_tracking_9_16", "face_tracking"):
        # 1. Modo manual_crop: Desenha o retângulo da área 9:16 selecionada
        crop_w = int(cfg.get("crop_w", cfg.get("w", int(H * (9.0 / 16.0)))))
        crop_h = int(cfg.get("crop_h", cfg.get("h", H)))
        crop_x = int(cfg.get("crop_x", cfg.get("x", (W - crop_w) // 2)))
        crop_y = int(cfg.get("crop_y", cfg.get("y", 0)))

        # Garante limites e números pares
        crop_x = max(0, min(W - 2, crop_x))
        crop_y = max(0, min(H - 2, crop_y))
        crop_w = max(2, min(W - crop_x, crop_w))
        crop_h = max(2, min(H - crop_y, crop_h))

        # Cria máscara para escurecer suavemente a área fora do corte (destaque visual)
        overlay = (img.copy() * 0.45).astype(np.uint8)
        overlay[crop_y:crop_y+crop_h, crop_x:crop_x+crop_w] = img[crop_y:crop_y+crop_h, crop_x:crop_x+crop_w]

        # Desenha retângulo verde/esmeralda ao redor da área de corte
        border_color = (0, 255, 128)  # BGR
        cv2.rectangle(overlay, (crop_x, crop_y), (crop_x + crop_w, crop_y + crop_h), border_color, 4)

        # Rótulo de identificação
        label = f"Corte 9:16 ({crop_w}x{crop_h})"
        cv2.putText(overlay, label, (crop_x + 10, max(30, crop_y + 35)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, border_color, 2, cv2.LINE_AA)

        result_img = overlay

    elif mode_clean in ("split_screen", "split"):
        # 2. Modo split_screen: Desenha 2 retângulos (Topo - Azul/Verde e Base - Laranja/Amarelo)
        box_top = cfg.get("box_top") or {}
        box_bottom = cfg.get("box_bottom") or {}

        # Aspecto para cada metade: 1080 / 960 = 1.125
        target_ar = 1080.0 / 960.0

        # Região Topo (padrão: lado esquerdo ou falante 1)
        tw = int(box_top.get("w", min(W // 2, int(H * target_ar))))
        th = int(box_top.get("h", int(tw / target_ar)))
        tx = int(box_top.get("x", max(0, (W // 4) - (tw // 2))))
        ty = int(box_top.get("y", max(0, (H - th) // 2)))

        # Região Base (padrão: lado direito ou falante 2)
        bw = int(box_bottom.get("w", min(W // 2, int(H * target_ar))))
        bh = int(box_bottom.get("h", int(bw / target_ar)))
        bx = int(box_bottom.get("x", min(W - bw, (3 * W // 4) - (bw // 2))))
        by = int(box_bottom.get("y", max(0, (H - bh) // 2)))

        # Ajustes de limites
        tx, ty = max(0, min(W - 2, tx)), max(0, min(H - 2, ty))
        tw, th = max(2, min(W - tx, tw)), max(2, min(H - ty, th))
        bx, by = max(0, min(W - 2, bx)), max(0, min(H - 2, by))
        bw, bh = max(2, min(W - bx, bw)), max(2, min(H - by, bh))

        overlay = (img.copy() * 0.45).astype(np.uint8)
        overlay[ty:ty+th, tx:tx+tw] = img[ty:ty+th, tx:tx+tw]
        overlay[by:by+bh, bx:bx+bw] = img[by:by+bh, bx:bx+bw]

        # Topo: Ciano/Azul claro (255, 230, 0 em BGR)
        color_top = (255, 230, 0)
        cv2.rectangle(overlay, (tx, ty), (tx + tw, ty + th), color_top, 4)
        cv2.putText(overlay, "TOPO (1080x960)", (tx + 10, max(30, ty + 35)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color_top, 2, cv2.LINE_AA)

        # Base: Laranja/Amarelo (0, 165, 255 em BGR)
        color_bot = (0, 165, 255)
        cv2.rectangle(overlay, (bx, by), (bx + bw, by + bh), color_bot, 4)
        cv2.putText(overlay, "BASE (1080x960)", (bx + 10, max(30, by + 35)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color_bot, 2, cv2.LINE_AA)

        result_img = overlay

    elif mode_clean in ("vertical_blur", "blur_background", "blur"):
        # 3. Modo vertical_blur: Gera miniatura 9:16 com vídeo encaixado no centro e fundo desfocado
        canvas_w, canvas_h = 1080, 1920
        bg_scale = max(canvas_w / W, canvas_h / H)
        bg_w = int(round(W * bg_scale))
        bg_h = int(round(H * bg_scale))
        bg_resized = cv2.resize(img, (bg_w, bg_h))
        bg_crop_x = (bg_w - canvas_w) // 2
        bg_crop_y = (bg_h - canvas_h) // 2
        bg = bg_resized[bg_crop_y:bg_crop_y+canvas_h, bg_crop_x:bg_crop_x+canvas_w]

        # Desfoque + escurecimento leve (eq=brightness=-0.15)
        bg_blurred = cv2.GaussianBlur(bg, (51, 51), 0)
        bg_darkened = np.clip(bg_blurred.astype(np.float32) * 0.82 - 15.0, 0, 255).astype(np.uint8)

        # Primeiro plano com proporção original (zoom_factor opcional de 1.0 a 1.3)
        zoom_factor = float(cfg.get("zoom_factor", 1.0))
        zoom_factor = max(1.0, min(1.3, zoom_factor))

        fg_scale = (canvas_w / W) * zoom_factor
        fg_w = int(round(W * fg_scale))
        fg_h = int(round(H * fg_scale))
        fg_resized = cv2.resize(img, (fg_w, fg_h))

        if fg_w > canvas_w:
            x_off = (fg_w - canvas_w) // 2
            fg_resized = fg_resized[:, x_off:x_off+canvas_w]
            fg_w = canvas_w

        pos_y = (canvas_h - fg_h) // 2
        pos_x = (canvas_w - fg_w) // 2

        if pos_y < 0:
            y_crop = abs(pos_y)
            fg_resized = fg_resized[y_crop:y_crop+canvas_h, :]
            pos_y = 0

        bg_darkened[pos_y:pos_y+fg_resized.shape[0], pos_x:pos_x+fg_resized.shape[1]] = fg_resized
        result_img = bg_darkened

    else:
        # horizontal_16_9 ou modo original sem crop
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
    Recorta um trecho do vídeo e formata de acordo com o modo de enquadramento selecionado.

    4 Modos de Renderização Suportados (V2.0):
    - 'horizontal_16_9': Mantém proporção horizontal 16:9 original (usa stream copy quando sem legendas).
    - 'vertical_blur': Canvas 1080x1920 com fundo ampliado + boxblur=20:5 + leve escurecimento,
                       e vídeo original centralizado sem distorção (suporta zoom_factor de 1.0 a 1.3).
    - 'manual_crop': Aplica crop=w:h:x:y nas coordenadas exatas escolhidas pelo usuário, escalando para 1080x1920.
    - 'split_screen': Recorta duas regiões (box_top e box_bottom), ajusta cada uma para 1080x960 sem distorcer
                      e empilha verticalmente (vstack=inputs=2) gerando 1080x1920.

    Face Tracking automático continua disponível como preset/sugestão opcional (face_tracking=True).
    Garante sempre dimensões pares no FFmpeg e escape estrito no filtro de legendas.
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
    orig_w = int(meta["width"])
    orig_h = int(meta["height"])

    # 2. Normalização do layout_mode e layout_params
    effective_mode = (layout_mode or mode).lower().strip()
    cfg = layout_params or {}

    # Mapeamento de compatibilidade
    if effective_mode in ("blur_background", "blur"):
        effective_mode = "vertical_blur"
    elif effective_mode in ("crop_center", "crop"):
        effective_mode = "manual_crop"
    elif effective_mode in ("long_16_9", "original"):
        effective_mode = "horizontal_16_9"
    elif effective_mode in ("face_tracking_9_16", "vertical_face"):
        effective_mode = "manual_crop"
        face_tracking = True

    # Garante target_width e target_height pares
    if target_width % 2 != 0:
        target_width -= 1
    if target_height % 2 != 0:
        target_height -= 1

    # 3. Geração da legenda temporária (.srt) se solicitado
    srt_file_path = None
    if burn_subtitles and segments:
        srt_file_path = generate_srt_for_segment(segments, start_time, end_time)

    # 4. Construção da chamada FFmpeg
    is_vertical_format = effective_mode != "horizontal_16_9"
    sub_filter = (
        _format_ffmpeg_subtitles_filter(srt_file_path, is_vertical=is_vertical_format)
        if srt_file_path
        else None
    )

    cmd = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-ss", str(round(start_time, 2)),
        "-i", str(src),
        "-t", str(round(duration, 2)),
    ]

    # ----------------------------------------------------
    # MODO 1: horizontal_16_9 (Vídeo original sem crop/zoom)
    # ----------------------------------------------------
    if effective_mode == "horizontal_16_9":
        if not sub_filter:
            # Stream copy ultra-rápido quando não há legenda queimada
            cmd.extend([
                "-c", "copy",
                "-movflags", "+faststart",
                str(dest)
            ])
            logger.info("Executando corte horizontal 16:9 via stream copy (sem reencode): %s", dest.name)
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if proc.returncode != 0:
                logger.error("Falha no FFmpeg stream copy: %s", proc.stderr)
                raise RuntimeError(f"Erro ao processar corte horizontal: {proc.stderr[:350]}")
            return str(dest.resolve())
        else:
            # Reencode limpo apenas para queimar a legenda
            video_filter = f"scale=trunc(iw/2)*2:trunc(ih/2)*2,{sub_filter}"
            cmd.extend(["-vf", video_filter])

    # ----------------------------------------------------
    # MODO 2: vertical_blur (Vertical 9:16 sem zoom forçado)
    # ----------------------------------------------------
    elif effective_mode == "vertical_blur":
        zoom_factor = float(cfg.get("zoom_factor", 1.0))
        zoom_factor = max(1.0, min(1.3, zoom_factor))
        fg_w = int(round(target_width * zoom_factor))
        if fg_w % 2 != 0:
            fg_w += 1

        bg_filter = (
            f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
            f"crop={target_width}:{target_height},boxblur=20:5,eq=brightness=-0.15[bg]"
        )

        if zoom_factor <= 1.0:
            fg_filter = f"[0:v]scale={target_width}:-2:force_original_aspect_ratio=decrease[fg]"
        else:
            fg_filter = (
                f"[0:v]scale={fg_w}:-2:force_original_aspect_ratio=decrease,"
                f"crop=min({target_width},iw):min({target_height},ih)[fg]"
            )

        filter_complex = f"{bg_filter};{fg_filter};[bg][fg]overlay=(W-w)/2:(H-h)/2[merged]"
        if sub_filter:
            filter_complex += f";[merged]{sub_filter}[outv]"
            cmd.extend(["-filter_complex", filter_complex, "-map", "[outv]", "-map", "0:a?"])
        else:
            cmd.extend(["-filter_complex", filter_complex, "-map", "[merged]", "-map", "0:a?"])

    # ----------------------------------------------------
    # MODO 3: manual_crop (Seleção manual de coordenadas 9:16)
    # ----------------------------------------------------
    elif effective_mode in ("manual_crop", "crop"):
        # Largura padrão 9:16 sobre a altura original
        def_crop_w = int(orig_h * (9.0 / 16.0))
        if def_crop_w % 2 != 0:
            def_crop_w -= 1

        # Preset opcional de Face Tracking se solicitado
        if face_tracking:
            face_x = None
            try:
                face_x = detect_smooth_face_crop_x(
                    video_path=str(src),
                    start_time=start_time,
                    end_time=end_time,
                    orig_w=orig_w,
                    orig_h=orig_h,
                    crop_w=def_crop_w
                )
            except Exception as exc:
                logger.warning("Preset Face Tracking falhou: %s. Utilizando fallback central.", exc)
                face_x = None

            if face_x is None:
                face_x = max(0, (orig_w - def_crop_w) // 2)
                if face_x % 2 != 0:
                    face_x -= 1
            crop_x = face_x
            crop_y = 0
            crop_w = def_crop_w
            crop_h = orig_h
        else:
            # Coordenadas personalizadas do usuário ou centro (com reescala proporcional se o canvas tinha resolução diferente)
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

        # Normaliza limites preservando a proporção 9:16 e garantindo dimensões pares >= 2
        target_ar = target_width / target_height  # 9/16 = 0.5625
        crop_w = max(2, min(orig_w, crop_w))
        crop_h = max(2, min(orig_h, crop_h))
        # Ajusta proporcionalmente se a altura ou largura excedeu o limite do vídeo
        if crop_w / crop_h > target_ar:
            crop_w = int(round(crop_h * target_ar))
        else:
            crop_h = int(round(crop_w / target_ar))
        crop_w = max(2, min(orig_w, crop_w))
        crop_h = max(2, min(orig_h, crop_h))

        crop_x = max(0, min(orig_w - crop_w, crop_x))
        crop_y = max(0, min(orig_h - crop_h, crop_y))

        crop_x = crop_x - (crop_x % 2)
        crop_y = crop_y - (crop_y % 2)
        crop_w = max(2, crop_w - (crop_w % 2))
        crop_h = max(2, crop_h - (crop_h % 2))

        video_filter = f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={target_width}:{target_height}"
        if sub_filter:
            video_filter += f",{sub_filter}"
        cmd.extend(["-vf", video_filter])

    # ----------------------------------------------------
    # MODO 4: split_screen (2 regiões empilhadas 1080x960)
    # ----------------------------------------------------
    elif effective_mode in ("split_screen", "split"):
        half_h = target_height // 2  # 960
        target_ar = target_width / half_h  # 1.125 (9:8)

        ref_w = float(cfg.get("ref_w", 0) or 0)
        ref_h = float(cfg.get("ref_h", 0) or 0)
        sx = (orig_w / ref_w) if ref_w > 0 else 1.0
        sy = (orig_h / ref_h) if ref_h > 0 else 1.0

        box_top = cfg.get("box_top") or {}
        box_bottom = cfg.get("box_bottom") or {}

        def_box_w = min(orig_w // 2, int(orig_h * target_ar))
        def_box_h = int(def_box_w / target_ar)

        def _fit_split_box(b: Dict[str, Any], def_x: int, def_y: int) -> Tuple[int, int, int, int]:
            bx = int(round(float(b["x"]) * sx)) if "x" in b else def_x
            by = int(round(float(b["y"]) * sy)) if "y" in b else def_y
            bw = int(round(float(b["w"]) * sx)) if "w" in b else def_box_w
            bh = int(round(float(b["h"]) * sy)) if "h" in b else def_box_h
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
            return (
                bx - (bx % 2),
                by - (by % 2),
                max(2, bw - (bw % 2)),
                max(2, bh - (bh % 2)),
            )

        top_x, top_y, top_w, top_h = _fit_split_box(
            box_top, max(0, (orig_w // 4) - (def_box_w // 2)), max(0, (orig_h - def_box_h) // 2)
        )
        bot_x, bot_y, bot_w, bot_h = _fit_split_box(
            box_bottom, min(orig_w - def_box_w, (3 * orig_w // 4) - (def_box_w // 2)), max(0, (orig_h - def_box_h) // 2)
        )

        filter_complex = (
            f"[0:v]crop={top_w}:{top_h}:{top_x}:{top_y},scale={target_width}:{half_h}[top];"
            f"[0:v]crop={bot_w}:{bot_h}:{bot_x}:{bot_y},scale={target_width}:{half_h}[bot];"
            f"[top][bot]vstack=inputs=2[stacked]"
        )
        if sub_filter:
            filter_complex += f";[stacked]{sub_filter}[outv]"
            cmd.extend(["-filter_complex", filter_complex, "-map", "[outv]", "-map", "0:a?"])
        else:
            cmd.extend(["-filter_complex", filter_complex, "-map", "[stacked]", "-map", "0:a?"])

    else:
        # Fallback genérico: vertical_blur
        bg_filter = (
            f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
            f"crop={target_width}:{target_height},boxblur=20:5,eq=brightness=-0.15[bg]"
        )
        fg_filter = f"[0:v]scale={target_width}:-2:force_original_aspect_ratio=decrease[fg]"
        filter_complex = f"{bg_filter};{fg_filter};[bg][fg]overlay=(W-w)/2:(H-h)/2[merged]"
        if sub_filter:
            filter_complex += f";[merged]{sub_filter}[outv]"
            cmd.extend(["-filter_complex", filter_complex, "-map", "[outv]", "-map", "0:a?"])
        else:
            cmd.extend(["-filter_complex", filter_complex, "-map", "[merged]", "-map", "0:a?"])

    # 5. Codecs e flags de exportação
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

    logger.info("Executando corte FFmpeg (%s -> %ss, layout: %s, legendas: %s): %s",
                start_time, end_time, effective_mode, bool(srt_file_path), dest.name)

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        logger.error("Falha no FFmpeg: %s", proc.stderr)
        raise RuntimeError(f"Erro ao processar corte de vídeo: {proc.stderr[:350]}")

    logger.info("Corte finalizado com sucesso: %s", dest)
    return str(dest.resolve())


def extract_frame_thumbnail(video_path: str, timestamp: float = 1.0, output_path: Optional[str] = None) -> str:
    """
    Extrai um frame estático de preview do vídeo como imagem JPEG (retrocompatibilidade).
    """
    return extract_preview_frame(source=video_path, timestamp_sec=timestamp, output_path=output_path)


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
