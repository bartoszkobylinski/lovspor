"""Catching up a nightly sweep the machine was powered off for (issue #356).

launchd fires a ``StartCalendarInterval`` trigger that fell inside a sleep as
soon as the machine wakes. A trigger that fell while the machine was powered
off is lost: nothing replays it at boot. So the job also runs at load, and a
load is not a schedule — it happens at every login, and after a reboot an hour
past a finished sweep it would start the next one against two hundred
municipal servers for no reason.

The guard is the owner's rule (2026-09-26): an invocation at load sweeps only
when no sweep has started in the last 24 hours. "Started" is read from two
traces, because a sweep killed by the shutdown never writes its run record:

* ``sweep-runs.jsonl`` — every sweep that reached its end. A ``failed`` run
  (deferred, refused at preflight) observed nothing, so it is not a sweep.
* the host's exclusive workload lock — its advisory record names when the
  sweep holding it began, and only a clean exit empties it.

The scheduled trigger itself is never guarded. launchd does not say which
trigger started the job, so a start inside the minutes after 03:00 Oslo time
is read as the calendar. Guarding it would turn a boot catch-up at 01:00 into
a 03:00 that silently did not happen — a dropped trigger nothing reports
(#218) — and in practice the catch-up is still running at 03:00, which launchd
already accounts for by not starting a second instance.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

import typer

from lovspor.errors import LogIntegrityError
from lovspor.observatory.storage import ObservatoryRoot
from lovspor.observatory.sweeps import OBSERVATION_SLA, SweepRun, read_sweep_runs, sweeps_path
from lovspor.observatory.triggers import SCHEDULE_TIME, SCHEDULE_ZONE, recorded_sweep_start

#: A load within this long of a sweep's start does not sweep. The owner's 24 h
#: is the observation SLA: a boot catches up exactly when the SLA would lapse.
CATCH_UP_GUARD = OBSERVATION_SLA

#: How long after 03:00 a start still reads as the calendar trigger. launchd
#: starts the job within seconds; a login inside these minutes after a power
#: cut is indistinguishable from it, and sweeping then is what it should do.
SCHEDULED_SLACK = timedelta(minutes=15)


def is_scheduled_trigger(now: datetime) -> bool:
    """Whether ``now`` is the calendar trigger rather than a load or a wake."""
    local = now.astimezone(SCHEDULE_ZONE)
    trigger = datetime.combine(local.date(), SCHEDULE_TIME, tzinfo=SCHEDULE_ZONE)
    return trigger <= local < trigger + SCHEDULED_SLACK


def catch_up_blocker(
    runs: Sequence[SweepRun], lock_start: datetime | None, now: datetime
) -> datetime | None:
    """The sweep start that makes this load skip, or None to sweep.

    A start stamped in the future is no evidence: a clock that jumped back
    must not hold catch-up shut for as long as the stamp stays ahead.
    """
    if is_scheduled_trigger(now):
        return None
    starts = [run.started_at for run in runs if run.status != "failed"]
    if lock_start is not None:
        starts.append(lock_start)
    recent = [start for start in starts if timedelta(0) <= now - start < CATCH_UP_GUARD]
    return max(recent, default=None)


def skip_catch_up(root: ObservatoryRoot, owner: str, now: datetime) -> bool:
    """Whether this load should not sweep, saying why when it should not.

    An unreadable run log proves no recent sweep, so the load sweeps; the
    damage is named here and refused loudly by `observatory status`.
    """
    try:
        runs = read_sweep_runs(sweeps_path(root))
    except LogIntegrityError as exc:
        typer.echo(f"catch-up: cannot read sweep runs, sweeping anyway: {exc}", err=True)
        runs = []
    blocker = catch_up_blocker(runs, recorded_sweep_start(owner), now)
    if blocker is None:
        return False
    started = blocker.isoformat(timespec="seconds")
    typer.echo(f"catch-up skipped: a sweep started at {started}, within the last 24h")
    return True
