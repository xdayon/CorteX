from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "gate9_benchmark.py"
SPEC = importlib.util.spec_from_file_location("gate9_benchmark", SCRIPT)
assert SPEC and SPEC.loader
gate9 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate9)


def _artifact(path: Path, kind: str) -> dict:
    return {
        "kind": kind,
        "path": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _scenario(
    scenario_id: str,
    *,
    source_kind: str,
    duration_kind: str,
    clip_count: int,
    framing_mode: str,
    toggles: bool,
    editing: list[str],
    encoder: str,
    artifact: dict,
    report: dict,
    lifecycle: dict[str, bool],
) -> dict:
    resources = {"ram_mb": 512, "cpu_percent": 65}
    if encoder == "h264_nvenc":
        resources.update({"vram_mb": 800, "gpu_percent": 72})
    return {
        "id": scenario_id,
        "status": "passed",
        "source": {"kind": source_kind, "duration_kind": duration_kind},
        "clip_count": clip_count,
        "features": {
            "framing_mode": framing_mode,
            "captions": toggles,
            "karaoke": toggles,
            "headline": toggles,
            "editing": editing,
        },
        "engines": {
            "transcription": {
                "requested": {"model": "large-v3-turbo", "device": "cuda", "compute_type": "int8"},
                "effective": {"model": "large-v3-turbo", "device": "cuda", "compute_type": "int8"},
            },
            "render": {
                "requested": {"encoder": encoder},
                "effective": {"encoder": encoder},
            },
        },
        "stages": [
            {"name": "transcribe", "duration_seconds": 2, "retries": 1, "cache_hit": False},
            {"name": "render", "duration_seconds": 3, "retries": 0, "cache_hit": lifecycle["cache_hit"]},
        ],
        "total_duration_seconds": 6,
        "media_duration_seconds": 12,
        "resources": resources,
        "lifecycle": lifecycle,
        "artifacts": [artifact, report],
    }


def _complete_matrix(tmp_path: Path) -> dict:
    media = tmp_path / "render.mp4"
    media.write_bytes(b"synthetic rendered media")
    persisted_report = tmp_path / "render-report.json"
    persisted_report.write_text('{"publish_ready":true}\n', encoding="utf-8")
    artifact = _artifact(media, "render")
    report = _artifact(persisted_report, "report")
    return {
        "matrix_schema_version": 1,
        "scenarios": [
            _scenario(
                "local-short-one", source_kind="local", duration_kind="short", clip_count=1,
                framing_mode="vertical_crop", toggles=True, editing=["reaction", "j_cut"],
                encoder="h264_nvenc", artifact=artifact, report=report,
                lifecycle={"cancellation": True, "resume": False, "cache_hit": False},
            ),
            _scenario(
                "youtube-long-ten", source_kind="youtube", duration_kind="long", clip_count=10,
                framing_mode="blurred_background", toggles=False, editing=["l_cut", "punch_in"],
                encoder="libx264", artifact=artifact, report=report,
                lifecycle={"cancellation": False, "resume": True, "cache_hit": True},
            ),
            _scenario(
                "local-long-twenty-five", source_kind="local", duration_kind="long", clip_count=25,
                framing_mode="face_static_crop", toggles=True, editing=[],
                encoder="h264_nvenc", artifact=artifact, report=report,
                lifecycle={"cancellation": False, "resume": False, "cache_hit": False},
            ),
        ],
        "comparisons": [
            {"kind": "editorial", "metric": "approval_rate", "before": 0.5, "after": 0.75},
            {"kind": "technical", "metric": "quality_pass_rate", "before": 0.8, "after": 1.0},
        ],
        "risks": ["Human review remains required"],
    }


def test_evaluate_complete_matrix_calculates_metrics_and_verifies_artifacts(tmp_path: Path) -> None:
    matrix = _complete_matrix(tmp_path)
    report, passed = gate9.evaluate(matrix, base_dir=tmp_path, matrix_sha256="a" * 64)

    assert passed is True
    assert report["status"] == "complete"
    assert report["errors"] == []
    assert report["metrics"]["stage_duration_seconds"] == {"render": 9.0, "transcribe": 6.0}
    assert report["metrics"]["total_duration_seconds"] == 18.0
    assert report["metrics"]["failed_scenario_count"] == 0
    assert report["metrics"]["render_time_per_media_second"] == 0.25
    assert report["metrics"]["retries"] == 3
    assert report["metrics"]["cache_hits"] == 1
    assert report["metrics"]["resource_peaks"] == {
        "ram_mb": 512.0, "vram_mb": 800.0, "cpu_percent": 65.0, "gpu_percent": 72.0,
    }
    expected_size = sum(path.stat().st_size for path in [tmp_path / "render.mp4", tmp_path / "render-report.json"])
    assert report["metrics"]["artifact_size_bytes"] == expected_size * 3
    assert report["coverage"]["missing"] == {}
    assert all(item["passed"] for item in report["scenarios"])
    assert report["scenarios"][0]["configuration"]["engines"]["render"]["effective"] == {
        "encoder": "h264_nvenc"
    }


def test_not_applicable_editing_evaluation_counts_as_coverage_and_adds_risk(tmp_path: Path) -> None:
    matrix = _complete_matrix(tmp_path)
    features = matrix["scenarios"][1]["features"]
    features["editing"].remove("punch_in")
    features["editing_evaluations"] = {
        "punch_in": {
            "status": "not_applicable",
            "reason": "No stable face track exists in this source.",
        }
    }

    report, passed = gate9.evaluate(matrix, base_dir=tmp_path, matrix_sha256="d" * 64)

    assert passed is True
    assert report["coverage"]["missing"] == {}
    assert "punch_in" in report["coverage"]["editing_features"]
    assert any(
        "editing feature punch_in was not applicable: No stable face track exists in this source."
        in risk
        for risk in report["risks"]
    )


def test_not_applicable_editing_evaluation_requires_non_empty_reason(tmp_path: Path) -> None:
    matrix = _complete_matrix(tmp_path)
    features = matrix["scenarios"][1]["features"]
    features["editing"].remove("punch_in")
    features["editing_evaluations"] = {
        "punch_in": {"status": "not_applicable", "reason": "  "}
    }

    report, passed = gate9.evaluate(matrix, base_dir=tmp_path, matrix_sha256="e" * 64)

    assert passed is False
    assert any("not_applicable requires a reason" in error for error in report["errors"])
    assert report["coverage"]["missing"]["editing_features"] == ["punch_in"]


def test_editing_evaluation_rejects_contradictory_not_applicable_status(tmp_path: Path) -> None:
    matrix = _complete_matrix(tmp_path)
    matrix["scenarios"][1]["features"]["editing_evaluations"] = {
        "punch_in": {"status": "not_applicable", "reason": "Not needed."}
    }

    report, passed = gate9.evaluate(matrix, base_dir=tmp_path, matrix_sha256="f" * 64)

    assert passed is False
    assert any("not_applicable but present in editing" in error for error in report["errors"])


def test_editing_evaluation_rejects_invalid_or_inconsistent_applied_status(tmp_path: Path) -> None:
    invalid_matrix = _complete_matrix(tmp_path)
    invalid_matrix["scenarios"][0]["features"]["editing_evaluations"] = {
        "reaction": {"status": "skipped"}
    }
    invalid_report, invalid_passed = gate9.evaluate(
        invalid_matrix, base_dir=tmp_path, matrix_sha256="1" * 64
    )

    inconsistent_matrix = _complete_matrix(tmp_path)
    inconsistent_matrix["scenarios"][2]["features"]["editing_evaluations"] = {
        "reaction": {"status": "applied"}
    }
    inconsistent_report, inconsistent_passed = gate9.evaluate(
        inconsistent_matrix, base_dir=tmp_path, matrix_sha256="2" * 64
    )

    assert invalid_passed is False
    assert any("has invalid status" in error for error in invalid_report["errors"])
    assert inconsistent_passed is False
    assert any("is applied but missing from editing" in error for error in inconsistent_report["errors"])


def test_evaluate_fails_closed_on_engine_and_artifact_hash_mismatch(tmp_path: Path) -> None:
    matrix = _complete_matrix(tmp_path)
    matrix["scenarios"][0]["engines"]["render"]["effective"]["encoder"] = "libx264"
    matrix["scenarios"][0]["artifacts"][0]["sha256"] = "0" * 64

    report, passed = gate9.evaluate(matrix, base_dir=tmp_path, matrix_sha256="b" * 64)

    assert passed is False
    assert report["status"] == "incomplete"
    assert any("render requested/effective engines differ" in error for error in report["errors"])
    assert any("SHA-256 mismatch" in error for error in report["errors"])
    assert report["scenarios"][0]["passed"] is False


def test_evaluate_rejects_artifact_outside_matrix_directory(tmp_path: Path) -> None:
    matrix_dir = tmp_path / "matrix"
    matrix_dir.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"must remain outside")
    report_file = matrix_dir / "report.json"
    report_file.write_text("{}\n", encoding="utf-8")
    matrix = _complete_matrix(matrix_dir)
    matrix["scenarios"][0]["artifacts"][0] = {
        "kind": "render",
        "path": "../outside.mp4",
        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
    }

    report, passed = gate9.evaluate(matrix, base_dir=matrix_dir, matrix_sha256="c" * 64)

    assert passed is False
    assert any("escapes matrix directory" in error for error in report["errors"])


