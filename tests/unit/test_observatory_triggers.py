"""Scheduled triggers lost to a sweep that was still running (issue #218)."""

import json
import os
import plistlib
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lovspor.exclusive_workload import default_lock_path
from lovspor.observatory.sweeps import SweepRun
from lovspor.observatory.triggers import (
    DROPPED_TRIGGER_LOOKBACK,
    SCHEDULE_TIME,
    SCHEDULE_ZONE,
    DroppedTrigger,
    dropped_triggers,
    running_sweep,
    scheduled_triggers,
)

PLIST = (
    Path(__file__).resolve().parents[2]
    / "deploy"
    / "launchd"
    / "no.lovspor.observatory.nightly.plist"
)


def _run(started: datetime, finished: datetime) -> SweepRun:
    return SweepRun(
        run_id=started.isoformat(),
        started_at=started,
        finished_at=finished,
        active_sources=1,
        sources_completed=1,
        sources_refused=0,
        captured=0,
        failed_fetches=0,
        unchanged=0,
        status="success",
    )


def _utc(day: int, hour: int, minute: int = 0, month: int = 9) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


class TestSchedule:
    def test_the_schedule_is_the_one_the_launchd_job_fires_on(self) -> None:
        """The constant mirrors the plist; drift between them fails here."""
        interval = plistlib.loads(PLIST.read_bytes())["StartCalendarInterval"]

        assert interval == {"Hour": SCHEDULE_TIME.hour, "Minute": SCHEDULE_TIME.minute}

    def test_the_schedule_is_read_in_oslo_time(self) -> None:
        assert SCHEDULE_ZONE.key == "Europe/Oslo"

    def test_the_lookback_is_two_weeks(self) -> None:
        assert timedelta(days=14) == DROPPED_TRIGGER_LOOKBACK


class TestScheduledTriggers:
    def test_the_window_start_uses_oslo_date_not_the_host_timezone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original_tz = os.environ.get("TZ")
        monkeypatch.setenv("TZ", "UTC-23")
        time.tzset()
        try:
            triggers = scheduled_triggers(
                datetime(2026, 1, 22, 1, 30, tzinfo=UTC),
                datetime(2026, 1, 22, 3, 0, tzinfo=UTC),
            )
        finally:
            if original_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original_tz
            time.tzset()

        assert triggers == [datetime(2026, 1, 22, 2, tzinfo=UTC)]

    def test_the_window_end_uses_oslo_date_not_the_host_timezone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original_tz = os.environ.get("TZ")
        monkeypatch.setenv("TZ", "America/Los_Angeles")
        time.tzset()
        try:
            triggers = scheduled_triggers(_utc(21, 12), _utc(22, 2))
        finally:
            if original_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original_tz
            time.tzset()

        assert triggers == [_utc(22, 1)]

    def test_one_trigger_per_day_at_three_oslo_time(self) -> None:
        triggers = scheduled_triggers(_utc(22, 0), _utc(24, 12))

        assert triggers == [_utc(22, 1), _utc(23, 1), _utc(24, 1)]

    def test_the_window_is_open_at_the_start_and_closed_at_the_end(self) -> None:
        assert scheduled_triggers(_utc(22, 1), _utc(23, 1)) == [_utc(23, 1)]

    def test_an_empty_window_has_no_trigger(self) -> None:
        assert scheduled_triggers(_utc(22, 2), _utc(23, 0, 59)) == []

    def test_triggers_follow_oslo_across_the_autumn_clock_change(self) -> None:
        """03:00 is CEST (01:00Z) before 25 October 2026 and CET (02:00Z) from it."""
        triggers = scheduled_triggers(_utc(24, 0, month=10), _utc(25, 12, month=10))

        assert triggers == [_utc(24, 1, month=10), _utc(25, 2, month=10)]

    def test_triggers_follow_oslo_across_the_spring_clock_change(self) -> None:
        """03:00 is CET (02:00Z) before 29 March 2026 and CEST (01:00Z) from it."""
        triggers = scheduled_triggers(
            datetime(2026, 3, 28, 0, tzinfo=UTC),
            datetime(2026, 3, 29, 12, tzinfo=UTC),
        )

        assert triggers == [
            datetime(2026, 3, 28, 2, tzinfo=UTC),
            datetime(2026, 3, 29, 1, tzinfo=UTC),
        ]

    def test_triggers_are_stated_in_oslo_time(self) -> None:
        (trigger,) = scheduled_triggers(_utc(22, 0), _utc(22, 12))

        assert trigger.isoformat() == "2026-09-22T03:00:00+02:00"


