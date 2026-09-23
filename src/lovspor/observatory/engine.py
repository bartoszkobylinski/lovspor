"""Which engine is running, and whether it is pinned (issue #219).

The nightly job runs from a dedicated worktree that is meant to sit detached
on a merged commit. Within an hour of its installation that worktree was found
on a feature branch — a stray ``cd`` had redirected a later ``git checkout -b``
— and nothing would have said so: the run record carried counts and timings
but not the commit that produced them, and the job had no equivalent of the
lane worker's refusal to start from a checkout that is on a branch or dirty.

Two answers live here. ``commit`` goes into every :class:`SweepRun`, so a
record names its engine. ``pinned`` is what the scheduled entry point refuses
on: a branch can move under a running job, and local edits are code nobody
reviewed. A wheel install is not a checkout at all — no commit, no verdict.
"""

import os
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict

import lovspor


class EngineCheckout(BaseModel):
    """The engine's git state, or the absence of one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: HEAD of the checkout the engine was imported from; None outside git.
    commit: str | None
    #: True when detached and clean, False when on a branch or dirty, None
    #: when there is no checkout to judge.
    pinned: bool | None
    #: Why it is not pinned, worded for the run record and the operator.
    reason: str | None


def engine_root() -> Path:
    """The directory the engine package was imported from — a repo root under
    an editable install, a site-packages directory under a wheel."""
    return Path(lovspor.__file__).resolve().parents[2]


def describe_engine(root: Path | None = None) -> EngineCheckout:
    root = engine_root() if root is None else root
    commit = _git(root, "rev-parse", "HEAD")
    if commit is None:
        return EngineCheckout(commit=None, pinned=None, reason=None)
    if _git(root, "symbolic-ref", "-q", "HEAD") is not None:
        return EngineCheckout(
            commit=commit, pinned=False, reason="the engine checkout is on a branch"
        )
    if _git(root, "status", "--porcelain"):
        return EngineCheckout(
            commit=commit, pinned=False, reason="the engine checkout has local changes"
        )
    return EngineCheckout(commit=commit, pinned=True, reason=None)


def _git(root: Path, *args: str) -> str | None:
    """Stripped stdout of a git query, or None when git refuses it.

    None covers both "not a repository" and "not a symbolic ref"; the callers
    ask questions where refusal is an answer, never a failure to report.
    """
    # A git hook exports GIT_DIR to what it spawns; the question here is about
    # ``root``, so that inheritance is dropped (issue #369).
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        # S603/S607: git with list args, never a shell string.
        completed = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None
