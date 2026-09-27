"""Scheduled triggers the nightly job lost to its own previous run (issue #218).

launchd will not start a second instance of a label that is still running, so
a sweep that outlives a day swallows the next 03:00 trigger without a trace:
the sweep log shows one run, and nothing says a scheduled observation did not
happen. The trace has to be reconstructed, and it can be — every recorded run
carries its start and finish, and the schedule is known — so a trigger that
fell strictly inside a run is a trigger that was dropped.

A sweep still in progress has no record yet; it appears in
``sweep-runs.jsonl`` only when it finishes. The host's exclusive workload lock
(issue #169) is what names it meanwhile: the sweep holds it for its whole
length and writes its start into the advisory record. That is this host's
view — a status read against the same archive from another machine sees no
running sweep.
"""

import os
from collections.abc import Sequence
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict

from lovspor.exclusive_workload import default_lock_path, read_holder
from lovspor.observatory.sweeps import SweepRun

#: When the nightly job fires. launchd reads ``StartCalendarInterval`` in the
#: host's local time, and the host is in Oslo. This mirrors the launchd plist
#: rather than configuring it; a unit test reads the plist and fails on drift.
SCHEDULE_ZONE = ZoneInfo("Europe/Oslo")
SCHEDULE_TIME = time(3, 0)

#: How far back `observatory status` looks for dropped triggers. Two weeks
#: covers the longest overrun seen so far (six days, #218) with room to spare,
#: and keeps the list short enough to read.
DROPPED_TRIGGER_LOOKBACK = timedelta(days=14)


class DroppedTrigger(BaseModel):
    """One scheduled start that did not happen, and the sweep that held the job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scheduled_at: AwareDatetime
    held_by: AwareDatetime
    still_running: bool


class _Span(NamedTuple):
    started_at: datetime
    finished_at: datetime | None


def scheduled_triggers(since: datetime, until: datetime) -> list[datetime]:
    """Every scheduled instant in ``(since, until]``, stated in Oslo time."""
    day = since.astimezone(SCHEDULE_ZONE).date()
    last = until.astimezone(SCHEDULE_ZONE).date()
    instants = []
    while day <= last:
        instant = datetime.combine(day, SCHEDULE_TIME, tzinfo=SCHEDULE_ZONE)
        if since < instant <= until:
            instants.append(instant)
        day += timedelta(days=1)
    return instants


def dropped_triggers(
    runs: Sequence[SweepRun], running_since: datetime | None, *, now: datetime
) -> list[DroppedTrigger]:
    """Triggers inside the lookback that fell strictly within a sweep.

    Strictly, because the trigger that started a run fires a moment before the
    run stamps its own start: that one was served, not dropped.
    """
    spans = [_Span(run.started_at, run.finished_at) for run in runs]
    if running_since is not None:
        spans.append(_Span(running_since, None))
    dropped = []
    for instant in scheduled_triggers(now - DROPPED_TRIGGER_LOOKBACK, now):
        for span in spans:
            if _holds(span, instant):
                dropped.append(_dropped(instant, span))
                break
    return dropped


def _holds(span: _Span, instant: datetime) -> bool:
    """A running sweep holds every trigger since it began; a finished one, until it ended."""
    if span.finished_at is None:
        return span.started_at < instant
    return span.started_at < instant < span.finished_at


def _dropped(instant: datetime, span: _Span) -> DroppedTrigger:
    return DroppedTrigger(
        scheduled_at=instant, held_by=span.started_at, still_running=span.finished_at is None
    )


def running_sweep(owner: str, path: Path | None = None) -> datetime | None:
    """When the sweep holding this host's workload lock began, if one does.

    The advisory record survives a killed holder — only a clean exit empties
    it — so the named process must still exist before the record is believed.
    """
    holder = read_holder(default_lock_path() if path is None else path)
    if holder is None or holder.owner != owner or not _alive(holder.pid):
        return None
    try:
        since = datetime.fromisoformat(holder.since)
    except ValueError:
        return None
    return since if since.tzinfo is not None else None


def _alive(pid: int) -> bool:
    """Whether a process with this pid exists, without signalling it.

    0 and negative pids address process groups, so they are refused outright.
    A process owned by another user refuses the probe, which proves it exists.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (ProcessLookupError, OverflowError):
        return False
    return True
