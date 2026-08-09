"""Check a committed control/treatment run pair for fairness violations.

Reads only what the two runs left on disk (METHODOLOGY §5) and prints
every way the pair fails to be a control-treatment comparison. Exits
non-zero when it finds any, so this can gate a report: a delta computed
from a pair that does not pass here is not a delta between conditions.

Usage:
    uv run python benchmarks/llhb/runner/check_fairness.py \
        --control llhb-v1-run-20260810-ctrl \
        --treatment llhb-v1-run-20260810-treat
"""

import argparse
import json
import sys
from pathlib import Path

from lovspor.errors import LovsporError
from lovspor.llhb.fairness import RunArtifacts, check_pair

LLHB_DIR = Path(__file__).resolve().parents[1]
RUNS_ROOT = LLHB_DIR / "results" / "runs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", required=True, help="run id of the control run")
    parser.add_argument("--treatment", required=True, help="run id of the lovspor run")
    parser.add_argument("--runs-root", type=Path, default=RUNS_ROOT)
    return parser.parse_args()


def load_run(runs_root: Path, run_id: str) -> RunArtifacts:
    run_dir = runs_root / run_id
    metadata_path = run_dir / "run-metadata.json"
    records_path = run_dir / "records.jsonl"
    if not metadata_path.is_file():
        raise LovsporError(f"no run metadata at {metadata_path}")
    records = [
        json.loads(line)
        for line in records_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return RunArtifacts(
        metadata=json.loads(metadata_path.read_text(encoding="utf-8")),
        records=records,
    )


def main() -> int:
    args = parse_args()
    control = load_run(args.runs_root, args.control)
    treatment = load_run(args.runs_root, args.treatment)
    problems = check_pair(control, treatment)
    cases = f"{len(control.records)} control / {len(treatment.records)} treatment records"
    if not problems:
        print(f"fair: {args.control} vs {args.treatment} ({cases})")
        return 0
    print(f"UNFAIR: {len(problems)} finding(s) for {args.control} vs {args.treatment} ({cases})")
    for problem in problems:
        print(f"  - {problem}")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except LovsporError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
