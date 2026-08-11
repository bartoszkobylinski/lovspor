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

from pydantic import ValidationError

from lovspor.errors import LovsporError
from lovspor.llhb.fairness import ExpectedSurface, RunArtifacts, check_pair
from lovspor.llhb.schema import load_schema, validate_case

LLHB_DIR = Path(__file__).resolve().parents[1]
RUNS_ROOT = LLHB_DIR / "results" / "runs"
SCHEMA_DIR = LLHB_DIR / "schema"
SURFACE_PATH = LLHB_DIR / "runner" / "tool-surface-v1.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", required=True, help="run id of the control run")
    parser.add_argument("--treatment", required=True, help="run id of the lovspor run")
    parser.add_argument("--runs-root", type=Path, default=RUNS_ROOT)
    return parser.parse_args()


def load_run(runs_root: Path, run_id: str) -> RunArtifacts:
    """One run's artifacts, refused unless they are schema-valid.

    ``ResultsStore`` validates on write, but this module reads committed
    files — a document edited or assembled afterwards would otherwise be
    compared as though the store had produced it.
    """
    run_dir = runs_root / run_id
    metadata_path = run_dir / "run-metadata.json"
    records_path = run_dir / "records.jsonl"
    if not metadata_path.is_file():
        raise LovsporError(f"no run metadata at {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    _check(f"{run_id} run metadata", metadata, "run_metadata.schema.json")
    records = [
        json.loads(line)
        for line in records_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for record in records:
        _check(f"{run_id} record {record.get('case_id')}", record, "result_record.schema.json")
    return RunArtifacts(metadata=metadata, records=records)


def _check(label: str, document: dict[str, object], schema_name: str) -> None:
    errors = validate_case(document, load_schema(SCHEMA_DIR / schema_name))
    if errors:
        raise LovsporError(f"{label} is not schema-valid: " + "; ".join(errors))


def load_expected_surface(path: Path) -> ExpectedSurface:
    """The frozen apparatus surface: what a treatment run must declare.

    A unit test regenerates this document from the code, so reading it
    here is reading the code's own account, not the run's.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        return ExpectedSurface(
            tools=tuple(document["tools"]),
            tool_schema_sha256=document["tool_schema_sha256"],
        )
    except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
        raise LovsporError(f"cannot read the frozen tool surface at {path}: {exc}") from exc


def main() -> int:
    args = parse_args()
    control = load_run(args.runs_root, args.control)
    treatment = load_run(args.runs_root, args.treatment)
    problems = check_pair(control, treatment, load_expected_surface(SURFACE_PATH))
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
