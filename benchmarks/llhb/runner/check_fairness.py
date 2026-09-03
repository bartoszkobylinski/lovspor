"""Check a committed control/treatment run pair for fairness violations.

Reads only what the two runs left on disk (METHODOLOGY §5) and prints
every way the pair fails to be a control-treatment comparison. Exits
non-zero when it finds any, so this can gate a report: a delta computed
from a pair that does not pass here is not a delta between conditions.

``--frozen`` additionally anchors both arms to the frozen evaluation
(Stage 7, mandatory before any published number): dataset checksum and
lovverk pin against ``dataset/frozen/llhb-v1.lock.json``, the exact
case-id set against the frozen JSONL, and the prompt hash and path
against the committed prompt file. Cross-arm equality cannot see any of
these — both arms agreeing on the wrong dataset is exactly the failure
mode. Pilots run discarded candidates by design and must not use it.

Usage:
    uv run python benchmarks/llhb/runner/check_fairness.py \
        --control llhb-v1-run-20260810-ctrl \
        --treatment llhb-v1-run-20260810-treat [--frozen]
"""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from lovspor.errors import LovsporError
from lovspor.llhb.fairness import (
    ExpectedSurface,
    FrozenExpectation,
    RunArtifacts,
    check_pair,
    frozen_violations,
)
from lovspor.llhb.run_setup import sha256_text, verify_frozen_against_lock
from lovspor.llhb.schema import load_schema, validate_case

LLHB_DIR = Path(__file__).resolve().parents[1]
RUNS_ROOT = LLHB_DIR / "results" / "runs"
SCHEMA_DIR = LLHB_DIR / "schema"
# v5 is the post-ADR-0012 apparatus (get_temporal_events added — the first
# tool-name change since v1). Earlier runs recorded earlier hashes —
# re-verifying one needs ``--surface-path`` pointed at its own version,
# which stays committed untouched.
SURFACE_PATH = LLHB_DIR / "runner" / "tool-surface-v5.json"
FROZEN_DIR = LLHB_DIR / "dataset" / "frozen"
FROZEN_CASES_PATH = FROZEN_DIR / "llhb-v1.jsonl"
FROZEN_LOCK_PATH = FROZEN_DIR / "llhb-v1.lock.json"
PROMPT_PATH = LLHB_DIR / "runner" / "system-prompt-v1.txt"
# The path a frozen run's metadata must record, repo-relative by contract.
PROMPT_REPO_PATH = "benchmarks/llhb/runner/system-prompt-v1.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", required=True, help="run id of the control run")
    parser.add_argument("--treatment", required=True, help="run id of the lovspor run")
    parser.add_argument("--runs-root", type=Path, default=RUNS_ROOT)
    parser.add_argument(
        "--frozen",
        action="store_true",
        help="also anchor both arms to the frozen dataset, lock and prompt "
        "(mandatory for a published pair; pilots must not pass this)",
    )
    parser.add_argument(
        "--surface-path",
        type=Path,
        default=SURFACE_PATH,
        help="apparatus surface document to compare declarations against "
        "(default: the current apparatus; pass runner/tool-surface-v1.json, "
        "-v2 or -v3 to re-verify a pair recorded under "
        "an earlier surface)",
    )
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


def load_frozen_expectation() -> FrozenExpectation:
    """The preregistered evaluation, from committed artifacts only.

    The frozen JSONL is verified against its lock before anything is
    read out of it — an expectation built from a tampered dataset would
    anchor both arms to the tampering.
    """
    try:
        cases = [
            json.loads(line)
            for line in FROZEN_CASES_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        lock = json.loads(FROZEN_LOCK_PATH.read_text(encoding="utf-8"))
        verify_frozen_against_lock(cases, lock)
        return FrozenExpectation(
            dataset_sha256=lock["dataset_sha256"],
            case_ids=tuple(str(case["case_id"]) for case in cases),
            lovverk_commit=lock["corpus_pin"]["lovverk_commit"],
            system_prompt_sha256=sha256_text(PROMPT_PATH.read_text(encoding="utf-8")),
            system_prompt_path=PROMPT_REPO_PATH,
        )
    except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
        raise LovsporError(f"cannot build the frozen expectation: {exc}") from exc


def main() -> int:
    args = parse_args()
    control = load_run(args.runs_root, args.control)
    treatment = load_run(args.runs_root, args.treatment)
    problems = check_pair(control, treatment, load_expected_surface(args.surface_path))
    if args.frozen:
        expected = load_frozen_expectation()
        problems += frozen_violations(control, "control", expected)
        problems += frozen_violations(treatment, "treatment", expected)
    label = "frozen evaluation" if args.frozen else "pair"
    cases = f"{len(control.records)} control / {len(treatment.records)} treatment records"
    if not problems:
        print(f"fair {label}: {args.control} vs {args.treatment} ({cases})")
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
