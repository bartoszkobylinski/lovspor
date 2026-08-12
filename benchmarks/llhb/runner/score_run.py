"""Score a committed run pair and emit the SCORING.md §6 report.

Reads the two runs' committed artifacts, scores every case at the
pinned corpus (the checkout is verified against the cases' own pin
before a single answer is read), and writes the pair report: absolute
rates, control-treatment deltas, bootstrap CIs, unresolved buckets.

Run the fairness gate first — a report from a pair that does not pass
``check_fairness.py`` (with ``--frozen`` for the published evaluation)
is not a comparison between conditions, and this script does not
re-check fairness.

Usage:
    uv run python benchmarks/llhb/runner/score_run.py \
        --control llhb-v1-run-<...> --treatment llhb-v1-run-<...> \
        --corpus-path <pinned lovverk checkout> \
        [--cases benchmarks/llhb/dataset/frozen/llhb-v1.jsonl] [--out <path>]
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from lovspor.errors import LovsporError
from lovspor.llhb.corpus_pin import CorpusPin, verify_pin
from lovspor.llhb.metrics import PairReport, compute_pair_report
from lovspor.llhb.reporting import score_arm
from lovspor.llhb.results import ResultsStore
from lovspor.llhb.schema import load_cases_jsonl
from lovspor.llhb.scoring import CaseScorer
from lovspor.mcp import CorpusReader

LLHB_DIR = Path(__file__).resolve().parents[1]
RUNS_ROOT = LLHB_DIR / "results" / "runs"
SCHEMA_DIR = LLHB_DIR / "schema"
FROZEN_CASES = LLHB_DIR / "dataset" / "frozen" / "llhb-v1.jsonl"
REPORTS_DIR = LLHB_DIR / "results" / "reports"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", required=True, help="run id of the control run")
    parser.add_argument("--treatment", required=True, help="run id of the lovspor run")
    parser.add_argument("--corpus-path", type=Path, required=True)
    parser.add_argument("--runs-root", type=Path, default=RUNS_ROOT)
    parser.add_argument("--cases", type=Path, default=FROZEN_CASES)
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args()


def load_cases(path: Path, corpus_path: Path) -> dict[str, dict[str, Any]]:
    """Cases keyed by id, with the checkout verified against their pin.

    Every case carries the same corpus pin by construction; a mixed
    file or a checkout at any other state must not score anything.
    """
    cases = load_cases_jsonl(path)
    pins = {json.dumps(case.get("corpus_pin"), sort_keys=True) for case in cases}
    if len(pins) != 1:
        raise LovsporError(f"{path} carries {len(pins)} distinct corpus pins; refusing to score")
    verify_pin(corpus_path, CorpusPin(**cases[0]["corpus_pin"]))
    return {str(case["case_id"]): case for case in cases}


def score_pair(args: argparse.Namespace) -> tuple[PairReport, int]:
    cases_by_id = load_cases(args.cases, args.corpus_path)
    store = ResultsStore(runs_root=args.runs_root, schema_dir=SCHEMA_DIR)
    scorer = CaseScorer(CorpusReader(args.corpus_path))
    control = score_arm(scorer, cases_by_id, store.read_records(args.control))
    treatment = score_arm(scorer, cases_by_id, store.read_records(args.treatment))
    return compute_pair_report(control, treatment), len(control)


def main() -> int:
    args = parse_args()
    report, cases = score_pair(args)
    out = args.out or REPORTS_DIR / f"{args.control}-vs-{args.treatment}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "control_run": args.control,
        "treatment_run": args.treatment,
        "cases_scored": cases,
        **report.model_dump(mode="json"),
    }
    out.write_text(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"report: {out} ({cases} cases per arm)")
    for name, pair in report.metrics.items():
        _print_metric(name, pair)
    return 0


def _print_metric(name: str, pair: Any) -> None:
    def show(est: Any) -> str:
        if est is None:
            return "n/a"
        if est.rate is None:
            return f"-/{est.denominator:g}"
        return f"{est.rate:.3f} ({est.numerator:g}/{est.denominator:g})"

    delta = "" if pair.delta is None else f"  delta {pair.delta:+.3f}"
    print(f"  {name}: control {show(pair.control)}  treatment {show(pair.treatment)}{delta}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except LovsporError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
