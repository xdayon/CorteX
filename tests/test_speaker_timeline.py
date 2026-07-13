from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from cortex.analyze.face_schemas import FaceIndexDocument
from cortex.analyze.scene_schemas import SceneIndexDocument
from cortex.analyze.schemas import AnalysisDocument
from cortex.analyze.speaker_schemas import SpeakerObservation, SpeakerTimelineDocument
from cortex.analyze.speaker_service import (
    SpeakerTimelinePreconditionError,
    SpeakerTimelineService,
    _coalesced_vad_chunks,
    _iter_chunk_frames,
    _segments_from_observations,
)
from cortex.api import create_app
from cortex.config import CortexConfig, load_config
from cortex.domain.models import SourceAsset, SourceKind, StageArtifact
from cortex.domain.store import DomainStore
from cortex.jobs import JobStore
from cortex.schemas import JobStatus
from cortex.worker import process_next

from conftest import FakeTranscriptionEngine


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_streaming_decoder_yields_scaled_timestamped_frames(tmp_path: Path) -> None:
    media_path = tmp_path / "stream.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc=size=640x360:rate=10:duration=1", "-pix_fmt", "yuv420p",
        str(media_path),
    ], check=True, timeout=30)

    frames = list(_iter_chunk_frames(
        "ffmpeg", media_path, start=0.2, end=0.8, sample_fps=4.0,
        should_cancel=lambda: False,
    ))

    assert len(frames) in {2, 3}
    assert frames[0][0] == pytest.approx(0.2)
    assert frames[0][1].shape[1] == 320
    assert frames[0][1].shape[2] == 3


def _config(tmp_path: Path) -> CortexConfig:
    base = load_config()
    return base.model_copy(update={
        "paths": base.paths.model_copy(update={
            "data_dir": tmp_path,
            "projects_dir": tmp_path / "projects",
            "cache_dir": tmp_path / "cache",
            "output_dir": tmp_path / "output",
            "database": tmp_path / "cortex.sqlite3",
        }),
    })


def _write_artifact(
    domain: DomainStore, project_id: str, stage: str, path: Path, document: object,
) -> StageArtifact:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = document.model_dump(mode="json")  # type: ignore[attr-defined]
    path.write_text(json.dumps(payload), encoding="utf-8")
    return domain.create_stage_artifact(StageArtifact(
        project_id=project_id,
        stage=stage,
        path=str(path),
        input_hash=f"{stage}-input-hash",
    ))


def _upstream_fixture(tmp_path: Path, *, with_vad: bool = False):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Speaker timeline fixture")
    source_path = config.paths.projects_dir / project.id / "source" / "episode.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"not decoded when VAD is empty")
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id,
        kind=SourceKind.UPLOAD,
        original_filename="episode.mp4",
        stored_path=str(source_path.relative_to(config.paths.data_dir)),
        sha256="source-sha256",
        size_bytes=source_path.stat().st_size,
    ))

    scene_document = SceneIndexDocument.model_validate({
        "project_id": project.id,
        "source_asset_id": source.id,
        "source_sha256": source.sha256,
        "input_hash": "scene-document-hash",
        "duration_seconds": 4.0,
        "cuts": [],
        "scenes": [{"index": 0, "start": 0.0, "end": 4.0}],
        "cut_count": 0,
        "engine": {
            "ffmpeg_path": "ffmpeg", "ffmpeg_version": "test", "filter": "scdet",
            "threshold_requested": 10.0, "threshold_effective": 10.0,
        },
    })
    scene_artifact = _write_artifact(
        domain, project.id, "scene_index", tmp_path / "upstream" / "scenes.json", scene_document,
    )
    face_document = FaceIndexDocument.model_validate({
        "project_id": project.id,
        "source_asset_id": source.id,
        "source_sha256": source.sha256,
        "scene_index_artifact_id": scene_artifact.id,
        "input_hash": "face-document-hash",
        "duration_seconds": 4.0,
        "frames": [], "scenes": [], "frame_count": 0,
        "engine": {
            "detector": "test", "model_path": "test.onnx", "providers": ["CPUExecutionProvider"],
            "score_threshold": 0.8, "nms_threshold": 0.3, "sample_fps": 1.0,
            "ffmpeg_path": "ffmpeg", "ffmpeg_version": "test",
        },
    })
    face_artifact = _write_artifact(
        domain, project.id, "face_index", tmp_path / "upstream" / "faces.json", face_document,
    )
    vad = ([{
        "start": 0.5, "end": 2.5, "duration": 2.0,
        "start_sample": 8000, "end_sample": 40000,
    }] if with_vad else [])
    analysis_document = AnalysisDocument.model_validate({
        "project_id": project.id,
        "source_asset_id": source.id,
        "transcript_artifact_id": "transcript-id",
        "input_hash": "analysis-document-hash",
        "normalized_audio_sha256": "audio-sha256",
        "sample_rate": 16000, "channels": 1, "duration_seconds": 4.0,
        "engines": {
            "vad": "test", "vad_version": "1", "vad_parameters": {},
            "waveform": "test", "loudness": "test",
        },
        "waveform": [], "vad_intervals": vad, "pauses": [], "speech_density": [],
        "overall_speech_ratio": 0.5 if with_vad else 0.0,
        "loudness": {
            "integrated_lufs": None, "loudness_range_lu": None, "true_peak_dbfs": None,
            "threshold_lufs": None, "engine": "test",
        },
        "room_tone": [],
    })
    analysis_artifact = _write_artifact(
        domain, project.id, "analysis", tmp_path / "upstream" / "analysis.json", analysis_document,
    )
    return (
        config, domain, project, source, scene_artifact, face_artifact, analysis_artifact,
        analysis_document,
    )


