#!/usr/bin/env python3
"""Take an exclusive lock on an inherited file descriptor, waiting a bounded time.

A drop-in for util-linux `flock -w SECONDS FD`, which stock macOS does not ship
(#445: the Codex lanes moved from the Linux box to a Mac mini). The agent step
opens the host lock file on fd 9 and hands the number here:

    exec 9>"$HOME/.agent-box.lock"
    python3 scripts/ci/fd_lock.py --wait 3600 9 || { echo "..." >&2; exit 1; }

flock(2) locks belong to the open file description, not to the process that
took them, so the lock outlives this helper: the step's shell still holds fd 9,
and the lock is released only when that shell exits, cancellation included.
That is exactly how `flock -w 3600 9` behaved, and why the lock and the agent
round share one step (#382).

Exit status mirrors util-linux flock: 0 locked, 1 the wait ran out, 2 usage or
an fd that cannot be locked.
"""

from __future__ import annotations

import argparse
import fcntl
import sys
import time
from collections.abc import Sequence

LOCKED = 0
WAIT_EXPIRED = 1
USAGE = 2
POLL_SECONDS = 1.0


def acquire(fd: int, wait_seconds: float, poll_seconds: float = POLL_SECONDS) -> bool:
    """Lock `fd` exclusively, retrying until `wait_seconds` have passed."""
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(poll_seconds, remaining))
        else:
            return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Lock an inherited fd exclusively, with a bounded wait."
    )
    parser.add_argument("--wait", type=float, required=True, help="seconds to wait")
    parser.add_argument("fd", type=int, help="an open file descriptor to lock")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.wait < 0:
        print("fd_lock: --wait must not be negative", file=sys.stderr)
        return USAGE
    try:
        locked = acquire(args.fd, args.wait)
    except OSError as error:
        print(f"fd_lock: cannot lock fd {args.fd}: {error}", file=sys.stderr)
        return USAGE
    return LOCKED if locked else WAIT_EXPIRED


if __name__ == "__main__":
    raise SystemExit(main())
