from __future__ import annotations

import threading
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps


EMBEDDING_MODEL = "sface-2021dec"
_MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
_DETECTOR_PATH = _MODEL_DIR / "face_detection_yunet_2023mar.onnx"
_RECOGNIZER_PATH = _MODEL_DIR / "face_recognition_sface_2021dec.onnx"
_ENGINE_LOCK = threading.RLock()
_detector = None
_recognizer = None


@dataclass
class LocatedFace:
    embedding: np.ndarray
    box: tuple[int, int, int, int]  # top, right, bottom, left
    detection_score: float


def load_rgb(data: bytes) -> np.ndarray:
    try:
        image = ImageOps.exif_transpose(Image.open(BytesIO(data))).convert("RGB")
    except Exception as exc:
        raise ValueError("The uploaded file is not a readable image") from exc
    # CCTV frames do not benefit from unlimited resolution, and this bound keeps
    # CPU use predictable while retaining enough detail for faces at a distance.
    image.thumbnail((1600, 1600))
    return np.asarray(image)


def _load_models():
    global _detector, _recognizer
    if _detector is not None and _recognizer is not None:
        return _detector, _recognizer
    missing = [str(path) for path in (_DETECTOR_PATH, _RECOGNIZER_PATH) if not path.is_file()]
    if missing:
        raise RuntimeError(f"Face model file is missing: {', '.join(missing)}")
    _detector = cv2.FaceDetectorYN.create(
        str(_DETECTOR_PATH), "", (320, 320), 0.75, 0.3, 5000
    )
    _recognizer = cv2.FaceRecognizerSF.create(str(_RECOGNIZER_PATH), "")
    return _detector, _recognizer


def _normalize(value: np.ndarray) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-12:
        raise ValueError("The face could not be encoded; try a sharper photo")
    return vector / norm


def normalize_embedding(value: np.ndarray) -> np.ndarray:
    return _normalize(value)


def _detect_and_encode(rgb: np.ndarray, minimum_score: float) -> list[LocatedFace]:
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    height, width = bgr.shape[:2]
    if height < 20 or width < 20:
        return []

    with _ENGINE_LOCK:
        detector, recognizer = _load_models()
        detector.setInputSize((width, height))
        _, rows = detector.detect(bgr)
        output: list[LocatedFace] = []
        if rows is None:
            return output
        for face in rows:
            score = float(face[14])
            if score < minimum_score:
                continue
            aligned = recognizer.alignCrop(bgr, face)
            feature = recognizer.feature(aligned)
            embedding = _normalize(feature)
            x, y, box_width, box_height = (int(round(v)) for v in face[:4])
            top = max(0, y)
            left = max(0, x)
            bottom = min(height, y + box_height)
            right = min(width, x + box_width)
            if bottom > top and right > left:
                output.append(LocatedFace(embedding, (top, right, bottom, left), score))
        return output


def locate_faces(data: bytes) -> tuple[np.ndarray, list[LocatedFace]]:
    rgb = load_rgb(data)
    return rgb, _detect_and_encode(rgb, minimum_score=0.85)


def one_face_embedding(data: bytes) -> np.ndarray:
    rgb = load_rgb(data)
    faces = _detect_and_encode(rgb, minimum_score=0.75)
    # Enrollment is allowed a larger second pass because it is infrequent and
    # uploaded photos sometimes contain a small face.
    if not faces and max(rgb.shape[:2]) < 2000:
        scale = min(2.0, 2000.0 / max(rgb.shape[:2]))
        enlarged = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        faces = _detect_and_encode(enlarged, minimum_score=0.65)
    if not faces:
        raise ValueError("No face was found in the image")
    if len(faces) > 1:
        raise ValueError("Use a photo containing exactly one face")
    return faces[0].embedding


def pack_embedding(value: np.ndarray) -> bytes:
    return _normalize(value).astype(np.float32).tobytes()


def unpack_embedding(value: bytes) -> np.ndarray:
    return _normalize(np.frombuffer(value, dtype=np.float32))


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(_normalize(a), _normalize(b)))


def confidence_from_similarity(value: float, threshold: float) -> float:
    # Map a threshold-level match to a conservative 50% and a perfect cosine
    # match to 100%. Scores below threshold are never reported as identities.
    scaled = 0.5 + 0.5 * (value - threshold) / max(1.0 - threshold, 0.001)
    return max(0.0, min(1.0, scaled))
