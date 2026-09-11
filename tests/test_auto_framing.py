from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex.analyze.camera_schemas import CameraTimelineDocument
from cortex.analyze.speaker_schemas import SpeakerTimelineDocument
from cortex.domain.models import StageArtifact
from cortex.domain.store import DomainStore
from cortex.edit.camera_plan_schemas import CameraEditPlanDocument
from cortex.render.auto_framing import load_framing_inputs, resolve_auto_framing
from cortex.render.schemas import RenderDocument
from cortex.render.service import RenderService
from test_face_crop import _document, _face
from test_face_static_crop_gate1 import (
    _center_pixel, _config, _make_edit_plan_artifact, _make_transcript_artifact,
    _render_settings, _split_color_source,
)


def _speakers(turns):
    return SpeakerTimelineDocument.model_validate({
        "project_id": "project", "source_asset_id": "source", "source_sha256": "sha",
        "scene_index_artifact_id": "scenes", "scene_index_input_hash": "scene-hash",
        "face_index_artifact_id": "faces", "face_index_input_hash": "hash",
        "analysis_artifact_id": "analysis", "analysis_input_hash": "analysis-hash",
        "input_hash": "speaker-hash", "duration_us": 6_000_000, "observations": [],
        "segments": [{"start_us": a, "end_us": b, "state": "speaker",
                      "speaker_track_id": track, "confidence": .9, "evidence": "mouth_motion"}
                     for a, b, track in turns],
        "engine": {"algorithm": "test", "algorithm_version": "1", "sample_fps_requested": 4,
                   "sample_fps_effective": 4, "frame_width_requested": 640, "frame_width_effective": 640,
                   "vad_padding_seconds": 1, "motion_threshold": .1, "winner_margin": .1,
                   "ffmpeg_path": "ffmpeg", "ffmpeg_version": "test", "decoder_requested": "cpu",
                   "decoder_effective": "cpu", "device_requested": "cpu", "device_effective": "cpu"},
    })


def _plan(windows):
    return CameraEditPlanDocument.model_validate({
        "project_id": "project", "source_asset_id": "source", "edit_plan_artifact_id": "edit",
        "edit_plan_input_hash": "edit-hash", "camera_timeline_artifact_id": "camera",
        "camera_timeline_input_hash": "camera-hash", "identity_index_artifact_id": "identity",
        "identity_index_input_hash": "identity-hash", "visual_quality_artifact_id": "quality",
        "visual_quality_input_hash": "quality-hash", "input_hash": "plan-hash",
        "shots": [{"edit_segment_order": 0, "video_source_asset_id": "source",
                   "audio_source_asset_id": "source", "source_start_us": a, "source_end_us": b,
                   "audio_source_start_us": a, "audio_source_end_us": b, "scene_index": i,
                   "layout_id": str(i), "camera_role": "two_shot", "intent": "context",
                   "confirmed_identity_ids": [], "visual_quality_usable": True, "evidence": []}
                  for i, (a, b) in enumerate(windows)],
        "diagnostics": {"shot_count": len(windows), "speaker_shot_count": 0,
                        "context_shot_count": len(windows), "fallback_shot_count": 0,
                        "unusable_scene_count": 0, "reaction_shots_blocked_by": []},
        "engine": {"algorithm": "test", "algorithm_version": "1"},
    })


def _faces(single_listener=True):
    return _document([{"time": time, "shot_type": "two_shot", "faces": (
        [_face(.7, .1, "right")] if single_listener and 2 <= time < 4
        else [_face(.2, .1, "left"), _face(.7, .1, "right")]
    )} for time in (.1, .6, 1.1, 1.6, 2.1, 2.6, 3.1, 3.6, 4.1, 4.6, 5.1, 5.6)])


def _resolve(plan, faces, speakers, **kwargs):
    return resolve_auto_framing(plan, faces, speakers, source_width=1920, source_height=1080,
                                width=1080, height=1920, fps=30, **kwargs)


def test_wide_speaker_then_real_silent_listener_then_uncertain_context():
    spans = _resolve(_plan([(0, 2_000_000), (2_000_000, 4_000_000), (4_000_000, 6_000_000)]),
                     _faces(), _speakers([(0, 2_000_000, "left")]))
    assert [(s.mode, s.track_id) for s in spans] == [
        ("face_crop", "left"), ("face_crop", "right"), ("blurred_background", None)]
    assert spans[0].crop_x < 600
    assert spans[1].crop_x > 1000
    assert spans[1].reason == "single_visible_person"
    assert spans[2].reason == "speaker_uncertain_keep_context"
    assert sum(s.source_end_us - s.source_start_us for s in spans) == 6_000_000


