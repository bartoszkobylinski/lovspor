#!/usr/bin/env python3
"""Stop a mutant's test process before it takes the runner's memory (#971, #978).

mutmut bounds a mutant's CPU and wall time, never its memory. A mutant that
makes a loop endless and appends on every pass grows for as long as the wall
limit allows: 17 minutes for the mutants of regjeringen_fetch/normalize.py, on
a 16 GiB runner that was shut down at 14.5 GiB with no report written.

This program runs beside `mutmut run`. Every few seconds it reads /proc for
the processes mutmut has forked to test a mutant and compares the memory each
one holds, resident plus swapped, with the process that forked it. One that
holds more than the allowance above its parent is sent SIGXCPU, the signal
mutmut's own timeout thread sends, so mutmut records it as a timeout: the
verdict such a mutant gets anyway, minutes earlier and with the runner still up.

A mutant process is recognised in either of two ways:

* by the title mutmut gives its fork, ``mutmut: <mutant name>``, where that
  title is in /proc. Then the log can name the mutant.
* as a fork of `mutmut run`: a process that still carries its parent's command
  line, and that command line is ``python .../mutmut run ...``. This needs
  nothing from mutmut. On the hosted runner the title never reached /proc
  (#978: three hours of scans, no titled process, memory at 15.4 of 16 GiB),
  so this is the rule that holds there. The log then gives the pid; mutmut's
  own records name the mutant that timed out.

mutmut itself is out of reach of both: it has no title, and its parent
(`timeout`, or the wrapper) has another command line. Where /proc does not
exist (macOS) there is nothing to read; the program says so and exits.

Usage:
    mutant_memory_guard.py --allowance-mb 2048 [--interval 2] [--proc /proc]

It prints one line when it starts, one when it first sees a mutant process,
one every 15 minutes with what its scans have seen, and one for each process
it stops. A night whose 15-minute lines report no mutant process ran unguarded.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import NamedTuple

TITLE_PREFIX = "mutmut: "
MUTANT_NAME = re.compile(r"\S+__mutmut_\d+")
# The name of a mutant process whose title is not in /proc.
UNTITLED = "untitled mutant"
# A title sits at the start of the argument area; the rest of that area can be
# every pattern `mutmut run` was given. Two command lines are compared over the
# same first bytes.
TITLE_BYTES = 4096
# SIGXCPU's default action ends the process. One still there this long after
# has blocked or handled it, and only SIGKILL is left; mutmut files that as a
# segfault, which is no verdict.
KILL_AFTER_SECONDS = 30.0
# The ledger checkpoints of the same step run at this pace.
REPORT_SECONDS = 900.0


class Mutant(NamedTuple):
    """A process mutmut forked to test one mutant."""

    pid: int
    name: str
    over_parent_kb: int


def mutant_name(cmdline: bytes) -> str | None:
    """The mutant a process is testing, read from the title mutmut gave it."""
    title = cmdline.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
    if not title.startswith(TITLE_PREFIX):
        return None
    name = title[len(TITLE_PREFIX) :].strip()
    return name if MUTANT_NAME.fullmatch(name) else None


def runs_mutmut(cmdline: bytes) -> bool:
    """Whether a command line is ``python .../mutmut run ...``, as the wrapper starts it."""
    program, script, command = [*cmdline.split(b"\0"), b"", b"", b""][:3]
    return (
        program.rpartition(b"/")[2].startswith(b"python")
        and script.rpartition(b"/")[2] == b"mutmut"
        and command == b"run"
    )


def status_field(status: str, field: str) -> int:
    """The number a /proc/<pid>/status text gives for ``field``; 0 when it has none."""
    for line in status.splitlines():
        name, _, value = line.partition(":")
        if name == field:
            number = value.split()[:1]
            return int(number[0]) if number and number[0].isdigit() else 0
    return 0


def memory_kb(status: str) -> int:
    """Resident plus swapped memory in kB: swapped pages still belong to the process."""
    return status_field(status, "VmRSS") + status_field(status, "VmSwap")


def _read(path: Path) -> bytes | None:
    """The file's first bytes, or None when the process ended before the read."""
    try:
        with path.open("rb") as handle:
            return handle.read(TITLE_BYTES)
    except OSError:
        return None


def _mutant_at(proc: Path, pid: int) -> Mutant | None:
    cmdline = _read(proc / str(pid) / "cmdline") or b""
    name = mutant_name(cmdline)
    if name is None and not runs_mutmut(cmdline):
        return None
    own = _read(proc / str(pid) / "status")
    if own is None:
        return None
    status = own.decode("utf-8", errors="replace")
    parent_dir = proc / str(status_field(status, "PPid"))
    parent = _read(parent_dir / "status")
    if parent is None:
        return None
    if name is None:
        # No title. A fork of mutmut still has mutmut's command line; mutmut
        # itself was started by a process with another one.
        if _read(parent_dir / "cmdline") != cmdline:
            return None
        name = UNTITLED
    over = memory_kb(status) - memory_kb(parent.decode("utf-8", errors="replace"))
    return Mutant(pid, name, over)


