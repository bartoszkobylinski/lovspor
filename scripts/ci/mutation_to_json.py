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
from typing import NamedTuple

SCHEMA_VERSION = 1
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
TOOL = "mutmut 2.5.1 (PR-scoped via scripts/mutmut-pr.sh)"

# mutmut's verdict bitfield (0 clean; 2 survived / 4 timeout / 8 suspicious,
# OR-combined) — exactly the codes mutmut-pr.sh itself treats as legal
# verdicts. Anything else (1 fatal, 3 "no score — do not report one") means
# the tool itself failed and NO score exists — but a progress line from an
# EARLIER file may still sit completed in the raw log, so without this check
# a fatal run could read as PASS off stale output (issue #72; found again
# independently by a Codex CI test on the milamber port's first flight).
# Bit 16 is mutmut-pr.sh's own (issue #102): a per-file wall-clock budget
# cut the run short. Still a legal verdict — the script harvested what was
# measured — but the gate must read it as "surface not fully measured".
_MUTMUT_VERDICTS = frozenset({0, 2, 4, 6, 8, 10, 12, 14})
ALLOWED_TOOL_EXIT_CODES = _MUTMUT_VERDICTS | frozenset(code | 16 for code in _MUTMUT_VERDICTS)


class RunHealth(NamedTuple):
    """Everything the gate needs to know beyond the mutant counts."""

    not_applicable: bool
    completed: bool
    baseline_ok: bool
    tool_exit_code: int
    budget_exceeded: bool


# mutmut 2.x progress line, e.g.:  12/12  🎉 10  ⏰ 0  🤔 0  🙁 2  🔇 0
MUTMUT_LINE = re.compile(
    r"(?P<done>\d+)/(?P<total>\d+)\s+🎉\s*(?P<killed>\d+)\s+⏰\s*(?P<timeout>\d+)"
    r"\s+🤔\s*(?P<suspicious>\d+)\s+🙁\s*(?P<survived>\d+)\s+🔇\s*(?P<skipped>\d+)"
)
NOT_APPLICABLE = "mutation not applicable:"
BASELINE_FAILED = "failed when run without mutation"
BUDGET_EXCEEDED = "mutation budget exceeded:"
# The decisive raw-log line for a failing run: pytest's FAILED/ERROR or
# mutmut-pr.sh's own `error:`. Three blocked runs in a row required digging
# the job logs for exactly this line — the artifact already contains it.
FAILURE_LINE = re.compile(r"^(?:FAILED |ERROR |error: ).*", re.MULTILINE)


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


def parse_failure_hint(raw: str) -> str | None:
    """First FAILED/ERROR/error: line of the raw log, single line, capped."""
    m = FAILURE_LINE.search(raw)
    return m.group(0)[:300].rstrip() if m else None


def parse_survivors(survivors_file: Path | None) -> list[dict[str, object]]:
    if survivors_file is None or not survivors_file.exists():
        return []
    ids = survivors_file.read_text().split()
    return [
        {"id": mid, "file": None, "line": None, "symbol": None, "operator": None} for mid in ids
    ]


def compute_gate(counts: dict[str, int], health: RunHealth) -> dict[str, object]:
    # Tool health outranks EVERYTHING, including not_applicable: the marker
    # line is data read out of the raw log, and a run that died after
    # printing it still produced no trustworthy verdict.
    if health.tool_exit_code not in ALLOWED_TOOL_EXIT_CODES:
        return {"passed": False, "reason": "tool_failed"}
    if health.not_applicable:
        return {"passed": True, "reason": "not_applicable"}
    # Budget outranks the per-bucket reasons AND run_incomplete: the cut is
    # the CAUSE, the incomplete progress line only its symptom — and the
    # remediation workflow must see a reason it knows not to hand to Codex
    # (tests cannot kill a mutant that was never measured).
    if health.budget_exceeded:
        return {"passed": False, "reason": "budget_exceeded"}
    # The counts come from the LAST mutmut progress line, which on a
    # multi-file run describes only the last file — an earlier file's
    # survivors are invisible there. mutmut-pr.sh recomputes its exit code
    # from the ACROSS-FILES totals, so the exit bits (2 survived / 4 timeout
    # / 8 suspicious) are the authoritative aggregate and must fail the gate
    # even when the parsed counts look clean.
    bits = health.tool_exit_code
    failures = (
        (not health.baseline_ok, "baseline_tests_failed"),
        (not health.completed, "run_incomplete"),
        (counts["survived"] > 0 or bool(bits & 2), "surviving_mutants"),
        (counts["timeout"] > 0 or bool(bits & 4), "timeout_mutants"),
        (counts["invalid"] > 0 or bool(bits & 8), "suspicious_mutants"),
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

    if not FULL_SHA_RE.fullmatch(args.commit):
        print(f"refusing to write result for non-full SHA: {args.commit!r}", file=sys.stderr)
        return 2

    raw = args.raw.read_text(errors="replace") if args.raw.exists() else ""
    not_applicable = NOT_APPLICABLE in raw
    baseline_ok = BASELINE_FAILED not in raw.lower()
    budget_exceeded = BUDGET_EXCEEDED in raw or bool(args.tool_exit_code & 16)
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
            RunHealth(
                not_applicable=not_applicable,
                completed=completed,
                baseline_ok=baseline_ok,
                tool_exit_code=args.tool_exit_code,
                budget_exceeded=budget_exceeded,
            ),
        ),
        "failure_hint": parse_failure_hint(raw),
        "survivors": parse_survivors(args.survivors_file),
    }
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {args.out} (gate.passed={result['gate']['passed']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
