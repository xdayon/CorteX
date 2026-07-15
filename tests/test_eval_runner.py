from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cortex.eval.runner import (
    EVAL_RUNNER_SCHEMA_VERSION,
    EvalDatasetError,
    check_against_baseline,
    evaluate,
    load_dataset,
    load_suggestion_document,
    run_eval,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_DATASET_DIR = REPO_ROOT / "eval" / "dataset"
BASELINE_PATH = REPO_ROOT / "eval" / "baseline.json"
CLI_SCRIPT = REPO_ROOT / "scripts" / "eval_selection.py"


def _clip(
    *,
    rank: int,
    start: float,
    end: float,
    duration: float,
    headline: str = "Headline",
    reasoning: str = "Reasoning",
) -> dict:
    return {
        "rank": rank,
        "title": f"Clip {rank}",
        "headline": headline,
        "start_second": start,
        "end_second": end,
        "estimated_duration": duration,
        "primary_speaker": "Speaker",
        "topic": "Topic",
        "pacing": "balanced",
        "hook": {"start_second": start, "end_second": start + 5, "summary": "hook", "evidence": "ev"},
        "context": {"start_second": start + 5, "end_second": end - 5, "summary": "context", "evidence": "ev"},
        "payoff": {"start_second": end - 5, "end_second": end, "summary": "payoff", "evidence": "ev"},
        "reasoning": reasoning,
        "warnings": [],
    }


def _dataset_entry() -> dict:
    return {
        "dataset_schema_version": 1,
        "episode": {
            "title": "Synthetic Episode",
            "source_sha256": "a" * 64,
            "duration_seconds": 1000.0,
            "transcript_artifact_hash": "b" * 64,
        },
        "decisions": [
            {
                "clip_ref": {"start_second": 10.0, "end_second": 40.0},
                "verdict": "approved",
                "theme": "theme-a",
                "duration_seconds": 30.0,
                "hook_summary": "hook a",
                "payoff_summary": "payoff a",
                "speakers": ["Speaker"],
                "reason": "great hook",
                "decided_by": "reviewer_a",
                "decided_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "clip_ref": {"start_second": 200.0, "end_second": 260.0},
                "verdict": "approved",
                "theme": "theme-b",
                "duration_seconds": 60.0,
                "hook_summary": "hook b",
                "payoff_summary": "payoff b",
                "speakers": ["Speaker"],
                "reason": "strong payoff",
                "decided_by": "reviewer_a",
                "decided_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "clip_ref": {"start_second": 500.0, "end_second": 560.0},
                "verdict": "rejected",
                "theme": "theme-c",
                "duration_seconds": 60.0,
                "hook_summary": "hook c",
                "payoff_summary": "payoff c",
                "speakers": ["Speaker"],
                "reason": "off-topic tangent",
                "decided_by": "reviewer_a",
                "decided_at": "2026-01-01T00:00:00+00:00",
            },
        ],
    }


def _suggestion_document() -> dict:
    return {
        "artifact_schema_version": 1,
        "created_at": "2026-01-02T00:00:00+00:00",
        "input_hash": "deadbeef1234",
        "transcript_artifact_id": "transcript-1",
        "analysis_artifact_id": None,
        "provenance": {
            "provider": "codex_cli",
            "model": "gpt-5.5",
            "prompt_sha256": "prompthash123",
            "schema_id": "https://hitechx.local/schemas/clip-selection-1.0.json",
        },
        "_evaluation_evidence": {
            "source_sha256": "a" * 64,
            "transcript_artifact_hash": "b" * 64,
            "words": [
                {"start": 11.0, "end": 15.0},
                {"start": 501.0, "end": 505.0},
                {"start": 701.0, "end": 705.0},
            ],
            "vad_intervals": [],
        },
        "selection": {
            "schema_version": "1.0",
            "selection_notes": "synthetic",
            "clips": [
                # Matches decision 1 (approved) closely -> covered.
                _clip(rank=1, start=10.0, end=42.0, duration=32.0),
                # Overlaps decision 3 (rejected) -> improper overlap. Missing headline.
                _clip(rank=2, start=500.0, end=555.0, duration=55.0, headline=""),
                # Does not overlap any decision.
                _clip(rank=3, start=700.0, end=750.0, duration=50.0),
            ],
        },
    }


def test_dataset_real_entry_validates_against_schema():
    entries = load_dataset(REAL_DATASET_DIR)
    titles = [entry["episode"]["title"] for entry in entries]
    assert any("Prosa Inversa" in title or "ETs" in title for title in titles)
    prosa = next(e for e in entries if e["_source_path"].endswith("prosa-inversa-20.json"))
    assert len(prosa["decisions"]) == 15
    assert all(d["decided_by"] == "pending_human_review" for d in prosa["decisions"])


def test_evaluate_computes_known_metrics_for_synthetic_fixture():
    report = evaluate(_suggestion_document(), [_dataset_entry()], iou_threshold=0.5)

    metrics = report["metrics"]
    assert metrics["clip_count"] == 3
    # Only decision 1 out of 2 approved decisions is matched -> partial coverage.
    assert metrics["coverage_all_approved"] == 0.5
    assert metrics["coverage_human_confirmed_approved"] == 0.5
    assert metrics["human_confirmed_approved_count"] == 2
    # The single rejected decision is overlapped by suggested clip 2.
    assert metrics["improper_rejected_overlap_rate"] == 1.0
    assert metrics["rejected_decision_count"] == 1
    # Clip 2 is missing headline -> 2 of 3 clips complete.
    assert metrics["completeness_rate"] == 2 / 3
    assert metrics["duration_within_bounds_rate"] == 1.0
    assert metrics["boundary_safe_rate"] == 1.0

    assert report["eval_runner_schema_version"] == EVAL_RUNNER_SCHEMA_VERSION
    assert report["suggestion_input_hash"] == "deadbeef1234"
    assert report["suggestion_provenance"]["prompt_sha256"] == "prompthash123"
    assert report["suggestion_provenance"]["provider"] == "codex_cli"

    clip_reports = {c["rank"]: c for c in report["clips"]}
    assert clip_reports[1]["matched_decision_verdict"] == "approved"
    assert clip_reports[1]["complete_fields"] is True
    assert clip_reports[2]["matched_decision_verdict"] == "rejected"
    assert clip_reports[2]["complete_fields"] is False
    assert clip_reports[3]["matched_decision_verdict"] is None


def test_check_against_baseline_reports_failures():
    report = evaluate(_suggestion_document(), [_dataset_entry()], iou_threshold=0.5)
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    passed, failures = check_against_baseline(report, baseline)

    assert passed is False
    assert any("improper_rejected_overlap_rate" in f for f in failures)
    assert any("completeness_rate" in f for f in failures)
    # coverage 0.5 satisfies the 0.5 baseline, so it should not be listed as a failure.
    assert not any("coverage_human_confirmed_approved" in f for f in failures)


def test_check_against_baseline_passes_lenient_baseline():
    report = evaluate(_suggestion_document(), [_dataset_entry()], iou_threshold=0.5)
    lenient_baseline = {
        "min_coverage_when_human_decisions": 0.0,
        "max_improper_rejected_overlap": 1.0,
        "min_completeness_rate": 0.5,
        "min_duration_within_bounds_rate": 1.0,
        "min_boundary_safe_rate": 1.0,
    }

    passed, failures = check_against_baseline(report, lenient_baseline)

    assert passed is True
    assert failures == []


def test_production_baseline_requires_reviewed_and_rejected_decisions():
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    assert baseline["baseline_schema_version"] == 2
    assert baseline["min_human_confirmed_approved_decisions"] > 0
    assert baseline["min_rejected_decisions"] > 0


@pytest.mark.parametrize(
    ("metric_field", "baseline_field", "reason_code"),
    [
        (
            "human_confirmed_approved_count",
            "min_human_confirmed_approved_decisions",
            "insufficient_human_confirmed_decisions",
        ),
        (
            "rejected_decision_count",
            "min_rejected_decisions",
            "insufficient_rejected_decisions",
        ),
    ],
)
def test_baseline_fails_closed_when_required_decision_count_is_zero(
    metric_field, baseline_field, reason_code,
):
    report = evaluate(_suggestion_document(), [_dataset_entry()])
    report["metrics"][metric_field] = 0

    passed, failures = check_against_baseline(report, {baseline_field: 1})

    assert passed is False
    assert any(f"[{reason_code}]" in failure for failure in failures)


@pytest.mark.parametrize(
    ("metric_field", "baseline_field", "reason_code"),
    [
        (
            "human_confirmed_approved_count",
            "min_human_confirmed_approved_decisions",
            "human_confirmed_decision_count_unavailable",
        ),
        (
            "rejected_decision_count",
            "min_rejected_decisions",
            "rejected_decision_count_unavailable",
        ),
    ],
)
def test_baseline_fails_closed_when_required_decision_count_is_missing(
    metric_field, baseline_field, reason_code,
):
    report = evaluate(_suggestion_document(), [_dataset_entry()])
    report["metrics"].pop(metric_field)

    passed, failures = check_against_baseline(report, {baseline_field: 1})

    assert passed is False
    assert any(f"[{reason_code}]" in failure for failure in failures)


def test_legacy_baseline_without_decision_minimums_remains_compatible():
    report = evaluate(_suggestion_document(), [_dataset_entry()])
    report["metrics"]["human_confirmed_approved_count"] = 0
    report["metrics"]["rejected_decision_count"] = 0

    passed, failures = check_against_baseline(report, {
        "baseline_schema_version": 1,
        "min_completeness_rate": 0.5,
    })

    assert passed is True
    assert failures == []


@pytest.mark.parametrize(
    "baseline_field",
    ["min_human_confirmed_approved_decisions", "min_rejected_decisions"],
)
def test_decision_minimum_must_be_a_positive_integer(baseline_field):
    report = evaluate(_suggestion_document(), [_dataset_entry()])

    passed, failures = check_against_baseline(report, {baseline_field: 0})

    assert passed is False
    assert any(f"[invalid_{baseline_field}]" in failure for failure in failures)


def test_episode_hash_mismatch_fails_closed():
    document = _suggestion_document()
    document["_evaluation_evidence"]["source_sha256"] = "f" * 64
    document["_evaluation_evidence"]["transcript_artifact_hash"] = "f" * 64

    with pytest.raises(EvalDatasetError, match="match exactly one"):
        evaluate(document, [_dataset_entry()])


def test_boundary_inside_word_is_unsafe_and_missing_evidence_is_unavailable():
    document = _suggestion_document()
    document["_evaluation_evidence"]["words"] = [{"start": 9.0, "end": 12.0}]
    report = evaluate(document, [_dataset_entry()])
    assert report["metrics"]["boundary_safe_rate"] == 2 / 3
    assert report["clips"][0]["boundary_evidence"] == "word_timestamps"

    document.pop("_evaluation_evidence")
    report = evaluate(document, [_dataset_entry()])
    assert report["metrics"]["boundary_safe_rate"] is None
    passed, failures = check_against_baseline(report, {"min_boundary_safe_rate": 1.0})
    assert passed is False
    assert "words/VAD evidence required" in failures[0]


def test_load_suggestion_resolves_hash_words_and_vad_from_artifacts(tmp_path):
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(json.dumps({
        "segments": [{"words": [{"start": 1.0, "end": 2.0, "word": "teste"}]}],
    }), encoding="utf-8")
    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text(json.dumps({
        "vad_intervals": [{"start": 0.5, "end": 2.5}],
    }), encoding="utf-8")
    suggestion_path = tmp_path / "suggestion.json"
    suggestion_path.write_text(json.dumps({
        "transcript_artifact_id": "transcript-1",
        "analysis_artifact_id": "analysis-1",
        "selection": {"clips": []},
    }), encoding="utf-8")

    class FakeDomainStore:
        def get_transcript_artifact(self, artifact_id):
            assert artifact_id == "transcript-1"
            return SimpleNamespace(
                path=str(transcript_path), audio_sha256="b" * 64,
                source_asset_id="source-1",
            )

        def get_source_asset(self, source_asset_id):
            assert source_asset_id == "source-1"
            return SimpleNamespace(sha256="a" * 64)

        def get_stage_artifact(self, artifact_id):
            assert artifact_id == "analysis-1"
            return SimpleNamespace(path=str(analysis_path), stage="analysis")

    document = load_suggestion_document(str(suggestion_path), FakeDomainStore())
    evidence = document["_evaluation_evidence"]
    assert evidence["source_sha256"] == "a" * 64
    assert evidence["transcript_artifact_hash"] == "b" * 64
    assert evidence["words"] == [{"start": 1.0, "end": 2.0}]
    assert evidence["vad_intervals"] == [{"start": 0.5, "end": 2.5}]


def test_run_eval_persists_valid_json_report(tmp_path):
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    (dataset_dir / "schema.json").write_text(
        (REAL_DATASET_DIR / "schema.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (dataset_dir / "synthetic.json").write_text(
        json.dumps(_dataset_entry()), encoding="utf-8"
    )

    suggestion_path = tmp_path / "suggestion.json"
    suggestion_path.write_text(json.dumps(_suggestion_document()), encoding="utf-8")

    output_path = tmp_path / "report.json"

    report, passed, failures = run_eval(
        dataset_dir=dataset_dir,
        suggestion_source=str(suggestion_path),
        output_path=output_path,
        iou_threshold=0.5,
        baseline_path=BASELINE_PATH,
        check=True,
    )

    assert output_path.exists()
    persisted = json.loads(output_path.read_text(encoding="utf-8"))
    assert persisted["metrics"]["clip_count"] == 3
    assert persisted["baseline_check_passed"] is False
    assert passed is False
    assert failures


def _write_cli_fixture(tmp_path: Path) -> tuple[Path, Path]:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    (dataset_dir / "schema.json").write_text(
        (REAL_DATASET_DIR / "schema.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (dataset_dir / "synthetic.json").write_text(
        json.dumps(_dataset_entry()), encoding="utf-8"
    )
    suggestion_path = tmp_path / "suggestion.json"
    suggestion_path.write_text(json.dumps(_suggestion_document()), encoding="utf-8")
    return dataset_dir, suggestion_path


def test_cli_check_mode_exit_code_nonzero_below_baseline(tmp_path):
    dataset_dir, suggestion_path = _write_cli_fixture(tmp_path)
    output_path = tmp_path / "report.json"

    result = subprocess.run(
        [
            sys.executable, str(CLI_SCRIPT),
            "--dataset", str(dataset_dir),
            "--suggestions", str(suggestion_path),
            "--output", str(output_path),
            "--check",
            "--baseline", str(BASELINE_PATH),
        ],
        capture_output=True, text=True,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAILED" in result.stdout


def test_cli_check_mode_exit_code_zero_above_baseline(tmp_path):
    dataset_dir, suggestion_path = _write_cli_fixture(tmp_path)
    output_path = tmp_path / "report.json"
    lenient_baseline_path = tmp_path / "baseline.json"
    lenient_baseline_path.write_text(json.dumps({
        "min_coverage_when_human_decisions": 0.0,
        "max_improper_rejected_overlap": 1.0,
        "min_completeness_rate": 0.5,
        "min_duration_within_bounds_rate": 1.0,
        "min_boundary_safe_rate": 1.0,
    }), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable, str(CLI_SCRIPT),
            "--dataset", str(dataset_dir),
            "--suggestions", str(suggestion_path),
            "--output", str(output_path),
            "--check",
            "--baseline", str(lenient_baseline_path),
        ],
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASSED" in result.stdout


def test_cli_before_after_persists_comparison(tmp_path):
    dataset_dir, before_path = _write_cli_fixture(tmp_path)
    after = _suggestion_document()
    after["selection"]["clips"] = after["selection"]["clips"][:1]
    after_path = tmp_path / "after.json"
    after_path.write_text(json.dumps(after), encoding="utf-8")
    output_path = tmp_path / "comparison.json"

    result = subprocess.run(
        [
            sys.executable, str(CLI_SCRIPT),
            "--dataset", str(dataset_dir),
            "--before", str(before_path),
            "--after", str(after_path),
            "--output", str(output_path),
        ],
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    comparison = json.loads(output_path.read_text(encoding="utf-8"))
    assert comparison["eval_comparison_schema_version"] == 1
    assert comparison["before"]["metrics"]["clip_count"] == 3
    assert comparison["after"]["metrics"]["clip_count"] == 1
    assert comparison["metric_deltas_after_minus_before"]["clip_count"] == -2.0
