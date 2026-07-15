#!/usr/bin/env python3
"""Validate and summarize a declarative Gate 9 host benchmark matrix.

This command never runs media tools or commands embedded in the input. Host runs
must be performed separately and recorded in the matrix with persisted artifact
paths and SHA-256 hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_SCHEMA_VERSION = 1
REQUIRED_SETS = {
    "source_kinds": {"local", "youtube"},
    "duration_kinds": {"long", "short"},
    "clip_counts": {1, 10, 25},
    "framing_modes": {"vertical_crop", "blurred_background", "face_static_crop"},
    "editing_features": {"reaction", "j_cut", "l_cut", "punch_in"},
    "render_paths": {"nvenc", "cpu"},
}
REQUIRED_TOGGLES = {"captions", "karaoke", "headline"}
REQUIRED_LIFECYCLE = {"cancellation", "resume", "cache_hit"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None


def _engine_equal(engine: Any) -> bool:
    return (
        isinstance(engine, dict)
        and isinstance(engine.get("requested"), dict)
        and engine.get("requested") == engine.get("effective")
    )


def evaluate(matrix: dict[str, Any], *, base_dir: Path, matrix_sha256: str) -> tuple[dict[str, Any], bool]:
    base_dir = base_dir.resolve()
    errors: list[str] = []
    risks: list[str] = []
    scenarios = matrix.get("scenarios")
    if matrix.get("matrix_schema_version") != 1:
        errors.append("matrix_schema_version must be 1")
    if not isinstance(scenarios, list) or not scenarios:
        errors.append("scenarios must be a non-empty list")
        scenarios = []

    coverage: dict[str, set[Any]] = {name: set() for name in REQUIRED_SETS}
    toggle_coverage = {name: set() for name in REQUIRED_TOGGLES}
    lifecycle_coverage: set[str] = set()
    stage_totals: dict[str, float] = defaultdict(float)
    total_seconds = 0.0
    media_seconds = 0.0
    artifact_bytes = 0
    retries = 0
    cache_hits = 0
    resource_peaks = {"ram_mb": 0.0, "vram_mb": 0.0, "cpu_percent": 0.0, "gpu_percent": 0.0}
    scenario_reports: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for index, raw in enumerate(scenarios):
        label = f"scenario[{index}]"
        scenario_errors: list[str] = []
        if not isinstance(raw, dict):
            errors.append(f"{label} must be an object")
            continue
        scenario_id = raw.get("id")
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            scenario_errors.append("id is required")
            scenario_id = label
        elif scenario_id in seen_ids:
            scenario_errors.append("id must be unique")
        seen_ids.add(str(scenario_id))
        prefix = f"scenario {scenario_id}"

        if raw.get("status") != "passed":
            scenario_errors.append("status is not passed")
        source = raw.get("source", {})
        source_kind = source.get("kind") if isinstance(source, dict) else None
        duration_kind = source.get("duration_kind") if isinstance(source, dict) else None
        coverage["source_kinds"].add(source_kind)
        coverage["duration_kinds"].add(duration_kind)
        clip_count = raw.get("clip_count")
        coverage["clip_counts"].add(clip_count)

        features = raw.get("features", {})
        if not isinstance(features, dict):
            features = {}
            scenario_errors.append("features must be an object")
        coverage["framing_modes"].add(features.get("framing_mode"))
        editing = features.get("editing", [])
        if not isinstance(editing, list):
            editing = []
        for feature in editing:
            coverage["editing_features"].add(feature)
        editing_evaluations = features.get("editing_evaluations")
        if editing_evaluations is not None and not isinstance(editing_evaluations, dict):
            scenario_errors.append("features.editing_evaluations must be an object")
        elif isinstance(editing_evaluations, dict):
            for feature, evaluation in editing_evaluations.items():
                if feature not in REQUIRED_SETS["editing_features"]:
                    scenario_errors.append(
                        f"features.editing_evaluations has unknown feature {feature}"
                    )
                    continue
                if not isinstance(evaluation, dict):
                    scenario_errors.append(
                        f"features.editing_evaluations.{feature} must be an object"
                    )
                    continue
                status = evaluation.get("status")
                if status == "applied":
                    if feature not in editing:
                        scenario_errors.append(
                            f"features.editing_evaluations.{feature} is applied but missing from editing"
                        )
                elif status == "not_applicable":
                    reason = evaluation.get("reason")
                    if feature in editing:
                        scenario_errors.append(
                            f"features.editing_evaluations.{feature} is not_applicable but present in editing"
                        )
                    elif not isinstance(reason, str) or not reason.strip():
                        scenario_errors.append(
                            f"features.editing_evaluations.{feature} not_applicable requires a reason"
                        )
                    else:
                        coverage["editing_features"].add(feature)
                        risks.append(
                            f"scenario {scenario_id}: editing feature {feature} was not applicable: "
                            f"{reason.strip()}"
                        )
                else:
                    scenario_errors.append(
                        f"features.editing_evaluations.{feature} has invalid status"
                    )
        for toggle in REQUIRED_TOGGLES:
            value = features.get(toggle)
            if isinstance(value, bool):
                toggle_coverage[toggle].add(value)

        engines = raw.get("engines", {})
        transcription = engines.get("transcription") if isinstance(engines, dict) else None
        render = engines.get("render") if isinstance(engines, dict) else None
        if not _engine_equal(transcription):
            scenario_errors.append("transcription requested/effective engines differ or are missing")
        if not _engine_equal(render):
            scenario_errors.append("render requested/effective engines differ or are missing")
        requested_transcription = transcription.get("requested", {}) if isinstance(transcription, dict) else {}
        if not ({"model": "large-v3-turbo", "device": "cuda", "compute_type": "int8"}.items()
                <= requested_transcription.items()):
            scenario_errors.append("transcription is not requested as large-v3-turbo CUDA int8")
        requested_render = render.get("requested", {}) if isinstance(render, dict) else {}
        encoder = requested_render.get("encoder")
        if encoder == "h264_nvenc":
            coverage["render_paths"].add("nvenc")
            render_path = "nvenc"
        elif encoder == "libx264":
            coverage["render_paths"].add("cpu")
            render_path = "cpu"
        else:
            render_path = None
            scenario_errors.append("render encoder must explicitly request h264_nvenc or libx264")

        stages = raw.get("stages")
        scenario_stage_total = 0.0
        if not isinstance(stages, list) or not stages:
            scenario_errors.append("stages must be a non-empty list")
            stages = []
        for stage_index, stage in enumerate(stages):
            if not isinstance(stage, dict) or not isinstance(stage.get("name"), str):
                scenario_errors.append(f"stage[{stage_index}] is invalid")
                continue
            duration = _number(stage.get("duration_seconds"))
            if duration is None:
                scenario_errors.append(f"stage {stage['name']} has invalid duration_seconds")
                continue
            scenario_stage_total += duration
            stage_totals[stage["name"]] += duration
            stage_retries = stage.get("retries", 0)
            if not isinstance(stage_retries, int) or isinstance(stage_retries, bool) or stage_retries < 0:
                scenario_errors.append(f"stage {stage['name']} has invalid retries")
            else:
                retries += stage_retries
            if stage.get("cache_hit") is True:
                cache_hits += 1
        declared_total = _number(raw.get("total_duration_seconds"))
        if declared_total is None:
            scenario_errors.append("total_duration_seconds is required")
        elif declared_total + 0.001 < scenario_stage_total:
            scenario_errors.append("total_duration_seconds is smaller than summed stage time")
        else:
            total_seconds += declared_total
        media_duration = _number(raw.get("media_duration_seconds"))
        if media_duration is None or media_duration == 0:
            scenario_errors.append("media_duration_seconds must be positive")
        else:
            media_seconds += media_duration

        resources = raw.get("resources", {})
        if not isinstance(resources, dict):
            resources = {}
        for metric in ("ram_mb", "cpu_percent"):
            value = _number(resources.get(metric))
            if value is None:
                scenario_errors.append(f"resources.{metric} is required")
            else:
                resource_peaks[metric] = max(resource_peaks[metric], value)
        if render_path == "nvenc":
            for metric in ("vram_mb", "gpu_percent"):
                value = _number(resources.get(metric))
                if value is None:
                    scenario_errors.append(f"resources.{metric} is required for NVENC")
                else:
                    resource_peaks[metric] = max(resource_peaks[metric], value)

        lifecycle = raw.get("lifecycle", {})
        if isinstance(lifecycle, dict):
            lifecycle_coverage.update(key for key in REQUIRED_LIFECYCLE if lifecycle.get(key) is True)

        artifacts = raw.get("artifacts")
        report_found = False
        artifact_results: list[dict[str, Any]] = []
        if not isinstance(artifacts, list) or not artifacts:
            scenario_errors.append("artifacts must be a non-empty list")
            artifacts = []
        for artifact_index, artifact in enumerate(artifacts):
            if not isinstance(artifact, dict):
                scenario_errors.append(f"artifact[{artifact_index}] is invalid")
                continue
            relative = artifact.get("path")
            expected_hash = artifact.get("sha256")
            kind = artifact.get("kind")
            if kind == "report":
                report_found = True
            path = (base_dir / relative).resolve() if isinstance(relative, str) else None
            inside_base = bool(path and (path == base_dir or base_dir in path.parents))
            if path is not None and not inside_base:
                scenario_errors.append(f"artifact[{artifact_index}] escapes matrix directory")
            exists = bool(path and inside_base and path.is_file())
            actual_hash = _sha256(path) if exists and path else None
            valid_hash = isinstance(expected_hash, str) and actual_hash == expected_hash.lower()
            if not exists:
                scenario_errors.append(f"artifact[{artifact_index}] does not exist")
            elif not valid_hash:
                scenario_errors.append(f"artifact[{artifact_index}] SHA-256 mismatch")
            size = path.stat().st_size if exists and path else 0
            artifact_bytes += size
            artifact_results.append({
                "kind": kind, "path": relative, "exists": exists,
                "sha256_matches": valid_hash, "size_bytes": size,
            })
        if not report_found:
            scenario_errors.append("a persisted artifact with kind=report is required")

        errors.extend(f"{prefix}: {message}" for message in scenario_errors)
        scenario_reports.append({
            "id": scenario_id,
            "passed": not scenario_errors,
            "configuration": {
                "source": source if isinstance(source, dict) else {},
                "clip_count": clip_count,
                "features": features,
                "engines": engines if isinstance(engines, dict) else {},
                "lifecycle": lifecycle if isinstance(lifecycle, dict) else {},
                "resources": resources,
            },
            "stage_total_seconds": round(scenario_stage_total, 6),
            "errors": scenario_errors,
            "artifacts": artifact_results,
        })

    missing_coverage: dict[str, list[Any]] = {}
    for name, required in REQUIRED_SETS.items():
        missing = required - coverage[name]
        if missing:
            missing_coverage[name] = sorted(missing, key=str)
    for toggle, values in toggle_coverage.items():
        missing = {False, True} - values
        if missing:
            missing_coverage[f"{toggle}_states"] = sorted(missing)
    missing_lifecycle = REQUIRED_LIFECYCLE - lifecycle_coverage
    if missing_lifecycle:
        missing_coverage["lifecycle"] = sorted(missing_lifecycle)
    for name, values in missing_coverage.items():
        errors.append(f"missing coverage {name}: {values}")

    comparisons = matrix.get("comparisons")
    comparison_kinds: set[str] = set()
    if isinstance(comparisons, list):
        for item in comparisons:
            if (isinstance(item, dict) and item.get("kind") in {"editorial", "technical"}
                    and isinstance(item.get("metric"), str)
                    and _number(item.get("before")) is not None
                    and _number(item.get("after")) is not None):
                comparison_kinds.add(item["kind"])
    for kind in {"editorial", "technical"} - comparison_kinds:
        errors.append(f"missing valid {kind} before/after comparison")

    declared_risks = matrix.get("risks", [])
    if not isinstance(declared_risks, list) or not all(isinstance(risk, str) for risk in declared_risks):
        errors.append("risks must be a list of strings")
        declared_risks = []
    risks.extend(declared_risks)
    if errors:
        risks.append("Gate 9 matrix is incomplete or contains unverifiable evidence")

    complete = not errors
    report = {
        "gate9_report_schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "matrix_sha256": matrix_sha256,
        "status": "complete" if complete else "incomplete",
        "check_passed": complete,
        "metrics": {
            "scenario_count": len(scenarios),
            "failed_scenario_count": sum(not item["passed"] for item in scenario_reports),
            "stage_duration_seconds": {key: round(value, 6) for key, value in sorted(stage_totals.items())},
            "total_duration_seconds": round(total_seconds, 6),
            "media_duration_seconds": round(media_seconds, 6),
            "render_time_per_media_second": (
                round(stage_totals.get("render", 0.0) / media_seconds, 6)
                if media_seconds else None
            ),
            "artifact_size_bytes": artifact_bytes,
            "retries": retries,
            "cache_hits": cache_hits,
            "resource_peaks": resource_peaks,
        },
        "coverage": {
            **{name: sorted(values, key=str) for name, values in coverage.items()},
            "toggles": {name: sorted(values) for name, values in toggle_coverage.items()},
            "lifecycle": sorted(lifecycle_coverage),
            "missing": missing_coverage,
        },
        "comparisons": comparisons if isinstance(comparisons, list) else [],
        "scenarios": scenario_reports,
        "errors": errors,
        "risks": risks,
    }
    return report, complete


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", required=True, type=Path, help="Declarative Gate 9 matrix JSON")
    parser.add_argument("--output", required=True, type=Path, help="Persisted JSON report path")
    parser.add_argument(
        "--artifact-root", type=Path,
        help="Trusted root for artifact paths (defaults to the matrix directory)",
    )
    parser.add_argument("--check", action="store_true", help="Exit non-zero unless the matrix is complete")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    raw = args.matrix.read_bytes()
    matrix = json.loads(raw)
    if not isinstance(matrix, dict):
        raise SystemExit("matrix root must be an object")
    artifact_root = (
        args.artifact_root.resolve()
        if args.artifact_root is not None else args.matrix.resolve().parent
    )
    report, complete = evaluate(
        matrix, base_dir=artifact_root, matrix_sha256=hashlib.sha256(raw).hexdigest()
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"report written to {args.output}")
    print(f"Gate 9 status: {report['status']}")
    if args.check and not complete:
        for error in report["errors"]:
            print(f"  - {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
