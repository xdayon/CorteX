from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

from cortex.render.safe_zones import SAFE_ZONES
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


def _make_overlay(ffmpeg: Path, path: Path, boxes: list[tuple[int, int, int, int]]) -> None:
    """Gera um WebM VP9 yuva420p transparente com retângulos opacos nas posições
    (x, y, w, h) informadas, para exercer a análise de pixels alpha. O canal
    alpha é escrito explicitamente por `geq` porque `drawbox` não altera o plano
    alpha; o decoder libvpx-vp9 é quem preserva o alpha no roundtrip."""
    conditions = "+".join(
        f"between(X,{x},{x + w})*between(Y,{y},{y + h})"
        for (x, y, w, h) in boxes
    )
    alpha_expr = f"if(gt({conditions},0),255,0)"
    subprocess.run([
        str(ffmpeg), "-y", "-v", "error", "-f", "lavfi", "-i",
        f"color=c=white:size={_WIDTH}x{_HEIGHT}:rate=4:duration=0.5",
        "-vf", f"format=yuva420p,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{alpha_expr}'",
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", str(path),
    ], check=True, timeout=60)


def _service(ffmpeg: Path) -> RenderService:
    service = RenderService.__new__(RenderService)
    service._config = SimpleNamespace(
        render=SimpleNamespace(ffmpeg=ffmpeg, safe_zone_sample_fps=8.0)
    )
    return service


def test_safe_zone_issues_detect_real_overlay_overflow(tmp_path: Path) -> None:
    ffmpeg = Path(shutil.which("ffmpeg") or "ffmpeg")
    service = _service(ffmpeg)
    zone = SAFE_ZONES["9:16"]

    inside = tmp_path / "inside.webm"
    # headline dentro da metade superior; legenda dentro da metade inferior.
    _make_overlay(ffmpeg, inside, [(200, 300, 400, 200), (200, 1400, 400, 120)])
    assert service._safe_zone_issues(
        overlay_path=str(inside), width=_WIDTH, height=_HEIGHT, safe_zone=zone
    ) == []

    # A caption whose ink begins exactly at the 16% lower margin would spill
    # once its stroke/shadow is included. The renderer keeps a 20px inset.
    inset = tmp_path / "caption-inset.webm"
    _make_overlay(ffmpeg, inset, [(200, 1590, 400, 20)])
    assert service._safe_zone_issues(
        overlay_path=str(inset), width=_WIDTH, height=_HEIGHT, safe_zone=zone
    ) == []

    outside = tmp_path / "outside.webm"
    # caixa colada no canto superior esquerdo (viola top+left) e caixa que
    # ultrapassa a margem inferior.
    _make_overlay(ffmpeg, outside, [(10, 10, 300, 100), (200, 1700, 400, 200)])
    issues = service._safe_zone_issues(
        overlay_path=str(outside), width=_WIDTH, height=_HEIGHT, safe_zone=zone
    )
    assert "headline_outside_safe_zone" in issues
    assert "caption_outside_safe_zone" in issues


def test_safe_zone_missing_zone_and_absent_overlay() -> None:
    service = RenderService.__new__(RenderService)
    assert service._safe_zone_issues(
        overlay_path=None, width=_WIDTH, height=_HEIGHT, safe_zone=None
    ) == ["safe_zone_missing"]
    assert service._safe_zone_issues(
        overlay_path=None, width=_WIDTH, height=_HEIGHT, safe_zone=SAFE_ZONES["9:16"]
    ) == []


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
