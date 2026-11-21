#!/usr/bin/env python3
"""Read the register of mutants left alive and try a test against one of them (#984).

For the session that kills survivors: the nightly lane keeps every mutant known
to be alive in `register.jsonl` on the `mutation-reports` branch, and this
reads it without mutmut.

    mutant_check.py list [--file PATH] [--kind TEXT] [--limit N]
    mutant_check.py show KEY
    mutant_check.py try KEY -- PYTEST ARGUMENTS

`try` answers one question: do these tests fail when the registered change is
in place? It runs them on the tree as it is, where they must pass, then copies
`src/` to a temporary directory, makes the change in the copy and runs them
again with the copy first on the import path, where they must fail. The
working tree is not touched and no mutmut runs.

It is an aid, not a measurement. mutmut decides which tests run for a mutant
and with which timeout; this runs the tests it is given. So a red here is a
reason to open the pull request and nothing more: the register changes only
when the nightly lane measures the function again, which the commit asks for
with `Kills-mutant: <key>` (docs/agentic-ci.md, "Claimed kills").

Exit codes of `try`: 0 the tests fail on the mutant; 1 they pass on it; 2 no
answer (the tests do not pass on the tree as it is, the run timed out, pytest
could not run, or the copy was not what the tests imported); 3 the change does
not apply, because the function is no longer the one that was measured.

Runs from the repo root.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

Entry = dict[str, Any]

BRANCH = "mutation-reports"
REGISTER = "register.jsonl"
SOURCE = "src"
PACKAGE = "lovspor"
# mutmut joins a class and its method with this in a mutant's name.
CLASS_SEPARATOR = "ǁ"
SHORTEST_PREFIX = 6
RED, GREEN, NO_ANSWER, NOT_APPLICABLE = 0, 1, 2, 3
PYTEST_FAILED = 1


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
    )


def load(path: Path | None) -> list[Entry]:
    """The register: from a file, or from the branch as the remote has it."""
    if path is not None:
        text = path.read_text(encoding="utf-8")
    else:
        if _git("fetch", "-q", "--no-tags", "origin", BRANCH).returncode != 0:
            print(f"warning: {BRANCH} was not fetched; reading the local copy", file=sys.stderr)
        shown = _git("show", f"origin/{BRANCH}:{REGISTER}")
        if shown.returncode != 0:
            raise SystemExit(f"error: no {REGISTER} on origin/{BRANCH}")
        text = shown.stdout
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def find(entries: list[Entry], key: str) -> Entry:
    """The entry with this key, or with the one key that starts with it."""
    exact = [entry for entry in entries if entry["key"] == key]
    close = [entry for entry in entries if entry["key"].startswith(key)]
    found = exact or (close if len(key) >= SHORTEST_PREFIX else [])
    if len(found) != 1:
        raise SystemExit(f"error: {len(found)} entries match {key!r}; give one whole key")
    return found[0]


def hunk(diff: str) -> tuple[list[str], list[str]]:
    """The lines a diff takes (with their context) and the lines it leaves in their place.

    Empty context lines at either end are dropped: the diff can open with the
    blank lines above the function, which are not part of it.
    """
    old: list[str] = []
    new: list[str] = []
    body = False
    for line in diff.splitlines():
        if line.startswith("@@"):
            body = True
        elif body and line[:1] in (" ", "-"):
            old.append(line[1:].rstrip())
            if line[:1] == " ":
                new.append(line[1:].rstrip())
        elif body and line[:1] == "+":
            new.append(line[1:].rstrip())
    for end in (0, -1):
        while old and new and old[end] == "" and new[end] == "":
            del old[end], new[end]
    return old, new


def _definition(tree: ast.AST, entry: Entry) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    scope: ast.AST | None = tree
    names = [*(entry["class"].split(CLASS_SEPARATOR)[-1:] if entry.get("class") else ()), ""]
    names[-1] = entry["symbol"]
    for name in names:
        scope = next(
            (
                node
                for node in ast.walk(scope)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name == name
            ),
            None,
        )
        if scope is None:
            return None
    return scope if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)) else None


def apply(source: str, entry: Entry) -> str | None:
    """The file with the entry's change made in its function; None when it does not fit.

    The diff was taken from the function alone, without its indentation. It
    fits when the lines it takes are in the function once, as they were.
    """
    old, new = hunk(entry.get("diff") or "")
    try:
        node = _definition(ast.parse(source), entry)
    except SyntaxError:
        return None
    if node is None or not old or old == new:
        return None
    lines = source.splitlines()
    first = min([node.lineno, *(item.lineno for item in node.decorator_list)]) - 1
    last = node.end_lineno or node.lineno
    indent = " " * node.col_offset
    function = [line.removeprefix(indent).rstrip() for line in lines[first:last]]
    places = [at for at in range(len(function)) if function[at : at + len(old)] == old]
    if len(places) != 1:
        return None
    changed = [indent + line if line else line for line in new]
    start = first + places[0]
    lines[start : start + len(old)] = changed
    return "\n".join(lines) + ("\n" if source.endswith("\n") else "")


def _pytest(arguments: list[str], env: dict[str, str], timeout: int) -> int | None:
    """pytest's exit code, or None when the run was stopped for taking too long."""
    command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *arguments]
    try:
        return subprocess.run(command, check=False, env=env, timeout=timeout).returncode  # noqa: S603
    except subprocess.TimeoutExpired:
        return None


