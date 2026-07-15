from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException

from cortex.analyze.face_schemas import (
    FaceDetection,
    FaceIndexDocument,
    FaceIndexEngineInfo,
    FaceLandmarks,
    FrameFaces,
)
from cortex.analyze.identity_schemas import (
    IdentityEngineInfo,
    IdentityEntity,
    IdentityIndexDocument,
    IdentityObservation,
)
from cortex.api import ProjectCreate, RenderRequest, create_app
from cortex.config import CortexConfig, load_config
from cortex.domain.models import SourceAsset, SourceKind, StageArtifact, TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.edit.camera_plan_schemas import CameraEditPlanDocument
from cortex.edit.schemas import (
    EditPlanDiagnostics,
    EditPlanDocument,
    EditQualityReport,
    EditSegment,
)
from cortex.ingest.ffprobe import probe_media
from cortex.jobs import JobStore
from cortex.render.face_crop import resolve_static_face_crop
from cortex.render.schemas import (
    RenderCanvasSettings,
    RenderCaptionSettings,
    RenderDocument,
    RenderFramingSettings,
    RenderHeadlineSettings,
    RenderSettings,
    RenderSubtitleSettings,
)
from cortex.render.service import RenderPreconditionError, RenderService
from cortex.schemas import JobCreate, JobStatus, JobType
from cortex.transcribe.schemas import EngineInfo, TranscriptDocument
from cortex.worker import process_next


LANDMARKS = FaceLandmarks(
    right_eye=(0.4, 0.4), left_eye=(0.6, 0.4), nose_tip=(0.5, 0.5),
    right_mouth_corner=(0.45, 0.6), left_mouth_corner=(0.55, 0.6),
)


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
        "render": base.render.model_copy(update={
            "ffmpeg": Path(shutil.which("ffmpeg") or "ffmpeg"),
            "ffprobe": Path(shutil.which("ffprobe") or "ffprobe"),
            "encoder": "libx264", "width": 320, "height": 320, "fps": 30,
        }),
    })


def _route(app, path: str, method: str):
    return next(route.endpoint for route in app.routes if route.path == path and method in route.methods)


def _make_source(domain: DomainStore, tmp_path: Path, project_id: str, name: str = "source.mp4") -> SourceAsset:
    """A placeholder source file; no decodable content is required unless ffmpeg
    actually renders (some tests reject the request before touching the file)."""
    source_dir = tmp_path / "projects" / project_id / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    path = source_dir / name
    path.write_bytes(b"not-a-real-video")
    return domain.create_source_asset(SourceAsset(
        project_id=project_id, kind=SourceKind.UPLOAD, original_filename=name,
        stored_path=str(path.relative_to(tmp_path)),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        size_bytes=path.stat().st_size, probe={},
    ))


def _make_edit_plan_artifact(
    domain: DomainStore, tmp_path: Path, project_id: str, source_id: str, transcript_id: str,
    *, segments: list[EditSegment] | None = None, duration: float = 1.0,
) -> StageArtifact:
    segments = segments or [EditSegment(start=0.0, end=duration, timeline_order=0)]
    timeline_duration_seconds = sum(segment.end - segment.start for segment in segments)
    clip_end = max(segment.end for segment in segments)
    plan = EditPlanDocument(
        project_id=project_id, source_asset_id=source_id, transcript_artifact_id=transcript_id,
        analysis_artifact_id="analysis", input_hash=f"edit-{source_id}-{clip_end}-{timeline_duration_seconds}",
        clip_start=0.0, clip_end=clip_end, profile="balanced", segments=segments,
        timeline_duration_seconds=timeline_duration_seconds,
        diagnostics=EditPlanDiagnostics(profile="balanced", waveform_used=True, candidate_pauses=0,
                                        cuts=0, saved_seconds=0.0, crossfade=0.0, vad_used=True),
        quality=EditQualityReport(passed=True, issues=[], profile="balanced", degraded=False),
    )
    plan_path = tmp_path / f"plan-{plan.input_hash}.json"
    plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    return domain.create_stage_artifact(StageArtifact(
        project_id=project_id, stage="edit_plan", path=str(plan_path), input_hash=plan.input_hash,
    ))


