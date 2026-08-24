"""Sweep telemetry: the run as an observable object (issue #167).

Nothing is mocked. Runs are appended to a real file under a real validated
root, because the append-only property and the "did it run at all" question
are the behaviour under test — a fixture holding records in memory would
prove neither.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lovspor.errors import LogIntegrityError
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT, ObservatoryRoot
from lovspor.observatory.sweeps import (
    OBSERVATION_SLA,
    SWEEP_DEADLINE,
    CadenceState,
    SweepRun,
    append_sweep_run,
    cadence_state,
    latest_sweep_run,
    read_sweep_runs,
    sweep_status,
    sweeps_path,
)

START = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ObservatoryRoot:
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(tmp_path / "observatory"))
    return ObservatoryRoot(tmp_path / "observatory", ())


def _run(*, refused: int = 0, completed: int = 2, started: datetime = START) -> SweepRun:
    return SweepRun(
        run_id=started.isoformat(),
        started_at=started,
        finished_at=started + timedelta(minutes=76),
        active_sources=2,
        sources_completed=completed,
        sources_refused=refused,
        captured=47,
        failed_fetches=1,
        unchanged=4218,
        status=sweep_status(active=2, refused=refused),
    )


class TestTheSlaIsStatedOnce:
    def test_the_observation_target_is_twenty_four_hours(self) -> None:
        assert timedelta(hours=24) == OBSERVATION_SLA

    def test_the_alert_deadline_leaves_room_for_sleep_and_a_long_run(self) -> None:
        """36h, not 24h: room for sleep/wake and a slow sweep, but two whole
        days must never pass unnoticed."""
        assert timedelta(hours=36) == SWEEP_DEADLINE
        assert OBSERVATION_SLA < SWEEP_DEADLINE < 2 * OBSERVATION_SLA


class TestStatusClassification:
    def test_every_active_source_swept_is_success(self) -> None:
        assert sweep_status(active=201, refused=0) == "success"

    def test_one_refusal_degrades_the_whole_sweep(self) -> None:
        """The sweep ran; it is not a success. Issue #151's silent zero is
        exactly the case where a partial sweep reads as a complete one."""
        assert sweep_status(active=201, refused=1) == "degraded"

    def test_a_sweep_with_no_active_sources_is_not_a_success(self) -> None:
        assert sweep_status(active=0, refused=0) == "failed"


class TestAppendOnly:
    def test_a_written_run_round_trips_without_losing_fields(self, root: ObservatoryRoot) -> None:
        run = _run(refused=1, completed=1)

        append_sweep_run(root, run)

        assert read_sweep_runs(sweeps_path(root)) == [run]

    def test_a_run_is_appended_next_to_the_registry(self, root: ObservatoryRoot) -> None:
        append_sweep_run(root, _run())

        assert sweeps_path(root).name == "sweep-runs.jsonl"
        assert sweeps_path(root).parent == root.path

    def test_a_second_run_does_not_replace_the_first(self, root: ObservatoryRoot) -> None:
        append_sweep_run(root, _run())
        append_sweep_run(root, _run(started=START + timedelta(days=1)))

        runs = read_sweep_runs(sweeps_path(root))

        assert [run.started_at for run in runs] == [START, START + timedelta(days=1)]

    def test_each_run_is_one_line(self, root: ObservatoryRoot) -> None:
        append_sweep_run(root, _run())
        append_sweep_run(root, _run(started=START + timedelta(days=1)))

        assert sweeps_path(root).read_text().count("\n") == 2

    def test_the_newest_run_is_the_one_reported(self, root: ObservatoryRoot) -> None:
        append_sweep_run(root, _run())
        append_sweep_run(root, _run(refused=1, started=START + timedelta(days=1)))

        latest = latest_sweep_run(sweeps_path(root))

        assert latest is not None
        assert latest.status == "degraded"

    def test_latest_is_selected_by_start_time_not_file_position(
        self, root: ObservatoryRoot
    ) -> None:
        newest = _run(refused=1, started=START + timedelta(days=1))
        append_sweep_run(root, newest)
        append_sweep_run(root, _run())

        assert latest_sweep_run(sweeps_path(root)) == newest

    def test_no_file_yet_is_not_an_error(self, root: ObservatoryRoot) -> None:
        """A fresh archive has never swept. That is a fact to report, not a
        failure to read."""
        assert latest_sweep_run(sweeps_path(root)) is None


class TestDamageIsRefused:
    def test_an_unreadable_line_refuses_rather_than_being_skipped(
        self, root: ObservatoryRoot
    ) -> None:
        """Same posture as the observation log: reading past a line we do not
        understand would silently answer "when did we last sweep" with the
        wrong run."""
        append_sweep_run(root, _run())
        with sweeps_path(root).open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")

        with pytest.raises(LogIntegrityError) as exc:
            read_sweep_runs(sweeps_path(root))

        assert "2" in str(exc.value)

    def test_a_line_missing_a_field_is_damage_too(self, root: ObservatoryRoot) -> None:
        sweeps_path(root).parent.mkdir(parents=True, exist_ok=True)
        with sweeps_path(root).open("w", encoding="utf-8") as handle:
            handle.write('{"run_id": "x"}\n')

        with pytest.raises(LogIntegrityError):
            read_sweep_runs(sweeps_path(root))

    def test_an_unknown_field_is_damage_too(self, root: ObservatoryRoot) -> None:
        """A newer writer's field dropped on read would discard part of what
        the run recorded."""
        line = _run().model_dump_json()
        sweeps_path(root).parent.mkdir(parents=True, exist_ok=True)
        with sweeps_path(root).open("w", encoding="utf-8") as handle:
            handle.write(line[:-1] + ',"invented":1}\n')

        with pytest.raises(LogIntegrityError):
            read_sweep_runs(sweeps_path(root))

    def test_malformed_utf8_is_reported_as_log_damage(self, root: ObservatoryRoot) -> None:
        sweeps_path(root).parent.mkdir(parents=True, exist_ok=True)
        sweeps_path(root).write_bytes(b'{"run_id":"\xff"}\n')

        with pytest.raises(LogIntegrityError, match="unreadable sweep run"):
            read_sweep_runs(sweeps_path(root))

    def test_a_timestamp_without_a_timezone_is_log_damage(self, root: ObservatoryRoot) -> None:
        """Cadence compares the stored timestamp with an aware UTC clock, so
        accepting a naive timestamp only postpones the integrity failure until
        the status command tries to subtract the two."""
        line = _run().model_dump(mode="json")
        line["started_at"] = "2026-08-25T01:00:00"
        sweeps_path(root).parent.mkdir(parents=True, exist_ok=True)
        sweeps_path(root).write_text(f"{json.dumps(line)}\n", encoding="utf-8")

        with pytest.raises(LogIntegrityError, match="unreadable sweep run"):
            read_sweep_runs(sweeps_path(root))

    def test_a_finish_timestamp_without_a_timezone_is_log_damage(
        self, root: ObservatoryRoot
    ) -> None:
        line = _run().model_dump(mode="json")
        line["finished_at"] = "2026-08-25T02:16:00"
        sweeps_path(root).parent.mkdir(parents=True, exist_ok=True)
        sweeps_path(root).write_text(f"{json.dumps(line)}\n", encoding="utf-8")

        with pytest.raises(LogIntegrityError, match="unreadable sweep run"):
            read_sweep_runs(sweeps_path(root))

    @pytest.mark.parametrize(
        "field",
        [
            "active_sources",
            "sources_completed",
            "sources_refused",
            "captured",
            "failed_fetches",
            "unchanged",
        ],
    )
    def test_negative_counts_are_log_damage(self, root: ObservatoryRoot, field: str) -> None:
        line = _run().model_dump(mode="json")
        line[field] = -1
        sweeps_path(root).parent.mkdir(parents=True, exist_ok=True)
        sweeps_path(root).write_text(f"{json.dumps(line)}\n", encoding="utf-8")

        with pytest.raises(LogIntegrityError, match="unreadable sweep run"):
            read_sweep_runs(sweeps_path(root))


class TestCadence:
    def test_a_recent_sweep_is_ok(self) -> None:
        state = cadence_state(_run(), now=START + timedelta(hours=18, minutes=47))

        assert state == CadenceState(age=timedelta(hours=18, minutes=47), overdue=False)

    def test_the_age_is_measured_from_the_start_not_the_finish(self) -> None:
        """A sweep that started 35h ago and ran for two hours has still not
        begun a new observation in 35h. Measuring from the finish would hide
        exactly the slow run the deadline exists to catch."""
        state = cadence_state(_run(), now=START + timedelta(hours=35))

        assert state.age == timedelta(hours=35)

    def test_past_the_deadline_is_overdue(self) -> None:
        assert cadence_state(_run(), now=START + SWEEP_DEADLINE).overdue is True

    def test_never_swept_is_overdue(self) -> None:
        """No sweep on record cannot read as OK — that is the Mac-was-off case
        the dead-man switch exists for."""
        assert cadence_state(None, now=START).overdue is True
        assert cadence_state(None, now=START).age is None

    def test_between_the_target_and_the_deadline_is_not_yet_an_alert(self) -> None:
        state = cadence_state(_run(), now=START + OBSERVATION_SLA + timedelta(hours=1))

        assert state.overdue is False
