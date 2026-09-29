"""Catching up a 03:00 sweep the machine was powered off for (issue #356)."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lovspor.observatory.catch_up import CATCH_UP_GUARD, catch_up_blocker, is_scheduled_trigger
from lovspor.observatory.sweeps import OBSERVATION_SLA, SweepRun, SweepStatus
from lovspor.observatory.triggers import SCHEDULE_ZONE, recorded_sweep_start

OWNER = "observatory-sweep"


def _oslo(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=SCHEDULE_ZONE)


def _run(started: datetime, status: SweepStatus = "success") -> SweepRun:
    return SweepRun(
        run_id=started.isoformat(),
        started_at=started,
        finished_at=started + timedelta(hours=2),
        active_sources=0 if status == "failed" else 1,
        sources_completed=0 if status == "failed" else 1,
        sources_refused=0,
        captured=0,
        failed_fetches=0,
        unchanged=0,
        status=status,
        failure_reason="deferred_exclusive_workload" if status == "failed" else None,
    )


def _holder(path: Path, *, owner: str = OWNER, pid: int = 1, since: str) -> Path:
    path.write_text(json.dumps({"owner": owner, "pid": pid, "since": since}) + "\n")
    return path


def _dead_pid() -> int:
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    return finished.pid


class TestTheGuardWindow:
    def test_it_is_the_observation_sla(self) -> None:
        """The owner's 24 h: a boot catches up exactly when the SLA would lapse."""
        assert CATCH_UP_GUARD == OBSERVATION_SLA == timedelta(hours=24)


class TestScheduledTrigger:
    def test_the_calendar_trigger_is_recognised_in_oslo_time(self) -> None:
        assert is_scheduled_trigger(_oslo(28, 3, 0)) is True
        assert is_scheduled_trigger(_oslo(28, 3, 14)) is True

    def test_utc_three_oclock_is_not_the_trigger(self) -> None:
        assert is_scheduled_trigger(datetime(2026, 9, 28, 3, 0, tzinfo=UTC)) is False

    def test_the_trigger_window_is_fifteen_minutes(self) -> None:
        assert is_scheduled_trigger(_oslo(28, 3, 15)) is False
        assert is_scheduled_trigger(_oslo(28, 2, 59)) is False


class TestCatchUpBlocker:
    def test_nothing_ever_swept_runs(self) -> None:
        assert catch_up_blocker([], None, _oslo(28, 10)) is None

    def test_a_sweep_started_within_24_hours_blocks_a_boot(self) -> None:
        started = _oslo(28, 3)

        assert catch_up_blocker([_run(started)], None, _oslo(28, 10)) == started

    def test_the_latest_start_is_the_one_that_blocks(self) -> None:
        runs = [_run(_oslo(28, 3)), _run(_oslo(26, 3))]

        assert catch_up_blocker(runs, None, _oslo(28, 10)) == _oslo(28, 3)

    def test_a_sweep_started_more_than_24_hours_ago_does_not(self) -> None:
        assert catch_up_blocker([_run(_oslo(27, 3))], None, _oslo(28, 3, 30)) is None

    def test_exactly_24_hours_ago_is_no_longer_within_them(self) -> None:
        assert catch_up_blocker([_run(_oslo(27, 10))], None, _oslo(28, 10)) is None

    def test_a_run_that_never_swept_is_not_a_sweep(self) -> None:
        """A deferred or refused night observed nothing; catching it up is the point."""
        runs = [_run(_oslo(28, 3), status="failed")]

        assert catch_up_blocker(runs, None, _oslo(28, 10)) is None

    def test_a_sweep_killed_by_shutdown_blocks_through_its_lock_record(self) -> None:
        """A killed sweep writes no run record; its start survives in the lock."""
        assert catch_up_blocker([], _oslo(28, 3), _oslo(28, 10)) == _oslo(28, 3)

    def test_a_start_stamped_in_the_future_is_no_evidence(self) -> None:
        """A clock that jumped back must not hold catch-up shut indefinitely."""
        assert catch_up_blocker([_run(_oslo(29, 3))], None, _oslo(28, 10)) is None

    def test_the_scheduled_trigger_is_never_guarded(self) -> None:
        """A boot catch-up at 01:00 does not swallow the 03:00 run: the
        schedule is the contract, and a trigger skipped by a guard would be a
        dropped trigger nothing reports (#218)."""
        runs = [_run(_oslo(28, 1))]

        assert catch_up_blocker(runs, None, _oslo(28, 3, 0)) is None


class TestRecordedSweepStart:
    def test_a_dead_sweep_still_names_its_start(self, tmp_path: Path) -> None:
        lock = _holder(tmp_path / "lock", pid=_dead_pid(), since="2026-09-28T01:00:05+00:00")

        assert recorded_sweep_start(OWNER, lock) == datetime(2026, 9, 28, 1, 0, 5, tzinfo=UTC)

    def test_another_workload_is_not_a_sweep(self, tmp_path: Path) -> None:
        lock = _holder(tmp_path / "lock", owner="llhb-run-arm", since="2026-09-28T01:00:05+00:00")

        assert recorded_sweep_start(OWNER, lock) is None

    def test_a_cleanly_released_lock_names_nothing(self, tmp_path: Path) -> None:
        lock = tmp_path / "lock"
        lock.write_text("")

        assert recorded_sweep_start(OWNER, lock) is None

    def test_a_naive_or_torn_stamp_names_nothing(self, tmp_path: Path) -> None:
        assert (
            recorded_sweep_start(OWNER, _holder(tmp_path / "a", since="2026-09-28T01:00")) is None
        )
        assert recorded_sweep_start(OWNER, _holder(tmp_path / "b", since="yesterday")) is None
