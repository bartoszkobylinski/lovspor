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
from pydantic import ValidationError

from lovspor.errors import LogIntegrityError
from lovspor.observatory import sweeps
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


def _run(
    *,
    refused: int = 0,
    completed: int | None = None,
    started: datetime = START,
    capped: int = 0,
    held: int = 0,
) -> SweepRun:
    """A valid run. `completed` defaults to whatever `refused` leaves over —
    the helper used to take it independently and could build a record with more
    outcomes than active sources, which the model now refuses."""
    return SweepRun(
        run_id=started.isoformat(),
        started_at=started,
        finished_at=started + timedelta(minutes=76),
        active_sources=2,
        sources_completed=2 - refused - held if completed is None else completed,
        sources_refused=refused,
        sources_capped=capped,
        sources_held=held,
        captured=47,
        failed_fetches=1,
        unchanged=4218,
        status=sweep_status(active=2, refused=refused, capped=capped),
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
    def test_an_append_is_locked_and_synced_before_returning(
        self, root: ObservatoryRoot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Telemetry is the dead-man switch's evidence, so returning before
        the line is locked and durable would permit overlapping or lost runs."""
        locked: list[tuple[int, int]] = []
        synced: list[int] = []
        monkeypatch.setattr(
            sweeps.fcntl,
            "flock",
            lambda descriptor, operation: locked.append((descriptor, operation)),
        )
        monkeypatch.setattr(sweeps.os, "fsync", synced.append)

        append_sweep_run(root, _run())

        assert len(locked) == 1
        assert locked[0][1] == sweeps.fcntl.LOCK_EX
        assert synced == [locked[0][0]]

    def test_the_append_encoding_is_explicitly_utf8(
        self, root: ObservatoryRoot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original_open = Path.open
        encodings: list[str | None] = []

        def recording_open(path: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            encodings.append(kwargs.get("encoding"))  # type: ignore[arg-type]
            return original_open(path, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "open", recording_open)

        append_sweep_run(root, _run())

        assert encodings == ["utf-8"]

    def test_a_deep_archive_root_is_created_for_the_first_run(self, tmp_path: Path) -> None:
        root = ObservatoryRoot(tmp_path / "archive" / "lovspor" / "observatory", ())

        append_sweep_run(root, _run())

        assert read_sweep_runs(sweeps_path(root)) == [_run()]

    def test_a_written_run_round_trips_without_losing_fields(self, root: ObservatoryRoot) -> None:
        run = _run(refused=1, completed=1)

        append_sweep_run(root, run)

        assert read_sweep_runs(sweeps_path(root)) == [run]

    def test_a_run_written_before_capped_telemetry_defaults_to_uncapped(
        self, root: ObservatoryRoot
    ) -> None:
        """Existing append-only archives predate ``sources_capped`` and must
        remain readable after the telemetry schema grows."""
        line = _run().model_dump(mode="json")
        del line["sources_capped"]
        sweeps_path(root).parent.mkdir(parents=True, exist_ok=True)
        sweeps_path(root).write_text(f"{json.dumps(line)}\n", encoding="utf-8")

        assert read_sweep_runs(sweeps_path(root))[0].sources_capped == 0

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

        assert str(exc.value).startswith(f"{sweeps_path(root)}:2: unreadable sweep run:")

    def test_blank_lines_between_runs_are_ignored(self, root: ObservatoryRoot) -> None:
        first = _run().model_copy(update={"run_id": "run with spaces"})
        second = _run(started=START + timedelta(days=1))
        sweeps_path(root).parent.mkdir(parents=True, exist_ok=True)
        sweeps_path(root).write_bytes(
            first.model_dump_json().encode()
            + b"\n \t\n"
            + second.model_dump_json().encode()
            + b"\n"
        )

        assert read_sweep_runs(sweeps_path(root)) == [first, second]

    def test_damage_after_a_blank_line_reports_its_physical_line(
        self, root: ObservatoryRoot
    ) -> None:
        sweeps_path(root).parent.mkdir(parents=True, exist_ok=True)
        sweeps_path(root).write_bytes(_run().model_dump_json().encode() + b"\n\n{broken\n")

        with pytest.raises(LogIntegrityError) as exc:
            read_sweep_runs(sweeps_path(root))

        assert str(exc.value).startswith(f"{sweeps_path(root)}:3: unreadable sweep run:")

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
            "sources_capped",
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

    @pytest.mark.parametrize(
        "updates",
        [
            {"sources_completed": 3},
            {"sources_refused": 3},
            {"sources_completed": 2, "sources_refused": 1},
            {"sources_completed": 1, "sources_refused": 0},
            {"sources_held": 1},
        ],
        ids=[
            "too-many-completed",
            "too-many-refused",
            "too-many-outcomes",
            "missing-outcome",
            "held-on-top-of-a-full-account",
        ],
    )
    def test_inconsistent_source_totals_are_log_damage(
        self, root: ObservatoryRoot, updates: dict[str, int]
    ) -> None:
        """Counts are operational evidence, not independent counters: every
        active source must end in exactly one of completed, refused or held."""
        line = _run().model_dump(mode="json")
        line.update(updates)
        sweeps_path(root).parent.mkdir(parents=True, exist_ok=True)
        sweeps_path(root).write_text(f"{json.dumps(line)}\n", encoding="utf-8")

        with pytest.raises(LogIntegrityError, match="unreadable sweep run") as exc:
            read_sweep_runs(sweeps_path(root))

        assert str(exc.value).startswith(f"{sweeps_path(root)}:1:")

    @pytest.mark.parametrize(
        ("updates", "status"),
        [
            ({}, "degraded"),
            ({"sources_completed": 1, "sources_refused": 1}, "success"),
            (
                {"active_sources": 0, "sources_completed": 0, "sources_refused": 0},
                "success",
            ),
        ],
    )
    def test_status_that_contradicts_source_outcomes_is_log_damage(
        self, root: ObservatoryRoot, updates: dict[str, int], status: str
    ) -> None:
        """The persisted classification must be derivable from the counts;
        otherwise status can report green from internally contradictory data."""
        line = _run().model_dump(mode="json")
        line.update(updates)
        line["status"] = status
        sweeps_path(root).parent.mkdir(parents=True, exist_ok=True)
        sweeps_path(root).write_text(f"{json.dumps(line)}\n", encoding="utf-8")

        with pytest.raises(LogIntegrityError, match="unreadable sweep run"):
            read_sweep_runs(sweeps_path(root))


class TestAHeldSourceIsCountedNotHidden:
    """Issue #195. A source under a capture verdict is not asked until the
    verdict's re-check date, and the run says how many were spared: a source
    that quietly stopped being asked would read as an archive with nothing
    missing."""

    def test_a_held_source_accounts_for_one_active_source(self) -> None:
        run = SweepRun.model_validate(_run(held=1).model_dump())
        assert (run.sources_held, run.sources_completed, run.status) == (1, 1, "success")

    def test_holding_does_not_degrade_the_sweep(self) -> None:
        assert sweep_status(active=2, refused=0, capped=0) == "success"

    def test_a_run_written_before_held_telemetry_defaults_to_none_held(
        self, root: ObservatoryRoot
    ) -> None:
        line = _run().model_dump(mode="json")
        del line["sources_held"]
        sweeps_path(root).parent.mkdir(parents=True, exist_ok=True)
        sweeps_path(root).write_text(f"{json.dumps(line)}\n", encoding="utf-8")

        assert read_sweep_runs(sweeps_path(root))[0].sources_held == 0


class TestACappedSourceIsNotAComplete:
    """Issue #172. A source stopped by the fetch limit has been *truncated*,
    not observed — and the counts alone cannot tell the two apart. During the
    bootstrap seven municipalities stopped this way, Bergen among them, and
    every one read as finished."""

    def test_capping_degrades_a_sweep_with_no_refusals(self) -> None:
        assert sweep_status(active=201, refused=0, capped=1) == "degraded"

    def test_an_uncapped_sweep_with_no_refusals_is_still_success(self) -> None:
        assert sweep_status(active=201, refused=0, capped=0) == "success"

    def test_no_active_sources_still_outranks_capping(self) -> None:
        assert sweep_status(active=0, refused=0, capped=1) == "failed"

    def test_more_capped_than_completed_is_log_damage(self, root: ObservatoryRoot) -> None:
        """A source cannot be truncated without having been swept at all."""
        line = _run().model_dump(mode="json")
        line["sources_capped"] = line["sources_completed"] + 1
        sweeps_path(root).parent.mkdir(parents=True, exist_ok=True)
        sweeps_path(root).write_text(f"{json.dumps(line)}\n", encoding="utf-8")

        with pytest.raises(LogIntegrityError, match="unreadable sweep run"):
            read_sweep_runs(sweeps_path(root))

    def test_a_status_ignoring_the_cap_is_log_damage(self, root: ObservatoryRoot) -> None:
        """`success` beside a capped source is the exact claim #172 is about."""
        line = _run().model_dump(mode="json")
        line["sources_capped"] = 1
        line["status"] = "success"
        sweeps_path(root).parent.mkdir(parents=True, exist_ok=True)
        sweeps_path(root).write_text(f"{json.dumps(line)}\n", encoding="utf-8")

        with pytest.raises(LogIntegrityError, match="unreadable sweep run"):
            read_sweep_runs(sweeps_path(root))

    def test_a_capped_run_round_trips(self, root: ObservatoryRoot) -> None:
        run = _run(capped=1)
        append_sweep_run(root, run)

        assert read_sweep_runs(sweeps_path(root)) == [run]
        assert run.status == "degraded"


class TestAFailedRunSaysWhy:
    """#167: `failed` means the sweep could not execute — the archive was not
    mounted, the log was damaged. Recording that without the reason leaves an
    operator with a red light and no next step."""

    def _failed(self, *, reason: str | None) -> dict[str, object]:
        line = _run().model_dump(mode="json")
        line.update(
            active_sources=0,
            sources_completed=0,
            sources_refused=0,
            sources_capped=0,
            captured=0,
            failed_fetches=0,
            unchanged=0,
            status="failed",
            failure_reason=reason,
        )
        return line

    def test_a_failed_run_carries_its_reason(self) -> None:
        run = SweepRun.model_validate(self._failed(reason="storage_unavailable"))

        assert (run.status, run.failure_reason) == ("failed", "storage_unavailable")

    def test_a_failed_run_without_a_reason_is_damage(self, root: ObservatoryRoot) -> None:
        sweeps_path(root).parent.mkdir(parents=True, exist_ok=True)
        sweeps_path(root).write_text(f"{json.dumps(self._failed(reason=None))}\n", encoding="utf-8")

        with pytest.raises(LogIntegrityError, match="unreadable sweep run"):
            read_sweep_runs(sweeps_path(root))

    def test_a_reason_on_a_run_that_did_not_fail_is_damage(self, root: ObservatoryRoot) -> None:
        """A reason beside a green run is a contradiction, and the direction
        that matters: it would let a healthy record carry an alarming string
        nobody can act on."""
        line = _run().model_dump(mode="json")
        line["failure_reason"] = "storage_unavailable"
        sweeps_path(root).parent.mkdir(parents=True, exist_ok=True)
        sweeps_path(root).write_text(f"{json.dumps(line)}\n", encoding="utf-8")

        with pytest.raises(LogIntegrityError, match="unreadable sweep run"):
            read_sweep_runs(sweeps_path(root))

    def test_a_successful_run_needs_no_reason(self) -> None:
        assert _run().failure_reason is None


class TestNegativeDurationsCannotHappen:
    """The surviving `_hm` mutant (`// 60` -> `/ 60`) differs only on negative
    input, so the question it asks is whether a negative duration can reach a
    renderer at all. These pin the two answers: it cannot, and each way it
    could have is refused where the damage is still locatable."""

    def test_a_run_that_finished_before_it_started_is_damage(self, root: ObservatoryRoot) -> None:
        line = _run().model_dump(mode="json")
        line["finished_at"] = (START - timedelta(minutes=1)).isoformat()
        sweeps_path(root).parent.mkdir(parents=True, exist_ok=True)
        sweeps_path(root).write_text(f"{json.dumps(line)}\n", encoding="utf-8")

        with pytest.raises(LogIntegrityError, match="unreadable sweep run"):
            read_sweep_runs(sweeps_path(root))

    def test_an_inverted_pair_cannot_be_constructed_either(self) -> None:
        with pytest.raises(ValidationError):
            SweepRun(
                run_id="x",
                started_at=START,
                finished_at=START - timedelta(seconds=1),
                active_sources=1,
                sources_completed=1,
                sources_refused=0,
                captured=0,
                failed_fetches=0,
                unchanged=0,
                status="success",
            )

    def test_a_zero_length_run_is_allowed(self) -> None:
        """Equal stamps are not damage — a sweep of an empty candidate list can
        finish inside the clock's resolution."""
        run = SweepRun(
            run_id="x",
            started_at=START,
            finished_at=START,
            active_sources=1,
            sources_completed=1,
            sources_refused=0,
            captured=0,
            failed_fetches=0,
            unchanged=0,
            status="success",
        )

        assert run.finished_at == run.started_at

    def test_a_sweep_that_started_this_instant_is_fresh_not_overdue(self) -> None:
        """The boundary at exactly zero: a sweep beginning now has an age of
        zero, which is a real age and a healthy one. Folding it in with the
        ahead-of-clock case would report a starting sweep as OVERDUE."""
        state = cadence_state(_run(), now=START)

        assert state == CadenceState(age=timedelta(0), overdue=False)

    def test_a_sweep_stamped_ahead_of_the_clock_is_overdue_not_fresh(self) -> None:
        """A clock that jumped backwards, or a forged record, would otherwise
        hold the dead-man switch shut for as long as the stamp stayed ahead."""
        state = cadence_state(_run(), now=START - timedelta(hours=2))

        assert state == CadenceState(age=None, overdue=True)


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
