"""Pure numpy pre/post-processing around the YuNet ONNX face detector.

Zero new Python dependencies beyond ``onnxruntime`` and ``numpy`` (both
already transitive dependencies of ``faster-whisper``, pinned explicitly in
the ``analysis`` extra of pyproject.toml). No opencv-python, mediapipe,
dlib or insightface.

The model is ``face_detection_yunet_2023mar.onnx`` from the OpenCV Zoo
(https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet,
MIT-licensed, see ``src/cortex/models/FACE_DETECTION_YUNET_LICENSE``). Its
graph has a *fixed* 640x640 input (no dynamic axes), so unlike the upstream
``yunet.py`` demo (which feeds arbitrary-sized images straight to
``cv.FaceDetectorYN``) we must letterbox every frame into a 640x640 canvas
ourselves before inference, and un-letterbox the decoded coordinates
afterwards. The decode/NMS math below is a line-for-line numpy port of
OpenCV's own postprocessing (``modules/objdetect/src/face_detect.cpp``,
``FaceDetectorYNImpl::postProcess``), not a reimplementation from scratch:
same per-stride grid decode, same score/NMS formulas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

MODEL_INPUT_SIZE = 640
_STRIDES = (8, 16, 32)
_OUTPUT_NAMES = (
    "cls_8", "cls_16", "cls_32",
    "obj_8", "obj_16", "obj_32",
    "bbox_8", "bbox_16", "bbox_32",
    "kps_8", "kps_16", "kps_32",
)

DEFAULT_SCORE_THRESHOLD = 0.6
DEFAULT_NMS_THRESHOLD = 0.3

MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "face_detection_yunet_2023mar.onnx"


class FaceDetectionError(RuntimeError):
    pass


def load_session(model_path: Path | str = MODEL_PATH) -> Any:
    """Create an onnxruntime session pinned to CPU (never CUDA: the GPU is
    reserved for Whisper transcription and NVENC rendering)."""
    try:
        import onnxruntime as ort
    except ImportError as exc:  # pragma: no cover - exercised only w/o the analysis extra
        raise FaceDetectionError(
            "onnxruntime não está instalado; instale o extra 'analysis' do pacote"
        ) from exc
    model_path = Path(model_path)
    if not model_path.exists():
        raise FaceDetectionError(f"modelo YuNet não encontrado em {model_path}")
    return ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])


def _bilinear_resize(image: np.ndarray, new_w: int, new_h: int) -> np.ndarray:
    """Vectorized bilinear resize, HxWx3 uint8 in, HxWx3 uint8 out."""
    height, width = image.shape[:2]
    if new_w == width and new_h == height:
        return image
    x = (np.arange(new_w) + 0.5) * (width / new_w) - 0.5
    y = (np.arange(new_h) + 0.5) * (height / new_h) - 0.5
    x = np.clip(x, 0, width - 1)
    y = np.clip(y, 0, height - 1)
    x0 = np.floor(x).astype(np.int32)
    x1 = np.clip(x0 + 1, 0, width - 1)
    y0 = np.floor(y).astype(np.int32)
    y1 = np.clip(y0 + 1, 0, height - 1)
    wx = (x - x0).reshape(1, -1, 1)
    wy = (y - y0).reshape(-1, 1, 1)
    img = image.astype(np.float32)
    top = img[y0][:, x0] * (1 - wx) + img[y0][:, x1] * wx
    bottom = img[y1][:, x0] * (1 - wx) + img[y1][:, x1] * wx
    return (top * (1 - wy) + bottom * wy).astype(np.uint8)


def letterbox_to_model_input(image_rgb: np.ndarray, target: int = MODEL_INPUT_SIZE) -> tuple[np.ndarray, float]:
    """Resize (preserving aspect ratio) + zero-pad an RGB HxWx3 uint8 frame
    into a ``target``x``target`` canvas anchored at the top-left corner.

    Returns ``(canvas_bgr, scale)``. ``scale`` is the single uniform factor
    applied to both axes — callers must divide decoded pixel coordinates by
    it (no offset subtraction needed, since the image is anchored at (0, 0)
    and only the bottom/right margins are padding) to map back into the
    original frame's pixel space. Getting this division backwards or adding
    a spurious offset is exactly the "bug gera bbox deslocado silenciosamente"
    failure mode this function exists to avoid.
    """
    height, width = image_rgb.shape[:2]
    if height <= 0 or width <= 0:
        raise FaceDetectionError("frame vazio não pode ser processado pelo detector de faces")
    scale = min(target / width, target / height)
    new_w = max(1, round(width * scale))
    new_h = max(1, round(height * scale))
    # YuNet/OpenCV's dnn::blobFromImage assumes BGR (as produced by cv.imread,
    # without swapRB) — our frames arrive as RGB from ffmpeg, so swap here.
    bgr = image_rgb[:, :, ::-1]
    resized = _bilinear_resize(bgr, new_w, new_h)
    canvas = np.zeros((target, target, 3), dtype=np.uint8)
    canvas[:new_h, :new_w] = resized
    return canvas, scale


def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
    """Greedy IoU-based non-max suppression. boxes are (x, y, w, h)."""
    if len(boxes) == 0:
        return []
    x1, y1 = boxes[:, 0], boxes[:, 1]
    x2, y2 = boxes[:, 0] + boxes[:, 2], boxes[:, 1] + boxes[:, 3]
    areas = boxes[:, 2] * boxes[:, 3]
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        iou = inter / (areas[i] + areas[rest] - inter)
        order = rest[iou <= threshold]
    return keep


def decode_outputs(
    outputs: list[np.ndarray],
    *,
    pad_size: int = MODEL_INPUT_SIZE,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    nms_threshold: float = DEFAULT_NMS_THRESHOLD,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decode raw YuNet ONNX outputs (in ``_OUTPUT_NAMES`` order) into
    ``(boxes, scores, landmarks)`` after score filtering and NMS.

    ``boxes`` is Nx4 ``(x1, y1, w, h)`` in the padded model-input pixel
    space (not yet un-letterboxed). ``landmarks`` is Nx10
    ``(re_x, re_y, le_x, le_y, nt_x, nt_y, rcm_x, rcm_y, lcm_x, lcm_y)`` in
    the same space.
    """
    by_name = dict(zip(_OUTPUT_NAMES, outputs))
    all_boxes: list[np.ndarray] = []
    all_scores: list[np.ndarray] = []
    all_kps: list[np.ndarray] = []
    for stride in _STRIDES:
        cols = pad_size // stride
        rows = pad_size // stride
        cls = np.clip(np.asarray(by_name[f"cls_{stride}"]).reshape(-1), 0.0, 1.0)
        obj = np.clip(np.asarray(by_name[f"obj_{stride}"]).reshape(-1), 0.0, 1.0)
        bbox = np.asarray(by_name[f"bbox_{stride}"]).reshape(-1, 4)
        kps = np.asarray(by_name[f"kps_{stride}"]).reshape(-1, 10)

        scores = np.sqrt(cls * obj)
        keep = scores >= score_threshold
        if not np.any(keep):
            continue

        idx = np.arange(rows * cols)[keep]
        r = (idx // cols).astype(np.float32)
        c = (idx % cols).astype(np.float32)
        bbox_k = bbox[keep]
        kps_k = kps[keep]

        cx = (c + bbox_k[:, 0]) * stride
        cy = (r + bbox_k[:, 1]) * stride
        width = np.exp(bbox_k[:, 2]) * stride
        height = np.exp(bbox_k[:, 3]) * stride
        x1 = cx - width / 2.0
        y1 = cy - height / 2.0
        boxes = np.stack([x1, y1, width, height], axis=1)

        landmarks = np.empty_like(kps_k)
        for n in range(5):
            landmarks[:, 2 * n] = (kps_k[:, 2 * n] + c) * stride
            landmarks[:, 2 * n + 1] = (kps_k[:, 2 * n + 1] + r) * stride

        all_boxes.append(boxes)
        all_scores.append(scores[keep])
        all_kps.append(landmarks)

    if not all_boxes:
        return np.empty((0, 4)), np.empty((0,)), np.empty((0, 10))

    boxes = np.concatenate(all_boxes, axis=0)
    scores = np.concatenate(all_scores, axis=0)
    kps = np.concatenate(all_kps, axis=0)
    keep_idx = _nms(boxes, scores, nms_threshold)
    if not keep_idx:
        return np.empty((0, 4)), np.empty((0,)), np.empty((0, 10))
    return boxes[keep_idx], scores[keep_idx], kps[keep_idx]


def detect_faces(
    session: Any,
    image_rgb: np.ndarray,
    *,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    nms_threshold: float = DEFAULT_NMS_THRESHOLD,
) -> list[dict]:
    """Run the full detect pipeline on one RGB HxWx3 uint8 frame.

    Returns a list of dicts with normalized (0-1) ``x, y, width, height,
    score`` and a ``landmarks`` dict of normalized ``(x, y)`` pairs, ready
    to feed into ``FaceDetection``/``FaceLandmarks``.
    """
    height, width = image_rgb.shape[:2]
    canvas, scale = letterbox_to_model_input(image_rgb)
    blob = canvas.astype(np.float32).transpose(2, 0, 1)[None]
    outputs = session.run(None, {"input": blob})
    boxes, scores, kps = decode_outputs(
        outputs, score_threshold=score_threshold, nms_threshold=nms_threshold
    )

    results = []
    for box, score, landmark in zip(boxes, scores, kps):
        x1, y1, w, h = box / scale
        lm = landmark / scale
        results.append({
            "x": float(np.clip(x1 / width, 0.0, 1.0)),
            "y": float(np.clip(y1 / height, 0.0, 1.0)),
            "width": float(np.clip(w / width, 0.0, 1.0)),
            "height": float(np.clip(h / height, 0.0, 1.0)),
            "score": float(np.clip(score, 0.0, 1.0)),
            "landmarks": {
                "right_eye": (float(lm[0] / width), float(lm[1] / height)),
                "left_eye": (float(lm[2] / width), float(lm[3] / height)),
                "nose_tip": (float(lm[4] / width), float(lm[5] / height)),
                "right_mouth_corner": (float(lm[6] / width), float(lm[7] / height)),
                "left_mouth_corner": (float(lm[8] / width), float(lm[9] / height)),
            },
        })
    return results