def _make_transcript_artifact(
    domain: DomainStore, tmp_path: Path, project_id: str, source_id: str, duration: float = 1.0,
) -> TranscriptArtifact:
    transcript_path = tmp_path / f"transcript-{source_id}.json"
    transcript_path.write_text(TranscriptDocument(
        language="pt", duration_seconds=duration,
        engine=EngineInfo(
            model="fixture", requested_device="cpu", effective_device="cpu",
            requested_compute_type="int8", effective_compute_type="int8",
            batch_size=1, vad=True,
        ), segments=[],
    ).model_dump_json(), encoding="utf-8")
    return domain.create_transcript_artifact(TranscriptArtifact(
        project_id=project_id, source_asset_id=source_id, path=str(transcript_path),
        audio_sha256="audio", engine="fixture", model="fixture", device="cpu",
        compute_type="int8", language="pt", vad=True, batch_size=1, duration_seconds=duration,
    ))


def _face_engine() -> FaceIndexEngineInfo:
    return FaceIndexEngineInfo(
        detector="fixture", model_path="model.onnx", providers=["CPUExecutionProvider"],
        score_threshold=0.5, nms_threshold=0.4, sample_fps=1.0,
        ffmpeg_path="ffmpeg", ffmpeg_version="6.0",
    )


def _identity_engine() -> IdentityEngineInfo:
    return IdentityEngineInfo(
        algorithm="fixture", algorithm_version="1", recognizer="sface_2021dec",
        model_path="model.onnx", model_sha256="a" * 64, embedding_dimension=128,
        cosine_match_threshold=0.35, ambiguity_margin=0.05,
    )


def _make_face_index_artifact(
    domain: DomainStore, tmp_path: Path, *, artifact_project_id: str, doc_project_id: str,
    source_id: str, source_sha256: str, frames: list[FrameFaces], name: str = "faces.json",
) -> tuple[StageArtifact, FaceIndexDocument]:
    doc = FaceIndexDocument(
        project_id=doc_project_id, source_asset_id=source_id, source_sha256=source_sha256,
        scene_index_artifact_id="scenes", input_hash=f"face-{name}", duration_seconds=10.0,
        frames=frames, scenes=[], frame_count=len(frames), engine=_face_engine(),
    )
    path = tmp_path / name
    path.write_text(doc.model_dump_json(), encoding="utf-8")
    artifact = domain.create_stage_artifact(StageArtifact(
        project_id=artifact_project_id, stage="face_index", path=str(path), input_hash=doc.input_hash,
    ))
    return artifact, doc


def _make_identity_index_artifact(
    domain: DomainStore, tmp_path: Path, *, artifact_project_id: str, doc_project_id: str,
    source_id: str, source_sha256: str, face_index_artifact: StageArtifact,
    observations: list[IdentityObservation], identities: list[IdentityEntity],
    name: str = "identities.json",
) -> tuple[StageArtifact, IdentityIndexDocument]:
    doc = IdentityIndexDocument(
        project_id=doc_project_id, source_asset_id=source_id, source_sha256=source_sha256,
        face_index_artifact_id=face_index_artifact.id, face_index_input_hash=face_index_artifact.input_hash,
        camera_timeline_artifact_id="camera", camera_timeline_input_hash="camera-hash",
        input_hash=f"identity-{name}", observations=observations, identities=identities,
        unresolved_face_count=0, engine=_identity_engine(),
    )
    path = tmp_path / name
    path.write_text(doc.model_dump_json(), encoding="utf-8")
    artifact = domain.create_stage_artifact(StageArtifact(
        project_id=artifact_project_id, stage="identity_index", path=str(path), input_hash=doc.input_hash,
    ))
    return artifact, doc


def _confirmed_identity(identity_id: str, observation_id: str, local_track_id: str) -> IdentityEntity:
    return IdentityEntity(
        identity_id=identity_id, status="confirmed", observation_ids=[observation_id],
        layout_ids=["default"], scene_indices=[0], local_track_ids=[local_track_id],
        sample_count=1, evidence=[],
    )


