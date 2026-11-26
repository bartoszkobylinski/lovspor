#!/usr/bin/env python3
"""Refuse a pull request that changes the owner-change pin file with anything else.

`ops/lovverk-owner-changes` decides which reviewed lovverk commits the sync
may later merge into `lokale-forskrifter/` (docs/owner-changes.md). A
40-hex line in a large diff is easy to miss, so a pull request that changes
the file must change nothing else, and must carry no commit made by an
automated agent lane (subject ending in `[agent:<lane>]`, as
`pr-pipeline.yml` and `mutation-remediation.yml` stamp them).

Usage:
    gh api --paginate repos/<repo>/pulls/<n>/files \
        --jq '.[] | {filename, previous_filename}' > files.jsonl
    gh api --paginate repos/<repo>/pulls/<n>/commits \
        --jq '.[].commit.message | split("\\n")[0]' > subjects.txt
    check_owner_change_pr.py --changed-files <n reported> --commits <n reported> \
        --files files.jsonl --subjects subjects.txt

The workflow calls it only when git shows the file changed. A complete listing
without the file passes (reached only on a stale base). For a pull request
that touches it, every doubt fails: a file or commit listing shorter than the
count the pull request reports (the API lists 3000 files and 250 commits at
most), or a rename into or out of the file.
Exit 0 = pass, 1 = refused (reasons on stderr), 2 = bad input.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PIN_FILE = "ops/lovverk-owner-changes"
AGENT_SUBJECT = re.compile(r"\[agent:[A-Za-z0-9_.-]+\]\s*$")


def names(lines: list[str]) -> list[list[str]]:
    """The names each changed file goes by: its own, and the one it was renamed from."""
    files = []
    for line in lines:
        if not line.strip():
            continue
        entry = json.loads(line)
        if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str):
            raise ValueError(f"not a changed-file record: {line[:80]}")
        earlier = entry.get("previous_filename")
        if earlier is not None and not isinstance(earlier, str):
            raise ValueError(f"previous_filename is not a string: {line[:80]}")
        files.append([entry["filename"], *([earlier] if earlier else [])])
    return files


def refusals(files: list[list[str]], reported: int, subjects: list[str], commits: int) -> list[str]:
    """Why the pull request may not change the pin file; empty when it may."""
    if not any(PIN_FILE in file for file in files):
        if len(files) != reported:
            return [f"listed {len(files)} changed files, the pull request reports {reported}"]
        return []
    problems = []
    if reported != 1 or files != [[PIN_FILE]]:
        others = sorted({name for file in files for name in file} - {PIN_FILE})
        detail = f": {', '.join(others)}" if others else " (renamed)"
        problems.append(f"{PIN_FILE} must be the only file changed{detail}")
    if len(subjects) != commits:
        problems.append(f"listed {len(subjects)} commits, the pull request reports {commits}")
    agents = [subject for subject in subjects if AGENT_SUBJECT.search(subject)]
    if agents:
        problems.append(f"automated agent commit on a pin-file pull request: {agents[0]}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--changed-files", type=int, required=True)
    parser.add_argument("--commits", type=int, required=True)
    parser.add_argument("--files", type=Path, required=True)
    parser.add_argument("--subjects", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        files = names(args.files.read_text(encoding="utf-8").splitlines())
        subjects = args.subjects.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"check_owner_change_pr: bad input: {exc}", file=sys.stderr)
        return 2
    problems = refusals(files, args.changed_files, subjects, args.commits)
    for problem in problems:
        print(f"refused: {problem}", file=sys.stderr)
    if problems:
        return 1
    touched = any(PIN_FILE in file for file in files)
    print(f"{PIN_FILE}: {'changed alone' if touched else 'not changed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
