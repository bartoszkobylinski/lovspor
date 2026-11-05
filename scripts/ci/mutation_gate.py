#!/usr/bin/env python3
"""Deterministic 0/1 gate over mutation-result.json.

NO policy lives here — `mutation_to_json.py` computes `gate`; this program reads
it, optionally prints a markdown summary, and exits. Never an LLM, never a
threshold decision.

Usage:
    mutation_gate.py result.json              # exit 0 if gate.passed else 1
    mutation_gate.py --summary result.json    # markdown job summary, exit 0
    mutation_gate.py --unmeasured result.json # PR comment for a run that measured nothing
    mutation_gate.py --no-change result.json  # PR comment when remediation changed nothing
    mutation_gate.py --brief result.json      # remediation's work list, bucket by bucket
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load_result(path: Path) -> dict[str, object] | str:
    """Return the parsed result object, or an error message string.

    The artifact is untrusted data: a truncated or hand-mangled file must
    produce a clean failure, never a traceback.
    """
    try:
        r = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return f"cannot read mutation result: {e}"
    if not isinstance(r, dict):
        return f"malformed mutation result: not a JSON object ({type(r).__name__})"
    if r.get("schema_version") != 1:
        return f"unsupported schema_version: {r.get('schema_version')}"
    return r


def _is_count(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _extract(r: dict[str, object]) -> tuple[str, float | None, bool, str, tuple[int, ...]] | str:
    """Return (commit, score, passed, reason, counts) or an error message.

    JSON truthiness is not enough: a string "false" in gate.passed must be
    rejected, never treated as a passing gate.
    """
    try:
        m, gate = r["mutants"], r["gate"]
        commit, score = r["commit"], r["score"]
        passed, reason = gate["passed"], gate["reason"]  # type: ignore[index]
        counts = tuple(m[k] for k in ("total", "killed", "survived", "timeout"))  # type: ignore[index]
    except (KeyError, TypeError) as e:
        return f"malformed mutation result: {e!r}"
    if not isinstance(passed, bool) or not isinstance(reason, str):
        return "malformed mutation result: gate.passed must be a boolean, gate.reason a string"
    if not isinstance(commit, str) or not all(_is_count(c) for c in counts):
        return "malformed mutation result: commit must be a string, mutant counts integers"
    # null is the one non-number allowed: a run that measured no mutant has no
    # score, and saying so beats printing a 100 nobody earned (#311).
    if score is None:
        return commit, None, passed, reason, counts
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return "malformed mutation result: score must be a number or null"
    return commit, float(score), passed, reason, counts


def _added_line(diff: object) -> str | None:
    """The mutant's replacement line — the one thing triage always needs."""
    if not isinstance(diff, str):
        return None
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            return line[1:].strip()[:120]
    return None


def _equivalent_note(s: dict[str, object]) -> str:
    """A registered survivor says so, and says on whose written word."""
    equivalent = s.get("equivalent")
    if not isinstance(equivalent, dict):
        return ""
    registered = equivalent.get("registered")
    stamp = f", {registered}" if isinstance(registered, str) and registered else ""
    return f" — **equivalent**{stamp}"


def _survivor_bullet(s: object) -> str | None:
    """One survivor as `id — file:line — replacement`, skipping what is unknown."""
    if not isinstance(s, dict) or not isinstance(s.get("id"), str):
        return None
    where = s.get("file") if isinstance(s.get("file"), str) else "file unknown"
    if isinstance(s.get("symbol_line"), int):
        where = f"{where}:{s['symbol_line']}"
    change = _added_line(s.get("diff"))
    bullet = f"  - `{s['id']}` — {where}" + (f" — `{change}`" if change else "")
    return bullet + _equivalent_note(s)


def _survivor_lines(survivors: object, label: str = "Survivors", limit: int = 10) -> list[str]:
    """Triage list for the job summary; the artifact holds the full detail."""
    if not isinstance(survivors, list):
        return []
    bullets = [b for b in (_survivor_bullet(s) for s in survivors) if b]
    if not bullets:
        return []
    shown = bullets[:limit]
    if len(bullets) > limit:
        shown.append(f"  - … and {len(bullets) - limit} more (see the artifact)")
    return [f"- {label}:", *shown]


