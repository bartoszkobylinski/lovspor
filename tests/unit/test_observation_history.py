"""An instant placed against a local document's observation intervals (ADR-0016 2, 5; S7).

The intervals are the closed ``[observed_at_first, observed_at_last]`` of each
promoted version, as ``observations/<slug>.json`` carries them (S6). Four
typed outcomes, none of which asserts anything about an instant nothing was
observed at.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lovspor.errors import LovsporError, ObservedAtError
from lovspor.observation_history import (
    OBSERVATION_FLOOR,
    AfterLastObservation,
    BeforeFirstObservation,
    BetweenObservations,
    Contained,
    ObservedVersion,
    locate,
    parse_observed_at,
)


def _version(number: int, first: str, last: str) -> ObservedVersion:
    return ObservedVersion(
        version=number,
        content_hash=f"{number}" * 64,
        observed_at_first=first,
        observed_at_last=last,
        observation_count=2,
        primary_url="https://eksempel.kommune.invalid/forskrift",
        corroborating_urls=(),
    )


V1 = _version(1, "2026-08-19T15:17:23Z", "2026-08-20T15:17:23Z")
V2 = _version(2, "2026-08-21T15:17:23Z", "2026-08-21T15:17:23Z")
V3 = _version(3, "2026-08-22T15:17:23Z", "2026-08-30T09:00:00Z")
VERSIONS = (V1, V2, V3)


def _at(text: str) -> datetime:
    return parse_observed_at(text)


class TestContained:
    @pytest.mark.parametrize(
        ("instant", "expected"),
        [
            ("2026-08-19T15:17:23Z", V1),
            ("2026-08-20T00:00:00Z", V1),
            ("2026-08-20T15:17:23Z", V1),
            ("2026-08-21T15:17:23Z", V2),
            ("2026-08-30T09:00:00Z", V3),
        ],
    )
    def test_an_instant_inside_a_closed_interval_names_that_version(
        self, instant: str, expected: ObservedVersion
    ) -> None:
        outcome = locate(VERSIONS, _at(instant))

        assert outcome == Contained(observed_at=instant, version=expected)
        assert outcome.outcome == "contained"
        assert outcome.text is None

    def test_a_single_observation_is_an_interval_of_one_instant(self) -> None:
        assert locate((V2,), _at("2026-08-21T15:17:23Z")).outcome == "contained"


class TestBetweenObservations:
    def test_an_instant_in_a_gap_names_both_neighbours(self) -> None:
        outcome = locate(VERSIONS, _at("2026-08-21T00:00:00Z"))

        assert outcome == BetweenObservations(
            observed_at="2026-08-21T00:00:00Z", before=V1, after=V2
        )
        assert outcome.outcome == "between_observations"
        assert "neither" in outcome.notice

    def test_one_microsecond_past_an_interval_is_between(self) -> None:
        outcome = locate(VERSIONS, _at("2026-08-21T15:17:23.000001Z"))

        assert isinstance(outcome, BetweenObservations)
        assert (outcome.before, outcome.after) == (V2, V3)


class TestBeforeFirstObservation:
    def test_an_instant_before_the_first_observation_is_the_boundary(self) -> None:
        outcome = locate(VERSIONS, _at("2026-08-19T15:17:22Z"))

        assert outcome == BeforeFirstObservation(
            observed_at="2026-08-19T15:17:22Z", observed_at_first=V1.observed_at_first
        )
        assert outcome.outcome == "before_first_observation"
        assert outcome.observation_floor == OBSERVATION_FLOOR == "2026-08-19"

    def test_the_boundary_holds_for_a_document_first_seen_later(self) -> None:
        outcome = locate((V3,), _at("2026-08-20T00:00:00Z"))

        assert isinstance(outcome, BeforeFirstObservation)
        assert outcome.observed_at_first == V3.observed_at_first


class TestAfterLastObservation:
    def test_an_instant_after_the_last_observation_is_not_contained(self) -> None:
        """S6 intervals are closed: ``observed_at_last`` is the last instant seen."""
        outcome = locate(VERSIONS, _at("2026-08-30T09:00:01Z"))

        assert outcome == AfterLastObservation(
            observed_at="2026-08-30T09:00:01Z", last=V3, observed_at_last=V3.observed_at_last
        )
        assert outcome.outcome == "after_last_observation"
        assert "not asserted" in outcome.notice

    def test_a_far_future_instant_needs_no_clock(self) -> None:
        assert locate(VERSIONS, _at("2099-01-01T00:00:00Z")).outcome == "after_last_observation"


class TestParseObservedAt:
    @pytest.mark.parametrize(
        ("instant", "expected"),
        [
            ("2026-08-19T17:17:22.999999+02:00", "before_first_observation"),
            ("2026-08-19T17:17:23+02:00", "contained"),
            ("2026-08-20T10:17:23-05:00", "contained"),
            ("2026-08-20T10:17:23.000001-05:00", "between_observations"),
            ("2026-08-30T11:00:00+02:00", "contained"),
            ("2026-08-30T11:00:00.000001+02:00", "after_last_observation"),
        ],
    )
    def test_offset_instants_preserve_closed_interval_microsecond_boundaries(
        self, instant: str, expected: str
    ) -> None:
        """docs/mcp.md: bounds are included and offset instants select observations."""
        parsed = parse_observed_at(instant)
        canonical = parsed.isoformat().replace("+00:00", "Z")

        outcome = locate(VERSIONS, parsed)

        assert outcome.outcome == expected
        assert outcome.observed_at == canonical
        assert outcome == locate(VERSIONS, parse_observed_at(canonical))

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("2026-09-01T12:00:00Z", datetime(2026, 9, 1, 12, tzinfo=UTC)),
            ("2026-09-01T14:00:00+02:00", datetime(2026, 9, 1, 12, tzinfo=UTC)),
            ("2026-08-24T11:05:06.922416Z", datetime(2026, 8, 24, 11, 5, 6, 922416, tzinfo=UTC)),
        ],
    )
    def test_an_instant_with_an_offset_is_read_in_utc(self, text: str, expected: datetime) -> None:
        parsed = parse_observed_at(text)

        assert parsed == expected
        assert parsed.utcoffset() is not None

    @pytest.mark.parametrize(
        "text",
        [
            "2026-09-01",
            "2026-09-01T12:00:00",
            "20260901T120000Z",
            "2026-09-31T12:00:00Z",
            "2026-09-01 12:00:00Z",
            "2026-09-01T12:00:00Z ",
            "",
        ],
    )
    def test_anything_but_an_instant_with_an_offset_is_refused(self, text: str) -> None:
        with pytest.raises(ObservedAtError, match="observed_at must be an instant") as caught:
            parse_observed_at(text)

        assert isinstance(caught.value, LovsporError)
        assert repr(text) in str(caught.value)

    def test_a_calendar_date_is_refused_with_why(self) -> None:
        with pytest.raises(ObservedAtError, match="a calendar date would hide"):
            parse_observed_at("2026-09-01")

    def test_the_outcome_echoes_the_instant_in_utc(self) -> None:
        outcome = locate(VERSIONS, parse_observed_at("2026-08-20T02:00:00+02:00"))

        assert outcome.observed_at == "2026-08-20T00:00:00Z"
