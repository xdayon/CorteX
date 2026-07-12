from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from cortex.analyze.face_detect import (
    MODEL_PATH,
    decode_outputs,
    detect_faces,
    letterbox_to_model_input,
    load_session,
)

FIXTURE = Path(__file__).parent / "fixtures" / "face_golden_portrait.jpg"


# -- 1. Letterbox -----------------------------------------------------------

def test_letterbox_no_resize_when_already_square_and_fits() -> None:
    image = np.zeros((640, 640, 3), dtype=np.uint8)
    image[:] = (10, 20, 30)
    canvas, scale = letterbox_to_model_input(image, target=640)
    assert scale == 1.0
    assert canvas.shape == (640, 640, 3)
    # RGB->BGR channel swap: (10,20,30) becomes (30,20,10)
    assert tuple(canvas[0, 0]) == (30, 20, 10)


def test_letterbox_pads_short_side_anchored_top_left() -> None:
    # Wide frame (matches ffmpeg's scale=640:-2 for a 16:9 source): width
    # already 640, height < 640 -> only bottom padding, scale stays 1.0.
    image = np.full((360, 640, 3), 200, dtype=np.uint8)
    canvas, scale = letterbox_to_model_input(image, target=640)
    assert scale == 1.0
    assert canvas.shape == (640, 640)[0:1] + (640, 3)
    assert np.all(canvas[:360, :640] == 200)
    assert np.all(canvas[360:, :] == 0)


def test_letterbox_downscales_tall_frame_uniformly() -> None:
    # Portrait frame taller than the model input: both axes must scale by
    # the SAME factor (min(target/w, target/h)) - this is the scale-bug the
    # spec explicitly warns about.
    image = np.full((1280, 640, 3), 128, dtype=np.uint8)
    canvas, scale = letterbox_to_model_input(image, target=640)
    assert scale == pytest.approx(640 / 1280)
    # resized width should be round(640 * scale) = 320, height = 640
    assert np.any(canvas[:, :320] != 0)
    assert np.all(canvas[:, 320:] == 0)


# -- 2. Decode/NMS with synthetic fixed outputs ------------------------------

def _flat_outputs(rows_cols: dict[int, int], score_positions: dict[int, list[tuple[int, float, float]]]):
    """Build fake YuNet ONNX outputs. ``score_positions[stride]`` is a list of
    ``(flat_idx, cls, obj)`` tuples; everything else is zeroed out."""
    outputs = []
    names = ["cls_8", "cls_16", "cls_32", "obj_8", "obj_16", "obj_32",
             "bbox_8", "bbox_16", "bbox_32", "kps_8", "kps_16", "kps_32"]
    per_stride = {}
    for stride, n in rows_cols.items():
        cls = np.zeros((1, n * n, 1), dtype=np.float32)
        obj = np.zeros((1, n * n, 1), dtype=np.float32)
        bbox = np.zeros((1, n * n, 4), dtype=np.float32)
        kps = np.zeros((1, n * n, 10), dtype=np.float32)
        for idx, c, o in score_positions.get(stride, []):
            cls[0, idx, 0] = c
            obj[0, idx, 0] = o
            bbox[0, idx, :] = [0.0, 0.0, 0.0, 0.0]  # zero offsets, unit-ish size
        per_stride[stride] = (cls, obj, bbox, kps)
    for name in names:
        kind, stride = name.rsplit("_", 1)
        stride = int(stride)
        cls, obj, bbox, kps = per_stride[stride]
        outputs.append({"cls": cls, "obj": obj, "bbox": bbox, "kps": kps}[kind])
    return outputs


def test_decode_outputs_filters_by_score_threshold() -> None:
    # stride 32 grid is 640/32 = 20x20 = 400 priors; put one detection at idx 0.
    outputs = _flat_outputs(
        {8: 80, 16: 40, 32: 20},
        {32: [(0, 0.9, 0.9)]},
    )
    boxes, scores, kps = decode_outputs(outputs, score_threshold=0.6, nms_threshold=0.3)
    assert len(boxes) == 1
    assert scores[0] == pytest.approx(0.9, abs=1e-5)
    # r=c=0 at stride 32: cx=(0+0)*32=0, cy=0, w=exp(0)*32=32, h=32 -> x1=-16, y1=-16
    assert boxes[0] == pytest.approx([-16.0, -16.0, 32.0, 32.0])