def _register_lines(equivalents: object) -> list[str]:
    """What the equivalent-mutant register did to this run, if anything.

    A refused entry is louder than an applied one: it means someone tried to
    excuse a survivor and the register would not let them, so the gate is red
    for a reason that is not in the mutants.
    """
    if not isinstance(equivalents, dict):
        return []
    registered = equivalents.get("registered")
    refused = equivalents.get("refused")
    lines = []
    if isinstance(registered, int) and registered:
        lines.append(f"- Registered equivalents: {registered} (`mutation-equivalents/`)")
    if isinstance(refused, list):
        lines += [f"- ⚠ Refused register entry: {r}" for r in refused if isinstance(r, str)]
    return lines


def _unmeasured_changed_lines(notices: object) -> list[str]:
    """Changed lines the score says nothing about (#289, #292).

    Absent in pre-notice artifacts and empty on most runs; either way, silent.
    """
    if not isinstance(notices, list):
        return []
    texts = [notice for notice in notices if isinstance(notice, str)]
    legacy = [notice for notice in texts if LEGACY_REMAINDER in notice]
    reachable = [notice for notice in texts if notice not in legacy]
    return [
        *_notice_bullets("- Unmeasured changed lines (no mutant can reach them):", reachable),
        *_notice_bullets(
            "- Legacy remainder, not run (#228) — never counted killed, not in the score:", legacy
        ),
    ]


# `mutation_legacy.py`'s region for the unchanged part of a legacy function:
# reachable by mutants, just not run, so not "no mutant can reach them".
LEGACY_REMAINDER = " (legacy remainder "


def _notice_bullets(heading: str, notices: list[str]) -> list[str]:
    bullets = []
    for notice in notices:
        where, _, region = notice.partition(" (")
        bullets.append(f"  - `{where}` — {region.removesuffix(')')}")
    return [heading, *bullets] if bullets else []


# Every bucket outside 🎉, keyed as the artifact stores it. A timed-out mutant
# is not a survivor and not a kill: it got no verdict inside mutmut's
# per-mutant time limit, so a report that folds it into either lies (#423).
NOT_KILLED = (
    ("survived", "survived"),
    ("timeout", "timed out"),
    ("invalid", "suspicious"),
    ("no_tests", "without tests"),
    ("unmeasured", "without a verdict"),
)
BLOCKERS = {
    "survived": (
        "{n} survivor(s) remediation called non-killable — a verdict that stands only"
        " with a stated equivalence argument per survivor (#455)"
    ),
    "timeout": (
        "{n} timed-out mutant(s) got no verdict — mutmut stopped them at its per-mutant"
        " time limit, so whether a test kills them was never measured; the artifact"
        " names each under `timed_out` (#776)"
    ),
    "invalid": (
        "{n} suspicious mutant(s) — mutmut could not tell killed from survived; the"
        " artifact names each under `suspicious`"
    ),
    "no_tests": "{n} mutant(s) no test reaches",
    "unmeasured": "{n} mutant(s) got no verdict — killed by a signal",
}


def _not_killed(r: dict[str, object]) -> dict[str, int]:
    """Bucket counts; one an older artifact does not carry reads as 0."""
    mutants = r.get("mutants")
    known = mutants if isinstance(mutants, dict) else {}
    return {k: v if _is_count(v := known.get(k, 0)) else 0 for k, _ in NOT_KILLED}


def _tally(counts: dict[str, int]) -> str:
    return " · ".join(f"{counts[key]} {label}" for key, label in NOT_KILLED)


# The bucket key in `mutants` and the list in the artifact that names its mutants.
NAMED_LISTS = {"timeout": "timed_out", "invalid": "suspicious"}
_NAMED_IN_NO_CHANGE = 3


def _clean(value: object, limit: int = 160) -> str:
    """Artifact text on one line, outside any code span: it is quoted, not trusted."""
    text = value if isinstance(value, str) else str(value)
    return " ".join(text.replace("`", "'").split())[:limit]


def _entries(listed: object) -> list[dict[str, object]]:
    if not isinstance(listed, list):
        return []
    return [e for e in listed if isinstance(e, dict) and isinstance(e.get("id"), str)]


def _where(entry: dict[str, object]) -> str:
    where = _clean(entry["file"]) if isinstance(entry.get("file"), str) else "file unknown"
    line = entry.get("symbol_line")
    return f"{where}:{line}" if _is_count(line) else where