class TestDroppedTriggers:
    def test_a_running_sweep_does_not_drop_the_trigger_at_its_exact_start(self) -> None:
        trigger = _utc(26, 1)

        assert dropped_triggers([], trigger, now=_utc(26, 21)) == []

    def test_the_real_overrun_of_24_september_dropped_the_25th(self) -> None:
        """The run recorded in production: 24.09 01:00Z to 25.09 04:36Z."""
        run = _run(_utc(24, 1, 0) + timedelta(seconds=30), _utc(25, 4, 36))

        dropped = dropped_triggers([run], None, now=_utc(26, 21))

        assert dropped == [
            DroppedTrigger(scheduled_at=_utc(25, 1), held_by=run.started_at, still_running=False)
        ]

    def test_the_trigger_that_started_a_run_was_not_dropped(self) -> None:
        run = _run(_utc(24, 1, 0) + timedelta(seconds=30), _utc(24, 5))

        assert dropped_triggers([run], None, now=_utc(26, 21)) == []

    def test_a_trigger_at_the_exact_start_or_finish_was_not_dropped(self) -> None:
        runs = [_run(_utc(24, 1), _utc(24, 5)), _run(_utc(24, 20), _utc(25, 1))]

        assert dropped_triggers(runs, None, now=_utc(26, 21)) == []

    def test_every_trigger_a_multi_day_run_held_is_named(self) -> None:
        run = _run(_utc(20, 12), _utc(23, 6))

        dropped = dropped_triggers([run], None, now=_utc(26, 21))

        assert [trigger.scheduled_at for trigger in dropped] == [
            _utc(21, 1),
            _utc(22, 1),
            _utc(23, 1),
        ]

    def test_triggers_older_than_the_lookback_are_not_listed(self) -> None:
        now = _utc(26, 21)
        run = _run(now - DROPPED_TRIGGER_LOOKBACK - timedelta(days=2), _utc(25, 6))

        dropped = dropped_triggers([run], None, now=now)

        assert dropped[0].scheduled_at == _utc(13, 1)
        assert len(dropped) == 13

    def test_a_trigger_exactly_at_the_lookback_cutoff_is_not_listed(self) -> None:
        """The report covers (now - 14 days, now], not fifteen calendar dates."""
        now = _utc(26, 1)
        run = _run(_utc(11, 12), now + timedelta(hours=1))

        dropped = dropped_triggers([run], None, now=now)

        assert [trigger.scheduled_at for trigger in dropped] == [
            _utc(day, 1) for day in range(13, 27)
        ]

    def test_the_sweep_running_now_holds_the_triggers_since_it_began(self) -> None:
        since = _utc(24, 5, 15)

        dropped = dropped_triggers([], since, now=_utc(26, 21))

        assert dropped == [
            DroppedTrigger(scheduled_at=_utc(25, 1), held_by=since, still_running=True),
            DroppedTrigger(scheduled_at=_utc(26, 1), held_by=since, still_running=True),
        ]

    def test_a_sweep_running_since_after_the_last_trigger_dropped_nothing(self) -> None:
        assert dropped_triggers([], _utc(26, 5, 15), now=_utc(26, 21)) == []

    def test_no_runs_drop_no_triggers(self) -> None:
        assert dropped_triggers([], None, now=_utc(26, 21)) == []

    def test_the_trigger_is_attributed_to_the_run_that_held_it(self) -> None:
        early = _run(_utc(20, 12), _utc(20, 18))
        holder = _run(_utc(22, 5), _utc(23, 11))

        (dropped,) = dropped_triggers([early, holder], None, now=_utc(26, 21))

        assert dropped.held_by == holder.started_at