# ---------------------------------------------------------------------------
# 1. HTTP-level fail-closed validation for face_static_crop artifact chains.
# ---------------------------------------------------------------------------


def _http_fixture(tmp_path: Path):
    app = create_app(_config(tmp_path))
    domain: DomainStore = app.state.domain
    create_project = _route(app, "/api/v1/projects", "POST")
    create_render_job = _route(app, "/api/v1/projects/{project_id}/renders", "POST")
    return domain, create_project, create_render_job


def test_face_static_crop_rejects_face_index_from_another_project(tmp_path: Path) -> None:
    domain, create_project, create_render_job = _http_fixture(tmp_path)
    project = create_project(ProjectCreate(name="Projeto A"))
    other_project = create_project(ProjectCreate(name="Projeto B"))

    source = _make_source(domain, tmp_path, project.id)
    transcript = _make_transcript_artifact(domain, tmp_path, project.id, source.id)
    plan_artifact = _make_edit_plan_artifact(domain, tmp_path, project.id, source.id, transcript.id)

    other_source = _make_source(domain, tmp_path, other_project.id, name="other.mp4")
    face_artifact, _ = _make_face_index_artifact(
        domain, tmp_path, artifact_project_id=other_project.id, doc_project_id=other_project.id,
        source_id=other_source.id, source_sha256=other_source.sha256, frames=[],
    )
    identity_artifact, _ = _make_identity_index_artifact(
        domain, tmp_path, artifact_project_id=project.id, doc_project_id=project.id,
        source_id=source.id, source_sha256=source.sha256, face_index_artifact=face_artifact,
        observations=[], identities=[_confirmed_identity("me", "obs-1", "track-1")],
    )

    with pytest.raises(HTTPException) as error:
        create_render_job(project.id, RenderRequest(
            edit_plan_artifact_id=plan_artifact.id,
            face_index_artifact_id=face_artifact.id,
            identity_index_artifact_id=identity_artifact.id,
            target_identity_id="me",
        ))
    assert error.value.status_code == 400
    assert "não pertence ao projeto" in error.value.detail


def test_face_static_crop_rejects_identity_index_from_another_source(tmp_path: Path) -> None:
    domain, create_project, create_render_job = _http_fixture(tmp_path)
    project = create_project(ProjectCreate(name="Projeto A"))

    source = _make_source(domain, tmp_path, project.id, name="primary.mp4")
    other_source = _make_source(domain, tmp_path, project.id, name="other.mp4")
    transcript = _make_transcript_artifact(domain, tmp_path, project.id, source.id)
    plan_artifact = _make_edit_plan_artifact(domain, tmp_path, project.id, source.id, transcript.id)

    face_artifact, _ = _make_face_index_artifact(
        domain, tmp_path, artifact_project_id=project.id, doc_project_id=project.id,
        source_id=source.id, source_sha256=source.sha256, frames=[],
    )
    # identity_index built for a different source of the same project.
    identity_artifact, _ = _make_identity_index_artifact(
        domain, tmp_path, artifact_project_id=project.id, doc_project_id=project.id,
        source_id=other_source.id, source_sha256=other_source.sha256, face_index_artifact=face_artifact,
        observations=[], identities=[_confirmed_identity("me", "obs-1", "track-1")],
    )

    with pytest.raises(HTTPException) as error:
        create_render_job(project.id, RenderRequest(
            edit_plan_artifact_id=plan_artifact.id,
            face_index_artifact_id=face_artifact.id,
            identity_index_artifact_id=identity_artifact.id,
            target_identity_id="me",
        ))
    assert error.value.status_code == 400
    assert "não correspondem à fonte" in error.value.detail


