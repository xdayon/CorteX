from __future__ import annotations

import numpy as np

from cortex.analyze.face_schemas import FaceDetection


def frame_metrics(frame: np.ndarray) -> tuple[float, float, float]:
    """Return mean luma, black-pixel ratio, and Laplacian variance."""
    rgb = frame.astype(np.float32)
    luma = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    mean_luma = float(np.mean(luma))
    black_ratio = float(np.mean(luma <= 16.0))
    if min(luma.shape) < 3:
        blur_score = 0.0
    else:
        center = luma[1:-1, 1:-1]
        laplacian = (
            luma[:-2, 1:-1] + luma[2:, 1:-1]
            + luma[1:-1, :-2] + luma[1:-1, 2:] - 4.0 * center
        )
        blur_score = float(np.var(laplacian))
    return mean_luma, black_ratio, blur_score


def normalized_frame_delta(previous: np.ndarray, current: np.ndarray) -> float:
    if previous.shape != current.shape:
        return 1.0
    return float(np.mean(np.abs(current.astype(np.float32) - previous.astype(np.float32))) / 255.0)


def face_touches_edge(face: FaceDetection, margin: float) -> bool:
    return (
        face.x <= margin
        or face.y <= margin
        or face.x + face.width >= 1.0 - margin
        or face.y + face.height >= 1.0 - margin
    )
