from __future__ import annotations

import numpy as np

MOUTH_PATCH_HEIGHT = 24
MOUTH_PATCH_WIDTH = 48


def _face_value(face: object, name: str):
    return getattr(face, name) if hasattr(face, name) else face[name]  # type: ignore[index]


def _landmark(face: object, name: str) -> tuple[float, float]:
    landmarks = _face_value(face, "landmarks")
    value = getattr(landmarks, name) if hasattr(landmarks, name) else landmarks[name]
    return float(value[0]), float(value[1])


def normalized_patch(
    patch: np.ndarray, *, out_height: int = MOUTH_PATCH_HEIGHT, out_width: int = MOUTH_PATCH_WIDTH
) -> np.ndarray:
    """Resize a crop with nearest-neighbour sampling and normalize luminance."""
    if patch.size == 0:
        return np.zeros((out_height, out_width), dtype=np.float32)
    gray = patch.astype(np.float32)
    if gray.ndim == 3:
        gray = 0.299 * gray[..., 0] + 0.587 * gray[..., 1] + 0.114 * gray[..., 2]
    ys = np.linspace(0, gray.shape[0] - 1, out_height).round().astype(int)
    xs = np.linspace(0, gray.shape[1] - 1, out_width).round().astype(int)
    resized = gray[np.ix_(ys, xs)]
    median = float(np.median(resized))
    deviation = float(np.median(np.abs(resized - median)))
    scale = max(1.4826 * deviation, 6.0)
    return np.clip((resized - median) / scale, -3.0, 3.0).astype(np.float32)


def _crop(frame: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    height, width = frame.shape[:2]
    left = max(0, min(width - 1, int(round(x0 * width))))
    top = max(0, min(height - 1, int(round(y0 * height))))
    right = max(left + 1, min(width, int(round(x1 * width))))
    bottom = max(top + 1, min(height, int(round(y1 * height))))
    return frame[top:bottom, left:right]


def mouth_patch(frame: np.ndarray, face: object) -> np.ndarray:
    right = _landmark(face, "right_mouth_corner")
    left = _landmark(face, "left_mouth_corner")
    center_x = (right[0] + left[0]) / 2.0
    center_y = (right[1] + left[1]) / 2.0
    mouth_width = max(abs(left[0] - right[0]), float(_face_value(face, "width")) * 0.18)
    return normalized_patch(
        _crop(
            frame,
            center_x - mouth_width * 0.75,
            center_y - mouth_width * 0.35,
            center_x + mouth_width * 0.75,
            center_y + mouth_width * 0.55,
        )
    )


def _control_patch(frame: np.ndarray, face: object) -> np.ndarray:
    right_eye = _landmark(face, "right_eye")
    left_eye = _landmark(face, "left_eye")
    nose = _landmark(face, "nose_tip")
    eye_width = max(abs(left_eye[0] - right_eye[0]), float(_face_value(face, "width")) * 0.18)
    center_x = (right_eye[0] + left_eye[0] + nose[0]) / 3.0
    center_y = (right_eye[1] + left_eye[1] + nose[1]) / 3.0
    return normalized_patch(
        _crop(frame, center_x - eye_width * 0.7, center_y - eye_width * 0.45,
              center_x + eye_width * 0.7, center_y + eye_width * 0.45)
    )


def compensated_motion(
    previous_frame: np.ndarray, current_frame: np.ndarray, previous_face: object, current_face: object
) -> float:
    """Return mouth motion after subtracting photometric/global facial motion."""
    mouth_delta = float(np.mean(np.abs(
        mouth_patch(current_frame, current_face) - mouth_patch(previous_frame, previous_face)
    )))
    control_delta = float(np.mean(np.abs(
        _control_patch(current_frame, current_face) - _control_patch(previous_frame, previous_face)
    )))
    return float(np.clip(mouth_delta - 0.7 * control_delta, 0.0, 1.0))


def smooth_scores(scores: list[float]) -> list[float]:
    if len(scores) < 3:
        return [float(np.clip(value, 0.0, 1.0)) for value in scores]
    padded = [scores[0], *scores, scores[-1]]
    return [float(np.median(padded[index:index + 3])) for index in range(len(scores))]


def select_speaker(
    scores: dict[str, float], *, threshold: float = 0.08, margin: float = 0.03
) -> tuple[str, str | None, float]:
    if not scores:
        return "unknown", None, 0.0
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    winner, best = ordered[0]
    runner_up = ordered[1][1] if len(ordered) > 1 else 0.0
    best = float(np.clip(best, 0.0, 1.0))
    if best < threshold:
        return "unknown", None, float(np.clip((threshold - best) / max(threshold, 1e-6), 0.0, 1.0))
    if len(ordered) > 1 and best - runner_up < margin:
        return "overlap", None, float(np.clip(1.0 - (best - runner_up) / max(margin, 1e-6), 0.0, 1.0))
    confidence = min(1.0, 0.5 + (best - threshold) + max(0.0, best - runner_up))
    return "speaker", winner, confidence