def test_speaker_turn_within_the_same_wide_camera_changes_crop():
    spans = _resolve(_plan([(0, 6_000_000)]), _faces(False),
                     _speakers([(0, 3_000_000, "left"), (3_000_000, 6_000_000, "right")]))
    assert [s.track_id for s in spans] == ["left", "right"]
    assert spans[0].source_end_us == spans[1].source_start_us == 3_000_000


@pytest.mark.parametrize("kind", ["absent", "low_confidence", "brief", "empty_frame", "unsafe_face"])
def test_uncertainty_never_becomes_center_table_crop(kind):
    faces = _faces(False)
    speakers = _speakers([(0, 6_000_000, "left")])
    if kind == "absent":
        speakers.segments[0].speaker_track_id = "missing"
    elif kind == "low_confidence":
        speakers.segments[0].confidence = .2
    elif kind == "brief":
        speakers.segments[0].end_us = 500_000
    elif kind == "empty_frame":
        faces.frames[1].faces = []
    elif kind == "unsafe_face":
        faces.frames[1].faces[0].x = .6
    spans = _resolve(_plan([(0, 6_000_000)]), faces, speakers)
    assert all(span.mode == "blurred_background" for span in spans)


def test_zoom_records_effective_geometry_and_keeps_face_inside_crop():
    spans = _resolve(_plan([(0, 6_000_000)]), _faces(False),
                     _speakers([(0, 6_000_000, "left")]), zoom=1.15)
    assert spans[0].effective_zoom == 1.15
    assert spans[0].crop_width < 606
    assert spans[0].crop_x < .2 * 1920
    assert spans[0].crop_x + spans[0].crop_width > .3 * 1920


def _persist(domain, project_id, stage, path, doc, schema_version=1):
    path.write_text(doc.model_dump_json())
    return domain.create_stage_artifact(StageArtifact(project_id=project_id, stage=stage,
        path=str(path), input_hash=doc.input_hash, schema_version=schema_version))


def _fixture(tmp_path):
    config = _config(tmp_path)
    # Static test colors are intentional; audio/technical quality still run.
    config.render.visual_quality_enabled = False
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Auto framing regression")
    source = _split_color_source(config, domain, tmp_path, project.id)
    transcript = _make_transcript_artifact(domain, tmp_path, project.id, source.id, duration=6)
    edit = _make_edit_plan_artifact(domain, tmp_path, project.id, source.id, transcript.id, duration=6)
    common = dict(project_id=project.id, source_asset_id=source.id, source_sha256=source.sha256)
    faces = _faces().model_copy(update=common)
    face = _persist(domain, project.id, "face_index", tmp_path / "faces.json", faces, 2)
    speakers = _speakers([(0, 2_000_000, "left")]).model_copy(update={
        **common, "face_index_artifact_id": face.id, "face_index_input_hash": face.input_hash})
    speaker = _persist(domain, project.id, "speaker_timeline", tmp_path / "speakers.json", speakers, 2)
    camera_doc = CameraTimelineDocument.model_validate({
        **common, "scene_index_artifact_id": "scenes", "scene_index_input_hash": "scene-hash",
        "face_index_artifact_id": face.id, "face_index_input_hash": face.input_hash,
        "speaker_timeline_artifact_id": speaker.id, "speaker_timeline_input_hash": speaker.input_hash,
        "input_hash": "camera-hash", "duration_us": 6_000_000, "scenes": [],
        "engine": {"algorithm": "test", "algorithm_version": "1", "dominant_speaker_min_share": .55},
    })
    camera = _persist(domain, project.id, "camera_timeline", tmp_path / "cameras.json", camera_doc)
    plan = _plan([(0, 2_000_000), (2_000_000, 4_000_000), (4_000_000, 6_000_000)])
    plan.project_id, plan.source_asset_id = project.id, source.id
    plan.edit_plan_artifact_id, plan.edit_plan_input_hash = edit.id, edit.input_hash
    plan.camera_timeline_artifact_id = camera.id
    for shot in plan.shots:
        shot.video_source_asset_id = shot.audio_source_asset_id = source.id
    artifact = _persist(domain, project.id, "camera_edit_plan", tmp_path / "plan.json", plan, 5)
    return config, domain, source, edit, artifact, face


