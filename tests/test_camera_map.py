from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from asgi_client import ASGITestClient

from cortex.analyze import camera_map
from cortex.analyze.camera_map import (
    CameraMapCancelled, CameraMapDocument, build_camera_map, cluster, extract_keyframes,
    face_count, subject_voice_share, suggest_role,
)
from cortex.api import create_app
from cortex.domain.models import SourceAsset, SourceKind
from cortex.domain.store import DomainStore
from cortex.ingest.ffprobe import probe_media
from cortex.jobs import JobStore
from cortex.schemas import JobType
from cortex.worker import _HANDLERS, process_next
from test_render import _config

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg required")


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> Path:
    """A,B,A,C of 2 s each (blue, test pattern, blue, orange) with a keyframe every second."""
    path = tmp_path_factory.mktemp("camera-map") / "abac.mp4"
    inputs = ["color=c=0x2060c0:s=320x180:r=25:d=2", "testsrc2=s=320x180:r=25:d=2",
              "color=c=0x2060c0:s=320x180:r=25:d=2", "color=c=0xe08020:s=320x180:r=25:d=2"]
    command = [FFMPEG, "-y", "-v", "error"]
    for item in inputs:
        command += ["-f", "lavfi", "-i", item]
    command += ["-filter_complex", "[0][1][2][3]concat=n=4:v=1:a=0,format=yuv420p", "-c:v", "libx264",
                "-sc_threshold", "0", "-force_key_frames", "0,1,2,3,4,5,6,7", str(path)]
    subprocess.run(command, check=True, timeout=120)
    return path


def never() -> bool:
    return False


def test_extract_keyframes_and_grouping(video):
    times, features = extract_keyframes(FFMPEG, video, should_cancel=never)
    # Discarding non-key packets in the demuxer shifts H.264 keyframe times by up to a
    # few frames (B-frame reorder); AV1/VP9 masters keep exact times.
    assert len(times) == 8 and np.allclose(times, np.arange(8), atol=0.15)
    airtime = np.diff(np.append(times, 8.0))
    labels, centers = camera_map.group_keyframes(features, airtime)
    assert len(centers) == 3
    assert list(labels[:2]) == list(labels[4:6]) == [0, 0]
    assert labels[2] == labels[3] != labels[6] == labels[7]
    shots = camera_map.build_shots(times, labels, 8.0)
    assert [(round(s.start), round(s.end)) for s in shots] == [(0, 2), (2, 4), (4, 6), (6, 8)]
    assert [s.group for s in shots][0] == [s.group for s in shots][2] == 0
    assert abs(sum(airtime[labels == 0]) - 4.0) < 0.1


def test_extract_keyframes_cancel_and_failure(video, tmp_path):
    with pytest.raises(CameraMapCancelled):
        extract_keyframes(FFMPEG, video, should_cancel=lambda: True)
    with pytest.raises(camera_map.CameraMapError):
        extract_keyframes(FFMPEG, tmp_path / "missing.mp4", should_cancel=never)


def test_extract_keyframes_times_out_an_ffmpeg_that_stops_writing(tmp_path):
    stalled = tmp_path / "ffmpeg"
    stalled.write_text("#!/bin/sh\nexec sleep 30\n")
    stalled.chmod(0o755)
    started = time.monotonic()
    with pytest.raises(camera_map.CameraMapError, match="timeout"):
        extract_keyframes(stalled, tmp_path / "x.mp4", should_cancel=never, timeout=0.5)
    assert time.monotonic() - started < 10


def test_face_count_does_not_double_people_who_move_between_samples():
    def face(x, height=0.2, score=0.9):
        return {"x": x, "y": 0.2, "width": 0.1, "height": height, "score": score}
    samples = [[face(0.20), face(0.60)], [face(0.32), face(0.72)], [face(0.25), face(0.66)]]
    assert face_count(samples) == 2
    assert face_count([[face(0.2)], [], [face(0.2)]]) == 1
    assert face_count([]) == 0
    # Wide shots: small faces still count; weak or tiny detections do not.
    assert face_count([[face(0.1, 0.05), face(0.5, 0.06), face(0.8, 0.04), face(0.9, 0.02), face(0.3, 0.1, 0.4)]]) == 3


def test_nearest_matches_the_naive_distance():
    rng = np.random.default_rng(3)
    features, centers = rng.normal(size=(50, 16)), rng.normal(size=(6, 16))
    naive = np.argmin(((features[:, None, :] - centers[None]) ** 2).sum(axis=2), axis=1)
    assert (camera_map.nearest(features, centers) == naive).all()


def test_extract_keyframes_refuses_a_master_with_a_keyframe_on_every_frame(video):
    with pytest.raises(camera_map.CameraMapError, match="keyframes demais"):
        extract_keyframes(FFMPEG, video, should_cancel=never, max_frames=2)


def test_cluster_is_deterministic():
    rng = np.random.default_rng(1)
    features = np.vstack([rng.normal(0, 0.05, (20, 8)), rng.normal(3, 0.05, (20, 8))])
    first, centers = cluster(features)
    second, _ = cluster(features)
    assert (first == second).all() and len(centers) == 2


@pytest.mark.parametrize("faces,share,expected", [
    (0, 0.9, "ignore"), (0, None, "ignore"), (3, None, "wide"), (4, 0.1, "wide"), (1, None, None),
    (1, 0.6, "subject"), (1, 0.9, "subject"), (2, 0.8, "subject"), (3, 0.9, "subject"),
    (1, 0.5, None), (1, 0.4, None), (1, 0.35, "interviewers"),
    # Two faces: two hosts (low share) or the subject beside a host (wk7PY31TQBc two-shots).
    (2, 0.05, "interviewers"), (2, 0.12, "interviewers"), (2, 0.136, None), (2, 0.2, None),
    (2, 0.32, "subject"), (2, 0.53, "subject"),
])
def test_suggest_role(faces, share, expected):
    # The subject's voice decides first; face counts only flag graphics and wide shots.
    assert suggest_role(faces, share) == expected


