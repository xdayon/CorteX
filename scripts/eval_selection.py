#!/usr/bin/env python3
"""Offline runner for the Gate 8 editorial evaluation dataset.

Compares a suggestion artifact (clip selection) against a versioned dataset of
human approve/reject decisions and produces a JSON report measuring coverage,
improper overlap with rejected clips, duration distribution, field
completeness and boundary safety. Deterministic, no LLM calls, no network.

Usage:
    .venv/bin/python scripts/eval_selection.py \\
        --dataset eval/dataset \\
        --suggestions data/projects/<id>/suggestions/suggestion-<hash>.json \\
        --output /tmp/eval-report.json

Add --check to compare the report against eval/baseline.json and exit
non-zero when a metric falls below baseline.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cortex.eval.runner import (  # noqa: E402
    check_against_baseline,
    compare_eval_reports,
    evaluate,
    load_dataset,
    load_suggestion_document,
    run_eval,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", required=True, type=Path,
        help="Directory containing dataset entries and schema.json",
    )
    parser.add_argument(
        "--suggestions",
        help="Path to a suggestion artifact JSON file, or a StageArtifact id (requires --db)",
    )
    parser.add_argument("--before", help="Before suggestion artifact for comparison mode")
    parser.add_argument("--after", help="After suggestion artifact for comparison mode")
    parser.add_argument(
        "--output", required=True, type=Path,
        help="Path to write the JSON evaluation report",
    )
    parser.add_argument(
        "--iou-threshold", type=float, default=0.5,
        help="Minimum temporal IoU to consider a suggested clip a match for a decision (default: 0.5)",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Compare the report against --baseline and exit non-zero if below baseline",
    )
    parser.add_argument(
        "--baseline", type=Path, default=REPO_ROOT / "eval" / "baseline.json",
        help="Baseline thresholds JSON (default: eval/baseline.json)",
    )
    parser.add_argument(
        "--db", type=Path, default=None,
        help="Path to cortex.sqlite3; required for artifact ids and words/VAD evidence resolution",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    comparison_mode = args.before is not None or args.after is not None
    if comparison_mode:
        if not args.before or not args.after or args.suggestions:
            raise SystemExit("comparison mode requires --before and --after, without --suggestions")
    elif not args.suggestions:
        raise SystemExit("single mode requires --suggestions")

    domain_store = None
    if args.db is not None:
        from cortex.domain.store import DomainStore

        domain_store = DomainStore(args.db)

    if comparison_mode:
        entries = load_dataset(args.dataset)
        before = evaluate(
            load_suggestion_document(args.before, domain_store), entries,
            iou_threshold=args.iou_threshold,
        )
        after = evaluate(
            load_suggestion_document(args.after, domain_store), entries,
            iou_threshold=args.iou_threshold,
        )
        report = compare_eval_reports(before, after)
        passed = True
        failures: list[str] = []
        if args.check:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            passed, failures = check_against_baseline(after, baseline)
            report["baseline_used"] = str(args.baseline)
            report["baseline"] = baseline
            report["baseline_check_passed"] = passed
            report["baseline_check_failures"] = failures
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        report, passed, failures = run_eval(
            dataset_dir=args.dataset,
            suggestion_source=args.suggestions,
            output_path=args.output,
            iou_threshold=args.iou_threshold,
            domain_store=domain_store,
            baseline_path=args.baseline if args.check else None,
            check=args.check,
        )

    print(f"report written to {args.output}")
    metrics = report["after"]["metrics"] if comparison_mode else report["metrics"]
    print(f"metrics: {metrics}")

    if args.check:
        if passed:
            print("baseline check: PASSED")
        else:
            print("baseline check: FAILED")
            for failure in failures:
                print(f"  - {failure}")
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
