"""Score a committed run pair and emit the SCORING.md §6 report.

Reads the two runs' committed artifacts, scores every case at the
pinned corpus (the checkout is verified against the cases' own pin
before a single answer is read), and writes the pair report: absolute
rates, control-treatment deltas, bootstrap CIs, unresolved buckets.

Run the fairness gate first — a report from a pair that does not pass
``check_fairness.py`` (with ``--frozen`` for the published evaluation)
is not a comparison between conditions, and this script does not
re-check fairness.

Scoring is manifest-gated (DECISIONS.md ruling #30(d)): the pair, the
dataset, the analysis plan, the prompt and the scorer commit all come
from a verified pair manifest — "Aggregate scoring MUST NOT execute
until a valid pair manifest exists and all referenced hashes verify."
Build the manifest with ``make_pair_manifest.py`` after both arms are
committed.

Usage:
    uv run python benchmarks/llhb/runner/score_run.py \
        --manifest <pair-manifest.json> \
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
from lovspor.llhb.pair_manifest import (
    PairManifest,
    load_pair_manifest,
    verify_pair_manifest,
)
from lovspor.llhb.reporting import score_arm
from lovspor.llhb.results import ResultsStore
from lovspor.llhb.schema import load_cases_jsonl
from lovspor.llhb.scoring import CaseScorer
from lovspor.mcp import CorpusReader

LLHB_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = LLHB_DIR.parents[1]
RUNS_ROOT = LLHB_DIR / "results" / "runs"
SCHEMA_DIR = LLHB_DIR / "schema"
REPORTS_DIR = LLHB_DIR / "results" / "reports"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="pair manifest binding the run pair, dataset, plan and scorer commit",
    )
    parser.add_argument("--corpus-path", type=Path, required=True)
    parser.add_argument("--runs-root", type=Path, default=RUNS_ROOT)
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


def score_pair(
    manifest: PairManifest, corpus_path: Path, runs_root: Path
) -> tuple[PairReport, int]:
    cases_by_id = load_cases(REPO_ROOT / manifest.dataset_path, corpus_path)
    store = ResultsStore(runs_root=runs_root, schema_dir=SCHEMA_DIR)
    scorer = CaseScorer(CorpusReader(corpus_path))
    control = score_arm(scorer, cases_by_id, store.read_records(manifest.control_run_id))
    treatment = score_arm(scorer, cases_by_id, store.read_records(manifest.treatment_run_id))
    return compute_pair_report(control, treatment), len(control.cases)


def main() -> int:
    args = parse_args()
    manifest = load_pair_manifest(args.manifest)
    # Ruling #30(d): no aggregate scoring until every referenced hash — the
    # runs, the dataset, the plan, the prompt, the scorer commit — verifies.
    verify_pair_manifest(manifest, REPO_ROOT, args.runs_root)
    report, cases = score_pair(manifest, args.corpus_path, args.runs_root)
    out = args.out or REPORTS_DIR / f"{manifest.control_run_id}-vs-{manifest.treatment_run_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "control_run": manifest.control_run_id,
        "treatment_run": manifest.treatment_run_id,
        "cases_scored": cases,
        "pair_manifest": manifest.model_dump(mode="json"),
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