def _run_service(config, domain, source, scene, face, analysis):
    return SpeakerTimelineService(config, domain).run(
        source_asset=source,
        scene_index_artifact=scene,
        face_index_artifact=face,
        analysis_artifact=analysis,
        sample_fps=4.0,
        progress_cb=lambda *_args: None,
        should_cancel=lambda: False,
    )


def test_service_persists_no_speech_timeline_and_reuses_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _upstream_fixture(tmp_path)
    config, domain, _project, source, scene, face, analysis, _document = fixture
    monkeypatch.setattr("cortex.analyze.speaker_service.ffmpeg_version", lambda _path: "test")
    monkeypatch.setattr(
        "cortex.analyze.speaker_service._iter_chunk_frames",
        lambda *_args, **_kwargs: pytest.fail("empty VAD must not decode frames"),
    )

    first = _run_service(config, domain, source, scene, face, analysis)
    document = SpeakerTimelineDocument.model_validate_json(
        Path(first["speaker_timeline_path"]).read_text(encoding="utf-8")
    )
    second = _run_service(config, domain, source, scene, face, analysis)

    assert first["cached"] is False
    assert document.observations == []
    assert [(item.start_us, item.end_us, item.state) for item in document.segments] == [
        (0, 4_000_000, "no_speech")
    ]
    assert second["cached"] is True
    assert second["speaker_timeline_artifact_id"] == first["speaker_timeline_artifact_id"]
    assert len(domain.list_stage_artifacts(source.project_id, "speaker_timeline")) == 1


def test_service_rejects_incompatible_upstream_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _upstream_fixture(tmp_path)
    config, domain, _project, source, scene, face, analysis, _document = fixture
    payload = json.loads(Path(face.path).read_text(encoding="utf-8"))
    payload["scene_index_artifact_id"] = "different-scene-artifact"
    Path(face.path).write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("cortex.analyze.speaker_service.ffmpeg_version", lambda _path: "test")

    with pytest.raises(SpeakerTimelinePreconditionError, match="cadeia fonte/scene_index"):
        _run_service(config, domain, source, scene, face, analysis)


def test_service_emits_speaker_from_synthetic_visual_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _upstream_fixture(tmp_path, with_vad=True)
    config, domain, _project, source, scene, face, analysis, _document = fixture
    face_payload = json.loads(Path(face.path).read_text(encoding="utf-8"))
    detected_face = {
        "x": 0.25, "y": 0.15, "width": 0.4, "height": 0.6, "score": 0.99,
        "landmarks": {
            "right_eye": [0.35, 0.3], "left_eye": [0.55, 0.3],
            "nose_tip": [0.45, 0.45], "right_mouth_corner": [0.38, 0.6],
            "left_mouth_corner": [0.52, 0.6],
        },
        "track_id": "person_left", "embedding": None,
    }
    face_payload["frames"] = [
        {"time": time, "faces": [detected_face], "shot_type": "close_up"}
        for time in (0.5, 1.0, 1.5, 2.0, 2.5)
    ]
    face_payload["frame_count"] = len(face_payload["frames"])
    face_payload["scenes"] = [{
        "scene_index": 0, "dominant_shot_type": "close_up",
        "track_ids_present": ["person_left"], "sample_count": 5,
    }]
    Path(face.path).write_text(json.dumps(face_payload), encoding="utf-8")

    def synthetic_frames(*_args, **_kwargs):
        for time in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0):
            value = 255 if 0.5 <= time < 2.5 else 0
            yield time, np.full((180, 320, 3), value, dtype=np.uint8)

    monkeypatch.setattr("cortex.analyze.speaker_service.ffmpeg_version", lambda _path: "test")
    monkeypatch.setattr("cortex.analyze.speaker_service._iter_chunk_frames", synthetic_frames)
    monkeypatch.setattr(
        "cortex.analyze.speaker_service.compensated_motion",
        lambda _previous, current, *_faces: 0.25 if current.mean() else 0.01,
    )

    result = _run_service(config, domain, source, scene, face, analysis)
    document = SpeakerTimelineDocument.model_validate_json(
        Path(result["speaker_timeline_path"]).read_text(encoding="utf-8")
    )

    speaker_observations = [item for item in document.observations if item.state == "speaker"]
    assert speaker_observations
    silent_observations = [item for item in document.observations if item.state == "no_speech"]
    assert any(item.time_us == 3_000_000 and item.track_scores for item in silent_observations)
    assert all(item.speaker_track_id == "person_left" for item in speaker_observations)
    assert any(
        item.state == "speaker" and item.speaker_track_id == "person_left"
        for item in document.segments
    )