def _imports_from(env: dict[str, str], root: Path) -> bool:
    """Whether the package the tests would import is the copy under `root`."""
    asked = subprocess.run(  # noqa: S603
        [sys.executable, "-c", f"import {PACKAGE}; print({PACKAGE}.__file__)"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    return asked.returncode == 0 and Path(asked.stdout.strip()).resolve().is_relative_to(root)


def attempt(entry: Entry, arguments: list[str], timeout: int) -> int:
    """Run the tests on the tree as it is and on a copy with the change; say what happened."""
    target = Path(entry["file"])
    mutated = apply(target.read_text(encoding="utf-8"), entry) if target.is_file() else None
    if mutated is None:
        print(
            f"does not apply: {entry['file']} `{entry['symbol']}` is not the function that was "
            "measured. The entry is stale until a night measures the function again."
        )
        return NOT_APPLICABLE
    clean = _pytest(arguments, dict(os.environ), timeout)
    if clean != 0:
        print(f"no answer: the tests do not pass on the tree as it is (pytest: {clean})")
        return NO_ANSWER
    with tempfile.TemporaryDirectory(prefix="mutant-check-") as scratch:
        copy = Path(scratch).resolve() / SOURCE
        shutil.copytree(SOURCE, copy)
        (copy / target.relative_to(SOURCE)).write_text(mutated, encoding="utf-8")
        inherited = os.environ.get("PYTHONPATH")
        path = os.pathsep.join([str(copy), *([inherited] if inherited else [])])
        env = {**os.environ, "PYTHONPATH": path}
        if not _imports_from(env, copy):
            print("no answer: the tests would not import the copy that holds the change")
            return NO_ANSWER
        result = _pytest(arguments, env, timeout)
    return _verdict(entry, result, timeout)


def _verdict(entry: Entry, result: int | None, timeout: int) -> int:
    if result == PYTEST_FAILED:
        print(f"RED on the mutant: these tests fail with the change of {entry['key']} in place.")
        print("Not a measurement. For the nightly lane to measure it, the commit says:")
        print(f"Kills-mutant: {entry['key']}")
        return RED
    if result == 0:
        print(f"GREEN on the mutant: these tests pass with the change of {entry['key']} in place.")
        return GREEN
    if result is None:
        print(f"no answer: the tests did not end within {timeout} s with the change in place")
    else:
        print(f"no answer: pytest could not run the tests with the change in place (exit {result})")
    return NO_ANSWER


def show(entry: Entry) -> int:
    where = f"{entry['class']}." if entry.get("class") else ""
    seen, measured = entry.get("first_seen") or {}, entry.get("last_measured") or {}
    print(f"key:           {entry['key']}")
    print(f"function:      {entry['file']} `{where}{entry['symbol']}`")
    print(f"status:        {entry.get('status')}")
    print(f"kind:          {entry.get('kind') or 'not recorded'}")
    print(f"first seen:    {seen.get('date')} at {str(seen.get('commit'))[:8]}")
    print(f"last measured: {measured.get('date')} at {str(measured.get('commit'))[:8]}")
    print(entry.get("diff") or "(no diff recovered)")
    return 0


def listing(entries: list[Entry], file: str | None, kind: str | None, limit: int) -> int:
    """Without a file: how many are alive in each file. With one: its entries."""
    chosen = [e for e in entries if kind is None or kind in (e.get("kind") or "")]
    if file is None:
        counts = Counter(entry["file"] for entry in chosen)
        for name, count in sorted(counts.items(), key=lambda c: (-c[1], c[0]))[:limit]:
            print(f"{count:5d}  {name}")
        print(f"{len(chosen)} alive in {len(counts)} file(s)")
        return 0
    for entry in [e for e in chosen if e["file"] == file][:limit]:
        where = f"{entry['class']}." if entry.get("class") else ""
        kind_shown = entry.get("kind") or "kind not recorded"
        print(f"{entry['key']}  {entry.get('status')}  {where}{entry['symbol']}  {kind_shown}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--register", type=Path, help=f"a {REGISTER} file instead of the branch")
    commands = parser.add_subparsers(dest="command", required=True)
    list_args = commands.add_parser("list")
    list_args.add_argument("--file")
    list_args.add_argument("--kind")
    list_args.add_argument("--limit", type=int, default=40)
    commands.add_parser("show").add_argument("key")
    try_args = commands.add_parser("try")
    try_args.add_argument("key")
    try_args.add_argument("--timeout", type=int, default=600, help="seconds for each test run")
    try_args.add_argument("pytest", nargs=argparse.REMAINDER, help="-- then pytest arguments")
    args = parser.parse_args(argv)
    entries = load(args.register)
    if args.command == "list":
        return listing(entries, args.file, args.kind, args.limit)
    entry = find(entries, args.key)
    if args.command == "show":
        return show(entry)
    arguments = [value for value in args.pytest if value != "--"]
    if not arguments:
        raise SystemExit("error: name the tests to run after `--`")
    return attempt(entry, arguments, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
