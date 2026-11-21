#!/usr/bin/env python3
"""Find the registered survivors a commit in the nightly range claims to kill (#984).

The nightly lane measures the functions a range changed in `src/`. A survivor
is killed by a test, and a test is not a change of `src/`: nothing measured the
function again, so the register kept the mutant as alive for good
(`mutation_ledger.py` names the same price for a carried survivor).

A commit that kills registered mutants says so in its message, one line a
mutant or several keys on one line:

    Kills-mutant: 0123456789abcdef

This reads those lines over the range, looks the keys up in the register and
writes one whole-function mutmut pattern for each function to measure again.
The claim decides nothing: the night measures, and the register changes by the
measurement alone. A key the register does not hold, or one whose function is
no longer in the tree, is reported and not measured.

No mutmut import. Runs from the repo root, on the head it measures.

Usage:
    mutation_remeasure.py --base SHA --head SHA --register FILE
        --out-patterns FILE --out-claims FILE
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from mutation_scope import CLASS_NAME_SEPARATOR, head_source, keyed_units, module_of

Entry = dict[str, Any]

CLAIM = re.compile(r"^Kills-mutant:[ \t]*(.+)$", re.IGNORECASE | re.MULTILINE)
KEY = re.compile(r"\b[0-9a-f]{16}(?:-\d+)?\b")
TO_MEASURE = "to measure"
NOT_REGISTERED = "not in the register"
GONE = "its function is not in the tree"
# What `range_log` asks git to put after a commit and between its hash and its message.
_END, _SPLIT = "\x01", "\x00"


def claimed(log: str) -> list[tuple[str, str]]:
    """(key, commit) for every key a commit message claims, once, oldest claim kept."""
    found: dict[str, str] = {}
    for record in reversed(log.split(_END)):
        commit, _, message = record.strip().partition(_SPLIT)
        for line in CLAIM.findall(message):
            for key in KEY.findall(line):
                found.setdefault(key, commit)
    return sorted(found.items())


def read_register(path: Path) -> dict[str, Entry]:
    """The register's entries by key; nothing when the file is absent or empty."""
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as handle:
        entries = [json.loads(line) for line in handle if line.strip()]
    return {entry["key"]: entry for entry in entries}


def unit_key(entry: Entry) -> str:
    """The name mutmut gives the entry's function inside its module."""
    if entry.get("class"):
        return f"x{CLASS_NAME_SEPARATOR}{entry['class']}{CLASS_NAME_SEPARATOR}{entry['symbol']}"
    return f"x_{entry['symbol']}"


def pattern(entry: Entry, known: dict[str, set[str]]) -> str | None:
    """The pattern of all the function's mutants, or None when the head has no such function."""
    file = entry["file"]
    if file not in known:
        known[file] = {unit.key for unit in keyed_units(head_source(file))}
    key = unit_key(entry)
    return f"{module_of(file)}.{key}__mutmut_*" if key in known[file] else None


def resolve(claims: list[tuple[str, str]], register: dict[str, Entry]) -> list[Entry]:
    """Each claim with what the night does about it."""
    known: dict[str, set[str]] = {}
    resolved = []
    for key, commit in claims:
        entry = register.get(key)
        found = pattern(entry, known) if entry is not None else None
        state = NOT_REGISTERED if entry is None else GONE if found is None else TO_MEASURE
        resolved.append(
            {
                "key": key,
                "commit": commit,
                "state": state,
                "file": entry["file"] if entry else None,
                "class": entry.get("class") if entry else None,
                "symbol": entry["symbol"] if entry else None,
                "pattern": found,
            }
        )
    return resolved


def range_log(base: str, head: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", "log", "--format=%H%x00%B%x01", f"{base}..{head}"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ("--base", "--head"):
        parser.add_argument(name, required=True)
    for name in ("--register", "--out-patterns", "--out-claims"):
        parser.add_argument(name, required=True, type=Path)
    args = parser.parse_args(argv)
    claims = resolve(claimed(range_log(args.base, args.head)), read_register(args.register))
    patterns = sorted({claim["pattern"] for claim in claims if claim["state"] == TO_MEASURE})
    args.out_patterns.write_text("".join(f"{name}\n" for name in patterns), encoding="utf-8")
    args.out_claims.write_text(
        json.dumps({"base": args.base, "head": args.head, "claims": claims}, indent=1) + "\n",
        encoding="utf-8",
    )
    print(
        f"claims: {len(claims)} key(s) claimed in {args.base[:8]}..{args.head[:8]}, "
        f"{len(patterns)} function(s) to measure again"
    )
    for claim in claims:
        if claim["state"] != TO_MEASURE:
            print(f"claims: {claim['key']} ({claim['commit'][:8]}): {claim['state']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