def test_suggest_role_ignores_rare_groups():
    assert suggest_role(2, 0.1, airtime_share=0.004) == "ignore"


def test_subject_voice_share():
    turns = [SimpleNamespace(start=0, end=4, speaker="protagonista"), SimpleNamespace(start=4, end=6, speaker="outros")]
    assert subject_voice_share([(0, 6)], turns) == pytest.approx(4 / 6, abs=1e-3)
    assert subject_voice_share([(5, 6), (1, 2)], turns) == pytest.approx(0.5)
    assert subject_voice_share([(10, 12)], turns) is None
    assert subject_voice_share([(0, 6)], []) is None


def _setup(tmp_path, video):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    jobs = JobStore(config.paths.database)
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Camera map")
    stored = tmp_path / "abac.mp4"
    shutil.copyfile(video, stored)
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, original_filename=stored.name, stored_path=stored.name,
        sha256=hashlib.sha256(stored.read_bytes()).hexdigest(), size_bytes=stored.stat().st_size,
        probe=probe_media(config.render.ffprobe, stored),
    ))
    return config, jobs, domain, source


@pytest.fixture
def model_free(monkeypatch):
    monkeypatch.setattr(camera_map, "load_session", lambda: object())
    monkeypatch.setattr(camera_map, "_extract_frame_rgb", lambda *_a: np.zeros((4, 4, 3), dtype=np.uint8))
    monkeypatch.setattr(camera_map, "detect_faces", lambda *_a: [{"x": 0.4, "width": 0.1, "height": 0.2, "score": 0.9}])
    monkeypatch.setattr("cortex.diarize.voice_match.load_profile", lambda _config: None)


def test_build_caches_and_persists(tmp_path, video, model_free, monkeypatch):
    config, _jobs, domain, source = _setup(tmp_path, video)
    artifact = build_camera_map(config, domain, source, progress_cb=lambda *_: None, should_cancel=never)
    document = CameraMapDocument.model_validate_json(Path(artifact.path).read_text())
    assert artifact.stage == "camera_map" and artifact.metadata["groups"] == 3
    assert document.keyframe_count == 8 and len(document.shots) == 4
    assert document.engine["voice"]["status"] == "unavailable" and document.voice_artifact_id is None
    assert [g.id for g in document.groups] == [0, 1, 2] and document.groups[0].airtime_share == pytest.approx(0.5, abs=0.02)
    assert all(g.suggested_role is None and (config.paths.data_dir / g.thumbnail_path).is_file() for g in document.groups)

    def fail(*_a, **_k):
        raise AssertionError("ffmpeg must not run on a cache hit")
    monkeypatch.setattr(camera_map, "extract_keyframes", fail)
    monkeypatch.setattr(camera_map, "_write_thumbnail", fail)
    again = build_camera_map(config, domain, source, progress_cb=lambda *_: None, should_cancel=never)
    assert again.id == artifact.id


def test_api_cameras_flow(tmp_path, video, model_free):
    config, jobs, domain, source = _setup(tmp_path, video)
    client = ASGITestClient(create_app(config))
    episode = next(e for e in client.get("/api/v1/episodes").json() if source.project_id in e["project_ids"])
    base = f"/api/v1/episodes/{episode['id']}/cameras"
    never = client.get(base)
    assert never.status_code == 200 and never.json() == {"artifact_id": None, "document": None, "roles": None, "job": None}
    queued = client.post(base)
    assert queued.status_code == 200 and queued.json()["type"] == "camera_map"
    assert client.post(base).json()["id"] == queued.json()["id"]
    pending = client.get(base)
    assert pending.status_code == 200 and pending.json()["job"]["id"] == queued.json()["id"]
    assert JobType.CAMERA_MAP in _HANDLERS
    assert process_next(config, jobs, domain, None)
    done = jobs.get(queued.json()["id"])
    assert done.status.value == "succeeded", done.error
    assert done.result["groups"] == 3 and done.result["voice"] == "unavailable"
    artifact_id = done.result["camera_map_artifact_id"]

    body = client.get(base).json()
    assert body["artifact_id"] == artifact_id and body["roles"] is None and body["job"] is None
    assert body["document"]["keyframe_count"] == 8 and body["document"]["shots"] == 4
    first = body["document"]["groups"][0]
    assert "thumbnail_path" not in first
    thumb = client.get(first["thumbnail_url"])
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    assert client.get(f"{base}/{artifact_id}/groups/99/thumbnail").status_code == 404

    put = lambda payload: client.request("PUT", f"{base}/roles", json=payload)  # noqa: E731
    assert put({"artifact_id": artifact_id, "roles": {"0": "boss"}}).status_code == 422
    assert put({"artifact_id": artifact_id, "roles": {"9": "wide"}}).status_code == 400
    assert put({"artifact_id": "missing", "roles": {"0": "wide"}}).status_code == 400
    ok = put({"artifact_id": artifact_id, "roles": {"0": "wide", "1": "subject", "2": "ignore"}})
    assert ok.status_code == 200 and ok.json()["camera_roles"]["artifact_id"] == artifact_id
    assert client.get(base).json()["roles"] == {"0": "wide", "1": "subject", "2": "ignore"}
