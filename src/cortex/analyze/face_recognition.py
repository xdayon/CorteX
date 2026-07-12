from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from cortex.analyze.face_schemas import FaceDetection

MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "face_recognition_sface_2021dec.onnx"
MODEL_SHA256 = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
COSINE_MATCH_THRESHOLD = 0.363
EMBEDDING_DIMENSION = 128
ALIGNED_SIZE = 112
_CANONICAL_LANDMARKS = np.array([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041],
], dtype=np.float64)


class FaceRecognitionError(RuntimeError):
    pass


def load_recognition_session(model_path: Path = MODEL_PATH) -> Any:
    if not model_path.exists():
        raise FaceRecognitionError(f"modelo SFace não encontrado em {model_path}")
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise FaceRecognitionError(
            "onnxruntime não está instalado; instale o extra 'analysis'"
        ) from exc
    options = ort.SessionOptions()
    options.log_severity_level = 3
    try:
        return ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
    except Exception as exc:  # noqa: BLE001 - normalized boundary for model/runtime errors
        raise FaceRecognitionError(f"falha ao carregar modelo SFace: {exc}") from exc


def _landmark_points(face: FaceDetection, width: int, height: int) -> np.ndarray:
    landmarks = face.landmarks
    return np.array([
        [landmarks.right_eye[0] * width, landmarks.right_eye[1] * height],
        [landmarks.left_eye[0] * width, landmarks.left_eye[1] * height],
        [landmarks.nose_tip[0] * width, landmarks.nose_tip[1] * height],
        [landmarks.right_mouth_corner[0] * width, landmarks.right_mouth_corner[1] * height],
        [landmarks.left_mouth_corner[0] * width, landmarks.left_mouth_corner[1] * height],
    ], dtype=np.float64)


def estimate_similarity_transform(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    if source.shape != (5, 2) or target.shape != (5, 2):
        raise FaceRecognitionError("alinhamento SFace exige cinco landmarks 2-D")
    rows = []
    values = []
    for (x, y), (u, v) in zip(source, target):
        rows.extend(([x, -y, 1.0, 0.0], [y, x, 0.0, 1.0]))
        values.extend((u, v))
    parameters, _residuals, rank, _singular = np.linalg.lstsq(
        np.asarray(rows), np.asarray(values), rcond=None
    )
    if rank < 4:
        raise FaceRecognitionError("landmarks degenerados para alinhamento SFace")
    a, b, tx, ty = parameters
    return np.array([[a, -b, tx], [b, a, ty]], dtype=np.float64)


def align_face(frame_rgb: np.ndarray, face: FaceDetection) -> np.ndarray:
    height, width = frame_rgb.shape[:2]
    transform = estimate_similarity_transform(
        _landmark_points(face, width, height), _CANONICAL_LANDMARKS
    )
    homogeneous = np.vstack([transform, [0.0, 0.0, 1.0]])
    inverse = np.linalg.inv(homogeneous)
    ys, xs = np.indices((ALIGNED_SIZE, ALIGNED_SIZE), dtype=np.float64)
    destination = np.stack([xs.ravel(), ys.ravel(), np.ones(xs.size)])
    source = inverse @ destination
    source_x = source[0].reshape(ALIGNED_SIZE, ALIGNED_SIZE)
    source_y = source[1].reshape(ALIGNED_SIZE, ALIGNED_SIZE)
    x0 = np.floor(source_x).astype(int)
    y0 = np.floor(source_y).astype(int)
    x1 = x0 + 1
    y1 = y0 + 1
    valid = (x0 >= 0) & (y0 >= 0) & (x1 < width) & (y1 < height)
    output = np.zeros((ALIGNED_SIZE, ALIGNED_SIZE, 3), dtype=np.float32)
    if np.any(valid):
        dx = source_x[valid] - x0[valid]
        dy = source_y[valid] - y0[valid]
        top = (
            frame_rgb[y0[valid], x0[valid]].astype(np.float32) * (1.0 - dx[:, None])
            + frame_rgb[y0[valid], x1[valid]].astype(np.float32) * dx[:, None]
        )
        bottom = (
            frame_rgb[y1[valid], x0[valid]].astype(np.float32) * (1.0 - dx[:, None])
            + frame_rgb[y1[valid], x1[valid]].astype(np.float32) * dx[:, None]
        )
        output[valid] = top * (1.0 - dy[:, None]) + bottom * dy[:, None]
    return output


def extract_embedding(session: Any, frame_rgb: np.ndarray, face: FaceDetection) -> list[float]:
    aligned = align_face(frame_rgb, face)
    blob = aligned.transpose(2, 0, 1)[None].astype(np.float32)
    try:
        raw = np.asarray(session.run(["fc1"], {"data": blob})[0], dtype=np.float32).reshape(-1)
    except Exception as exc:  # noqa: BLE001 - normalized boundary for runtime failures
        raise FaceRecognitionError(f"inferência SFace falhou: {exc}") from exc
    if raw.size != EMBEDDING_DIMENSION or not np.all(np.isfinite(raw)):
        raise FaceRecognitionError("SFace retornou embedding inválido")
    norm = float(np.linalg.norm(raw))
    if norm <= 1e-12:
        raise FaceRecognitionError("SFace retornou embedding sem norma")
    return (raw / norm).astype(float).tolist()


def cosine_similarity(first: list[float], second: list[float]) -> float:
    a = np.asarray(first, dtype=np.float64)
    b = np.asarray(second, dtype=np.float64)
    if a.shape != b.shape or a.size == 0:
        raise FaceRecognitionError("embeddings incompatíveis")
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator <= 1e-12:
        raise FaceRecognitionError("embedding sem norma")
    return float(np.clip(np.dot(a, b) / denominator, -1.0, 1.0))
