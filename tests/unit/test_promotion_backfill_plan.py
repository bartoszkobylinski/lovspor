"""``plan_backfill`` on a history and a decision log built as data (ADR-0016 S6).

The CLI tests in ``test_promotion_backfill`` reach these states through the
operator's commands; the ones here pin what the plan says for states those
commands reach only with timing the test cannot choose: an approval given at
the very instant its version was first observed, two standing decisions on
one version's blobs, a held capture whose extractor left no detail.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lovspor.promotion.archive import SourceStatus
from lovspor.promotion.backfill import BackfillPlan, plan_backfill
from lovspor.promotion.decisions import ArtifactKey, Decision, HumanDecision, utc_text
from lovspor.promotion.extract import EXTRACTOR_VERSION
from lovspor.promotion.versions import DerivedVersion, PrimaryHistory, Sighting
from tests.unit.promotion_cli_fixtures import AUTHORITY, PAGE_URL, REVIEWER, REVIEWER_ROLE

SEEN = datetime(2026, 8, 19, 15, 17, 23, tzinfo=UTC)
HOUR = timedelta(hours=1)
TEXT = "c" * 64
OTHER_TEXT = "d" * 64
LOW_BLOB = "1" * 64
HIGH_BLOB = "2" * 64


def _sighting(sha256: str, at: datetime, content_hash: str | None = TEXT) -> Sighting:
    return Sighting(observed_at=at, sha256=sha256, content_hash=content_hash)


def _version(number: int, *sightings: Sighting) -> DerivedVersion:
    return DerivedVersion(version=number, primary_url=PAGE_URL, sightings=sightings)


def _history(*versions: DerivedVersion) -> PrimaryHistory:
    status = SourceStatus(outcome="retrieved", http_status=200, observed_at=utc_text(SEEN))
    return PrimaryHistory(
        primary_url=PAGE_URL, versions=versions, excluded=(), source_status=status
    )


def _decision(
    sha256: str, at: datetime, decision: Decision = Decision.APPROVE, text: str = TEXT
) -> HumanDecision:
    approval = decision is Decision.APPROVE
    return HumanDecision(
        artifact=ArtifactKey(authority_id=AUTHORITY, sha256=sha256, source_url=PAGE_URL),
        decision=decision,
        decided_by=REVIEWER,
        reviewer_role=REVIEWER_ROLE,
        decided_at=at,
        reason="Vedtatt forskrift; kilden lest i sin helhet.",
        content_hash=text if approval else None,
        extractor_version=EXTRACTOR_VERSION if approval else None,
    )


def _holds(plan: BackfillPlan) -> list[tuple[int, str, str]]:
    return [(h.version, h.reason, h.detail) for h in plan.holds]


def _two_blob_history() -> PrimaryHistory:
    return _history(_version(1, _sighting(LOW_BLOB, SEEN), _sighting(HIGH_BLOB, SEEN + HOUR)))


class TestExtractionHolds:
    def test_the_extractors_detail_is_the_holds_detail(self) -> None:
        held = Sighting(
            observed_at=SEEN,
            sha256=LOW_BLOB,
            content_hash=None,
            held_reason="empty_text",
            held_detail="no text in the body",
        )

        plan = plan_backfill(_history(_version(1, held)), [])

        assert _holds(plan) == [(1, "extraction:empty_text", "no text in the body")]

    def test_a_hold_without_a_detail_is_reported_with_an_empty_one(self) -> None:
        held = Sighting(
            observed_at=SEEN, sha256=LOW_BLOB, content_hash=None, held_reason="unreadable"
        )

        plan = plan_backfill(_history(_version(1, held)), [])

        assert _holds(plan) == [(1, "extraction:unreadable", "")]


class TestEveryVersionAfterAHoldIsCounted:
    def test_each_later_version_is_held_after_the_first_hold(self) -> None:
        texts = (TEXT, OTHER_TEXT, TEXT, OTHER_TEXT)
        versions = [
            _version(n, _sighting(LOW_BLOB, SEEN + n * HOUR, text))
            for n, text in enumerate(texts, start=1)
        ]

        plan = plan_backfill(_history(*versions), [])

        detail = f"no decision on {LOW_BLOB}; run `lovspor promote approve`"
        earlier = "v1 is held (not_approved)"
        assert _holds(plan) == [
            (1, "not_approved", detail),
            (2, "after_earlier_hold", earlier),
            (3, "after_earlier_hold", earlier),
            (4, "after_earlier_hold", earlier),
        ]
        assert plan.holds_by_reason() == {"after_earlier_hold": 3, "not_approved": 1}


class TestStandingDecisions:
    def test_an_approval_given_the_instant_the_version_was_first_observed_covers_it(
        self,
    ) -> None:
        approval = _decision(LOW_BLOB, SEEN)

        plan = plan_backfill(_history(_version(1, _sighting(LOW_BLOB, SEEN))), [approval])

        assert plan.holds == ()
        assert [a.decision for a in plan.approved] == [approval]

    @pytest.mark.parametrize("latest_blob", [LOW_BLOB, HIGH_BLOB])
    def test_of_two_fitting_approvals_the_latest_is_the_one_relied_on(
        self, latest_blob: str
    ) -> None:
        earliest_blob = HIGH_BLOB if latest_blob == LOW_BLOB else LOW_BLOB
        earlier = _decision(earliest_blob, SEEN + 2 * HOUR)
        later = _decision(latest_blob, SEEN + 3 * HOUR)

        plan = plan_backfill(_two_blob_history(), [earlier, later])

        assert [a.decision for a in plan.approved] == [later]
        assert plan.through == SEEN + 3 * HOUR

    @pytest.mark.parametrize("latest_blob", [LOW_BLOB, HIGH_BLOB])
    def test_of_two_stale_approvals_the_latest_is_the_one_explained(self, latest_blob: str) -> None:
        earliest_blob = HIGH_BLOB if latest_blob == LOW_BLOB else LOW_BLOB
        earlier = _decision(earliest_blob, SEEN - 2 * HOUR)
        later = _decision(latest_blob, SEEN - HOUR)

        plan = plan_backfill(_two_blob_history(), [earlier, later])

        detail = f"approved at {utc_text(SEEN - HOUR)}, before this version was first observed"
        assert _holds(plan) == [(1, "approval_stale", detail)]

    def test_a_rejection_is_explained_by_what_was_decided_on_which_blob_and_when(
        self,
    ) -> None:
        rejection = _decision(LOW_BLOB, SEEN + HOUR, Decision.REJECT)

        plan = plan_backfill(_history(_version(1, _sighting(LOW_BLOB, SEEN))), [rejection])

        detail = f"reject of {LOW_BLOB} at 2026-08-19T16:17:23Z"
        assert _holds(plan) == [(1, "rejected", detail)]