def test_decode_outputs_below_threshold_returns_empty() -> None:
    outputs = _flat_outputs({8: 80, 16: 40, 32: 20}, {32: [(0, 0.3, 0.3)]})
    boxes, scores, kps = decode_outputs(outputs, score_threshold=0.6, nms_threshold=0.3)
    assert len(boxes) == 0
    assert len(scores) == 0
    assert len(kps) == 0


def test_decode_outputs_nms_suppresses_overlapping_duplicate() -> None:
    # Two distinct grid priors (idx 0 and 1 on stride 32, i.e. r=0/c=0 and
    # r=0/c=1) whose bbox regression is crafted so they decode to the exact
    # same box (full overlap) - NMS must keep only the higher-scoring one.
    n = 20
    cls = np.zeros((1, n * n, 1), dtype=np.float32)
    obj = np.zeros((1, n * n, 1), dtype=np.float32)
    bbox = np.zeros((1, n * n, 4), dtype=np.float32)
    kps = np.zeros((1, n * n, 10), dtype=np.float32)
    cls[0, 0, 0] = 0.9
    obj[0, 0, 0] = 0.9
    cls[0, 1, 0] = 0.7
    obj[0, 1, 0] = 0.7
    # idx 1 has c=1: shift bbox_x by -1 so cx = (1 + (-1)) * 32 == 0, same as idx 0.
    bbox[0, 1, 0] = -1.0

    zeros80 = np.zeros((1, 6400, 1), dtype=np.float32)
    zeros40 = np.zeros((1, 1600, 1), dtype=np.float32)
    zeros_bbox80 = np.zeros((1, 6400, 4), dtype=np.float32)
    zeros_bbox40 = np.zeros((1, 1600, 4), dtype=np.float32)
    zeros_kps80 = np.zeros((1, 6400, 10), dtype=np.float32)
    zeros_kps40 = np.zeros((1, 1600, 10), dtype=np.float32)

    outputs = [
        zeros80, zeros40, cls,
        zeros80, zeros40, obj,
        zeros_bbox80, zeros_bbox40, bbox,
        zeros_kps80, zeros_kps40, kps,
    ]
    boxes, scores, _kps = decode_outputs(outputs, score_threshold=0.6, nms_threshold=0.3)
    assert len(boxes) == 1
    assert scores[0] == pytest.approx(0.9, abs=1e-5)


# -- 3. Golden real-image detection ------------------------------------------

@pytest.mark.skipif(not FIXTURE.exists(), reason="golden face fixture missing")
def test_detect_faces_on_golden_portrait() -> None:
    session = load_session()
    image = _read_fixture_rgb(FIXTURE)
    faces = detect_faces(session, image)
    assert len(faces) == 1
    face = faces[0]
    assert face["score"] > 0.8
    # Known approximate bbox (normalized), computed once against this exact
    # fixture + model; generous tolerance protects against tiny numerical
    # drift while still catching a letterbox/scale regression.
    assert face["x"] == pytest.approx(0.366, abs=0.05)
    assert face["y"] == pytest.approx(0.068, abs=0.05)
    assert face["width"] == pytest.approx(0.244, abs=0.05)
    assert face["height"] == pytest.approx(0.272, abs=0.05)


def _read_fixture_rgb(path: Path) -> np.ndarray:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    result = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-f", "image2pipe", "-vcodec", "ppm", "-"],
        capture_output=True, check=True, timeout=30,
    )
    data = result.stdout
    idx = 2
    values: list[int] = []
    while len(values) < 3:
        while data[idx : idx + 1].isspace():
            idx += 1
        start = idx
        while not data[idx : idx + 1].isspace():
            idx += 1
        values.append(int(data[start:idx]))
    idx += 1
    width, height, _ = values
    pixels = data[idx : idx + width * height * 3]
    return np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, 3)


def test_model_file_present_with_license() -> None:
    assert MODEL_PATH.exists()
    license_path = MODEL_PATH.parent / "FACE_DETECTION_YUNET_LICENSE"
    assert license_path.exists()
    assert "MIT" in license_path.read_text(encoding="utf-8")
