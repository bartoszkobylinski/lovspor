"""scripts/ci/fd_lock.py: the portable stand-in for `flock -w SECONDS FD` (#445).

Every test runs the real helper in a real process against a real lock file:
the property that matters is flock(2) semantics across processes, which an
in-process call cannot show.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

_HELPER = Path(__file__).parents[2] / "scripts" / "ci" / "fd_lock.py"


def _bash(script: str, lock: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script, "bash", sys.executable, str(_HELPER), str(lock)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_a_free_lock_is_taken_at_once(tmp_path: Path) -> None:
    result = _bash('exec 9>"$3"; "$1" "$2" --wait 0 9', tmp_path / "box.lock")

    assert result.returncode == 0, result.stderr


def test_the_lock_outlives_the_helper_while_the_shell_holds_the_fd(tmp_path: Path) -> None:
    """The agent step takes the lock through a child process and then runs the
    agent: the lock has to stay with the step's fd 9, not die with the helper.
    A second open of the same file is a second holder, so it must wait out."""
    result = _bash(
        'exec 9>"$3"; "$1" "$2" --wait 0 9 || exit 10; "$1" "$2" --wait 1 8 8>"$3"',
        tmp_path / "box.lock",
    )

    assert result.returncode == 1


def test_the_lock_is_released_when_the_holding_shell_exits(tmp_path: Path) -> None:
    lock = tmp_path / "box.lock"
    held = _bash('exec 9>"$3"; "$1" "$2" --wait 0 9', lock)

    after = _bash('exec 9>"$3"; "$1" "$2" --wait 0 9', lock)

    assert held.returncode == 0
    assert after.returncode == 0, "a finished step must not leave the host locked"


def test_the_wait_is_bounded_and_then_reports_expiry(tmp_path: Path) -> None:
    lock = tmp_path / "box.lock"
    ready = tmp_path / "held"
    holder = subprocess.Popen(
        [
            "bash",
            "-c",
            'exec 9>"$3"; "$1" "$2" --wait 10 9 && touch "$4" && sleep 20',
            "bash",
            sys.executable,
            str(_HELPER),
            str(lock),
            str(ready),
        ],
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            assert time.monotonic() < deadline, "the holder never took the lock"
            time.sleep(0.05)
        started = time.monotonic()
        waited = _bash('exec 9>"$3"; "$1" "$2" --wait 2 9', lock)
        elapsed = time.monotonic() - started
    finally:
        holder.kill()
        holder.wait()

    assert waited.returncode == 1
    assert 2 <= elapsed < 10


def test_a_lock_freed_during_the_wait_is_taken(tmp_path: Path) -> None:
    lock = tmp_path / "box.lock"
    started = time.monotonic()
    result = _bash(
        '("$1" "$2" --wait 0 9 && sleep 1.5) 9>"$3" & sleep 0.5; '
        'exec 8>"$3"; "$1" "$2" --wait 10 8',
        lock,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert elapsed >= 1.4, "the lock was never held, so nothing was waited for"


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--wait", "0", "97"], "cannot lock fd 97"),
        (["--wait", "-1", "0"], "--wait must not be negative"),
    ],
)
def test_usage_errors_exit_2_with_a_reason(arguments: list[str], message: str) -> None:
    result = subprocess.run(
        [sys.executable, str(_HELPER), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert message in result.stderr


def test_a_missing_wait_is_a_usage_error() -> None:
    result = subprocess.run(
        [sys.executable, str(_HELPER), "9"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