def test_cli_check_persists_incomplete_report_and_never_executes_json_command(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    matrix = {
        "matrix_schema_version": 1,
        "command": f"touch {marker}",
        "scenarios": [],
        "comparisons": [],
        "risks": ["not run"],
    }
    matrix_path = tmp_path / "matrix.json"
    output = tmp_path / "gate9-report.json"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--matrix", str(matrix_path), "--output", str(output), "--check"],
        capture_output=True, text=True, timeout=30, check=False,
    )

    assert result.returncode == 1
    assert output.is_file()
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "incomplete"
    assert not marker.exists()


def test_cli_check_persists_complete_report(tmp_path: Path) -> None:
    matrix_path = tmp_path / "matrix.json"
    output = tmp_path / "gate9-report.json"
    matrix_path.write_text(json.dumps(_complete_matrix(tmp_path)), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--matrix", str(matrix_path), "--output", str(output), "--check"],
        capture_output=True, text=True, timeout=30, check=False,
    )

    assert result.returncode == 0, result.stderr
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted["check_passed"] is True
    assert persisted["matrix_sha256"] == hashlib.sha256(matrix_path.read_bytes()).hexdigest()


def test_cli_artifact_root_keeps_matrix_separate_from_real_artifacts(tmp_path: Path) -> None:
    matrix_dir = tmp_path / "eval"
    artifact_root = tmp_path / "workspace"
    matrix_dir.mkdir()
    artifact_root.mkdir()
    matrix = _complete_matrix(artifact_root)
    matrix_path = matrix_dir / "matrix.json"
    output = matrix_dir / "report.json"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable, str(SCRIPT), "--matrix", str(matrix_path),
            "--artifact-root", str(artifact_root), "--output", str(output), "--check",
        ],
        capture_output=True, text=True, timeout=30, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["check_passed"] is True