def _named(listed: object) -> str:
    """A few of the bucket's mutants by id and place; the artifact has the rest."""
    entries = _entries(listed)
    names = [f"`{_clean(e['id'])}` ({_where(e)})" for e in entries[:_NAMED_IN_NO_CHANGE]]
    if len(entries) > _NAMED_IN_NO_CHANGE:
        names.append(f"and {len(entries) - _NAMED_IN_NO_CHANGE} more")
    return f": {', '.join(names)}" if names else ""


def _blocker(r: dict[str, object], key: str, n: int) -> str:
    named = _named(r.get(NAMED_LISTS[key])) if key in NAMED_LISTS else ""
    return BLOCKERS[key].format(n=n) + named


def _no_change_line(r: dict[str, object], reason: str) -> str:
    """The escalation when remediation changed nothing: it names what blocked.

    Survivors are called non-killable only when there were survivors;
    on PR #395 it was said over two timeouts and none (#423).
    """
    counts = _not_killed(r)
    blockers = "; ".join(_blocker(r, k, n) for k, n in counts.items() if n)
    if not blockers:
        blockers = "no mutant outside the killed count is on record, only the gate reason"
    return (
        f"Mutation remediation made no safe test-only change. Gate: FAIL ({reason})."
        f" Not killed: {_tally(counts)}. Blocked by: {blockers}."
    )


# What remediation is handed, bucket by bucket (#776). A timed-out or suspicious
# mutant is not a survivor: no test was seen to pass with it in place, so it is
# never listed as one, and the work it asks for is different.
BRIEF_BUCKETS = (
    (
        "survived",
        "survivors",
        "Survivors",
        "confirmed: every covering test passed with the mutant in place."
        " Attempt a killing test for each.",
    ),
    (
        "timeout",
        "timed_out",
        "Timed out",
        "not a survivor: mutmut stopped the covering tests at its per-mutant time"
        " limit, so no verdict was reached. Investigate the hang before any test.",
    ),
    (
        "invalid",
        "suspicious",
        "Suspicious",
        "not a survivor: the covering tests took unusually long and mutmut could"
        " not tell killed from survived. Re-run them with the diff applied first.",
    ),
)
_BRIEF_LIMIT = 20
_DIFF_LINES = 6


def _changed_lines(entry: dict[str, object]) -> list[str]:
    diff = entry.get("diff")
    if not isinstance(diff, str):
        source = _clean(entry.get("detail_source") or "no detail collected", 120)
        return [f"  - no diff recovered ({source}): locate the function from the id"]
    changed = [
        line
        for line in diff.splitlines()
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]
    return [f"  - `{_diff_line(line)}`" for line in changed[:_DIFF_LINES]]


def _diff_line(line: str) -> str:
    """Indentation is kept, unlike `_clean`: in Python it can be the mutation."""
    return line.replace("`", "'").rstrip()[:160]


def _brief_entry(entry: dict[str, object]) -> list[str]:
    symbol = entry.get("symbol")
    at = _where(entry) + (f" in {_clean(symbol, 60)}" if isinstance(symbol, str) else "")
    equivalent = entry.get("equivalent")
    if isinstance(equivalent, dict):
        stamp = equivalent.get("registered")
        stamp = f", {_clean(stamp, 40)}" if isinstance(stamp, str) and stamp else ""
        at += f" — **equivalent**{stamp} — skip"
    return [f"- `{_clean(entry['id'])}`", f"  - at {at}", *_changed_lines(entry)]


def _brief_bucket(r: dict[str, object], bucket: tuple[str, str, str, str]) -> list[str]:
    key, listed, heading, meaning = bucket
    counted = _not_killed(r)[key]
    lines = ["", f"### {heading} (`{listed}`) — {meaning}"]
    if listed not in r and counted:
        return [*lines, f"{counted} counted, none named: this artifact predates the list (#776)."]
    entries = _entries(r.get(listed))
    if not entries:
        return [*lines, f"none named ({counted} counted)" if counted else "none"]
    for entry in entries[:_BRIEF_LIMIT]:
        lines += _brief_entry(entry)
    if len(entries) > _BRIEF_LIMIT:
        lines.append(f"- … and {len(entries) - _BRIEF_LIMIT} more (see the artifact)")
    return lines


