#!/usr/bin/env python3
"""Parse `scripts/mutmut-pr.sh` output into the mutation-result.json contract.

This is the ONLY place that knows the mutation tool's output format AND the gate
policy. `mutation_gate.py` just reads the result. Policy mirrors the existing
lovspor practice (no numeric threshold was ever set, decisions.md §9c):

- "mutation not applicable" (release/packaging/docs PRs) is a valid PASS outcome;
- surviving, timed-out, suspicious, and uncovered mutants each fail the gate;
- the wrapper preserves the pipeline's 2/4/8 compatibility bitfield for aggregate
  survived / timeout / suspicious state; survivors route the PR to Codex remediation and,
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
TOOL = "mutmut 3.7.0 (function-scoped via scripts/mutmut-pr.sh)"
# Every survivor carries the same keys whether or not the detail step recovered
# anything, so a consumer never has to distinguish "absent" from "unknown".
# `detail_source` says which of the two a null means (issue #119).
SURVIVOR_KEYS = ("id", "file", "line", "symbol", "operator", "diff", "detail_source")

# The wrapper's compatibility bitfield (0 clean; 2 survived / 4 timeout /
# 8 suspicious, OR-combined) — exactly the codes mutmut-pr.sh treats as legal
# verdicts. Anything else (1 fatal, 3 "no score — do not report one") means
# the tool itself failed and NO score exists — but a progress line from an
# EARLIER file may still sit completed in the raw log, so without this check
# a fatal run could read as PASS off stale output (issue #72; found again
# independently by a Codex CI test on the milamber port's first flight).
# Bit 16 is mutmut-pr.sh's own (issue #102): a per-file wall-clock budget
# cut the run short. Still a legal verdict — the script harvested what was
# measured — but the gate must read it as "surface not fully measured".
_WRAPPER_VERDICTS = frozenset({0, 2, 4, 6, 8, 10, 12, 14})
ALLOWED_TOOL_EXIT_CODES = _WRAPPER_VERDICTS | frozenset(code | 16 for code in _WRAPPER_VERDICTS)


class RunHealth(NamedTuple):
    """Everything the gate needs to know beyond the mutant counts."""

    not_applicable: bool
    completed: bool
    baseline_ok: bool
    tool_exit_code: int
    budget_exceeded: bool


# Mutmut 3 progress line. Its denominator is the package-wide population;
# the numerator is the number measured by this function-scoped run.
MUTMUT_LINE = re.compile(
    r"(?P<done>\d+)/(?P<all_mutants>\d+)\s+🎉\s*(?P<killed>\d+)\s+🫥\s*(?P<no_tests>\d+)"
    r"\s+⏰\s*(?P<timeout>\d+)\s+🤔\s*(?P<suspicious>\d+)\s+🙁\s*(?P<survived>\d+)"
    r"\s+🔇\s*(?P<skipped>\d+)\s+🧙\s*(?P<type_checked>\d+)"
)
NOT_APPLICABLE = "mutation not applicable:"
BASELINE_FAILURES = ("failed when run without mutation", "failed to run clean test")
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
        empty = dict.fromkeys(
            ("total", "killed", "survived", "timeout", "invalid", "skipped", "no_tests"),
            0,
        )
        return empty, False
    counts = {
        "total": last["done"],
        "killed": last["killed"],
        "survived": last["survived"],
        "timeout": last["timeout"],
        "invalid": last["suspicious"],
        "skipped": last["skipped"],
        "no_tests": last["no_tests"],
    }
    return counts, last["done"] > 0


def parse_failure_hint(raw: str) -> str | None:
    """First FAILED/ERROR/error: line of the raw log, single line, capped."""
    m = FAILURE_LINE.search(raw)
    return m.group(0)[:300].rstrip() if m else None


def _id_only(mutant_id: str, detail_source: str) -> dict[str, object]:
    entry: dict[str, object] = dict.fromkeys(SURVIVOR_KEYS, None)
    entry["id"] = mutant_id
    entry["detail_source"] = detail_source
    return entry


def _parse_detail(line: str) -> dict[str, object]:
    # A malformed line must not lose the survivor it stands for: the survivor
    # list is the gate's evidence, and silently dropping one understates a red
    # run as a smaller problem than it is.
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        entry = None
    if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
        return _id_only(line.strip()[:200], "id_only: malformed detail line")
    return entry


def parse_survivors(survivors_file: Path | None) -> list[dict[str, object]]:
    """Survivors from `mutation_survivors.py` detail, or from a bare id list.

    Both are legal input: the detail file is what makes a red gate triageable
    (issue #119), and a plain list of ids is what `mutmut results` gives on its
    own when the detail step could not run.
    """
    if survivors_file is None or not survivors_file.exists():
        return []
    text = survivors_file.read_text()
    if not text.lstrip().startswith("{"):
        return [_id_only(mid, "id_only: no detail collected") for mid in text.split()]
    return [_parse_detail(line) for line in text.splitlines() if line.strip()]


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
        (counts.get("no_tests", 0) > 0, "uncovered_mutants"),
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
    baseline_ok = not any(marker in raw.lower() for marker in BASELINE_FAILURES)
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
