#!/usr/bin/env python3
"""Deterministic 0/1 gate over mutation-result.json.

NO policy lives here — `mutation_to_json.py` computes `gate`; this program reads
it, optionally prints a markdown summary, and exits. Never an LLM, never a
threshold decision.

Usage:
    mutation_gate.py result.json              # exit 0 if gate.passed else 1
    mutation_gate.py --summary result.json    # markdown job summary, exit 0
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


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


def _extract(r: dict[str, object]) -> tuple[str, float, bool, str, tuple[int, ...]] | str:
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
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return "malformed mutation result: score must be a number"
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


def _survivor_lines(survivors: object, limit: int = 10) -> list[str]:
    """Triage list for the job summary; the artifact holds the full detail."""
    if not isinstance(survivors, list):
        return []
    bullets = [b for b in (_survivor_bullet(s) for s in survivors) if b]
    if not bullets:
        return []
    shown = bullets[:limit]
    if len(bullets) > limit:
        shown.append(f"  - … and {len(bullets) - limit} more (see the artifact)")
    return ["- Survivors:", *shown]


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
        lines.append(f"- Registered equivalents: {registered} (`mutation-equivalents.toml`)")
    if isinstance(refused, list):
        lines += [f"- ⚠ Refused register entry: {r}" for r in refused if isinstance(r, str)]
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("result", type=Path)
    args = ap.parse_args()

    r = _load_result(args.result)
    if isinstance(r, str):
        print(r, file=sys.stderr)
        return 1

    extracted = _extract(r)
    if isinstance(extracted, str):
        print(extracted, file=sys.stderr)
        return 1
    commit, score, passed, reason, (total, killed, survived, timeout) = extracted
    # Diagnostics, not policy: absent in pre-hint artifacts, shown when present.
    hint = r.get("failure_hint")
    hint = hint if isinstance(hint, str) and hint else None

    if args.summary:
        print("## Mutation testing")
        print(f"- SHA: `{commit}`")
        print(
            f"- Total: {total} · Killed: {killed} · Survived: {survived}"
            f" · Timeout: {timeout} · Score: {score}"
        )
        print(f"- Gate: {'PASS' if passed else 'FAIL'} ({reason})")
        if hint:
            print(f"- Hint: `{hint}`")
        for line in _survivor_lines(r.get("survivors")):
            print(line)
        for line in _register_lines(r.get("equivalents")):
            print(line)
        print(f"- Artifact: `mutation-result-{commit}`")
        return 0

    if passed:
        print(f"mutation gate PASS ({reason})")
        return 0
    print(f"mutation gate FAIL ({reason}); survivors: {survived}")
    if hint:
        print(f"hint: {hint}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
