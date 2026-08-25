"""Score one LLHB arm on its own — a diagnostic baseline, never a delta (ruling #31).

``score_run.py`` scores a control/treatment *pair* and refuses to start
without a verified pair manifest (ruling #30(d)). A run with no partner
arm — a model that has no treatment arm yet, such as the first Norwegian
models through the openai-chat driver — has no pair to verify, so this
script runs the same per-case scorer and the same per-arm samplers over
one run and reports numerator / denominator / rate with an interval per
metric. It computes no delta, prints no verdict, and the report it writes
says so in ``epistemic_status``.

Usage:
    uv run python benchmarks/llhb/runner/score_arm.py \\
        --run-id llhb-v1-run-20260825-nmctrl1 \\
        --corpus-path <lovverk checkout at the frozen pin> \\
        --out benchmarks/llhb/results/reports/<run-id>-arm.json
"""

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any

from lovspor.errors import LovsporError
from lovspor.llhb.metrics import ArmReport, compute_arm_report
from lovspor.llhb.reporting import score_arm
from lovspor.llhb.results import ResultsStore
from lovspor.llhb.schema import load_schema, validate_case
from lovspor.llhb.scoring import SCORER_VERSION, CaseScorer
from lovspor.mcp import CorpusReader

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score_run import load_cases

LLHB_DIR = Path(__file__).resolve().parents[1]
FROZEN_JSONL = LLHB_DIR / "dataset" / "frozen" / "llhb-v1.jsonl"
RUNS_ROOT = LLHB_DIR / "results" / "runs"
SCHEMA_DIR = LLHB_DIR / "schema"
EPISTEMIC_STATUS = (
    "single-arm diagnostic baseline (ruling #31): no pair manifest, no delta, "
    "no verdict; rates are descriptive and not a treatment-effect estimate"
)
_IDENTITY_FIELDS = (
    "provider",
    "model_id",
    "condition",
    "dataset_checksum",
    "lovverk_commit",
    "lovspor_commit",
    "system_prompt_sha256",
    "sampling",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--corpus-path", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=FROZEN_JSONL)
    parser.add_argument("--runs-root", type=Path, default=RUNS_ROOT)
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args(argv)


def read_metadata(runs_root: Path, run_id: str) -> dict[str, Any]:
    """The run's own metadata, schema-valid, claiming this directory, control arm."""
    path = runs_root / run_id / "run-metadata.json"
    if not path.is_file():
        raise LovsporError(f"no run-metadata.json under {runs_root / run_id}")
    metadata = dict(json.loads(path.read_text(encoding="utf-8")))
    _check_metadata(metadata, run_id)
    return metadata


def _check_metadata(metadata: dict[str, Any], run_id: str) -> None:
    if metadata.get("run_id") != run_id:
        # A copied or renamed run directory would otherwise report under a name
        # its own metadata does not claim.
        raise LovsporError(
            f"run-metadata.json under {run_id} declares run_id {metadata.get('run_id')!r}"
        )
    if metadata.get("condition") != "control":
        # Ruling #31 scores lone control arms; a treatment arm has a partner and
        # belongs in score_run.py with its pair manifest.
        raise LovsporError(
            f"{run_id} is a {metadata.get('condition')!r} arm; single-arm scoring is for control"
        )
    problems = validate_case(metadata, load_schema(SCHEMA_DIR / "run_metadata.schema.json"))
    if problems:
        raise LovsporError(f"run-metadata.json under {run_id} is not schema-valid: {problems[0]}")


def build_report(run_id: str, metadata: dict[str, Any], arm: ArmReport) -> dict[str, Any]:
    """The single-arm report document; every number comes from the shared samplers."""
    return {
        "report_kind": "single-arm",
        "epistemic_status": EPISTEMIC_STATUS,
        "run_id": run_id,
        **{field: metadata.get(field) for field in _IDENTITY_FIELDS},
        "cases_scored": arm.outcomes.total,
        "scorer_version": SCORER_VERSION,
        "generated_at": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **arm.model_dump(mode="json"),
    }


def print_table(report: dict[str, Any]) -> None:
    header = (
        f"{report['run_id']}  ({report['provider']} / {report['model_id']} / {report['condition']})"
    )
    print(header)
    print(f"{'metric':42s} {'n/d':>11s}  rate [95% CI]")
    for name, est in report["metrics"].items():
        rate = est["rate"]
        shown = "n/a" if rate is None else f"{rate:.3f} [{est['ci_low']:.3f}, {est['ci_high']:.3f}]"
        print(f"{name:42s} {est['numerator']:>5.0f}/{est['denominator']:<5.0f} {shown}")
    print(f"outcomes     {report['outcomes']}")
    print(f"c8_outcomes  {report['c8_outcomes']}")
    print(f"unresolved   {report['unresolved']}")
    print(f"\n{report['epistemic_status']}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cases = load_cases(args.cases, args.corpus_path)
    store = ResultsStore(runs_root=args.runs_root, schema_dir=SCHEMA_DIR)
    metadata = read_metadata(args.runs_root, args.run_id)
    scored = score_arm(
        CaseScorer(CorpusReader(args.corpus_path)), cases, store.read_records(args.run_id)
    )
    report = build_report(args.run_id, metadata, compute_arm_report(scored))
    print_table(report)
    if args.out is not None:
        args.out.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"\nwritten {args.out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except LovsporError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