def mutant_processes(proc: Path) -> list[Mutant]:
    """Every mutant process, with the memory it holds above its parent."""
    pids = sorted(int(entry.name) for entry in proc.iterdir() if entry.name.isdigit())
    found = (_mutant_at(proc, pid) for pid in pids)
    return [mutant for mutant in found if mutant is not None]


class Tally:
    """What the scans have seen, for the line the step log gets every 15 minutes."""

    def __init__(self) -> None:
        self.titled: set[int] = set()
        self.untitled: set[int] = set()
        self.over: set[int] = set()
        self.peak_kb = 0

    def note(self, mutants: list[Mutant], allowance_kb: int) -> None:
        for mutant in mutants:
            (self.untitled if mutant.name == UNTITLED else self.titled).add(mutant.pid)
            self.peak_kb = max(self.peak_kb, mutant.over_parent_kb)
            if mutant.over_parent_kb > allowance_kb:
                self.over.add(mutant.pid)

    def line(self) -> str:
        if not self.titled and not self.untitled:
            return "no mutant process seen in the scans so far"
        return (
            f"scans so far saw {len(self.titled)} titled and {len(self.untitled)} untitled "
            f"mutant process(es), {len(self.over)} over the allowance; the largest held "
            f"{self.peak_kb // 1024} MiB more than its parent"
        )


def _say(message: str) -> None:
    # The step log is shared with mutmut's spinner, which ends on a bare \r.
    sys.stdout.write(f"\nmemory guard: {message}\n")
    sys.stdout.flush()


def _first_sight(mutant: Mutant) -> str:
    if mutant.name == UNTITLED:
        return (
            f"mutant processes are visible, first pid {mutant.pid}, a fork of `mutmut run` "
            "with no title of its own (#978)"
        )
    return f"mutant processes are visible, first {mutant.name}"


def _signal(pid: int, signum: signal.Signals) -> bool:
    try:
        os.kill(pid, signum)
    except ProcessLookupError:
        return False  # it ended on its own between the scan and the signal
    except PermissionError:
        _say(f"pid {pid} belongs to another user and could not be signalled")
        return False
    return True


def stop(mutant: Mutant, sent: dict[int, float], allowance_kb: int) -> None:
    """Send SIGXCPU once, and SIGKILL if the process outlives it."""
    now = time.monotonic()
    first = sent.get(mutant.pid)
    if first is None:
        sent[mutant.pid] = now
        if _signal(mutant.pid, signal.SIGXCPU):
            _say(
                f"{mutant.name} (pid {mutant.pid}) held {mutant.over_parent_kb // 1024} MiB "
                f"more than its parent, over the {allowance_kb // 1024} MiB allowance; "
                "sent SIGXCPU, which mutmut records as a timeout"
            )
    elif now - first >= KILL_AFTER_SECONDS:
        sent[mutant.pid] = float("inf")
        if _signal(mutant.pid, signal.SIGKILL):
            _say(
                f"{mutant.name} (pid {mutant.pid}) outlived SIGXCPU by "
                f"{KILL_AFTER_SECONDS:.0f} s; sent SIGKILL, which mutmut records as a "
                "segfault (no verdict)"
            )


def watch(proc: Path, allowance_kb: int, interval: float) -> None:
    """Scan until the process that started this one is gone."""
    started_by = os.getppid()
    sent: dict[int, float] = {}
    tally = Tally()
    seen = False
    report_at = time.monotonic() + REPORT_SECONDS
    while os.getppid() == started_by:
        mutants = mutant_processes(proc)
        tally.note(mutants, allowance_kb)
        if mutants and not seen:
            seen = True
            _say(_first_sight(mutants[0]))
        over = [mutant for mutant in mutants if mutant.over_parent_kb > allowance_kb]
        alive = {mutant.pid for mutant in over}
        sent = {pid: at for pid, at in sent.items() if pid in alive}
        for mutant in over:
            stop(mutant, sent, allowance_kb)
        if time.monotonic() >= report_at:
            report_at += REPORT_SECONDS
            _say(tally.line())
        time.sleep(interval)


def _megabytes(text: str) -> int:
    if not text.isdigit() or int(text) == 0:
        raise argparse.ArgumentTypeError(f"must be a positive whole number, got {text!r}")
    return int(text)


def _seconds(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = 0.0
    if not 0 < value < float("inf"):
        raise argparse.ArgumentTypeError(f"must be a positive number of seconds, got {text!r}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--allowance-mb", type=_megabytes, required=True)
    parser.add_argument("--interval", type=_seconds, default=2.0)
    parser.add_argument("--proc", type=Path, default=Path("/proc"))
    args = parser.parse_args(argv)
    if not args.proc.is_dir():
        _say(f"no {args.proc} here, so mutant processes cannot be read; nothing is guarded")
        return 0
    _say(
        f"a mutant process may hold {args.allowance_mb} MiB more than the process that "
        f"forked it; checked every {args.interval:g} s (#971)"
    )
    watch(args.proc, args.allowance_mb * 1024, args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
