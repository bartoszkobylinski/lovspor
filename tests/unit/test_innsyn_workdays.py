"""The offentleglova § 32 andre ledd mark: five working days after receipt (#509)."""

import os
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest

from lovspor.innsyn.workdays import (
    add_working_days,
    easter_sunday,
    is_working_day,
    klage_mark,
    public_holidays,
)


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2000, date(2000, 4, 23)),
        (2019, date(2019, 4, 21)),
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
        (2038, date(2038, 4, 25)),
    ],
)
def test_easter_sunday_matches_the_published_dates(year: int, expected: date) -> None:
    assert easter_sunday(year) == expected


def test_the_2026_holidays_are_exactly_the_norwegian_ones() -> None:
    assert public_holidays(2026) == {
        date(2026, 1, 1),
        date(2026, 4, 2),
        date(2026, 4, 3),
        date(2026, 4, 5),
        date(2026, 4, 6),
        date(2026, 5, 1),
        date(2026, 5, 14),
        date(2026, 5, 17),
        date(2026, 5, 24),
        date(2026, 5, 25),
        date(2026, 12, 25),
        date(2026, 12, 26),
    }


def test_weekends_and_holidays_are_not_working_days() -> None:
    assert is_working_day(date(2026, 10, 12))
    assert not is_working_day(date(2026, 10, 10))
    assert not is_working_day(date(2026, 10, 11))
    assert not is_working_day(date(2027, 5, 17))
    assert is_working_day(date(2027, 5, 18))


def test_the_pilot_mark_is_monday_12_october() -> None:
    assert klage_mark(datetime(2026, 10, 5, 7, 43, 43, tzinfo=UTC)) == date(2026, 10, 12)


def test_easter_week_skips_skjaertorsdag_langfredag_and_2_paaskedag() -> None:
    assert klage_mark(datetime(2026, 3, 31, 9, 0, tzinfo=UTC)) == date(2026, 4, 10)


def test_the_receipt_day_is_the_oslo_day_not_the_utc_day() -> None:
    # 22:30 UTC on Thursday 8 October is 00:30 on Friday 9 October in Oslo.
    assert klage_mark(datetime(2026, 10, 8, 22, 30, tzinfo=UTC)) == date(2026, 10, 16)


def test_christmas_and_new_year_are_skipped() -> None:
    # Wed 23 Dec 2026 -> Thu 24, Mon 28, Tue 29, Wed 30, Thu 31.
    assert klage_mark(datetime(2026, 12, 23, 10, 0, tzinfo=UTC)) == date(2026, 12, 31)
    assert add_working_days(date(2026, 12, 31), 1) == date(2027, 1, 4)


def test_a_request_sent_on_a_saturday_counts_from_the_monday() -> None:
    assert add_working_days(date(2026, 10, 10), 5) == date(2026, 10, 16)


def test_zero_working_days_is_the_start_day() -> None:
    assert add_working_days(date(2026, 10, 10), 0) == date(2026, 10, 10)


@pytest.fixture
def los_angeles_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A process zone that is neither Oslo nor UTC, so the Oslo day must be asked for."""
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.usefixtures("los_angeles_clock")
def test_the_receipt_day_is_oslo_whatever_zone_the_process_runs_in() -> None:
    assert os.environ["TZ"] == "America/Los_Angeles"
    # 15:30 on Thursday 8 October in Los Angeles; 00:30 on Friday 9 in Oslo.
    assert klage_mark(datetime(2026, 10, 8, 22, 30, tzinfo=UTC)) == date(2026, 10, 16)
