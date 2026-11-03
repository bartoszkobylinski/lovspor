"""Timestamps and dates must name a real calendar instant, not only match the shape."""

from typing import Any

import pytest

from lovspor.crosswalk.contract import record_problems, schema_problems, validate_records
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