@pytest.mark.parametrize("identities", [
    [],  # target_identity_id simply does not exist
    [IdentityEntity(
        identity_id="me", status="ambiguous", observation_ids=["obs-1"],
        layout_ids=["default"], scene_indices=[0], local_track_ids=["track-1"],
        sample_count=1, evidence=[],
    )],  # target_identity_id exists but is not confirmed (ambiguous)
], ids=["nonexistent", "ambiguous"])
def test_face_static_crop_rejects_unconfirmed_target_identity(tmp_path: Path, identities) -> None:
    domain, create_project, create_render_job = _http_fixture(tmp_path)
    project = create_project(ProjectCreate(name="Projeto A"))

    source = _make_source(domain, tmp_path, project.id)
    transcript = _make_transcript_artifact(domain, tmp_path, project.id, source.id)
    plan_artifact = _make_edit_plan_artifact(domain, tmp_path, project.id, source.id, transcript.id)

    face_artifact, _ = _make_face_index_artifact(
        domain, tmp_path, artifact_project_id=project.id, doc_project_id=project.id,
        source_id=source.id, source_sha256=source.sha256, frames=[],
    )
    identity_artifact, _ = _make_identity_index_artifact(
        domain, tmp_path, artifact_project_id=project.id, doc_project_id=project.id,
        source_id=source.id, source_sha256=source.sha256, face_index_artifact=face_artifact,
        observations=[], identities=identities,
    )

    with pytest.raises(HTTPException) as error:
        create_render_job(project.id, RenderRequest(
            edit_plan_artifact_id=plan_artifact.id,
            face_index_artifact_id=face_artifact.id,
            identity_index_artifact_id=identity_artifact.id,
            target_identity_id="me",
        ))
    assert error.value.status_code == 400
    assert "não correspondem à fonte" in error.value.detail


def test_face_static_crop_rejects_missing_artifact_file_on_disk(tmp_path: Path) -> None:
    domain, create_project, create_render_job = _http_fixture(tmp_path)
    project = create_project(ProjectCreate(name="Projeto A"))

    source = _make_source(domain, tmp_path, project.id)
    transcript = _make_transcript_artifact(domain, tmp_path, project.id, source.id)
    plan_artifact = _make_edit_plan_artifact(domain, tmp_path, project.id, source.id, transcript.id)

    face_artifact, _ = _make_face_index_artifact(
        domain, tmp_path, artifact_project_id=project.id, doc_project_id=project.id,
        source_id=source.id, source_sha256=source.sha256, frames=[],
    )
    identity_artifact, _ = _make_identity_index_artifact(
        domain, tmp_path, artifact_project_id=project.id, doc_project_id=project.id,
        source_id=source.id, source_sha256=source.sha256, face_index_artifact=face_artifact,
        observations=[], identities=[_confirmed_identity("me", "obs-1", "track-1")],
    )
    # The artifact record exists, but its file was deleted from disk.
    Path(identity_artifact.path).unlink()

    with pytest.raises(HTTPException) as error:
        create_render_job(project.id, RenderRequest(
            edit_plan_artifact_id=plan_artifact.id,
            face_index_artifact_id=face_artifact.id,
            identity_index_artifact_id=identity_artifact.id,
            target_identity_id="me",
        ))
    assert error.value.status_code == 409
    assert "indisponível" in error.value.detail


def test_worker_defense_in_depth_rejects_face_index_from_another_project(tmp_path: Path) -> None:
    """Even if a job is enqueued bypassing the API (payload tampering, older
    client, etc.), the worker/RenderService chain must still fail closed."""
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    jobs = JobStore(config.paths.database)
    project = domain.create_project("Projeto A")
    other_project = domain.create_project("Projeto B")

    source = _make_source(domain, tmp_path, project.id)
    transcript = _make_transcript_artifact(domain, tmp_path, project.id, source.id)
    plan_artifact = _make_edit_plan_artifact(domain, tmp_path, project.id, source.id, transcript.id)

    other_source = _make_source(domain, tmp_path, other_project.id, name="other.mp4")
    face_artifact, _ = _make_face_index_artifact(
        domain, tmp_path, artifact_project_id=other_project.id, doc_project_id=other_project.id,
        source_id=other_source.id, source_sha256=other_source.sha256, frames=[],
    )
    identity_artifact, _ = _make_identity_index_artifact(
        domain, tmp_path, artifact_project_id=project.id, doc_project_id=project.id,
        source_id=source.id, source_sha256=source.sha256, face_index_artifact=face_artifact,
        observations=[], identities=[_confirmed_identity("me", "obs-1", "track-1")],
    )

    job = jobs.create(JobCreate(
        type=JobType.RENDER, project_id=project.id,
        payload={
            "edit_plan_artifact_id": plan_artifact.id,
            "face_index_artifact_id": face_artifact.id,
            "identity_index_artifact_id": identity_artifact.id,
            "target_identity_id": "me",
        },
    ))
    assert process_next(config, jobs, domain, engine=None) is True  # type: ignore[arg-type]
    failed = jobs.get(job.id)
    assert failed.status == JobStatus.FAILED
    assert "não pertence ao projeto" in failed.error


