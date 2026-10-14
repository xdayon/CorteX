from __future__ import annotations

import hashlib
from pathlib import Path

from cortex.render.schemas import RenderQualityPublicationReport
from cortex.render.schemas import RenderDocument
from cortex.render.service import RenderService, _publication_checks
from cortex.domain.store import DomainStore
from test_render import _config
from test_render_punch_in import (
    _make_plan_artifact,
    _make_transcript,
    _settings,
    _stripe_source,
)
from cortex.edit.schemas import EditSegment

_WIDTH = 1080
_HEIGHT = 1920


def test_publication_report_keeps_reason_codes_and_verdict() -> None:
    checks = _publication_checks(
        issues=["loudness_out_of_tolerance"],
        warnings=["true_peak_near_limit"],
        evidence={
            "loudness_out_of_tolerance": {"delta_lu": 1.5},
            "true_peak_near_limit": {"true_peak_dbfs": -1.1},
            "full_decode_failed": {"passed": True},
        },
        thresholds={
            "loudness_out_of_tolerance": {"maximum_delta_lu": 1.0},
            "true_peak_near_limit": {"warning_margin_db": 0.25},
        },
    )
    report = RenderQualityPublicationReport(
        publish_ready=False,
        reasons=["headline_outside_safe_zone", "loudness_out_of_tolerance"],
        warnings=["true_peak_near_limit"],
        checks=checks,
        loudness={"target_lufs": -14.0, "integrated_lufs": -12.5, "true_peak_dbfs": -0.3},
        visual={"blackdetect": 1, "freezedetect": 0, "visual_analysis_performed": True, "black_threshold_seconds": 0.2, "freeze_threshold_seconds": 0.4},
        captions={"cue_count": 4, "subtitles_present": True, "headline_present": True},
        safe_zones={"version": 1, "canvas": "9:16", "margins": {"top": 0.07}},
        encoder={"requested": "libx264", "effective": "libx264"},
        dimensions={"width": 1080, "height": 1920, "fps": 30, "duration_seconds": 3.2},
        hashes={"input_hash": "a" * 64, "overlay_hash": "b" * 64, "render_sha256": None},
        provenance={"ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "node": "node", "node_version": "v20", "remotion_version": "4.0.489"},
    )
    assert report.publish_ready is False
    assert report.reasons == ["headline_outside_safe_zone", "loudness_out_of_tolerance"]
    assert [(check.code, check.status, check.severity) for check in report.checks] == [
        ("full_decode_failed", "pass", "info"),
        ("loudness_out_of_tolerance", "fail", "blocking"),
        ("true_peak_near_limit", "warning", "warning"),
    ]
    assert report.checks[1].evidence == {"delta_lu": 1.5}
    assert report.checks[1].thresholds == {"maximum_delta_lu": 1.0}


def test_blocked_render_persists_manifest_and_is_not_exported(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.render.freeze_threshold_seconds = 0.2
    config.render.freeze_block_threshold_seconds = 0.2
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Blocked quality fixture")
    source = _stripe_source(config, domain, tmp_path, project.id, duration=1.0)
    transcript = _make_transcript(domain, tmp_path, project.id, source.id, duration=1.0)
    plan = _make_plan_artifact(
        domain,
        tmp_path,
        project.id,
        source.id,
        transcript.id,
        [EditSegment(start=0.0, end=1.0, timeline_order=0)],
        1.0,
    )
    export_dir = tmp_path / "exports"

    result = RenderService(config, domain).run(
        edit_plan_artifact=plan,
        encoder=None,
        headline=None,
        progress_cb=lambda *_args: None,
        should_cancel=lambda: False,
        render_settings=_settings(scale=1.0, alternate=False),
        export_directory=str(export_dir),
    )

    assert result["publish_ready"] is False
    assert "export_path" not in result
    artifact = domain.get_stage_artifact(result["render_artifact_id"])
    document = RenderDocument.model_validate_json(Path(artifact.path).read_text(encoding="utf-8"))
    output = Path(document.output_path)
    assert output.is_file()
    assert document.publication is not None
    assert document.publication.publish_ready is False
    assert "frozen_frame_detected" in document.publication.reasons
    frozen_check = next(
        check for check in document.publication.checks
        if check.code == "frozen_frame_detected"
    )
    assert frozen_check.status == "fail"
    assert frozen_check.severity == "blocking"
    assert frozen_check.evidence["interval_count"] >= 1
    assert frozen_check.thresholds["detection_interval_seconds"] == 0.2
    assert frozen_check.thresholds["blocking_interval_seconds"] == 0.2
    assert document.publication.hashes["render_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert artifact.metadata["quality_passed"] is False
    assert not export_dir.exists()

    cached = RenderService(config, domain).run(
        edit_plan_artifact=plan,
        encoder=None,
        headline=None,
        progress_cb=lambda *_args: None,
        should_cancel=lambda: False,
        render_settings=_settings(scale=1.0, alternate=False),
        export_directory=str(export_dir),
    )
    assert cached["cached"] is True
    assert cached["publish_ready"] is False
    assert "export_path" not in cached
    assert not export_dir.exists()
