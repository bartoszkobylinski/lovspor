#!/usr/bin/env python3
"""Parse `scripts/mutmut-pr.sh` output into the mutation-result.json contract.

This is the ONLY place that knows the mutation tool's output format AND the gate
policy. `mutation_gate.py` just reads the result. Policy mirrors the existing
lovspor practice (no numeric threshold was ever set, decisions.md §9c):

- "mutation not applicable" (release/packaging/docs PRs) is a valid PASS outcome;
- surviving, timed-out, and suspicious mutants each fail the gate, mirroring
  mutmut's own exit-code bits (2 survived / 4 timeout / 8 suspicious — see
  mutmut.compute_exit_code); survivors route the PR to Codex remediation and,
  after two cycles, to a human — the automated form of "investigate survived
  mutants in critical paths" from AGENTS.md. Suspicious mutants are never
  folded into the killed count (CLAUDE.md testing strategy).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1
FULL_SHA_LEN = 40
TOOL = "mutmut 2.5.1 (PR-scoped via scripts/mutmut-pr.sh)"

# mutmut 2.x progress line, e.g.:  12/12  🎉 10  ⏰ 0  🤔 0  🙁 2  🔇 0
MUTMUT_LINE = re.compile(
    r"(?P<done>\d+)/(?P<total>\d+)\s+🎉\s*(?P<killed>\d+)\s+⏰\s*(?P<timeout>\d+)"
    r"\s+🤔\s*(?P<suspicious>\d+)\s+🙁\s*(?P<survived>\d+)\s+🔇\s*(?P<skipped>\d+)"
)
NOT_APPLICABLE = "mutation not applicable:"
BASELINE_FAILED = "failed when run without mutation"


def parse_counts(raw: str) -> tuple[dict[str, int], bool]:
    """Return (mutant counts, run_finished) from the last mutmut progress line."""
    last: dict[str, int] | None = None
    for m in MUTMUT_LINE.finditer(raw):
        last = {k: int(v) for k, v in m.groupdict().items()}
    if last is None:
        empty = dict.fromkeys(("total", "killed", "survived", "timeout", "invalid", "skipped"), 0)
        return empty, False
    counts = {
        "total": last["total"],
        "killed": last["killed"],
        "survived": last["survived"],
        "timeout": last["timeout"],
        "invalid": last["suspicious"],
        "skipped": last["skipped"],
    }
    return counts, last["done"] == last["total"] and last["total"] > 0


def parse_survivors(survivors_file: Path | None) -> list[dict[str, object]]:
    if survivors_file is None or not survivors_file.exists():
        return []
    ids = survivors_file.read_text().split()
    return [
        {"id": mid, "file": None, "line": None, "symbol": None, "operator": None} for mid in ids
    ]


def compute_gate(
    counts: dict[str, int], *, not_applicable: bool, completed: bool, baseline_ok: bool
) -> dict[str, object]:
    if not_applicable:
        return {"passed": True, "reason": "not_applicable"}
    failures = (
        (not baseline_ok, "baseline_tests_failed"),
        (not completed, "run_incomplete"),
        (counts["survived"] > 0, "surviving_mutants"),
        (counts["timeout"] > 0, "timeout_mutants"),
        (counts["invalid"] > 0, "suspicious_mutants"),
    )
    for failed, reason in failures:
        if failed:
            return {"passed": False, "reason": reason}
    return {"passed": True, "reason": "ok"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", required=True)
    ap.add_argument("--raw", required=True, type=Path)
    ap.add_argument("--survivors-file", type=Path, default=None)
    ap.add_argument("--tool-exit-code", required=True, type=int)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    if len(args.commit) != FULL_SHA_LEN:
        print(f"refusing to write result for non-full SHA: {args.commit!r}", file=sys.stderr)
        return 2

    raw = args.raw.read_text(errors="replace") if args.raw.exists() else ""
    not_applicable = NOT_APPLICABLE in raw
    baseline_ok = BASELINE_FAILED not in raw.lower()
    counts, run_finished = parse_counts(raw)
    completed = not_applicable or run_finished
    # Pessimistic score: only 🎉 counts as killed. Timeout and suspicious fail
    # the gate anyway (mutmut exit bits 4 and 8), so they never inflate the score.
    score = round(100.0 * counts["killed"] / counts["total"], 2) if counts["total"] else 100.0

    result = {
        "schema_version": SCHEMA_VERSION,
        "commit": args.commit,
        "completed": completed,
        "baseline_tests_passed": baseline_ok,
        "tool": TOOL,
        "tool_exit_code": args.tool_exit_code,
        "mutants": counts,
        "score": score,
        "gate": compute_gate(
            counts,
            not_applicable=not_applicable,
            completed=completed,
            baseline_ok=baseline_ok,
        ),
        "survivors": parse_survivors(args.survivors_file),
    }
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {args.out} (gate.passed={result['gate']['passed']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
