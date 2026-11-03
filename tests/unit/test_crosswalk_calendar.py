"""Timestamps and dates must name a real calendar instant, not only match the shape."""

from datetime import datetime
from typing import Any

import pytest

from lovspor.crosswalk import contract
from lovspor.crosswalk.contract import (
    _instant,
    _names_itself,
    record_problems,
    schema_problems,
    validate_records,
)
from lovspor.crosswalk.errors import CrosswalkContractError
from tests.unit.crosswalk_support import attested


def _withdrawn(withdrawn_on: str) -> dict[str, Any]:
    record = attested()
    record.update(
        record_revision=2,
        record_state="withdrawn",
        status="unresolved",
        edge_status={**record["edge_status"], "decision_case": "unresolved"},
        reason="producer edge was wrong",
        reason_code="withdrawn_by_producer",
        withdrawal={"withdrawn_on": withdrawn_on, "reason": "producer edge was wrong"},
    )
    return record


BAD_STAMPS = [
    "2026-02-30T10:00:00Z",
    "2026-13-01T10:00:00Z",
    "2026-11-02T24:00:00Z",
    "2026-11-02T24:00:00+00:00",
    "2026-12-31T24:00:00Z",
    "2026-11-02T10:60:00Z",
    "2026-11-02T10:00:61+00:00",
    "2025-02-29T10:00:00Z",
]


@pytest.mark.parametrize("stamp", BAD_STAMPS)
def test_evidence_retrieved_at_must_be_a_calendar_instant(stamp: str) -> None:
    record = attested()
    record["evidence"][0]["retrieved_at"] = stamp
    problems = record_problems(record)
    assert problems
    assert any("/evidence/0/retrieved_at" in problem for problem in problems)


@pytest.mark.parametrize("stamp", BAD_STAMPS)
@pytest.mark.parametrize("field", ["retrieved_at", "last_modified"])
def test_archive_pin_stamps_must_be_calendar_instants(stamp: str, field: str) -> None:
    record = attested()
    record["source_pins"]["lovdata_archives"][0][field] = stamp
    problems = record_problems(record)
    assert any(f"/source_pins/lovdata_archives/0/{field}" in p for p in problems)


@pytest.mark.parametrize("day", ["2026-02-30", "2026-00-10", "2025-02-29", "2026-04-31"])
def test_observed_on_must_be_a_calendar_date(day: str) -> None:
    record = attested()
    record["observed_on"] = day
    assert any(p.startswith("/observed_on: ") for p in schema_problems(record))


@pytest.mark.parametrize("day", ["2026-02-30", "2026-13-03"])
def test_withdrawn_on_must_be_a_calendar_date(day: str) -> None:
    assert schema_problems(_withdrawn("2026-11-03")) == []
    problems = schema_problems(_withdrawn(day))
    assert any(p.startswith("/withdrawal/withdrawn_on: ") for p in problems)


def test_leap_day_and_offset_forms_stay_valid() -> None:
    record = attested()
    record["evidence"][0]["retrieved_at"] = "2024-02-29T23:59:59.123456Z"
    record["observed_on"] = "2026-11-02"
    record["evidence"][0]["retrieved_at"] = "2026-11-02T08:00:00.5+00:00"
    assert record_problems(record) == []


def test_malformed_stamp_is_a_typed_contract_error_not_a_value_error() -> None:
    record = attested()
    record["evidence"][1]["retrieved_at"] = "2026-02-30T10:00:00Z"
    with pytest.raises(CrosswalkContractError) as caught:
        validate_records([record])
    assert any("/evidence/1/retrieved_at" in problem for problem in caught.value.problems)


@pytest.mark.parametrize("day", ["2000-02-29", "2024-02-29", "2026-04-30"])
@pytest.mark.parametrize("suffix", ["T00:00:00Z", "T23:59:59.123456+00:00"])
def test_real_calendar_boundaries_validate_through_all_date_fields(day: str, suffix: str) -> None:
    record = _withdrawn(day)
    record["observed_on"] = day
    for item in record["evidence"]:
        item["retrieved_at"] = day + suffix
    pin = record["source_pins"]["lovdata_archives"][0]
    pin["retrieved_at"] = pin["last_modified"] = day + suffix

    assert schema_problems(record) == []
    assert record_problems(record) == []
    first = attested()
    validate_records([first, record])


@pytest.mark.parametrize("day", ["1900-02-29", "2100-02-29", "0000-01-01"])
def test_non_calendar_dates_report_every_affected_path(day: str) -> None:
    record = _withdrawn(day)
    record["observed_on"] = day
    record["evidence"][0]["retrieved_at"] = day + "T00:00:00Z"
    pin = record["source_pins"]["lovdata_archives"][0]
    pin["retrieved_at"] = pin["last_modified"] = day + "T00:00:00Z"

    problems = schema_problems(record)
    assert record_problems(record) == problems
    assert {problem.split(": ", 1)[0] for problem in problems} == {
        "/observed_on",
        "/withdrawal/withdrawn_on",
        "/evidence/0/retrieved_at",
        "/source_pins/lovdata_archives/0/retrieved_at",
        "/source_pins/lovdata_archives/0/last_modified",
    }
    with pytest.raises(CrosswalkContractError) as caught:
        validate_records([record])
    assert caught.value.problems == tuple(f"LOV-2022-06-17-63@2: {p}" for p in problems)


@pytest.mark.parametrize(
    "stamp",
    ["2026-11-02T10:00:00Z", "2026-11-02T10:00:00.5+00:00", "2026-11-02T00:00:00Z"],
)
def test_names_itself_true_for_a_stamp_that_round_trips(stamp: str) -> None:
    assert _names_itself(stamp) is True


def test_names_itself_false_when_the_parsed_instant_prints_back_differently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Python 3.14's `fromisoformat` quietly rolls 24:00 into the next midnight
    instead of raising; a fake stands in so the rollover is caught on every
    interpreter this suite runs on, not only one that happens to roll over."""
    rolled_over = datetime(2026, 11, 3, 0, 0, 0)

    class _RollsOverSilently:
        @staticmethod
        def fromisoformat(value: str) -> datetime:
            return rolled_over

    monkeypatch.setattr(contract, "datetime", _RollsOverSilently)
    assert _names_itself("2026-11-02T24:00:00Z") is False


def test_instant_rejects_without_raising_when_the_rollover_hides_the_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The format checker must see an ordinary `False`, not an uncaught exception,
    when a lenient `fromisoformat` hides a 24:00 rollover."""
    rolled_over = datetime(2026, 11, 3, 0, 0, 0)

    class _RollsOverSilently:
        @staticmethod
        def fromisoformat(value: str) -> datetime:
            return rolled_over

    monkeypatch.setattr(contract, "datetime", _RollsOverSilently)
    assert _instant("2026-11-02T24:00:00Z") is False


@pytest.mark.parametrize("value", [None, 20261102, [], {}])
def test_non_string_calendar_fields_are_schema_errors(value: object) -> None:
    record = _withdrawn("2026-11-03")
    record["observed_on"] = value
    record["withdrawal"]["withdrawn_on"] = value
    record["evidence"][0]["retrieved_at"] = value
    pin = record["source_pins"]["lovdata_archives"][0]
    pin["retrieved_at"] = pin["last_modified"] = value

    problems = schema_problems(record)
    assert len(problems) == 5
    assert all("is not of type 'string'" in problem for problem in problems)
    assert record_problems(record) == problems
    with pytest.raises(CrosswalkContractError) as caught:
        validate_records([record])
    assert len(caught.value.problems) == 5
