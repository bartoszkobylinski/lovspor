#!/usr/bin/env python3
"""Say whether a pull request changes nothing outside tests/ (#986).

The independent test author writes tests for the code a pull request changes.
A pull request made only of tests holds no such code, and the round it would
start costs a session of the agent's weekly quota and a place in the one
runner's queue (owner decision 2026-11-21). `pr-pipeline.yml` asks this
program and skips the author when the answer is yes.

The answer is read from the diff and never from a label: a label would let any
pull request excuse itself from the round.

Usage:
    gh api --paginate repos/<repo>/pulls/<n>/files \
        --jq '.[] | {filename, previous_filename}' \
      | pr_diff_scope.py --changed-files <n the pull request reports>

Input: one JSON object per changed file. A renamed file counts under both of
its names, so a file moved out of `src/` is a change outside tests/.

Output: two lines in the form $GITHUB_OUTPUT takes: `tests_only=true|false`,
and `pin_only=true|false`, which says the pull request changes only the
owner-change pin file `ops/lovverk-owner-changes`. That file must be changed
alone and carry no agent commit (`check_owner_change_pr.py`), so the author,
whose round would add a commit, is not started for it either. A skipped
author is the answer that has to be earned, so every doubt is `false`: no
file listed, or fewer files listed than the pull request reports (the API
lists 3000 at most). Input that is not what the usage
shows ends the program with an error, and the workflow runs the author.
"""

from __future__ import annotations

import argparse
import json
import sys

TESTS = "tests/"
PIN_FILE = "ops/lovverk-owner-changes"


def names(lines: list[str]) -> list[list[str]]:
    """The names each changed file goes by: its own, and the one it was renamed from."""
    files = []
    for line in lines:
        if not line.strip():
            continue
        entry = json.loads(line)
        earlier = entry.get("previous_filename")
        files.append([entry["filename"], *([earlier] if earlier else [])])
    return files


def tests_only(files: list[list[str]], reported: int) -> bool:
    """Whether every name of every changed file is under tests/, with none missing."""
    if not files or len(files) != reported:
        return False
    return all(name.startswith(TESTS) for file in files for name in file)


def pin_only(files: list[list[str]], reported: int) -> bool:
    """Whether the pull request changes the pin file and nothing else, with none missing."""
    return reported == 1 and files == [[PIN_FILE]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--changed-files", type=int, required=True)
    args = parser.parse_args(argv)
    files = names(sys.stdin.read().splitlines())
    answer = tests_only(files, args.changed_files)
    print(f"tests_only={'true' if answer else 'false'}")
    print(f"pin_only={'true' if pin_only(files, args.changed_files) else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
