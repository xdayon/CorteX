from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cortex.analyze.audio import pause_intervals, read_normalized_wav, waveform_resolutions
from cortex.analyze.schemas import AnalysisDocument, LoudnessMetrics, VadInterval
from cortex.analyze.service import AnalysisJobCancelled, AnalysisService
from cortex.api import create_app
from cortex.config import CortexConfig, load_config
from cortex.domain.models import SourceAsset, SourceKind, TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.jobs import JobStore
from cortex.schemas import JobStatus
from cortex.worker import process_next

from conftest import FakeTranscriptionEngine


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


def _media_fixture(config: CortexConfig, domain: DomainStore, wav: Path):
    project = domain.create_project("Analysis fixture")
    source_path = config.paths.projects_dir / project.id / "source" / "tone.wav"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(wav.read_bytes())
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id,
        kind=SourceKind.UPLOAD,
        original_filename="tone.wav",
        stored_path=str(source_path.relative_to(config.paths.data_dir)),
        sha256=digest,
        size_bytes=source_path.stat().st_size,
    ))
    transcript_path = config.paths.projects_dir / project.id / "transcripts" / "transcript.json"
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(json.dumps({
        "schema_version": 1,
        "duration_seconds": 1.0,
        "engine": {
            "name": "fake", "model": "fake", "requested_device": "cpu",
            "effective_device": "cpu", "requested_compute_type": "int8",
            "effective_compute_type": "int8", "batch_size": 1, "vad": True,
        },
        "segments": [],
    }), encoding="utf-8")
    transcript = domain.create_transcript_artifact(TranscriptArtifact(
        project_id=project.id,
        source_asset_id=source.id,
        path=str(transcript_path),
        audio_sha256=digest,
        engine="fake",
        model="fake",
        device="cpu",
        compute_type="int8",
        language="pt",
        vad=True,
        batch_size=1,
        duration_seconds=1.0,
    ))
    return project, source, transcript


def _fake_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    interval = VadInterval(
        start=0.2, end=0.7, duration=0.5, start_sample=3200, end_sample=11200,
    )
    monkeypatch.setattr(
        "cortex.analyze.service.detect_speech", lambda _audio, _rate: ([interval], "test-vad"),
    )
    monkeypatch.setattr(
        "cortex.analyze.service.measure_loudness",
        lambda _ffmpeg, _path: LoudnessMetrics(
            integrated_lufs=-20.0,
            loudness_range_lu=2.0,
            true_peak_dbfs=-3.0,
            threshold_lufs=-30.0,
            engine="test-loudness",
        ),
    )


def test_pcm_waveform_and_pauses_use_real_normalized_wav(tmp_path: Path) -> None:
    wav = tmp_path / "normalized.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav),
        ],
        capture_output=True, check=True, timeout=30,
    )
    audio, sample_rate, channels = read_normalized_wav(wav)
    waveform = waveform_resolutions(audio, point_counts=(16,))
    pauses = pause_intervals(
        [VadInterval(start=0.25, end=0.75, duration=0.5, start_sample=4000, end_sample=12000)],
        duration=1.0,
    )

    assert (sample_rate, channels, len(audio)) == (16000, 1, 16000)
    assert waveform[0].points == 16
    assert max(waveform[0].peaks) > 0
    assert [(item.start, item.end) for item in pauses] == [(0.0, 0.25), (0.75, 1.0)]


def test_analysis_service_persists_schema_reuses_cache_and_cancels(
    tmp_path: Path, short_wav: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project, source, transcript = _media_fixture(config, domain, short_wav)
    _fake_engines(monkeypatch)
    service = AnalysisService(config, domain)
    progress: list[float] = []

    first = service.run(
        source_asset=source, transcript_artifact=transcript,
        progress_cb=lambda value, _message: progress.append(value),
        should_cancel=lambda: False,
    )
    document = AnalysisDocument.model_validate_json(Path(first["analysis_path"]).read_text())
    artifacts = domain.list_stage_artifacts(project.id, "analysis")

    assert first["cached"] is False
    assert len(artifacts) == 1 and artifacts[0].stage == "analysis"
    assert document.schema_version == 2
    assert document.waveform and document.vad_intervals and document.pauses
    assert document.speech_density and document.loudness and document.room_tone
    assert document.fillers == []
    assert progress == sorted(progress) and progress[-1] == 100.0

    second = service.run(
        source_asset=source, transcript_artifact=transcript,
        progress_cb=lambda _value, _message: None, should_cancel=lambda: False,
    )
    assert second["cached"] is True
    assert second["analysis_artifact_id"] == first["analysis_artifact_id"]
    assert len(domain.list_stage_artifacts(project.id, "analysis")) == 1

    other_project, other_source, other_transcript = _media_fixture(config, domain, short_wav)
    checks = iter((False, True))
    with pytest.raises(AnalysisJobCancelled):
        service.run(
            source_asset=other_source, transcript_artifact=other_transcript,
            progress_cb=lambda _value, _message: None,
            should_cancel=lambda: next(checks, True),
        )
    assert domain.list_stage_artifacts(other_project.id, "analysis") == []


def test_analysis_api_worker_and_artifact_get(
    tmp_path: Path, short_wav: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project, _source, transcript = _media_fixture(config, domain, short_wav)
    _fake_engines(monkeypatch)
    client = TestClient(create_app(config))

    response = client.post(
        f"/api/v1/projects/{project.id}/analyze",
        json={"transcript_artifact_id": transcript.id},
    )
    assert response.status_code == 201, response.text
    queued = response.json()
    assert queued["type"] == "analysis" and queued["status"] == "queued"

    jobs = JobStore(config.paths.database)
    assert process_next(config, jobs, domain, FakeTranscriptionEngine()) is True
    final = jobs.get(queued["id"])
    assert final.status == JobStatus.SUCCEEDED
    assert final.result is not None and final.result["cached"] is False

    artifact_id = final.result["analysis_artifact_id"]
    fetched = client.get(f"/api/v1/projects/{project.id}/analysis/{artifact_id}")
    assert fetched.status_code == 200, fetched.text
    payload = fetched.json()
    assert payload["artifact"]["stage"] == "analysis"
    assert payload["document"]["schema_version"] == 2
    assert json.loads(Path(final.result["analysis_path"]).read_text())["input_hash"]
