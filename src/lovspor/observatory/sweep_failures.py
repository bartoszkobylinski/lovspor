"""How a sweep that did not sweep leaves its trace (issues #167, #169, #534).

A sweep ends unswept three ways: preflight refuses the ground, the host is
reserved by a benchmark, or the archive goes away under the run. Each is a
``failed`` run carrying its reason, written where it can be written honestly.

That last clause is the one #534 is about. A crashed night that leaves no
record reads exactly like a night that never started, so the record is written
whenever the archive is still there. When the root itself is gone it is not
written at all — never to a directory recreated for the purpose, and never to a
fallback location, because either would later read as the archive's history.
The reason then reaches the operator on stderr and the dead-man switch in the
heartbeat body, which is everything an absent volume allows.
"""

from datetime import UTC, datetime
from typing import NoReturn

import typer

from lovspor.errors import StorageUnavailableError
from lovspor.exclusive_workload import ExclusiveWorkloadHeldError
from lovspor.observatory.heartbeat import report_run
from lovspor.observatory.storage import ObservatoryRoot
from lovspor.observatory.sweeps import SweepRun, append_sweep_run

#: Failure reasons. Strings rather than an enum because they are written into
#: the run record and read by a human at 03:00, not branched on.
STORAGE_UNAVAILABLE = "storage_unavailable"
#: The root is there, but a write under it failed mid-run (issue #534).
STORAGE_WRITE_FAILED = "storage_write_failed"
#: Not a preflight verdict: the ground was fine, the host was reserved. The
#: sweep did not start and says so (issue #169).
EXCLUSIVE_WORKLOAD = "deferred_exclusive_workload"


def failed_run(started_at: datetime, reason: str, engine_commit: str | None) -> SweepRun:
    """A run that could not sweep anything, as a record.

    Built before it is stored, because the case that most needs reporting —
    the archive is not mounted — is exactly the case with nowhere to store it.
    The dead-man switch does not need the archive to speak.
    """
    return SweepRun(
        run_id=started_at.isoformat(),
        started_at=started_at,
        finished_at=datetime.now(UTC),
        active_sources=0,
        sources_completed=0,
        sources_refused=0,
        captured=0,
        failed_fetches=0,
        unchanged=0,
        status="failed",
        failure_reason=reason,
        engine_commit=engine_commit,
    )


def storage_reason(root: ObservatoryRoot) -> str:
    """Which storage failure ended the run: the root gone, or a write under it."""
    return STORAGE_UNAVAILABLE if not root.path.is_dir() else STORAGE_WRITE_FAILED


def record_failed_run(root: ObservatoryRoot, run: SweepRun) -> None:
    """Append ``run`` best-effort: a record that cannot land is said, not raised.

    The run is already failing; a second storage error while recording it must
    not replace the first with a traceback. ``append_sweep_run`` never creates
    the root, so a vanished archive gets no record rather than a false one.
    """
    try:
        append_sweep_run(root, run)
    except StorageUnavailableError as exc:
        typer.echo(f"run record not written: {exc}", err=True)


def refuse_sweep(root: ObservatoryRoot, failed: SweepRun) -> NoReturn:
    """End a sweep preflight refused: say why, record it where possible, report it."""
    typer.echo(f"OBSERVATORY SWEEP FAILED\nreason: {failed.failure_reason}", err=True)
    typer.echo(f"expected: {root.path}", err=True)
    record_failed_run(root, failed)
    report_run(failed)
    raise typer.Exit(1)


def end_unswept(
    root: ObservatoryRoot,
    started_at: datetime,
    exc: ExclusiveWorkloadHeldError | StorageUnavailableError,
    engine_commit: str | None,
) -> NoReturn:
    """End a sweep that was deferred or lost its archive, leaving its trace."""
    if isinstance(exc, ExclusiveWorkloadHeldError):
        reason = EXCLUSIVE_WORKLOAD
        typer.echo(f"OBSERVATORY SWEEP DEFERRED\nreason: {reason}\n{exc}", err=True)
    else:
        reason = storage_reason(root)
        typer.echo(f"OBSERVATORY SWEEP FAILED\nreason: {reason}\n{exc}", err=True)
    failed = failed_run(started_at, reason, engine_commit)
    record_failed_run(root, failed)
    report_run(failed)
    raise typer.Exit(1) from exc