def test_vad_chunks_and_observation_segments() -> None:
    fixture_analysis = AnalysisDocument.model_validate({
        "project_id": "p", "source_asset_id": "s", "transcript_artifact_id": "t",
        "input_hash": "h", "normalized_audio_sha256": "a", "sample_rate": 16000,
        "channels": 1, "duration_seconds": 8.0,
        "engines": {"vad": "v", "vad_version": "1", "vad_parameters": {},
                    "waveform": "w", "loudness": "l"},
        "waveform": [],
        "vad_intervals": [
            {"start": 1.0, "end": 2.0, "duration": 1.0, "start_sample": 1, "end_sample": 2},
            {"start": 3.5, "end": 4.0, "duration": 0.5, "start_sample": 3, "end_sample": 4},
            {"start": 7.0, "end": 8.0, "duration": 1.0, "start_sample": 7, "end_sample": 8},
        ],
        "pauses": [], "speech_density": [], "overall_speech_ratio": 0.3125,
        "loudness": {"integrated_lufs": None, "loudness_range_lu": None,
                     "true_peak_dbfs": None, "threshold_lufs": None, "engine": "l"},
        "room_tone": [],
    })
    assert _coalesced_vad_chunks(fixture_analysis, padding=0.25, duration=8.0) == [
        (0.75, 4.25), (6.75, 8.0)
    ]
    observations = [
        SpeakerObservation(time_us=1_200_000, scene_index=0, speech_active=True,
                           state="speaker", speaker_track_id="person_left",
                           confidence=0.8, track_scores=[]),
        SpeakerObservation(time_us=1_800_000, scene_index=0, speech_active=True,
                           state="speaker", speaker_track_id="person_left",
                           confidence=0.6, track_scores=[]),
        SpeakerObservation(time_us=1_900_000, scene_index=0, speech_active=False,
                           state="no_speech", confidence=1.0, track_scores=[]),
    ]
    segments = _segments_from_observations(observations, fixture_analysis, 8_000_000)
    assert segments[0].state == "no_speech" and segments[0].end_us == 1_000_000
    assert segments[1].state == "speaker" and segments[1].speaker_track_id == "person_left"
    assert segments[1].confidence == 0.7
    assert segments[-1].state == "unknown"


def test_api_worker_and_get_with_empty_vad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _upstream_fixture(tmp_path)
    config, domain, project, source, scene, face, analysis, _document = fixture
    monkeypatch.setattr("cortex.analyze.speaker_service.ffmpeg_version", lambda _path: "test")
    client = TestClient(create_app(config))
    response = client.post(f"/api/v1/projects/{project.id}/speakers", json={
        "source_asset_id": source.id,
        "scene_index_artifact_id": scene.id,
        "face_index_artifact_id": face.id,
        "analysis_artifact_id": analysis.id,
        "sample_fps": 4.0,
    })
    assert response.status_code == 201, response.text
    queued = response.json()
    assert queued["type"] == "speaker_analysis" and queued["status"] == "queued"

    jobs = JobStore(config.paths.database)
    assert process_next(config, jobs, domain, FakeTranscriptionEngine()) is True
    final = jobs.get(queued["id"])
    assert final.status == JobStatus.SUCCEEDED, final.error
    artifact_id = final.result["speaker_timeline_artifact_id"]
    fetched = client.get(f"/api/v1/projects/{project.id}/speakers/{artifact_id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["document"]["segments"][0]["state"] == "no_speech"