def _holder(path: Path, *, owner: str, pid: int, since: str) -> Path:
    path.write_text(json.dumps({"owner": owner, "pid": pid, "since": since}) + "\n")
    return path


def _dead_pid() -> int:
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    return finished.pid


class TestRunningSweep:
    def test_a_live_sweep_holding_the_lock_is_running_since_it_took_it(
        self, tmp_path: Path
    ) -> None:
        lock = _holder(
            tmp_path / "lock",
            owner="observatory-sweep",
            pid=os.getpid(),
            since="2026-09-26T05:15:40+00:00",
        )

        assert running_sweep("observatory-sweep", lock) == _utc(26, 5, 15) + timedelta(seconds=40)

    def test_the_default_lock_path_is_the_hosts_lock(self) -> None:
        """conftest points the host lock at a temporary file; that file is read."""
        _holder(
            default_lock_path(),
            owner="observatory-sweep",
            pid=os.getpid(),
            since="2026-09-26T05:15:40+00:00",
        )

        assert running_sweep("observatory-sweep") is not None

    def test_another_workload_holding_the_lock_is_not_a_sweep(self, tmp_path: Path) -> None:
        lock = _holder(
            tmp_path / "lock", owner="llhb", pid=os.getpid(), since="2026-09-26T05:15:40+00:00"
        )

        assert running_sweep("observatory-sweep", lock) is None

    def test_a_record_left_by_a_killed_sweep_is_not_a_running_one(self, tmp_path: Path) -> None:
        lock = _holder(
            tmp_path / "lock",
            owner="observatory-sweep",
            pid=_dead_pid(),
            since="2026-09-26T05:15:40+00:00",
        )

        assert running_sweep("observatory-sweep", lock) is None

    @pytest.mark.parametrize("pid", [0, -1, 2**64])
    def test_a_pid_no_process_can_have_is_not_running(self, tmp_path: Path, pid: int) -> None:
        """0 and negatives would address a process group, not a process."""
        lock = _holder(
            tmp_path / "lock", owner="observatory-sweep", pid=pid, since="2026-09-26T05:15:40+00:00"
        )

        assert running_sweep("observatory-sweep", lock) is None

    @pytest.mark.parametrize("since", ["yesterday", "2026-09-26T05:15:40"])
    def test_a_start_that_is_not_an_aware_instant_is_not_used(
        self, tmp_path: Path, since: str
    ) -> None:
        lock = _holder(tmp_path / "lock", owner="observatory-sweep", pid=os.getpid(), since=since)

        assert running_sweep("observatory-sweep", lock) is None

    def test_no_lock_file_means_nothing_is_running(self, tmp_path: Path) -> None:
        assert running_sweep("observatory-sweep", tmp_path / "absent") is None

    def test_a_released_lock_means_nothing_is_running(self, tmp_path: Path) -> None:
        lock = tmp_path / "lock"
        lock.write_text("")

        assert running_sweep("observatory-sweep", lock) is None

    @pytest.mark.parametrize(
        "record",
        [
            b"not json\n",
            b'{"owner":"observatory-sweep","pid":',
            b'{"owner":"observatory-sweep","pid":"not-a-pid","since":"2026-09-26T05:15:40+00:00"}',
            b"\xff\n",
        ],
    )
    def test_an_unreadable_advisory_record_is_not_a_running_sweep(
        self, tmp_path: Path, record: bytes
    ) -> None:
        lock = tmp_path / "lock"
        lock.write_bytes(record)

        assert running_sweep("observatory-sweep", lock) is None

    def test_a_process_owned_by_another_user_is_alive(self, tmp_path: Path) -> None:
        """pid 1 exists on every host and signalling it is refused, not absent."""
        if os.geteuid() == 0:
            pytest.skip("root may signal pid 1")
        lock = _holder(
            tmp_path / "lock", owner="observatory-sweep", pid=1, since="2026-09-26T05:15:40+00:00"
        )

        assert running_sweep("observatory-sweep", lock) is not None
