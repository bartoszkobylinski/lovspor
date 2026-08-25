"""Telemetry for a whole sweep — the run as an observable object (issue #167).

The observation log answers what the servers did. It cannot answer whether the
Observatory ran last night, because a sweep that never started leaves no trace
in it by construction — and that is the failure worth catching. A Mac powered
off for three days looks, from the observation log alone, exactly like three
quiet days at two hundred municipalities.

So a sweep records itself here instead: process telemetry, deliberately not an
observation, in its own append-only file beside the registry. Three states,
because "it failed" hides a distinction that matters operationally — a sweep
that ran and lost one municipality needs a different response from a sweep that
could not start because the archive disk was not mounted.

The cadence target lives here too, as a value rather than a comment. How often
a source must be observed is a property of the data; which hour the job fires
is deployment configuration, and belongs in the launchd plist, not in this
module.
"""

import fcntl
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from lovspor.errors import LogIntegrityError
from lovspor.observatory.storage import ObservatoryRoot

SWEEPS_FILENAME = "sweep-runs.jsonl"

#: Every active source is observed at least this often. A first SLA for local
#: legal material, chosen to be measured against rather than defended: the
#: steady state of the register has never been timed, and the number should be
#: argued down only once sweep durations and delta counts exist.
OBSERVATION_SLA = timedelta(hours=24)

#: How long without a completed sweep before the dead-man switch alerts. Longer
#: than the target on purpose — sleep/wake and one slow run must not page
#: anyone — but under two targets, so two whole days cannot pass unnoticed.
SWEEP_DEADLINE = timedelta(hours=36)

SweepStatus = Literal["success", "degraded", "failed"]


class SweepRun(BaseModel):
    """What one pass over the register did.

    Unknown fields are forbidden for the same reason the observation records
    forbid them: this file is read back to answer whether the archive is
    healthy, and quietly dropping a field a newer writer added would answer it
    from a partial record.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    # Timezone-aware on purpose. Cadence compares these against an aware UTC
    # clock, so a naive stamp does not fail here — it fails later, inside
    # `observatory status`, as a TypeError from subtracting the two. Refusing
    # it at the boundary keeps the damage where the line number still exists.
    started_at: AwareDatetime
    finished_at: AwareDatetime
    active_sources: int = Field(ge=0)
    sources_completed: int = Field(ge=0)
    sources_refused: int = Field(ge=0)
    captured: int = Field(ge=0)
    failed_fetches: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    status: SweepStatus

    @model_validator(mode="after")
    def _finished_after_started(self) -> "SweepRun":
        """A run cannot end before it began.

        Damage, not a curiosity: the duration is rendered to an operator and
        subtracted elsewhere, so an inverted pair would print a negative
        elapsed time as though it meant something. Refused at the boundary,
        where the line number is still in reach.
        """
        if self.finished_at < self.started_at:
            raise ValueError("finished_at precedes started_at")
        return self


class CadenceState(NamedTuple):
    """How long since a sweep last began, and whether that is now an alert.

    ``age`` is None when nothing was ever swept, which is not the same as zero
    and must never be rendered as a healthy duration.
    """

    age: timedelta | None
    overdue: bool


def sweep_status(*, active: int, refused: int) -> SweepStatus:
    """Classify a sweep that ran to completion.

    No active sources is not a success: a register with nothing to sweep means
    the sweep observed nothing, and reporting that green is how issue #151's
    silent zero comes back at fleet scale.
    """
    if active == 0:
        return "failed"
    return "degraded" if refused else "success"


def sweeps_path(root: ObservatoryRoot) -> Path:
    """Where sweep telemetry lives inside a validated observatory root."""
    return root.path / SWEEPS_FILENAME


def append_sweep_run(root: ObservatoryRoot, run: SweepRun) -> None:
    """Append one run, flushed and fsynced before returning.

    Locked and fsynced like the observation log: the nightly job and an
    operator running a sweep by hand are two writers, and a torn line here
    costs the answer to when the archive was last healthy.
    """
    path = sweeps_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(run.model_dump_json() + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_sweep_runs(path: Path) -> list[SweepRun]:
    """Every recorded run, oldest first, or a refusal naming the bad line.

    Damage is never skipped. Reading past a line this module does not
    understand would answer "when did we last sweep successfully" with some
    older run, and that answer is indistinguishable from a true one.
    """
    if not path.exists():
        return []
    runs: list[SweepRun] = []
    for number, line in enumerate(path.read_bytes().split(b"\n"), start=1):
        if not line.strip():
            continue
        runs.append(_parse_run(path, number, line))
    return runs


def _parse_run(path: Path, number: int, line: bytes) -> SweepRun:
    """Split and validated as bytes, never decoded first.

    Decoding the file in one call would let a single bad byte take the whole
    read down with a `UnicodeDecodeError` before any line number could be
    named — the caller then learns the archive is damaged but not where. The
    observation log splits on bytes for the same reason.
    """
    try:
        return SweepRun.model_validate(json.loads(line))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise LogIntegrityError(f"{path}:{number}: unreadable sweep run: {exc}") from exc


def latest_sweep_run(path: Path) -> SweepRun | None:
    """The most recently started run, or None when nothing was ever swept."""
    runs = read_sweep_runs(path)
    if not runs:
        return None
    return max(runs, key=lambda run: run.started_at)


def cadence_state(run: SweepRun | None, *, now: datetime | None = None) -> CadenceState:
    """How stale the archive's observations are.

    Measured from when the last sweep *started*, not when it finished. A sweep
    that began 35 hours ago and took two hours has still not begun a new
    observation in 35 hours; measuring from the finish would hide precisely the
    slow run the deadline exists to catch.
    """
    moment = now if now is not None else datetime.now(UTC)
    if run is None:
        return CadenceState(age=None, overdue=True)
    age = moment - run.started_at
    if age < timedelta(0):
        # A sweep stamped in the future has not observed anything yet, so it
        # cannot be evidence of freshness. Returning its negative age as an
        # ordinary one would report OK — and a clock that jumped backwards, or
        # a forged record, would then hold the dead-man switch shut for as long
        # as the stamp stayed ahead. That is the exact failure this file exists
        # to catch, so it reads as overdue with no usable age.
        return CadenceState(age=None, overdue=True)
    return CadenceState(age=age, overdue=age >= SWEEP_DEADLINE)
