"""Score the stability repeats and report run-to-run variance (ruling #26).

Takes the five control and five treatment stability runs, scores every
repeat with the same deterministic scorer as the primary evaluation,
and writes one report: per-metric rate statistics across repeats
(mean, min, max, sample SD — exact, no bootstrap needed at n=5), the
per-repeat pair reports as evidence, and the per-case flip list — the
cases whose pass/fail verdict is not identical across repeats. The
subset's job is to measure per-case variance, not to suppress it.

Usage:
    uv run python benchmarks/llhb/runner/stability_report.py \
        --control  <run-id>,<run-id>,...  \
        --treatment <run-id>,<run-id>,... \
        --corpus-path <pinned lovverk checkout> [--out <path>]
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from lovspor.errors import LovsporError
from lovspor.llhb.corpus_pin import CorpusPin, verify_pin
from lovspor.llhb.metrics import PairReport, compute_pair_report
from lovspor.llhb.reporting import ArmScoring, score_arm
from lovspor.llhb.results import ResultsStore
from lovspor.llhb.schema import load_cases_jsonl
from lovspor.llhb.scoring import CaseScorer
from lovspor.llhb.stability import flipped_cases, summarize_rates
from lovspor.mcp import CorpusReader

LLHB_DIR = Path(__file__).resolve().parents[1]
RUNS_ROOT = LLHB_DIR / "results" / "runs"
SCHEMA_DIR = LLHB_DIR / "schema"
FROZEN_CASES = LLHB_DIR / "dataset" / "frozen" / "llhb-v1.jsonl"
REPORTS_DIR = LLHB_DIR / "results" / "reports"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", required=True, help="comma-separated control run ids")
    parser.add_argument("--treatment", required=True, help="comma-separated treatment run ids")
    parser.add_argument("--corpus-path", type=Path, required=True)
    parser.add_argument("--runs-root", type=Path, default=RUNS_ROOT)
    parser.add_argument("--cases", type=Path, default=FROZEN_CASES)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    args.control_ids = [rid for rid in args.control.split(",") if rid]
    args.treatment_ids = [rid for rid in args.treatment.split(",") if rid]
    if not args.control_ids or not args.treatment_ids:
        parser.error("at least one run id is required in each arm")
    if len(args.control_ids) != len(args.treatment_ids):
        parser.error("control and treatment must list the same number of runs")
    return args


def load_cases(path: Path, corpus_path: Path) -> dict[str, dict[str, Any]]:
    """Cases keyed by id, checkout verified against their single pin."""
    cases = load_cases_jsonl(path)
    pins = {json.dumps(case.get("corpus_pin"), sort_keys=True) for case in cases}
    if len(pins) != 1:
        raise LovsporError(f"{path} carries {len(pins)} distinct corpus pins; refusing to score")
    verify_pin(corpus_path, CorpusPin(**cases[0]["corpus_pin"]))
    return {str(case["case_id"]): case for case in cases}


def score_repeats(
    args: argparse.Namespace,
) -> tuple[list[PairReport], list[dict[str, str]], list[dict[str, str]]]:
    cases_by_id = load_cases(args.cases, args.corpus_path)
    store = ResultsStore(runs_root=args.runs_root, schema_dir=SCHEMA_DIR)
    scorer = CaseScorer(CorpusReader(args.corpus_path))
    reports: list[PairReport] = []
    control_outcomes: list[dict[str, str]] = []
    treatment_outcomes: list[dict[str, str]] = []
    for control_id, treatment_id in zip(args.control_ids, args.treatment_ids, strict=True):
        control = score_arm(scorer, cases_by_id, store.read_records(control_id))
        treatment = score_arm(scorer, cases_by_id, store.read_records(treatment_id))
        reports.append(compute_pair_report(control, treatment))
        control_outcomes.append(_outcomes(control))
        treatment_outcomes.append(_outcomes(treatment))
    return reports, control_outcomes, treatment_outcomes


def _outcomes(arm: ArmScoring) -> dict[str, str]:
    """The five-way reason code per case, not a tri-state bool: a repeat that
    the model or the scorer never got through is not an unresolved answer
    (ruling #30(c))."""
    return {case.case_id: case.outcome.value for case in arm.cases}


def summarize_metrics(reports: list[PairReport]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for name in reports[0].metrics:
        pairs = [report.metrics[name] for report in reports]
        summary[name] = {
            "control": summarize_rates(
                [p.control.rate if p.control else None for p in pairs]
            ).model_dump(),
            "treatment": summarize_rates(
                [p.treatment.rate if p.treatment else None for p in pairs]
            ).model_dump(),
            "delta": summarize_rates([p.delta for p in pairs]).model_dump(),
        }
    return summary


def main() -> int:
    args = parse_args()
    reports, control_outcomes, treatment_outcomes = score_repeats(args)
    document = {
        "control_runs": args.control_ids,
        "treatment_runs": args.treatment_ids,
        "repeats": len(reports),
        "metrics": summarize_metrics(reports),
        "case_stability": {
            "control_flipped": flipped_cases(control_outcomes),
            "treatment_flipped": flipped_cases(treatment_outcomes),
            "cases_per_run": len(control_outcomes[0]),
        },
        "per_repeat_reports": [report.model_dump(mode="json") for report in reports],
    }
    out = args.out or REPORTS_DIR / "llhb-v1-stability30x5.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    _print_summary(out, document)
    return 0


def _print_summary(out: Path, document: dict[str, Any]) -> None:
    print(f"report: {out} ({document['repeats']} repeats per arm)")
    for name, arms in document["metrics"].items():
        line = f"  {name}:"
        for arm in ("control", "treatment", "delta"):
            s = arms[arm]
            if s["mean"] is None:
                line += f"  {arm} n/a"
            else:
                sd = "n/a" if s["sd"] is None else f"{s['sd']:.3f}"
                line += f"  {arm} {s['mean']:.3f} sd {sd} [{s['minimum']:.3f}-{s['maximum']:.3f}]"
        print(line)
    stability = document["case_stability"]
    print(
        f"  flipped cases: control {len(stability['control_flipped'])}"
        f"/{stability['cases_per_run']}, treatment "
        f"{len(stability['treatment_flipped'])}/{stability['cases_per_run']}"
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except LovsporError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
