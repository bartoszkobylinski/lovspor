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

The one exception is the register of provably-equivalent mutants (issue #122):
a survivor whose mutation is recorded in `mutation-equivalents.toml`, with a
written justification, stops failing the gate. It does NOT stop being reported.
Counts, score and the survivor list are untouched — the raw signal is the point
(decisions.md §9c: "equivalent survivors are registered, not chased") — only the
pass/fail verdict moves, and each excused survivor carries the justification
that excused it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
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


EQUIVALENTS_FILE = Path("mutation-equivalents.toml")
EQUIVALENT_FIELDS = ("file", "symbol", "mutation", "justification")


class RunHealth(NamedTuple):
    """Everything the gate needs to know beyond the mutant counts."""

    not_applicable: bool
    completed: bool
    baseline_ok: bool
    tool_exit_code: int
    budget_exceeded: bool
    # None means "there is no survivor detail to check against the register",
    # which the gate must read as unexplained: a waiver it cannot verify is
    # not a waiver. 0 means every survivor is registered as equivalent.
    unexplained_survivors: int | None = None
    registered_equivalents: int = 0


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


class Equivalent(NamedTuple):
    """One registered equivalent mutant: what it changes, and why that is safe."""

    file: str
    symbol: str
    justification: str
    registered: str
    change: tuple[str, ...]


def normalise_mutation(diff: str) -> tuple[str, ...]:
    """The mutation itself: the -/+ lines, without file headers or context.

    Matching on this rather than on the mutant id is deliberate. Ids renumber
    whenever the file changes upstream of the mutant, so an id-keyed register
    would drift onto a different mutation without anyone touching it — the
    failure mode that makes a suppression list dangerous.
    """
    return tuple(
        line[0] + line[1:].strip()
        for line in diff.splitlines()
        if line.startswith(("-", "+")) and not line.startswith(("---", "+++"))
    )


def _equivalent(raw: object) -> Equivalent | str:
    """Parse one register entry, or say why it is refused."""
    if not isinstance(raw, dict):
        return f"not a table: {type(raw).__name__}"
    missing = [f for f in EQUIVALENT_FIELDS if not str(raw.get(f, "")).strip()]
    if missing:
        return f"{raw.get('file', '<no file>')}: missing {', '.join(missing)}"
    change = normalise_mutation(str(raw["mutation"]))
    if not change:
        return f"{raw['file']}: the mutation block has no -/+ lines"
    return Equivalent(
        file=str(raw["file"]),
        symbol=str(raw["symbol"]),
        justification=str(raw["justification"]).strip(),
        registered=str(raw.get("registered", "")).strip(),
        change=change,
    )


def load_equivalents(path: Path) -> tuple[list[Equivalent], list[str]]:
    """(registered equivalents, refused entries with the reason).

    A refused entry is never applied: silencing a survivor is a decision, and
    an undocumented one does not count as having been made. Refusals are
    reported rather than raised — a broken register must not cost the run its
    verdict, it must cost the entry its effect.
    """
    if not path.exists():
        return [], []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        return [], [f"unreadable register {path}: {e}"]
    parsed = [_equivalent(raw) for raw in data.get("equivalent", [])]
    return (
        [e for e in parsed if isinstance(e, Equivalent)],
        [r for r in parsed if isinstance(r, str)],
    )


def _match(survivor: dict[str, object], equivalents: list[Equivalent]) -> Equivalent | None:
    # The file is part of the key: two functions in different modules can carry
    # byte-identical mutations, and a waiver written for one is not evidence
    # about the other. A survivor with no recovered diff (issue #119) never
    # matches, which is the fail-closed direction.
    diff, file = survivor.get("diff"), survivor.get("file")
    if not isinstance(diff, str) or not isinstance(file, str):
        return None
    change = normalise_mutation(diff)
    return next((e for e in equivalents if e.file == file and e.change == change), None)


def annotate_equivalents(
    survivors: list[dict[str, object]], equivalents: list[Equivalent]
) -> tuple[int, int]:
    """Mark each survivor in place; return (registered, unexplained)."""
    registered = 0
    for survivor in survivors:
        match = _match(survivor, equivalents)
        survivor["equivalent"] = (
            None
            if match is None
            else {
                "symbol": match.symbol,
                "justification": match.justification,
                "registered": match.registered,
            }
        )
        registered += match is not None
    return registered, len(survivors) - registered


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
    # A survivor signal is excused only when EVERY survivor on record is a
    # registered equivalent (issue #122). `unexplained_survivors is None` means
    # the survivors could not be read at all, so there is nothing to excuse
    # them with and the signal stands.
    survivors_stand = (counts["survived"] > 0 or bool(bits & 2)) and (
        health.unexplained_survivors != 0
    )
    failures = (
        (not health.baseline_ok, "baseline_tests_failed"),
        (not health.completed, "run_incomplete"),
        (survivors_stand, "surviving_mutants"),
        (counts["timeout"] > 0 or bool(bits & 4), "timeout_mutants"),
        (counts["invalid"] > 0 or bool(bits & 8), "suspicious_mutants"),
        (counts.get("no_tests", 0) > 0, "uncovered_mutants"),
    )
    for failed, reason in failures:
        if failed:
            return {"passed": False, "reason": reason}
    if health.registered_equivalents:
        return {"passed": True, "reason": "equivalent_mutants_only"}
    return {"passed": True, "reason": "ok"}


def _health(raw: str, tool_exit_code: int, counts_done: bool) -> RunHealth:
    not_applicable = NOT_APPLICABLE in raw
    return RunHealth(
        not_applicable=not_applicable,
        completed=not_applicable or counts_done,
        baseline_ok=not any(marker in raw.lower() for marker in BASELINE_FAILURES),
        tool_exit_code=tool_exit_code,
        budget_exceeded=BUDGET_EXCEEDED in raw or bool(tool_exit_code & 16),
    )


def build_result(
    commit: str, raw: str, tool_exit_code: int, survivors_file: Path | None = None
) -> dict[str, object]:
    counts, run_finished = parse_counts(raw)
    health = _health(raw, tool_exit_code, run_finished)
    survivors = parse_survivors(survivors_file)
    equivalents, refused = load_equivalents(EQUIVALENTS_FILE)
    registered, unexplained = annotate_equivalents(survivors, equivalents)
    # Pessimistic score: only 🎉 counts as killed. Timeout and suspicious fail
    # the gate anyway (mutmut exit bits 4 and 8), so they never inflate the score.
    # Registered equivalents do not adjust it either: the register moves the
    # verdict, never the measurement (decisions.md §9c).
    score = round(100.0 * counts["killed"] / counts["total"], 2) if counts["total"] else 100.0
    checked = health._replace(
        unexplained_survivors=unexplained if survivors else None,
        registered_equivalents=registered,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "commit": commit,
        "completed": health.completed,
        "baseline_tests_passed": health.baseline_ok,
        "tool": TOOL,
        "tool_exit_code": tool_exit_code,
        "mutants": counts,
        "score": score,
        "gate": compute_gate(counts, checked),
        "equivalents": {"registered": registered, "refused": refused},
        "failure_hint": parse_failure_hint(raw),
        "survivors": survivors,
    }


def _report_register() -> int:
    """`--check-equivalents`: does the register parse, and what does it excuse?"""
    equivalents, refused = load_equivalents(EQUIVALENTS_FILE)
    for entry in equivalents:
        print(f"registered: {entry.file} {entry.symbol} — {' '.join(entry.change)}")
    for reason in refused:
        print(f"REFUSED: {reason}", file=sys.stderr)
    print(f"{len(equivalents)} registered, {len(refused)} refused ({EQUIVALENTS_FILE})")
    return 1 if refused else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit")
    ap.add_argument("--raw", type=Path)
    ap.add_argument("--survivors-file", type=Path, default=None)
    ap.add_argument("--tool-exit-code", type=int)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--check-equivalents", action="store_true")
    args = ap.parse_args()

    if args.check_equivalents:
        return _report_register()
    if args.commit is None or args.raw is None or args.tool_exit_code is None or args.out is None:
        ap.error("--commit, --raw, --tool-exit-code and --out are required")
    if not FULL_SHA_RE.fullmatch(args.commit):
        print(f"refusing to write result for non-full SHA: {args.commit!r}", file=sys.stderr)
        return 2

    raw = args.raw.read_text(errors="replace") if args.raw.exists() else ""
    result = build_result(args.commit, raw, args.tool_exit_code, args.survivors_file)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    gate = result["gate"]
    print(f"wrote {args.out} (gate.passed={gate['passed']}, reason={gate['reason']})")  # type: ignore[index]
    for reason in result["equivalents"]["refused"]:  # type: ignore[index]
        print(f"REFUSED register entry: {reason}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