def test_real_ffmpeg_render_persists_crop_decisions_and_reuses_cache(tmp_path):
    config, domain, source, edit, camera, face = _fixture(tmp_path)
    settings = _render_settings("speaker_auto")
    arguments = dict(edit_plan_artifact=edit, camera_edit_plan_artifact=camera,
                     encoder=None, headline=None, render_settings=settings,
                     progress_cb=lambda *_: None, should_cancel=lambda: False)
    result = RenderService(config, domain).run(**arguments)
    artifact = domain.get_stage_artifact(result["render_artifact_id"])
    document = RenderDocument.model_validate_json(Path(artifact.path).read_text())
    assert document.quality.passed
    assert len(document.auto_framing) == 3
    assert document.auto_framing_inputs["face_index"]
    red = _center_pixel(config.render.ffmpeg, Path(document.output_path), 1)
    blue = _center_pixel(config.render.ffmpeg, Path(document.output_path), 3)
    assert red[0] > red[2] + 100
    assert blue[2] > blue[0] + 100
    assert abs(document.timeline_duration_seconds - 6) < .04
    again = RenderService(config, domain).run(**arguments)
    assert again["cached"] and again["render_artifact_id"] == artifact.id
    payload = json.loads(Path(face.path).read_text())
    payload["frames"][0]["faces"] = []
    Path(face.path).write_text(json.dumps(payload))
    changed = RenderService(config, domain).run(**arguments)
    assert not changed["cached"] and changed["render_artifact_id"] != artifact.id
    changed_doc = RenderDocument.model_validate_json(
        Path(domain.get_stage_artifact(changed["render_artifact_id"]).path).read_text())
    import subprocess
    def audio_hash(path):
        return subprocess.run([str(config.render.ffmpeg), "-v", "error", "-i", path,
                               "-map", "0:a:0", "-f", "hash", "-"], capture_output=True,
                              check=True, timeout=30).stdout
    assert audio_hash(document.output_path) == audio_hash(changed_doc.output_path)



@pytest.mark.parametrize("field,value", [("source_sha256", "wrong"), ("project_id", "other"),
                                         ("scene_index_artifact_id", "wrong-scenes")])
def test_dependency_chain_rejected_before_render(tmp_path, field, value):
    _, domain, source, _, camera, face = _fixture(tmp_path)
    payload = json.loads(Path(face.path).read_text())
    payload[field] = value
    Path(face.path).write_text(json.dumps(payload))
    plan = CameraEditPlanDocument.model_validate_json(Path(camera.path).read_text())
    with pytest.raises(ValueError):
        load_framing_inputs(domain, plan, source)


def test_legacy_duplicate_track_cannot_select_arbitrary_face():
    faces = _faces(False)
    for frame in faces.frames:
        for face in frame.faces:
            face.track_id = "person"
    spans = _resolve(_plan([(0, 6_000_000)]), faces, _speakers([(0, 6_000_000, "person")]))
    assert all(span.mode == "blurred_background" for span in spans)
    assert spans[0].reason == "ambiguous_duplicate_track"


@pytest.mark.parametrize("target,expected", [("left", "left"), ("right", "right"), ("full", None)])
def test_manual_scene_choice_overrides_visual_speaker(target, expected):
    from cortex.render.auto_framing import SceneFramingOverride
    spans = _resolve(_plan([(0, 6_000_000)]), _faces(False), _speakers([(0, 6_000_000, "left")]),
                     overrides=[SceneFramingOverride(scene_index=0, target=target)])
    assert len(spans) == 1 and spans[0].track_id == expected
    assert spans[0].reason == ("manual_full_frame" if target == "full" else f"manual_{target}")


def test_auto_mode_requires_camera_plan_in_api_request():
    from cortex.api import RenderRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="camera_edit_plan_artifact_id"):
        RenderRequest(edit_plan_artifact_id="edit", render_settings=_render_settings("speaker_auto"))


def test_brief_uncertainty_does_not_flash_context_between_same_speaker():
    spans = _resolve(_plan([(0, 6_000_000)]), _faces(False),
                     _speakers([(125_000, 2_500_000, "left"), (3_000_000, 5_700_000, "left")]))
    assert len(spans) == 1
    assert spans[0].source_start_us == 0 and spans[0].source_end_us == 6_000_000
    assert spans[0].track_id == "left"
