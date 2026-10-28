#!/usr/bin/env python
"""List the worktrees whose work would be lost if they were pruned (issue #621).

A subagent that fixes a PR in ``.claude/worktrees/agent-*`` and commits
without pushing leaves the PR BLOCKED and the fix invisible: PR #609 sat
three days on two such commits. This check reads every worktree of the
repository and names the ones holding work that exists nowhere else:

- ``dirty``: staged or modified tracked files (untracked files too, with
  ``--untracked``);
- ``ahead N``: commits on the branch that its upstream lacks;
- ``no upstream``: a branch never pushed, when its head is on no remote ref;
- ``detached``: a detached head whose commit is on no remote ref;
- ``prunable``: the directory is gone (git's own flag; nothing to lose).

Read-only. It never deletes, resets, checks out, fetches or pushes: a
stranded worktree is for the operator to push or discard, and ``git
worktree prune`` is a separate, deliberate step. Exit 1 when anything is
stranded, so a periodic run is loud; ``--all`` lists the clean ones too.

Usage::

    uv run python scripts/quality/check_stranded_worktrees.py [--repo PATH] [--all] [--untracked]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_GIT_TIMEOUT = 60
_GIT = shutil.which("git") or "git"


@dataclass(frozen=True)
class Worktree:
    path: Path
    head: str
    branch: str | None
    prunable: bool


@dataclass
class Finding:
    worktree: Worktree
    flags: list[str] = field(default_factory=list)

    @property
    def stranded(self) -> bool:
        return bool(self.flags)

    def row(self) -> str:
        name = self.worktree.branch or f"(detached {self.worktree.head[:10]})"
        state = ", ".join(self.flags) if self.flags else "clean"
        return f"{self.worktree.path}\t{name}\t{state}"


def _git(cwd: Path, *args: str) -> str:
    """``git -C cwd args``, stdout stripped; a failing command is an empty answer."""
    done = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [_GIT, "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=_GIT_TIMEOUT,
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def list_worktrees(repo: Path) -> list[Worktree]:
    """Every worktree git knows for ``repo``, the main one first, from ``--porcelain``."""
    found: list[Worktree] = []
    block: dict[str, str] = {}
    for line in [*_git(repo, "worktree", "list", "--porcelain").splitlines(), ""]:
        if line:
            key, _, value = line.partition(" ")
            block[key] = value
        elif block:
            found.append(_worktree(block))
            block = {}
    return found


def _worktree(block: dict[str, str]) -> Worktree:
    branch = block.get("branch")
    return Worktree(
        path=Path(block["worktree"]),
        head=block.get("HEAD", ""),
        branch=branch.removeprefix("refs/heads/") if branch else None,
        prunable="prunable" in block,
    )


def _on_a_remote(worktree: Worktree) -> bool:
    return bool(_git(worktree.path, "branch", "--remotes", "--contains", worktree.head))


def _ahead_behind(worktree: Worktree) -> tuple[int, int] | None:
    counts = _git(worktree.path, "rev-list", "--left-right", "--count", "@{u}...HEAD")
    if not counts:
        return None
    behind, ahead = (int(part) for part in counts.split())
    return ahead, behind


def _dirty(worktree: Worktree, untracked: bool) -> bool:
    mode = "--untracked-files=all" if untracked else "--untracked-files=no"
    return bool(_git(worktree.path, "status", "--porcelain", mode))


def examine(worktree: Worktree, *, untracked: bool = False) -> Finding:
    """What this worktree holds that no remote does."""
    finding = Finding(worktree)
    if worktree.prunable:
        finding.flags.append("prunable")
        return finding
    if _dirty(worktree, untracked):
        finding.flags.append("dirty")
    if worktree.branch is None:
        if not _on_a_remote(worktree):
            finding.flags.append("detached")
        return finding
    counts = _ahead_behind(worktree)
    if counts is None:
        if not _on_a_remote(worktree):
            finding.flags.append("no upstream")
    elif counts[0]:
        finding.flags.append(f"ahead {counts[0]}")
    return finding


def scan(repo: Path, *, untracked: bool = False) -> list[Finding]:
    """Every linked worktree examined, in git's order; the main worktree is left out."""
    main, *linked = list_worktrees(repo)
    return [examine(worktree, untracked=untracked) for worktree in linked if worktree != main]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Any path inside the repo.")
    parser.add_argument("--all", action="store_true", help="List clean worktrees too.")
    parser.add_argument("--untracked", action="store_true", help="Count untracked files as dirty.")
    args = parser.parse_args(argv)
    findings = scan(args.repo, untracked=args.untracked)
    stranded = [
        finding for finding in findings if finding.stranded and "prunable" not in finding.flags
    ]
    for finding in findings:
        if args.all or finding.stranded:
            print(finding.row())
    print(
        f"stranded-worktrees: {len(stranded)} stranded of {len(findings)} linked worktrees "
        f"({sum('prunable' in f.flags for f in findings)} prunable); nothing was changed",
        file=sys.stderr,
    )
    return 1 if stranded else 0


if __name__ == "__main__":
    sys.exit(main())
