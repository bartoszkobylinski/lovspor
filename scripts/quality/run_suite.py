#!/usr/bin/env python
"""The deep gate's unit step: the push's scope, in a machine-wide slot (issue #864).

Several agent sessions push at once on one 8-core Mac that also hosts the CI
runners; a full single-core suite per push drove the load to 12 and a 15-minute
gate past an hour. This step runs only the unit test files the push reaches
(``unit_scope.py``; the whole suite on a shared-infrastructure change or with
``VERIFY_DEEP_FULL=1``), and at most ``LOVSPOR_SUITE_SLOTS`` (default 2) of
these runs at a time across every worktree, each at background priority.

Not the network-marked tests: they answer for the operator's third-party
credential, not for the change being pushed (issue #359).

CI runs the whole suite on every PR and stays authoritative.

Usage::

    uv run python scripts/quality/run_suite.py
"""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TextIO

import unit_scope
from unit_scope import Scope

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SLOTS = 2
POLL_SECONDS = 5.0
NO_TESTS_COLLECTED = 5

__all__ = ["Scope", "main"]


def qos_prefix(which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """Background QoS on macOS, else a lower nice value, else nothing."""
    taskpolicy = which("taskpolicy")
    if taskpolicy:
        return [taskpolicy, "-b"]
    nice = which("nice")
    return [nice, "-n", "10"] if nice else []


def slot_dir() -> Path:
    return Path.home() / ".cache" / "lovspor" / "suite-slots"


def slot_count(env: Mapping[str, str]) -> int:
    try:
        return max(1, int(env.get("LOVSPOR_SUITE_SLOTS", DEFAULT_SLOTS)))
    except ValueError:
        return DEFAULT_SLOTS


def try_slot(directory: Path, count: int) -> TextIO | None:
    """The first free slot's open lock file, held; None when all are taken."""
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        handle = (directory / f"slot-{index}.lock").open("a", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            continue
        return handle
    return None


def acquire_slot(
    directory: Path,
    count: int,
    poll: float = POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> TextIO:
    """Wait for a slot; the lock is held until the handle closes or the process exits."""
    handle = try_slot(directory, count)
    if handle is None:
        print(f"waiting for a test slot ({count} busy)", flush=True)
    while handle is None:
        sleep(poll)
        handle = try_slot(directory, count)
    return handle


def pytest_argv(scope: Scope) -> list[str]:
    targets = ["tests/unit/"] if scope.full is not None else list(scope.tests)
    return [sys.executable, "-m", "pytest", "-q", "-m", "not network", *targets]


def scope_line(scope: Scope) -> str:
    if scope.full is not None:
        return f"unit scope: full ({scope.full})"
    if scope.tests:
        return f"unit scope: {len(scope.tests)} test files from {scope.changed} changed paths"
    if scope.python_changed:
        return "unit scope: nothing to run (no unit test reaches the change)"
    return "unit scope: nothing to run (no Python change)"


def run_pytest(argv: list[str]) -> int:
    argv = [*qos_prefix(), *argv]
    return subprocess.run(argv, cwd=ROOT, check=False).returncode  # noqa: S603 -- list argv, no shell


def main(env: Mapping[str, str]) -> int:
    scope = unit_scope.scope_for_push(ROOT, env)
    print(scope_line(scope), flush=True)
    if scope.full is None and not scope.tests:
        return 0
    with acquire_slot(slot_dir(), slot_count(env)):
        code = run_pytest(pytest_argv(scope))
    # A scope whose files hold only network-marked tests collects nothing; a
    # full run that collects nothing is broken.
    return 0 if code == NO_TESTS_COLLECTED and scope.full is None else code


if __name__ == "__main__":
    sys.exit(main(os.environ))