def _brief_lines(r: dict[str, object], extracted: tuple[Any, ...]) -> list[str]:
    verdict = "PASS" if extracted[2] else "FAIL"
    lines = [
        "## Mutants to work on",
        "",
        f"Gate: {verdict} ({extracted[3]}). Not killed: {_tally(_not_killed(r))}.",
        "Read from mutation-result.json. Ids renumber: work from the place and the lines.",
    ]
    for bucket in BRIEF_BUCKETS:
        lines += _brief_bucket(r, bucket)
    return lines


def _hint(r: dict[str, object]) -> str | None:
    # Diagnostics, not policy: absent in pre-hint artifacts, shown when present.
    hint = r.get("failure_hint")
    return hint if isinstance(hint, str) and hint else None


def _print_summary(r: dict[str, object], extracted: tuple[Any, ...]) -> None:
    commit, score, passed, reason, (total, killed, survived, timeout) = extracted
    shown = "none — no mutant was measured" if score is None else score
    print("## Mutation testing")
    print(f"- SHA: `{commit}`")
    print(
        f"- Total: {total} · Killed: {killed} · Survived: {survived}"
        f" · Timeout: {timeout} · Score: {shown}"
    )
    print(f"- Not killed: {_tally(_not_killed(r))}")
    print(f"- Gate: {'PASS' if passed else 'FAIL'} ({reason})")
    if hint := _hint(r):
        print(f"- Hint: `{hint}`")
    for line in [
        *_survivor_lines(r.get("survivors")),
        *_survivor_lines(r.get("timed_out"), "Timed out"),
        *_survivor_lines(r.get("suspicious"), "Suspicious"),
        *_register_lines(r.get("equivalents")),
        *_unmeasured_changed_lines(r.get("unmeasured_changed_lines")),
    ]:
        print(line)
    print(f"- Artifact: `mutation-result-{commit}`")


def _unmeasured_lines(r: dict[str, object]) -> list[str]:
    """The PR comment for a gate that failed before any mutant was measured.

    Remediation classifies survivors; with none measured, naming that step
    would point the reader at a problem that does not exist (#311).
    """
    if r.get("baseline_tests_passed") is False:
        cause = "the clean baseline test run failed"
    else:
        cause = f"the mutation tool failed (exit {r.get('tool_exit_code')})"
    lines = [f"Mutation did not run: {cause}, so no mutant was measured."]
    if hint := _hint(r):
        lines += ["", "```", hint, "```"]
    lines += [
        "",
        "Codex remediation skipped: there are no survivors to classify. "
        "Fix the failure above and push. Human review required.",
    ]
    return lines


def _verdict(r: dict[str, object], extracted: tuple[Any, ...]) -> int:
    passed, reason = extracted[2], extracted[3]
    if passed:
        print(f"mutation gate PASS ({reason})")
        return 0
    print(f"mutation gate FAIL ({reason}); not killed: {_tally(_not_killed(r))}")
    if hint := _hint(r):
        print(f"hint: {hint}")
    return 1


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--summary", action="store_true")
    mode.add_argument("--unmeasured", action="store_true")
    mode.add_argument("--no-change", action="store_true")
    mode.add_argument("--brief", action="store_true")
    ap.add_argument("result", type=Path)
    return ap.parse_args()


def _print_unmeasured(r: dict[str, object], _extracted: tuple[Any, ...]) -> None:
    print("\n".join(_unmeasured_lines(r)))


def _print_no_change(r: dict[str, object], extracted: tuple[Any, ...]) -> None:
    print(_no_change_line(r, extracted[3]))


def _print_brief(r: dict[str, object], extracted: tuple[Any, ...]) -> None:
    print("\n".join(_brief_lines(r, extracted)))


# Every mode but the verdict prints and exits 0: it reports, it does not judge.
MODES = {
    "summary": _print_summary,
    "unmeasured": _print_unmeasured,
    "no_change": _print_no_change,
    "brief": _print_brief,
}


def main() -> int:
    args = _parse_args()
    r = _load_result(args.result)
    if isinstance(r, str):
        print(r, file=sys.stderr)
        return 1
    extracted = _extract(r)
    if isinstance(extracted, str):
        print(extracted, file=sys.stderr)
        return 1
    mode = next((name for name in MODES if getattr(args, name)), None)
    if mode is None:
        return _verdict(r, extracted)
    MODES[mode](r, extracted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
