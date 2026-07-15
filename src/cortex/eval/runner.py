from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

EVAL_RUNNER_SCHEMA_VERSION = 1
EVAL_BASELINE_SCHEMA_VERSION = 2

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASET_SCHEMA_PATH = REPO_ROOT / "eval" / "dataset" / "schema.json"

REQUIRED_CLIP_TEXT_FIELDS = ("headline", "reasoning")
REQUIRED_CLIP_BEAT_FIELDS = ("hook", "context", "payoff")

PENDING_DECIDED_BY = "pending_human_review"


class EvalDatasetError(ValueError):
    """Raised when a dataset entry fails schema validation."""


class SuggestionArtifactError(ValueError):
    """Raised when a suggestion artifact cannot be loaded or is malformed."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- Loading ------------------------------------------------------------


def load_dataset_schema(schema_path: Path | None = None) -> dict[str, Any]:
    path = schema_path or DEFAULT_DATASET_SCHEMA_PATH
    return json.loads(path.read_text(encoding="utf-8"))


def load_dataset(dataset_dir: Path, schema_path: Path | None = None) -> list[dict[str, Any]]:
    """Load and validate every dataset entry (*.json, excluding schema.json) in a directory."""
    dataset_dir = Path(dataset_dir)
    if not dataset_dir.is_dir():
        raise EvalDatasetError(f"dataset directory not found: {dataset_dir}")

    schema = load_dataset_schema(schema_path)
    validator = Draft202012Validator(schema)

    entries: list[dict[str, Any]] = []
    for entry_path in sorted(dataset_dir.glob("*.json")):
        if entry_path.name == "schema.json":
            continue
        document = json.loads(entry_path.read_text(encoding="utf-8"))
        errors = sorted(validator.iter_errors(document), key=str)
        if errors:
            first = errors[0]
            location = ".".join(str(part) for part in first.absolute_path) or "root"
            raise EvalDatasetError(
                f"dataset entry {entry_path} invalid at {location}: {first.message}"
            )
        document["_source_path"] = str(entry_path)
        entries.append(document)

    if not entries:
        raise EvalDatasetError(f"no dataset entries found in {dataset_dir}")
    return entries


def load_suggestion_document(source: str, domain_store: Any | None = None) -> dict[str, Any]:
    """Load a suggestion artifact document from a JSON file path or (optionally) a
    DomainStore stage artifact id. File-path loading is fully offline; artifact id
    lookup requires a DomainStore instance to resolve the id to a file path."""
    path = Path(source)
    if path.is_file():
        document = json.loads(path.read_text(encoding="utf-8"))
    elif domain_store is None:
        raise SuggestionArtifactError(
            f"suggestion source is not a file and no DomainStore was provided: {source}"
        )
    else:
        artifact = domain_store.get_stage_artifact(source)
        if artifact.stage != "suggestion":
            raise SuggestionArtifactError(
                f"stage artifact {source} is not a suggestion artifact (stage={artifact.stage})"
            )
        document = json.loads(Path(artifact.path).read_text(encoding="utf-8"))

    if domain_store is not None and document.get("transcript_artifact_id"):
        transcript_artifact = domain_store.get_transcript_artifact(
            document["transcript_artifact_id"]
        )
        source_asset = domain_store.get_source_asset(transcript_artifact.source_asset_id)
        transcript = json.loads(Path(transcript_artifact.path).read_text(encoding="utf-8"))
        words = [
            {"start": word["start"], "end": word["end"]}
            for segment in transcript.get("segments", [])
            for word in segment.get("words", [])
            if "start" in word and "end" in word
        ]
        vad_intervals: list[dict[str, float]] = []
        analysis_id = document.get("analysis_artifact_id")
        if analysis_id:
            analysis_artifact = domain_store.get_stage_artifact(analysis_id)
            if analysis_artifact.stage != "analysis":
                raise SuggestionArtifactError(
                    f"artifact {analysis_id} is not analysis (stage={analysis_artifact.stage})"
                )
            analysis = json.loads(Path(analysis_artifact.path).read_text(encoding="utf-8"))
            vad_intervals = analysis.get("vad_intervals", [])
        document["_evaluation_evidence"] = {
            "source_sha256": source_asset.sha256,
            "transcript_artifact_hash": transcript_artifact.audio_sha256,
            "words": words,
            "vad_intervals": vad_intervals,
        }
    return document


# -- Interval math --------------------------------------------------------


def interval_iou(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    overlap_start = max(a_start, b_start)
    overlap_end = min(a_end, b_end)
    overlap = max(0.0, overlap_end - overlap_start)
    if overlap <= 0.0:
        return 0.0
    union = (a_end - a_start) + (b_end - b_start) - overlap
    if union <= 0.0:
        return 0.0
    return overlap / union


# -- Episode matching -------------------------------------------------------


def select_episode(
    dataset_entries: list[dict[str, Any]], clips: list[dict[str, Any]],
    *, source_sha256: str | None = None, transcript_artifact_hash: str | None = None,
) -> dict[str, Any]:
    """Select exactly one episode, preferring artifact hashes over timing guesses."""
    if source_sha256 or transcript_artifact_hash:
        matched = [
            entry for entry in dataset_entries
            if (
                source_sha256 is None
                or entry["episode"]["source_sha256"] == source_sha256
            )
            and (
                transcript_artifact_hash is None
                or entry["episode"]["transcript_artifact_hash"] == transcript_artifact_hash
            )
        ]
        if len(matched) != 1:
            raise EvalDatasetError(
                "evaluation evidence must match exactly one dataset episode "
                f"(matched={len(matched)})"
            )
        return matched[0]

    if len(dataset_entries) != 1:
        raise EvalDatasetError(
            "suggestion has no source hash and dataset contains multiple episodes"
        )
    return dataset_entries[0]


# -- Metrics ----------------------------------------------------------------


def _clip_text_present(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _clip_beat_present(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    summary = value.get("summary")
    return _clip_text_present(summary)


def _clip_is_complete(clip: dict[str, Any]) -> bool:
    for field in REQUIRED_CLIP_TEXT_FIELDS:
        if not _clip_text_present(clip.get(field)):
            return False
    for field in REQUIRED_CLIP_BEAT_FIELDS:
        if not _clip_beat_present(clip.get(field)):
            return False
    return True


def _clip_within_duration_bounds(clip: dict[str, Any]) -> bool:
    duration = clip.get("estimated_duration")
    if not isinstance(duration, (int, float)):
        return False
    return 15.0 <= duration <= 180.0


def _clip_boundary_safe(
    clip: dict[str, Any], episode_duration_seconds: float, evidence: dict[str, Any]
) -> tuple[bool | None, str]:
    start = clip.get("start_second")
    end = clip.get("end_second")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return False, "invalid_interval"
    if start < 0 or end <= start:
        return False, "invalid_interval"
    if end > episode_duration_seconds:
        return False, "invalid_interval"
    boundaries = (float(start), float(end))
    words = evidence.get("words") or []
    if words:
        safe = all(
            not any(float(word["start"]) < boundary < float(word["end"]) for word in words)
            for boundary in boundaries
        )
        return safe, "word_timestamps"
    vad_intervals = evidence.get("vad_intervals") or []
    if vad_intervals:
        safe = all(
            not any(float(item["start"]) < boundary < float(item["end"]) for item in vad_intervals)
            for boundary in boundaries
        )
        return safe, "vad_intervals_conservative"
    return None, "missing_words_and_vad"


def evaluate(
    suggestion_document: dict[str, Any],
    dataset_entries: list[dict[str, Any]],
    *,
    iou_threshold: float = 0.5,
) -> dict[str, Any]:
    selection = suggestion_document.get("selection", {})
    clips: list[dict[str, Any]] = selection.get("clips", [])

    evidence = suggestion_document.get("_evaluation_evidence", {})
    episode_entry = select_episode(
        dataset_entries, clips,
        source_sha256=evidence.get("source_sha256"),
        transcript_artifact_hash=evidence.get("transcript_artifact_hash"),
    )
    episode_duration = float(episode_entry["episode"]["duration_seconds"])
    decisions = episode_entry["decisions"]

    approved_decisions = [d for d in decisions if d["verdict"] == "approved"]
    rejected_decisions = [d for d in decisions if d["verdict"] == "rejected"]
    human_approved_decisions = [
        d for d in approved_decisions if d["decided_by"] != PENDING_DECIDED_BY
    ]

    def best_iou(ref: dict[str, float]) -> float:
        best = 0.0
        for clip in clips:
            score = interval_iou(
                ref["start_second"], ref["end_second"],
                clip["start_second"], clip["end_second"],
            )
            best = max(best, score)
        return best

    matched_approved = sum(1 for d in approved_decisions if best_iou(d["clip_ref"]) >= iou_threshold)
    matched_human_approved = sum(
        1 for d in human_approved_decisions if best_iou(d["clip_ref"]) >= iou_threshold
    )
    matched_rejected = sum(1 for d in rejected_decisions if best_iou(d["clip_ref"]) >= iou_threshold)

    coverage_all = (matched_approved / len(approved_decisions)) if approved_decisions else None
    coverage_human_confirmed = (
        (matched_human_approved / len(human_approved_decisions))
        if human_approved_decisions
        else None
    )
    improper_rejected_overlap_rate = (
        (matched_rejected / len(rejected_decisions)) if rejected_decisions else None
    )

    clip_reports = []
    complete_count = 0
    within_duration_count = 0
    boundary_safe_count = 0
    for clip in clips:
        complete = _clip_is_complete(clip)
        within_duration = _clip_within_duration_bounds(clip)
        boundary_safe, boundary_method = _clip_boundary_safe(clip, episode_duration, evidence)
        complete_count += int(complete)
        within_duration_count += int(within_duration)
        boundary_safe_count += int(boundary_safe is True)

        matched_decision_verdict = None
        matched_iou = 0.0
        for decision in decisions:
            ref = decision["clip_ref"]
            score = interval_iou(
                ref["start_second"], ref["end_second"],
                clip["start_second"], clip["end_second"],
            )
            if score >= iou_threshold and score > matched_iou:
                matched_iou = score
                matched_decision_verdict = decision["verdict"]

        clip_reports.append({
            "rank": clip.get("rank"),
            "title": clip.get("title"),
            "start_second": clip.get("start_second"),
            "end_second": clip.get("end_second"),
            "estimated_duration": clip.get("estimated_duration"),
            "matched_decision_verdict": matched_decision_verdict,
            "matched_iou": round(matched_iou, 4),
            "complete_fields": complete,
            "within_duration_bounds": within_duration,
            "boundary_safe": boundary_safe,
            "boundary_evidence": boundary_method,
        })

    clip_count = len(clips)
    metrics = {
        "clip_count": clip_count,
        "coverage_all_approved": coverage_all,
        "coverage_human_confirmed_approved": coverage_human_confirmed,
        "human_confirmed_approved_count": len(human_approved_decisions),
        "improper_rejected_overlap_rate": improper_rejected_overlap_rate,
        "rejected_decision_count": len(rejected_decisions),
        "completeness_rate": (complete_count / clip_count) if clip_count else None,
        "duration_within_bounds_rate": (within_duration_count / clip_count) if clip_count else None,
        "boundary_safe_rate": (
            boundary_safe_count / clip_count
            if clip_count and all(item["boundary_safe"] is not None for item in clip_reports)
            else None
        ),
    }

    return {
        "eval_runner_schema_version": EVAL_RUNNER_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "dataset_entry_used": episode_entry.get("_source_path"),
        "episode_title": episode_entry["episode"]["title"],
        "suggestion_input_hash": suggestion_document.get("input_hash"),
        "suggestion_provenance": {
            "prompt_sha256": suggestion_document.get("provenance", {}).get("prompt_sha256"),
            "provider": suggestion_document.get("provenance", {}).get("provider"),
            "model": suggestion_document.get("provenance", {}).get("model"),
        },
        "thresholds": {"iou_threshold": iou_threshold},
        "metrics": metrics,
        "clips": clip_reports,
    }


# -- Baseline check ---------------------------------------------------------


def check_against_baseline(report: dict[str, Any], baseline: dict[str, Any]) -> tuple[bool, list[str]]:
    metrics = report["metrics"]
    failures: list[str] = []

    baseline_schema_version = baseline.get("baseline_schema_version")
    if baseline_schema_version is not None and (
        isinstance(baseline_schema_version, bool)
        or not isinstance(baseline_schema_version, int)
        or baseline_schema_version not in {1, EVAL_BASELINE_SCHEMA_VERSION}
    ):
        failures.append(
            "[unsupported_baseline_schema_version] baseline_schema_version "
            f"{baseline_schema_version!r} not in supported "
            f"[1, {EVAL_BASELINE_SCHEMA_VERSION}]"
        )

    def require_decision_count(
        *, baseline_field: str, metric_field: str, unavailable_code: str,
        insufficient_code: str,
    ) -> None:
        if baseline_field not in baseline:
            return

        minimum = baseline[baseline_field]
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum <= 0:
            failures.append(
                f"[invalid_{baseline_field}] {baseline_field} must be a positive integer, "
                f"got {minimum!r}"
            )
            return

        count = metrics.get(metric_field)
        if isinstance(count, bool) or not isinstance(count, int):
            failures.append(
                f"[{unavailable_code}] {metric_field} unavailable; baseline requires "
                f"at least {minimum}"
            )
        elif count < minimum:
            failures.append(
                f"[{insufficient_code}] {metric_field} {count} < baseline {minimum}"
            )

    require_decision_count(
        baseline_field="min_human_confirmed_approved_decisions",
        metric_field="human_confirmed_approved_count",
        unavailable_code="human_confirmed_decision_count_unavailable",
        insufficient_code="insufficient_human_confirmed_decisions",
    )
    require_decision_count(
        baseline_field="min_rejected_decisions",
        metric_field="rejected_decision_count",
        unavailable_code="rejected_decision_count_unavailable",
        insufficient_code="insufficient_rejected_decisions",
    )

    min_coverage = baseline.get("min_coverage_when_human_decisions")
    if min_coverage is not None and metrics["human_confirmed_approved_count"] > 0:
        coverage = metrics["coverage_human_confirmed_approved"]
        if coverage is None or coverage < min_coverage:
            failures.append(
                f"coverage_human_confirmed_approved {coverage} < baseline {min_coverage}"
            )

    max_improper = baseline.get("max_improper_rejected_overlap")
    if max_improper is not None and metrics["improper_rejected_overlap_rate"] is not None:
        if metrics["improper_rejected_overlap_rate"] > max_improper:
            failures.append(
                "improper_rejected_overlap_rate "
                f"{metrics['improper_rejected_overlap_rate']} > baseline {max_improper}"
            )

    min_completeness = baseline.get("min_completeness_rate")
    if min_completeness is not None and metrics["completeness_rate"] is not None:
        if metrics["completeness_rate"] < min_completeness:
            failures.append(
                f"completeness_rate {metrics['completeness_rate']} < baseline {min_completeness}"
            )

    min_duration_bounds = baseline.get("min_duration_within_bounds_rate")
    if min_duration_bounds is not None and metrics["duration_within_bounds_rate"] is not None:
        if metrics["duration_within_bounds_rate"] < min_duration_bounds:
            failures.append(
                "duration_within_bounds_rate "
                f"{metrics['duration_within_bounds_rate']} < baseline {min_duration_bounds}"
            )

    min_boundary_safe = baseline.get("min_boundary_safe_rate")
    if min_boundary_safe is not None:
        if metrics["boundary_safe_rate"] is None:
            failures.append("boundary_safe_rate unavailable: words/VAD evidence required")
        elif metrics["boundary_safe_rate"] < min_boundary_safe:
            failures.append(
                f"boundary_safe_rate {metrics['boundary_safe_rate']} < baseline {min_boundary_safe}"
            )

    return (len(failures) == 0, failures)


def run_eval(
    *,
    dataset_dir: Path,
    suggestion_source: str,
    output_path: Path,
    iou_threshold: float = 0.5,
    domain_store: Any | None = None,
    baseline_path: Path | None = None,
    check: bool = False,
) -> tuple[dict[str, Any], bool, list[str]]:
    dataset_entries = load_dataset(dataset_dir)
    suggestion_document = load_suggestion_document(suggestion_source, domain_store=domain_store)
    report = evaluate(suggestion_document, dataset_entries, iou_threshold=iou_threshold)

    passed = True
    failures: list[str] = []
    if check:
        if baseline_path is None:
            raise ValueError("--check requires a baseline path")
        baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
        report["baseline_used"] = str(baseline_path)
        report["baseline"] = baseline
        passed, failures = check_against_baseline(report, baseline)
        report["baseline_check_passed"] = passed
        report["baseline_check_failures"] = failures

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report, passed, failures


def compare_eval_reports(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Build a persisted, machine-readable before/after metric comparison."""
    metric_names = sorted(set(before["metrics"]) | set(after["metrics"]))
    deltas: dict[str, float | int | None] = {}
    for name in metric_names:
        old = before["metrics"].get(name)
        new = after["metrics"].get(name)
        deltas[name] = (
            round(float(new) - float(old), 6)
            if isinstance(old, (int, float)) and isinstance(new, (int, float))
            else None
        )
    return {
        "eval_comparison_schema_version": 1,
        "generated_at": utc_now_iso(),
        "before": before,
        "after": after,
        "metric_deltas_after_minus_before": deltas,
    }