# ---------------------------------------------------------------------------
# 2. Synthetic render regression: real MP4, static crop, manifest v7 fields.
# ---------------------------------------------------------------------------


def _split_color_source(config: CortexConfig, domain: DomainStore, tmp_path: Path, project_id: str) -> SourceAsset:
    """640x360, left half red / right half blue, static content (no motion),
    with an audio track. Used to prove a crop window statically samples one
    side of the frame without ever tracking or panning."""
    source_dir = tmp_path / "projects" / project_id / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    path = source_dir / "split.mp4"
    subprocess.run([
        str(config.render.ffmpeg), "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=red:size=320x360:rate=30:duration=6",
        "-f", "lavfi", "-i", "color=c=blue:size=320x360:rate=30:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
        "-filter_complex", "[0:v][1:v]hstack=inputs=2[v]",
        "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path),
    ], check=True, timeout=60)
    return domain.create_source_asset(SourceAsset(
        project_id=project_id, kind=SourceKind.UPLOAD, original_filename="split.mp4",
        stored_path=str(path.relative_to(tmp_path)),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        size_bytes=path.stat().st_size, probe=probe_media(config.render.ffprobe, path),
    ))


def _center_pixel(ffmpeg: Path, media_path: Path, timestamp: float) -> tuple[int, int, int]:
    result = subprocess.run(
        [
            str(ffmpeg), "-v", "error", "-ss", str(timestamp), "-i", str(media_path),
            "-frames:v", "1", "-vf", "crop=1:1:(iw-1)/2:(ih-1)/2,format=rgb24",
            "-f", "rawvideo", "-",
        ],
        capture_output=True, check=True, timeout=30,
    )
    return tuple(result.stdout[:3])  # type: ignore[return-value]


