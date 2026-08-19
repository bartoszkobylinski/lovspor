"""Deciding what not to fetch again — and erring toward fetching.

The rule only ever declines work. Every ambiguity resolves the other way,
because re-fetching costs one request while skipping wrongly costs an
observation window that cannot be recovered.
"""

from datetime import UTC, datetime

from lovspor.observatory.discovery import Candidate
from lovspor.observatory.freshness import (
    latest_observations,
    parse_site_lastmod,
    worth_capturing,
)
from lovspor.observatory.model import ArtifactObservation, FetchFailure, RetrievalProvenance

URL = "https://www.baerum.kommune.no/tjenester/forskrift"


def _provenance() -> RetrievalProvenance:
    return RetrievalProvenance(
        adapter="http",
        channel="http",
        discovery_method="sitemap",
        user_agent="lovspor-observatory/0.1",
        rate_limit_seconds=7.0,
    )


def _observation(when: datetime, url: str = URL) -> ArtifactObservation:
    return ArtifactObservation(
        authority_id="3201",
        url=url,
        observed_at=when,
        provenance=_provenance(),
        sha256="a" * 64,
        content_type="text/html",
        http_status=200,
    )


def _failure(when: datetime, url: str = URL) -> FetchFailure:
    return FetchFailure(
        authority_id="3201",
        url=url,
        observed_at=when,
        provenance=_provenance(),
        outcome="timeout",
    )


def _candidate(lastmod: str | None) -> Candidate:
    return Candidate(
        url=URL,
        discovery_method="sitemap",
        found_in="https://www.baerum.kommune.no/sitemap.xml",
        site_reported_lastmod=lastmod,
    )


class TestLatestObservations:
    def test_the_most_recent_sighting_wins(self) -> None:
        early = datetime(2026, 8, 1, tzinfo=UTC)
        late = datetime(2026, 8, 18, tzinfo=UTC)

        assert latest_observations([_observation(late), _observation(early)]) == {URL: late}

    def test_a_failure_is_not_a_sighting(self) -> None:
        """A timeout says nothing about whether the page changed. Counting it
        would let one bad night hide a page for as long as its lastmod held."""
        seen = datetime(2026, 8, 1, tzinfo=UTC)

        result = latest_observations(
            [_observation(seen), _failure(datetime(2026, 8, 18, tzinfo=UTC))]
        )

        assert result == {URL: seen}

    def test_a_failure_does_not_stop_the_scan(self) -> None:
        """Real logs interleave failures with successes. A reader that stopped
        at the first failure would treat everything after it as never seen, and
        re-fetch a whole site on the strength of one timeout."""
        seen = datetime(2026, 8, 5, tzinfo=UTC)

        result = latest_observations(
            [_failure(datetime(2026, 8, 1, tzinfo=UTC)), _observation(seen)]
        )

        assert result == {URL: seen}

    def test_urls_are_tracked_apart(self) -> None:
        other = "https://www.baerum.kommune.no/other"
        first = datetime(2026, 8, 1, tzinfo=UTC)
        second = datetime(2026, 8, 2, tzinfo=UTC)

        result = latest_observations([_observation(first), _observation(second, other)])

        assert result == {URL: first, other: second}

    def test_nothing_observed_is_an_empty_map(self) -> None:
        assert latest_observations([]) == {}


class TestParseSiteLastmod:
    def test_a_bare_date_becomes_the_end_of_that_day(self) -> None:
        """Erring later can only cause a re-fetch. Erring earlier would skip a
        page that changed later the same day."""
        parsed = parse_site_lastmod("2026-08-18")

        assert parsed == datetime(2026, 8, 18, 23, 59, 59, 999999, tzinfo=UTC)

    def test_an_offset_is_respected(self) -> None:
        parsed = parse_site_lastmod("2026-08-18T10:00:00+02:00")

        assert parsed == datetime(2026, 8, 18, 8, 0, tzinfo=UTC)

    def test_a_negative_offset_is_respected(self) -> None:
        parsed = parse_site_lastmod("2026-08-18T10:00:00-05:00")

        assert parsed == datetime(2026, 8, 18, 15, 0, tzinfo=UTC)

    def test_a_timestamp_without_a_zone_is_read_as_utc(self) -> None:
        parsed = parse_site_lastmod("2026-08-18T10:00:00")

        assert parsed == datetime(2026, 8, 18, 10, 0, tzinfo=UTC)

    def test_midnight_with_an_explicit_time_is_left_alone(self) -> None:
        """A stamp that says midnight *and* says it with a time component is a
        moment, not a whole day."""
        parsed = parse_site_lastmod("2026-08-18T00:00:00Z")

        assert parsed == datetime(2026, 8, 18, 0, 0, tzinfo=UTC)

    def test_an_unreadable_stamp_is_none(self) -> None:
        assert parse_site_lastmod("last tuesday") is None
        assert parse_site_lastmod("") is None

    def test_a_non_iso_stamp_like_rss_pubdate_is_unreadable(self) -> None:
        """RSS `pubDate` is RFC 822 (`Wed, 02 Oct 2024 15:00:00 GMT`), not ISO
        8601 — this parser only understands ISO. An RSS-sourced lastmod is
        therefore always unreadable, which means (via worth_capturing) RSS
        candidates are always fetched, never skipped on freshness alone."""
        assert parse_site_lastmod("Wed, 02 Oct 2024 15:00:00 GMT") is None


class TestWorthCapturing:
    def test_a_url_never_observed_is_worth_capturing(self) -> None:
        assert worth_capturing(_candidate("2026-08-01"), {}) is True

    def test_a_candidate_without_a_lastmod_is_worth_capturing(self) -> None:
        """No claim to compare against is not evidence that nothing changed."""
        observed = {URL: datetime(2026, 8, 18, tzinfo=UTC)}

        assert worth_capturing(_candidate(None), observed) is True

    def test_an_unreadable_lastmod_is_worth_capturing(self) -> None:
        observed = {URL: datetime(2026, 8, 18, tzinfo=UTC)}

        assert worth_capturing(_candidate("whenever"), observed) is True

    def test_a_page_changed_since_we_looked_is_worth_capturing(self) -> None:
        observed = {URL: datetime(2026, 8, 1, tzinfo=UTC)}

        assert worth_capturing(_candidate("2026-08-18"), observed) is True

    def test_a_page_unchanged_since_we_looked_is_not(self) -> None:
        """The only case safe to skip: the site's own claim predates an
        observation we already hold."""
        observed = {URL: datetime(2026, 8, 19, tzinfo=UTC)}

        assert worth_capturing(_candidate("2026-08-18"), observed) is False

    def test_an_observation_exactly_at_the_lastmod_still_fetches(self) -> None:
        """Seeing a page at the very moment it is said to have changed does not
        establish which came first. The rule declines work only when it is
        certain, so this fetches."""
        stamp = "2026-08-18T10:00:00Z"
        observed = {URL: datetime(2026, 8, 18, 10, 0, tzinfo=UTC)}

        assert worth_capturing(_candidate(stamp), observed) is True

    def test_an_observation_within_the_lastmod_day_still_fetches(self) -> None:
        """The bare date resolves to end-of-day, so a morning observation of a
        page stamped that same day does not count as having seen the change."""
        observed = {URL: datetime(2026, 8, 18, 6, 0, tzinfo=UTC)}

        assert worth_capturing(_candidate("2026-08-18"), observed) is True
