from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import numpy as np
import pytest

from cortex.analyze.face_recognition import (
    MODEL_PATH,
    MODEL_SHA256,
    align_face,
    cosine_similarity,
    estimate_similarity_transform,
    extract_embedding,
    load_recognition_session,
)
from cortex.analyze.face_schemas import FaceDetection, FaceLandmarks
from cortex.analyze.face_detect import detect_faces, load_session
from cortex.analyze.face_service import _parse_ppm

FIXTURE = Path(__file__).parent / "fixtures" / "face_golden_portrait.jpg"


def _canonical_face() -> FaceDetection:
    points = [
        (38.2946 / 112, 51.6963 / 112),
        (73.5318 / 112, 51.5014 / 112),
        (56.0252 / 112, 71.7366 / 112),
        (41.5493 / 112, 92.3655 / 112),
        (70.7299 / 112, 92.2041 / 112),
    ]
    return FaceDetection(
        x=0.2, y=0.2, width=0.6, height=0.7, score=1.0,
        landmarks=FaceLandmarks(
            right_eye=points[0], left_eye=points[1], nose_tip=points[2],
            right_mouth_corner=points[3], left_mouth_corner=points[4],
        ),
    )


def test_similarity_transform_recovers_known_mapping() -> None:
    source = np.array([[0, 0], [1, 0], [0, 1], [2, 1], [1, 2]], dtype=float)
    target = source @ np.array([[2.0, 0.5], [-0.5, 2.0]]) + [4.0, 7.0]
    transform = estimate_similarity_transform(source, target)
    predicted = np.c_[source, np.ones(len(source))] @ transform.T

    assert predicted == pytest.approx(target)


def test_sface_model_runs_and_returns_normalized_embedding() -> None:
    y, x = np.indices((112, 112))
    frame = np.stack([x * 2, y * 2, (x + y) % 256], axis=2).astype(np.uint8)
    face = _canonical_face()
    aligned = align_face(frame, face)
    embedding = extract_embedding(load_recognition_session(), frame, face)

    assert aligned[2:-2, 2:-2] == pytest.approx(frame[2:-2, 2:-2], abs=1.0)
    assert len(embedding) == 128
    assert np.linalg.norm(embedding) == pytest.approx(1.0, abs=1e-6)
    assert cosine_similarity(embedding, embedding) == pytest.approx(1.0)


def test_sface_model_and_license_are_pinned() -> None:
    digest = hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()
    license_path = MODEL_PATH.parent / "SFACE_OPENCV_ZOO_LICENSE"

    assert digest == MODEL_SHA256
    assert license_path.exists()
    assert "Apache License" in license_path.read_text(encoding="utf-8")


def test_sface_embedding_from_real_detected_face() -> None:
    decoded = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(FIXTURE),
         "-f", "image2pipe", "-vcodec", "ppm", "-"],
        capture_output=True, check=True, timeout=30,
    )
    frame = _parse_ppm(decoded.stdout)
    detected = detect_faces(load_session(), frame)
    assert len(detected) == 1

    embedding = extract_embedding(
        load_recognition_session(), frame, FaceDetection.model_validate(detected[0])
    )
    assert len(embedding) == 128
    assert np.linalg.norm(embedding) == pytest.approx(1.0, abs=1e-6)