def _render_settings(mode: str = "face_static_crop") -> RenderSettings:
    return RenderSettings(
        encoder="libx264", canvas=RenderCanvasSettings(width=320, height=568, fps=30),
        framing=RenderFramingSettings(mode=mode),
        captions=RenderCaptionSettings(enabled=False, font_family="Montserrat", font_size=28,
                                       words_per_cue=3, outline=False),
        headline=RenderHeadlineSettings(enabled=False, font_family="Montserrat", font_size=36,
                                        duration_seconds=1.0),
        subtitles=RenderSubtitleSettings(sidecar_srt=False),
    )


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="ffmpeg and ffprobe required")
def test_face_static_crop_renders_static_target_crop_with_auditable_manifest(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Face crop fixture")
    source = _split_color_source(config, domain, tmp_path, project.id)

    transcript = _make_transcript_artifact(domain, tmp_path, project.id, source.id, duration=6.0)
    # Segment 0: target identity present, biased to the right (blue) half.
    plan_artifact = _make_edit_plan_artifact(
        domain, tmp_path, project.id, source.id, transcript.id,
        segments=[EditSegment(start=0.0, end=1.0, timeline_order=0)], duration=1.0,
    )

    face_frames = [
        FrameFaces(time=t, shot_type="close", faces=[
            FaceDetection(x=0.70, y=0.3, width=0.10, height=0.2, score=0.9,
                          landmarks=LANDMARKS, track_id="track-1"),
        ])
        for t in (0.1, 0.4, 0.7)
    ]
    face_artifact, face_doc = _make_face_index_artifact(
        domain, tmp_path, artifact_project_id=project.id, doc_project_id=project.id,
        source_id=source.id, source_sha256=source.sha256, frames=face_frames,
    )
    observations = [
        IdentityObservation(
            observation_id=f"obs-{i}", time_us=round(t * 1_000_000), scene_index=0,
            layout_id="default", local_track_id="track-1", identity_id="me", status="confirmed",
        )
        for i, t in enumerate((0.1, 0.4, 0.7))
    ]
    identity_artifact, identity_doc = _make_identity_index_artifact(
        domain, tmp_path, artifact_project_id=project.id, doc_project_id=project.id,
        source_id=source.id, source_sha256=source.sha256, face_index_artifact=face_artifact,
        observations=observations,
        identities=[IdentityEntity(
            identity_id="me", status="confirmed", observation_ids=[o.observation_id for o in observations],
            layout_ids=["default"], scene_indices=[0], local_track_ids=["track-1"],
            sample_count=3, evidence=[],
        )],
    )

    result = RenderService(config, domain).run(
        edit_plan_artifact=plan_artifact, face_index_artifact=face_artifact,
        identity_index_artifact=identity_artifact, target_identity_id="me",
        encoder=None, headline=None, progress_cb=lambda *_a: None, should_cancel=lambda: False,
        render_settings=_render_settings(),
    )
    render_artifact = domain.get_stage_artifact(result["render_artifact_id"])
    document = RenderDocument.model_validate_json(Path(render_artifact.path).read_text())

    assert document.schema_version == 13
    assert document.target_identity_id == "me"
    assert document.face_index_artifact_id == face_artifact.id
    assert document.identity_index_artifact_id == identity_artifact.id
    assert len(document.static_face_crops) == 1
    crop_entry = document.static_face_crops[0]
    provenance = crop_entry["provenance"]
    assert provenance["fallback"] is None
    assert provenance["temporal_motion"] is False
    assert provenance["effective_target_identity_id"] == "me"
    assert provenance["sample_count"] == 3
    assert len(provenance["samples"]) == 3
    # Target sits on the right (blue) half; crop should be pulled off-center.
    video_stream = next(s for s in probe_media(config.render.ffprobe, tmp_path / source.stored_path)["streams"] if s["codec_type"] == "video")
    expected = resolve_static_face_crop(
        face_doc, start_seconds=0.0, end_seconds=1.0,
        source_width=int(video_stream["width"]), source_height=int(video_stream["height"]),
        canvas_width=320, canvas_height=568,
        identity_index=identity_doc, target_identity_id="me",
    )
    assert crop_entry["crop_x"] == expected.crop_x
    assert crop_entry["crop_y"] == expected.crop_y
    center_default_x = max(0, (int(video_stream["width"]) - crop_entry["crop_width"]) // 2)
    assert crop_entry["crop_x"] != center_default_x

    output_path = Path(document.output_path)
    assert output_path.is_file()
    # Sample >= 3 different instants inside the single segment: the crop is
    # resolved once per segment, so the sampled window over the source must
    # stay on the blue (right) half throughout, proving no pan/track motion.
    for timestamp in (0.15, 0.5, 0.85):
        pixel = _center_pixel(config.render.ffmpeg, output_path, timestamp)
        assert pixel[2] > pixel[0], f"expected blue-dominant frame at t={timestamp}, got {pixel}"


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="ffmpeg and ffprobe required")
def test_face_static_crop_falls_back_to_center_when_identity_absent(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Face crop fallback fixture")
    source = _split_color_source(config, domain, tmp_path, project.id)

    transcript = _make_transcript_artifact(domain, tmp_path, project.id, source.id, duration=6.0)
    # Segment far outside the range covered by any face observation.
    plan_artifact = _make_edit_plan_artifact(
        domain, tmp_path, project.id, source.id, transcript.id,
        segments=[EditSegment(start=5.0, end=6.0, timeline_order=0)], duration=6.0,
    )

    face_artifact, face_doc = _make_face_index_artifact(
        domain, tmp_path, artifact_project_id=project.id, doc_project_id=project.id,
        source_id=source.id, source_sha256=source.sha256, frames=[],
    )
    identity_artifact, identity_doc = _make_identity_index_artifact(
        domain, tmp_path, artifact_project_id=project.id, doc_project_id=project.id,
        source_id=source.id, source_sha256=source.sha256, face_index_artifact=face_artifact,
        observations=[],
        identities=[_confirmed_identity("me", "obs-1", "track-1")],
    )

    result = RenderService(config, domain).run(
        edit_plan_artifact=plan_artifact, face_index_artifact=face_artifact,
        identity_index_artifact=identity_artifact, target_identity_id="me",
        encoder=None, headline=None, progress_cb=lambda *_a: None, should_cancel=lambda: False,
        render_settings=_render_settings(),
    )
    render_artifact = domain.get_stage_artifact(result["render_artifact_id"])
    document = RenderDocument.model_validate_json(Path(render_artifact.path).read_text())

    assert len(document.static_face_crops) == 1
    provenance = document.static_face_crops[0]["provenance"]
    assert provenance["fallback"] == "center_crop"
    assert provenance["temporal_motion"] is False
    assert provenance["effective_target_identity_id"] is None
    assert provenance["sample_count"] == 0
    # No identity chosen silently: request stays auditable as the confirmed target.
    assert document.target_identity_id == "me"


def test_face_static_crop_still_rejects_camera_edit_plan_combination(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Combo rejection fixture")
    source = _make_source(domain, tmp_path, project.id)

    transcript = _make_transcript_artifact(domain, tmp_path, project.id, source.id)
    plan_artifact = _make_edit_plan_artifact(domain, tmp_path, project.id, source.id, transcript.id)

    camera_plan = CameraEditPlanDocument.model_validate({
        "project_id": project.id, "source_asset_id": source.id,
        "edit_plan_artifact_id": plan_artifact.id, "edit_plan_input_hash": plan_artifact.input_hash,
        "camera_timeline_artifact_id": "camera", "camera_timeline_input_hash": "camera-hash",
        "identity_index_artifact_id": "identity", "identity_index_input_hash": "identity-hash",
        "visual_quality_artifact_id": "quality", "visual_quality_input_hash": "quality-hash",
        "input_hash": "combo-camera-plan",
        "shots": [{
            "edit_segment_order": 0, "video_source_asset_id": source.id,
            "audio_source_asset_id": source.id, "source_start_us": 0, "source_end_us": 1_000_000,
            "audio_source_start_us": 0, "audio_source_end_us": 1_000_000, "scene_index": 0,
            "layout_id": "primary", "camera_role": "speaker_close", "intent": "speaker",
            "confirmed_identity_ids": [], "visual_quality_usable": True, "evidence": [],
        }],
        "diagnostics": {"shot_count": 1, "speaker_shot_count": 1, "context_shot_count": 0,
                        "fallback_shot_count": 0, "unusable_scene_count": 0,
                        "reaction_shots_blocked_by": []},
        "engine": {"algorithm": "fixture", "algorithm_version": "2"},
    })
    camera_path = tmp_path / "combo-camera-plan.json"
    camera_path.write_text(camera_plan.model_dump_json(), encoding="utf-8")
    camera_artifact = domain.create_stage_artifact(StageArtifact(
        project_id=project.id, stage="camera_edit_plan", schema_version=5,
        path=str(camera_path), input_hash=camera_plan.input_hash,
    ))

    face_artifact, _ = _make_face_index_artifact(
        domain, tmp_path, artifact_project_id=project.id, doc_project_id=project.id,
        source_id=source.id, source_sha256=source.sha256, frames=[],
    )
    identity_artifact, _ = _make_identity_index_artifact(
        domain, tmp_path, artifact_project_id=project.id, doc_project_id=project.id,
        source_id=source.id, source_sha256=source.sha256, face_index_artifact=face_artifact,
        observations=[], identities=[_confirmed_identity("me", "obs-1", "track-1")],
    )

    with pytest.raises(RenderPreconditionError, match="não aceita camera_edit_plan"):
        RenderService(config, domain).run(
            edit_plan_artifact=plan_artifact, camera_edit_plan_artifact=camera_artifact,
            face_index_artifact=face_artifact, identity_index_artifact=identity_artifact,
            target_identity_id="me", encoder=None, headline=None,
            progress_cb=lambda *_a: None, should_cancel=lambda: False,
            render_settings=_render_settings(),
        )
