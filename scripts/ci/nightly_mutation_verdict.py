#!/usr/bin/env python3
"""Map a nightly mutation-result.json onto the nightly lane's verdict.

The nightly lane (.github/workflows/nightly-mutation.yml) reports one of five
verdicts and moves the `nightly-mutation-base` tag only after a COMPLETED run:
one that measured every mutant in its range. A partial run is never reported
as clean, and never moves the tag, so the next night measures the range again.

NO gate policy lives here: `mutation_to_json.py` computes `gate.reason`; this
program only buckets it. An unknown reason is a tool failure, never clean.

Usage:
    nightly_mutation_verdict.py result.json   # prints verdict=, complete=, reason=

Always exits 0 with a verdict: a missing or malformed result is the verdict
`tool_failure`, which the report must still publish.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CLEAN = frozenset({"ok", "not_applicable", "equivalent_mutants_only"})
SURVIVORS = frozenset(
    {"surviving_mutants", "timeout_mutants", "suspicious_mutants", "uncovered_mutants"}
)
PARTIAL = frozenset({"run_incomplete", "unmeasured_mutants"})
BUDGET = frozenset({"budget_exceeded"})
TOOL = frozenset({"tool_failed", "baseline_tests_failed"})

# Only these two verdicts measured the whole range.
COMPLETE = frozenset({"clean", "survivors"})


def verdict_for(reason: str) -> str:
    """The nightly verdict for one `gate.reason`."""
    for bucket, verdict in (
        (CLEAN, "clean"),
        (SURVIVORS, "survivors"),
        (PARTIAL, "partial"),
        (BUDGET, "budget_exceeded"),
        (TOOL, "tool_failure"),
    ):
        if reason in bucket:
            return verdict
    return "tool_failure"


def read_reason(path: Path) -> str:
    """`gate.reason` from the result, or a description of why there is none."""
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"unreadable result ({type(exc).__name__})"
    if not isinstance(result, dict) or result.get("schema_version") != 1:
        return "malformed result"
    gate = result.get("gate")
    reason = gate.get("reason") if isinstance(gate, dict) else None
    return reason if isinstance(reason, str) else "malformed result"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: nightly_mutation_verdict.py result.json", file=sys.stderr)
        return 2
    reason = read_reason(Path(argv[0]))
    verdict = verdict_for(reason)
    print(f"verdict={verdict}")
    print(f"complete={str(verdict in COMPLETE).lower()}")
    print(f"reason={reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
