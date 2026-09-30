#!/usr/bin/env python3
"""Run only the changed lines' mutants of a large legacy function (#228).

A function with a `function-lines` entry in the ratchet baseline is legacy
(owner decision 2026-09-30). When a PR changes it, mutating the whole body puts
every untouched line's debt on that PR: #227 changed three lines of `run_sync`
and 179 of its 188 survivors were pre-existing code. So for such a function
only the mutants whose changed line is one of the PR's post-image lines run.
The rest are not run, and are named as legacy remainder through the #420
`unmeasured changed lines` notice: never counted killed, never in the score.

    mutation_legacy.py generate [--jobs N]
        write the shadow tree's mutants without running any (cwd: repo root)
    mutation_legacy.py select --plan PLAN --mutmut BIN [--jobs N]
        print the mutant names to run; notices go to stderr

PLAN is the JSON `mutation_scope.py --legacy-plan` writes.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

UNMEASURED_PREFIX = "unmeasured changed lines: "
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")
RESULT_RE = re.compile(r"^\s+(\S+): ", re.MULTILINE)


@dataclass(frozen=True)
class Target:
    """One entry of the plan: `mutation_scope.LegacyTarget`, read back."""

    path: str
    function: str
    prefix: str
    start: int
    end: int
    changed: tuple[int, ...]


@dataclass(frozen=True)
class Selection:
    run: list[str]
    skipped: dict[str, set[int]]


def load_plan(plan: Path) -> list[Target]:
    entries = json.loads(plan.read_text(encoding="utf-8"))
    return [Target(**{**entry, "changed": tuple(entry["changed"])}) for entry in entries]


def names_of(results: str, prefix: str) -> list[str]:
    """The mutants `mutmut results --all true` lists for one function."""
    names = RESULT_RE.findall(results)
    return [n for n in names if n.startswith(prefix) and n[len(prefix) :].isdigit()]


def _number(name: str) -> int:
    return int(name.rsplit("_", 1)[1])


def hunks(diff: str) -> list[list[str]]:
    """Each hunk of a `mutmut show` diff, as its prefixed lines."""
    found: list[list[str]] = []
    for line in diff.splitlines():
        if HUNK_RE.match(line):
            found.append([])
        elif found and (line == "" or line[0] in " -+"):
            found[-1].append(line or " ")
    return found


def _hunk_lines(hunk: list[str], source: list[str], span: tuple[int, int]) -> set[int]:
    """Source lines of the hunk's removed lines, wherever its pre-image
    matches inside `span`.

    `mutmut show` numbers lines from the function's own first line, comments
    above it included, and dedents a method; so the pre-image (context and
    removed lines) is matched by content, whitespace-insensitively.
    """
    pre = [line for line in hunk if not line.startswith("+")]
    text = [line[1:].strip() for line in pre]
    offsets = [i for i, line in enumerate(pre) if line.startswith("-")]
    hits: set[int] = set()
    for start in range(len(source) - len(text) + 1):
        if [line.strip() for line in source[start : start + len(text)]] != text:
            continue
        lines = {start + 1 + offset for offset in offsets}
        if all(span[0] <= line <= span[1] for line in lines):
            hits |= lines
    return hits


def mutant_lines(diff: str, source: list[str], span: tuple[int, int]) -> set[int] | None:
    """The source lines a mutant changes; None when that cannot be resolved."""
    found = hunks(diff)
    lines: set[int] = set()
    for hunk in found:
        hit = _hunk_lines(hunk, source, span)
        if not hit:
            return None
        lines |= hit
    return lines or None


def select(diffs: dict[str, str], target: Target, source: list[str]) -> Selection:
    """Run a mutant on a changed line, or one whose line is unresolved: the
    carve-out may skip only what it has placed off the change."""
    run: list[str] = []
    skipped: dict[str, set[int]] = {}
    for name in sorted(diffs, key=_number):
        lines = mutant_lines(diffs[name], source, (target.start, target.end))
        if lines is None or lines & set(target.changed):
            run.append(name)
        else:
            skipped[name] = lines
    return Selection(run, skipped)


def _ranges(lines: set[int]) -> str:
    spans: list[list[int]] = []
    for line in sorted(lines):
        if spans and line == spans[-1][1] + 1:
            spans[-1][1] = line
        else:
            spans.append([line, line])
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in spans)


def remainder_notice(target: Target, selection: Selection) -> str | None:
    """The #420 notice for the mutants not run; None when every one ran."""
    if not selection.skipped:
        return None
    lines = set().union(*selection.skipped.values())
    total = len(selection.run) + len(selection.skipped)
    region = f"legacy remainder {target.function}: {len(selection.skipped)} of {total}"
    return f"{UNMEASURED_PREFIX}{target.path}:{_ranges(lines)} ({region} mutants not run, #228)"


def _show(mutmut: str, name: str) -> str:
    """A failed `mutmut show` yields no diff, so the mutant runs."""
    shown = subprocess.run(  # noqa: S603
        [mutmut, "show", name], capture_output=True, text=True, check=False
    )
    return shown.stdout if shown.returncode == 0 else ""


def _select_target(target: Target, results: str, mutmut: str, jobs: int) -> list[str]:
    names = names_of(results, target.prefix)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        diffs = dict(zip(names, pool.map(lambda n: _show(mutmut, n), names), strict=True))
    source = Path(target.path).read_text(encoding="utf-8").splitlines()
    selection = select(diffs, target, source)
    changed = _ranges(set(target.changed))
    print(
        f"legacy function {target.path} {target.function}: {len(selection.run)} of "
        f"{len(names)} mutants run, those on changed lines {changed} (#228)",
        file=sys.stderr,
    )
    if notice := remainder_notice(target, selection):
        print(notice, file=sys.stderr)
    return selection.run


def _results(mutmut: str) -> str:
    return subprocess.run(  # noqa: S603
        [mutmut, "results", "--all", "true"], capture_output=True, text=True, check=True
    ).stdout


def generate(jobs: int) -> None:
    """Write the mutants and their meta without running one. mutmut 3.8.0 has
    no command for it, so this runs the steps `mutmut run` starts with
    (`_run`, mutmut/__main__.py:955-960); `mutmut run` then reuses them."""
    files = importlib.import_module("mutmut.utils.file_utils")
    runner = importlib.import_module("mutmut.__main__")
    files.copy_src_dir()
    files.copy_also_copy_files()
    runner.create_mutants(jobs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["generate", "select"])
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--mutmut", default="mutmut")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if args.command == "generate":
        generate(args.jobs)
        return 0
    if args.plan is None:
        parser.error("select needs --plan")
    results = _results(args.mutmut)
    for target in load_plan(args.plan):
        for name in _select_target(target, results, args.mutmut, args.jobs):
            print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
