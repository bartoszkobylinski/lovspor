"""The history derivation reads the local-regulation commit subjects (ADR-0016 3, S3)."""

from __future__ import annotations

import pytest

from lovspor.history import EventType, _classify_commit

PATH = "lokale-forskrifter/0301/renovasjonsforskrift.md"


def _event_type(subject: str) -> EventType | None:
    event = _classify_commit("abc1234def", "2026-10-03T10:00:00Z", subject, [f"40\t2\t{PATH}"])
    return None if event is None else event.type


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("promote(lokal-forskrift): 0301/renovasjonsforskrift v1", "added"),
        ("promote(lokal-forskrift): 0301/renovasjonsforskrift v2", "updated"),
        ("promote(lokal-forskrift): 0301/renovasjonsforskrift v10", "updated"),
        ("withdraw(lokal-forskrift): 0301/renovasjonsforskrift", "removed"),
    ],
)
def test_local_subjects_name_their_event(subject: str, expected: EventType) -> None:
    assert _event_type(subject) == expected


def test_an_observation_refresh_is_no_event() -> None:
    assert _event_type("observe: refresh observation intervals (3 documents)") is None


@pytest.mark.parametrize(
    "subject",
    [
        "promote(lokal-forskrift): 0301/renovasjonsforskrift v0",
        "promote(lokal-forskrift): 0301/renovasjonsforskrift",
        "promote(lokal-forskrift): 0301/renovasjonsforskrift v1 extra",
    ],
)
def test_a_malformed_promote_subject_falls_to_the_generic_rule(subject: str) -> None:
    event = _classify_commit("abc1234def", "2026-10-03T10:00:00Z", subject, [f"0\t9\t{PATH}"])
    assert event is not None
    assert event.type == "added"


def test_central_subjects_are_read_as_before() -> None:
    assert _event_type("remove(forskrift): eksempelforskrift") == "removed"
    assert _event_type("migration: re-render 3 documents (renderer v9)") is None
