"""
Módulo de detecção e rastreamento facial suave (OpenCV Haar Cascade / YuNet ONNX / DNN).
Inclui verificação de integridade SHA-256 no download automático do modelo YuNet ONNX (SEC-08).
"""

from collections import deque
import hashlib
import logging
from pathlib import Path
from typing import List, Optional, Tuple
import urllib.request

import cv2
import numpy as np

from core.config import DATA_DIR

logger = logging.getLogger(__name__)

MODELS_DIR = DATA_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
YUNET_MODEL_NAME = "face_detection_yunet_2023mar.onnx"
YUNET_MODEL_PATH = MODELS_DIR / YUNET_MODEL_NAME
YUNET_DOWNLOAD_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
# Hash SHA-256 verificado do binário oficial face_detection_yunet_2023mar.onnx (OpenCV Zoo)
YUNET_MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"


def verify_sha256_bytes(content: bytes, expected_hex: str) -> bool:
    """Verifica se o digest SHA-256 de `content` corresponde exatamente a `expected_hex`."""
    digest = hashlib.sha256(content).hexdigest().lower()
    return digest == expected_hex.strip().lower()


def verify_file_sha256(path: Path, expected_hex: str) -> bool:
    """Verifica integridade SHA-256 de um arquivo em disco."""
    if not path.exists() or not path.is_file():
        return False
    return verify_sha256_bytes(path.read_bytes(), expected_hex)


def _ensure_yunet_model() -> Optional[Path]:
    """
    Localiza ou baixa o modelo ONNX YuNet validando rigorosamente o checksum SHA-256
    antes de gravar/utilizar o binário (mitigação contra comprometimento de supply chain).
    """
    candidates = [
        YUNET_MODEL_PATH,
        Path(__file__).resolve().parent / "models" / YUNET_MODEL_NAME,
        Path(__file__).resolve().parent.parent / "models" / YUNET_MODEL_NAME,
    ]
    for p in candidates:
        if p.exists() and p.is_file() and p.stat().st_size > 5000:
            if verify_file_sha256(p, YUNET_MODEL_SHA256):
                return p
            logger.warning("Checksum SHA-256 inválido para modelo YuNet local em '%s'; ignorando arquivo.", p)

    if not YUNET_DOWNLOAD_URL.startswith("https://"):
        logger.error("URL de download do modelo YuNet não utiliza HTTPS.")
        return None

    try:
        logger.info("Baixando modelo facial YuNet do OpenCV Zoo para %s...", YUNET_MODEL_PATH)
        req = urllib.request.Request(
            YUNET_DOWNLOAD_URL,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:  # nosec B310
            content = response.read()

        if len(content) <= 5000:
            logger.warning("Download do modelo YuNet retornou conteúdo inesperado (%d bytes).", len(content))
            return None

        if not verify_sha256_bytes(content, YUNET_MODEL_SHA256):
            logger.error(
                "Falha na verificação SHA-256 do modelo YuNet baixado! "
                "Binário rejeitado por segurança."
            )
            return None

        YUNET_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        YUNET_MODEL_PATH.write_bytes(content)
        logger.info("Modelo YuNet baixado e verificado via SHA-256 (%d bytes).", len(content))
        return YUNET_MODEL_PATH
    except Exception as exc:
        logger.warning("Não foi possível baixar/validar o modelo YuNet automaticamente: %s", exc)

    return None


class _FaceDetectorWrapper:
    """
    Detector facial unificado compatível com OpenCV 4.x e OpenCV 5.x.
    Prioriza o maior rosto com fallback seguro caso nenhum detector esteja disponível.
    """

    def __init__(self, target_size: Tuple[int, int] = (640, 360)):
        self.detector_type = "none"
        self.backend = None
        self.target_size = target_size

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
                        top_k=5000,
                    )
                    self.detector_type = "facedetectoryn"
                    logger.info("Detector facial ativo: cv2.FaceDetectorYN (YuNet ONNX).")
                except Exception as exc:
                    logger.warning("Falha ao inicializar cv2.FaceDetectorYN: %s", exc)

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
                getattr(cv2, "__version__", "desconhecido"),
            )

    def is_available(self) -> bool:
        return self.detector_type != "none" and self.backend is not None

    def detect_largest_face(self, frame: np.ndarray, scale_factor: float = 0.5) -> Optional[Tuple[int, int, int, int]]:
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
                    minSize=(int(40 * scale_factor), int(40 * scale_factor)),
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
                for i in range(detections.shape[2]):
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
    window_size: int = 10,
) -> Optional[int]:
    """
    Detecta rostos no trecho com Média Móvel (Moving Average) para estabilizar a janela 9:16.
    Garante liberação determinística do handle cv2.VideoCapture em bloco try/finally.
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

        history_x: deque = deque(maxlen=window_size)
        smoothed_x_list: List[float] = []
        max_crop_x = max(0, orig_w - crop_w)

        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            frame_step = max(1, int(fps * sample_rate_sec))
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, start_time * 1000.0))

            current_sec = start_time
            scale_factor = 0.5

            while current_sec < end_time:
                ret, frame = cap.read()
                if not ret:
                    break

                face = detector.detect_largest_face(frame, scale_factor=scale_factor)
                if face is not None:
                    fx, fy, fw, fh = face
                    face_center_x = fx + (fw / 2.0)
                    target_x = max(0.0, min(float(max_crop_x), face_center_x - (crop_w / 2.0)))

                    if history_x:
                        last_pos = history_x[-1]
                        max_step = float(crop_w * 0.25)
                        if abs(target_x - last_pos) > max_step:
                            target_x = last_pos + max_step if target_x > last_pos else last_pos - max_step

                    history_x.append(target_x)
                    smoothed_x_list.append(sum(history_x) / len(history_x))

                for _ in range(frame_step - 1):
                    if not cap.grab():
                        break
                current_sec += sample_rate_sec
        finally:
            cap.release()

        if not smoothed_x_list:
            logger.info("Nenhum rosto detectado no trecho %.2fs -> %.2fs. Fallback para center crop.", start_time, end_time)
            return None

        final_x = int(round(float(np.median(smoothed_x_list))))
        final_x = max(0, min(max_crop_x, final_x))
        if final_x % 2 != 0:
            final_x = max(0, final_x - 1)

        logger.info("Face tracking OpenCV definiu X=%d suavizado (Janela: %dx%d em %dx%d).", final_x, crop_w, orig_h, orig_w, orig_h)
        return final_x

    except Exception as exc:
        logger.warning("Falha durante o face tracking: %s. Fallback seguro para center crop.", exc)
        return None
