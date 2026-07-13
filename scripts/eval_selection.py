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
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cortex.eval.runner import run_eval  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", required=True, type=Path,
        help="Directory containing dataset entries and schema.json",
    )
    parser.add_argument(
        "--suggestions", required=True,
        help="Path to a suggestion artifact JSON file, or a StageArtifact id (requires --db)",
    )
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
        help="Path to cortex.sqlite3, only needed if --suggestions is a StageArtifact id",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    domain_store = None
    if args.db is not None:
        from cortex.domain.store import DomainStore

        domain_store = DomainStore(args.db)

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
    print(f"metrics: {report['metrics']}")

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
