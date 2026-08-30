"""Deciding what not to fetch again — and erring toward fetching.

The rule only ever declines work. Every ambiguity resolves the other way,
because re-fetching costs one request while skipping wrongly costs an
observation window that cannot be recovered.
"""

from datetime import UTC, datetime, timedelta

from lovspor.observatory.discovery import Candidate
from lovspor.observatory.freshness import (
    FAILED_RECHECK,
    FAILED_RECHECK_CEILING,
    UNDATED_RECHECK,
    CaptureState,
    FailureHold,
    capture_state,
    collect_capture_state,
    collect_latest_observations,
    failure_backoff,
    is_url_property,
    latest_observations,
    parse_site_lastmod,
    worth_capturing,
)
from lovspor.observatory.model import (
    ArtifactObservation,
    FetchFailure,
    RetrievalProvenance,
    Tombstone,
)

URL = "https://www.baerum.kommune.no/tjenester/forskrift"
#: Fixed so the re-check window is reasoned about, not raced against.
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


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


def _failure(
    when: datetime, url: str = URL, outcome: str = "timeout", authority_id: str = "3201"
) -> FetchFailure:
    return FetchFailure(
        authority_id=authority_id,
        url=url,
        observed_at=when,
        provenance=_provenance(),
        outcome=outcome,
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
        assert worth_capturing(_candidate("2026-08-01"), CaptureState.empty(), NOW) is True

    def test_an_undated_candidate_not_seen_for_a_day_is_worth_capturing(self) -> None:
        """No claim to compare against is not evidence that nothing changed.
        Past the window the page is asked about again, exactly as before."""
        observed = {URL: NOW - UNDATED_RECHECK}

        assert worth_capturing(_candidate(None), CaptureState(observed, {}), NOW) is True

    def test_an_undated_candidate_seen_just_now_is_left_alone(self) -> None:
        """Issue #209. This is the one judgement in the module made from our
        own record rather than the site's claim, and it exists because the
        alternative was re-fetching the page on every pass forever."""
        observed = {URL: NOW - timedelta(minutes=20)}

        assert worth_capturing(_candidate(None), CaptureState(observed, {}), NOW) is False

    def test_an_unreadable_lastmod_is_treated_as_no_claim_at_all(self) -> None:
        """A stamp we cannot read tells us exactly what an absent one does, so
        the two must not end up on different paths."""
        recent = {URL: NOW - timedelta(minutes=20)}
        stale = {URL: NOW - UNDATED_RECHECK}

        assert worth_capturing(_candidate("whenever"), CaptureState(recent, {}), NOW) is False
        assert worth_capturing(_candidate("whenever"), CaptureState(stale, {}), NOW) is True

    def test_an_observation_stamped_ahead_of_the_clock_still_fetches(self) -> None:
        """An age cannot be computed from it, and a clock that disagrees with
        the archive must not be able to hold a page back."""
        observed = {URL: NOW + timedelta(hours=1)}

        assert worth_capturing(_candidate(None), CaptureState(observed, {}), NOW) is True

    def test_a_future_observation_cannot_make_a_dated_candidate_look_unchanged(self) -> None:
        """The clock-safety rule applies even when the site supplies a claim.
        A future archive timestamp cannot prove that an earlier site change
        was observed, so it must resolve toward fetching."""
        observed = {URL: NOW + timedelta(hours=1)}

        assert worth_capturing(_candidate("2026-08-19"), CaptureState(observed, {}), NOW) is True

    def test_an_observation_at_this_very_instant_is_left_alone(self) -> None:
        """The near boundary. Age zero is inside the window, not on the far
        edge of it: a page seen at exactly this moment is the strongest case
        there is for not asking again, and the two boundaries answer opposite
        ways on purpose — nothing has elapsed here, the whole window has
        elapsed there."""
        observed = {URL: NOW}

        assert worth_capturing(_candidate(None), CaptureState(observed, {}), NOW) is False

    def test_the_window_boundary_fetches(self) -> None:
        """Exactly at the window the page is asked about, for the reason every
        tie in this module resolves toward fetching."""
        observed = {URL: NOW - UNDATED_RECHECK}

        assert worth_capturing(_candidate(None), CaptureState(observed, {}), NOW) is True

    def test_a_dated_candidate_ignores_the_window_entirely(self) -> None:
        """The site made a claim, so the claim decides. A page stamped as
        changed after our sighting is fetched however recently we saw it."""
        observed = {URL: NOW - timedelta(minutes=1)}

        assert worth_capturing(_candidate("2099-01-01"), CaptureState(observed, {}), NOW) is True

    def test_a_page_changed_since_we_looked_is_worth_capturing(self) -> None:
        observed = {URL: datetime(2026, 8, 1, tzinfo=UTC)}

        assert worth_capturing(_candidate("2026-08-18"), CaptureState(observed, {}), NOW) is True

    def test_a_page_unchanged_since_we_looked_is_not(self) -> None:
        """The only case safe to skip: the site's own claim predates an
        observation we already hold."""
        observed = {URL: datetime(2026, 8, 19, tzinfo=UTC)}

        assert worth_capturing(_candidate("2026-08-18"), CaptureState(observed, {}), NOW) is False

    def test_an_observation_exactly_at_the_lastmod_still_fetches(self) -> None:
        """Seeing a page at the very moment it is said to have changed does not
        establish which came first. The rule declines work only when it is
        certain, so this fetches."""
        stamp = "2026-08-18T10:00:00Z"
        observed = {URL: datetime(2026, 8, 18, 10, 0, tzinfo=UTC)}

        assert worth_capturing(_candidate(stamp), CaptureState(observed, {}), NOW) is True

    def test_an_observation_within_the_lastmod_day_still_fetches(self) -> None:
        """The bare date resolves to end-of-day, so a morning observation of a
        page stamped that same day does not count as having seen the change."""
        observed = {URL: datetime(2026, 8, 18, 6, 0, tzinfo=UTC)}

        assert worth_capturing(_candidate("2026-08-18"), CaptureState(observed, {}), NOW) is True


class TestCollectingWithoutHoldingTheRecords:
    """Issue #199. The freshness map is what the log is read for, and it is
    smaller than the log by orders of magnitude — 81,408 URLs out of 610,850
    records. The collector folds each record as it streams past, so a caller
    keeps the map rather than the archive."""

    def test_it_folds_the_same_map_the_pull_form_returns(self) -> None:
        early = datetime(2026, 8, 1, tzinfo=UTC)
        late = datetime(2026, 8, 18, tzinfo=UTC)
        records = [_observation(early), _failure(late), _observation(late, "https://other/x")]
        folded: dict[str, datetime] = {}
        collect = collect_latest_observations(folded)

        for record in records:
            collect(record)

        assert folded == latest_observations(records)

    def test_the_latest_sighting_wins_whatever_order_they_arrive_in(self) -> None:
        early = datetime(2026, 8, 1, tzinfo=UTC)
        late = datetime(2026, 8, 18, tzinfo=UTC)
        folded: dict[str, datetime] = {}
        collect = collect_latest_observations(folded)

        collect(_observation(late))
        collect(_observation(early))

        assert folded == {URL: late}

    def test_narrowing_to_one_source_ignores_another_authority(self) -> None:
        """A capture asks about URLs on its own cleared domain, so its own
        records are the only ones that can answer."""
        folded: dict[str, datetime] = {}
        collect = collect_latest_observations(folded, "3201")

        collect(
            _observation(datetime(2026, 8, 18, tzinfo=UTC)).model_copy(
                update={"authority_id": "9999"}
            )
        )

        assert folded == {}

    def test_narrowing_keeps_the_sources_own_records(self) -> None:
        seen = datetime(2026, 8, 18, tzinfo=UTC)
        folded: dict[str, datetime] = {}

        collect_latest_observations(folded, "3201")(_observation(seen))

        assert folded == {URL: seen}

    def test_a_failure_is_still_not_a_sighting_when_narrowed(self) -> None:
        folded: dict[str, datetime] = {}

        collect_latest_observations(folded, "3201")(_failure(datetime(2026, 8, 18, tzinfo=UTC)))

        assert folded == {}


class TestIsUrlProperty:
    """Which failures describe the URL, and which only describe the moment.

    The category is what the backoff keys on, so a misfiled outcome is either
    a URL re-asked forever (issue #204) or a page held back over a bad night.
    """

    def test_a_declined_redirect_is_a_property_of_the_url(self) -> None:
        """The largest deterministic bucket in the fleet sample that motivated
        this, and the one no status code can recognise: the record carries a
        301 or a 302, which are ordinary responses. What makes it repeatable is
        that the target sits outside a domain a human cleared."""
        assert is_url_property("redirect_not_followed") is True

    def test_the_other_named_url_properties(self) -> None:
        assert is_url_property("redirect_limit_exceeded") is True
        assert is_url_property("response_exceeded_max_bytes") is True
        assert is_url_property("robots_disallowed") is True

    def test_a_client_error_is_a_property_of_the_url(self) -> None:
        assert is_url_property("http_400") is True
        assert is_url_property("http_403") is True
        assert is_url_property("http_404") is True
        assert is_url_property("http_410") is True

    def test_a_server_error_is_a_property_of_the_moment(self) -> None:
        assert is_url_property("http_500") is False
        assert is_url_property("http_503") is False

    def test_the_server_saying_not_now_is_not_the_url(self) -> None:
        """408, 425 and 429 are 4xx by number and momentary by meaning. Reading
        them by range would let one throttled minute hold a source's own pages
        back for two days."""
        assert is_url_property("http_408") is False
        assert is_url_property("http_425") is False
        assert is_url_property("http_429") is False

    def test_the_client_error_boundary(self) -> None:
        """399 is not a client error and 499 is; 500 starts the server's own."""
        assert is_url_property("http_399") is False
        assert is_url_property("http_499") is True

    def test_a_transport_failure_is_a_property_of_the_moment(self) -> None:
        assert is_url_property("timeout") is False
        assert is_url_property("transport_error: ConnectError") is False

    def test_an_unrecognised_outcome_is_treated_as_momentary(self) -> None:
        """The safe default. An outcome a future fetcher invents costs requests
        until somebody classifies it, rather than costing observations."""
        assert is_url_property("something_new") is False

    def test_an_http_prefix_with_no_number_is_not_a_status(self) -> None:
        """`http_` is a naming convention, not a guarantee. Reading the suffix
        without checking would raise inside a fold over the whole archive."""
        assert is_url_property("http_teapot") is False
        assert is_url_property("http_") is False


class TestFailureBackoff:
    def test_the_first_failure_waits_one_window(self) -> None:
        assert failure_backoff(1) == FAILED_RECHECK

    def test_the_wait_doubles_with_the_run(self) -> None:
        assert failure_backoff(2) == FAILED_RECHECK * 2

    def test_the_ceiling_holds(self) -> None:
        """A page published after a long absence stays findable. Whatever the
        run behind it, the URL is asked again within two observation windows."""
        assert failure_backoff(10) == FAILED_RECHECK_CEILING
        assert failure_backoff(1000) == FAILED_RECHECK_CEILING

    def test_a_run_of_none_still_waits_a_window(self) -> None:
        """Not reachable from the fold, which never records a zero — a total
        function here is what keeps that true of the fold rather than of luck."""
        assert failure_backoff(0) == FAILED_RECHECK
        assert failure_backoff(-5) == FAILED_RECHECK

    def test_an_implausible_run_does_not_overflow_the_arithmetic(self) -> None:
        """A URL failing every round for years is a bug elsewhere, not an
        exception from the module that is supposed to stop asking it."""
        assert failure_backoff(1_000_000) == FAILED_RECHECK_CEILING


class TestCaptureStateFold:
    def test_a_url_property_failure_starts_a_run(self) -> None:
        when = datetime(2026, 8, 19, tzinfo=UTC)

        state = capture_state([_failure(when, outcome="http_404")])

        assert state.holds == {URL: FailureHold("http_404", 1, when)}
        assert state.observed == {}

    def test_the_same_failure_again_extends_the_run(self) -> None:
        first = datetime(2026, 8, 19, tzinfo=UTC)
        second = datetime(2026, 8, 20, tzinfo=UTC)

        state = capture_state(
            [_failure(first, outcome="http_404"), _failure(second, outcome="http_404")]
        )

        assert state.holds == {URL: FailureHold("http_404", 2, second)}

    def test_a_different_failure_starts_the_run_over(self) -> None:
        """A page that went from 404 to a declined redirect changed. Carrying
        the count across would describe two behaviours as one."""
        first = datetime(2026, 8, 19, tzinfo=UTC)
        second = datetime(2026, 8, 20, tzinfo=UTC)

        state = capture_state(
            [
                _failure(first, outcome="http_404"),
                _failure(second, outcome="redirect_not_followed"),
            ]
        )

        assert state.holds == {URL: FailureHold("redirect_not_followed", 1, second)}

    def test_records_arriving_out_of_order_keep_the_latest_failure(self) -> None:
        early = datetime(2026, 8, 1, tzinfo=UTC)
        late = datetime(2026, 8, 19, tzinfo=UTC)

        state = capture_state(
            [_failure(late, outcome="http_404"), _failure(early, outcome="http_404")]
        )

        assert state.holds[URL].last_failed_at == late

    def test_content_ends_the_run(self) -> None:
        """The URL serves something now, so what it used to refuse with decides
        nothing. Dropped rather than zeroed."""
        state = capture_state(
            [
                _failure(datetime(2026, 8, 18, tzinfo=UTC), outcome="http_404"),
                _observation(datetime(2026, 8, 19, tzinfo=UTC)),
            ]
        )

        assert state.holds == {}
        assert state.observed == {URL: datetime(2026, 8, 19, tzinfo=UTC)}

    def test_a_momentary_failure_neither_starts_nor_ends_a_run(self) -> None:
        """A timeout says nothing about the URL, so it must not create a hold —
        and must not reset one either, or a flaky night would restart the wait
        on a page that has 404'd for a week."""
        first = datetime(2026, 8, 18, tzinfo=UTC)

        state = capture_state(
            [
                _failure(first, outcome="http_404"),
                _failure(datetime(2026, 8, 19, tzinfo=UTC), outcome="timeout"),
            ]
        )

        assert state.holds == {URL: FailureHold("http_404", 1, first)}

    def test_only_a_momentary_failure_leaves_nothing_held(self) -> None:
        state = capture_state([_failure(datetime(2026, 8, 18, tzinfo=UTC))])

        assert state.holds == {}

    def test_each_url_keeps_its_own_run(self) -> None:
        other = "https://www.baerum.kommune.no/annet"
        when = datetime(2026, 8, 19, tzinfo=UTC)

        state = capture_state(
            [_failure(when, outcome="http_404"), _failure(when, other, outcome="http_404")]
        )

        assert set(state.holds) == {URL, other}

    def test_a_tombstone_is_folded_past(self) -> None:
        """It carries no URL at all. The fold must read it and move on rather
        than reaching for a field that is not there."""
        state = capture_state(
            [
                Tombstone(
                    sha256="b" * 64,
                    removed_at=datetime(2026, 8, 19, tzinfo=UTC),
                    basis="test",
                    authorised_by="test",
                )
            ]
        )

        assert state == CaptureState.empty()

    def test_narrowing_ignores_another_sources_failure(self) -> None:
        state = CaptureState.empty()

        collect_capture_state(state, "3201")(
            _failure(datetime(2026, 8, 19, tzinfo=UTC), outcome="http_404", authority_id="9999")
        )

        assert state.holds == {}

    def test_narrowing_keeps_this_sources_failure(self) -> None:
        when = datetime(2026, 8, 19, tzinfo=UTC)
        state = CaptureState.empty()

        collect_capture_state(state, "3201")(_failure(when, outcome="http_404"))

        assert state.holds == {URL: FailureHold("http_404", 1, when)}

    def test_pull_fold_applies_the_authority_filter(self) -> None:
        """The convenience wrapper must preserve the collector's narrowing."""
        when = datetime(2026, 8, 19, tzinfo=UTC)

        state = capture_state(
            [
                _failure(when, outcome="http_404"),
                _failure(when, outcome="http_404", authority_id="9999"),
            ],
            authority_id="3201",
        )

        assert state.holds == {URL: FailureHold("http_404", 1, when)}

    def test_narrowing_ignores_another_sources_content(self) -> None:
        """A hold belongs to the source that recorded the failures. Another
        source's artifact for the same URL must not clear it, for the same
        reason its sighting does not count as ours."""
        held = datetime(2026, 8, 18, tzinfo=UTC)
        state = CaptureState.empty()
        collect = collect_capture_state(state, "3201")

        collect(_failure(held, outcome="http_404"))
        collect(
            _observation(datetime(2026, 8, 19, tzinfo=UTC)).model_copy(
                update={"authority_id": "9999"}
            )
        )

        assert state.holds == {URL: FailureHold("http_404", 1, held)}

    def test_both_maps_come_from_one_reading(self) -> None:
        """The fold exists to be handed to `scan_into` once. A caller that had
        to read the archive twice would pay the cost of issue #201 twice."""
        seen = datetime(2026, 8, 19, tzinfo=UTC)
        other = "https://www.baerum.kommune.no/annet"

        state = capture_state([_observation(seen), _failure(seen, other, outcome="http_404")])

        assert state.observed == {URL: seen}
        assert state.holds == {other: FailureHold("http_404", 1, seen)}


class TestWorthCapturingAfterFailure:
    """Issue #204: a URL that has never yielded content, judged on its refusals."""

    def test_a_url_that_has_never_failed_is_worth_capturing(self) -> None:
        assert worth_capturing(_candidate(None), CaptureState.empty(), NOW) is True

    def test_a_url_that_just_refused_is_left_alone(self) -> None:
        """The whole of #204 in one assertion: without this the URL is asked
        again on the next round, minutes later, forever."""
        holds = {URL: FailureHold("redirect_not_followed", 1, NOW - timedelta(minutes=20))}

        assert worth_capturing(_candidate(None), CaptureState({}, holds), NOW) is False

    def test_the_window_boundary_asks_again(self) -> None:
        """Exactly at the window the URL is asked about, for the reason every
        tie in this module resolves toward fetching."""
        holds = {URL: FailureHold("http_404", 1, NOW - FAILED_RECHECK)}

        assert worth_capturing(_candidate(None), CaptureState({}, holds), NOW) is True

    def test_a_longer_run_waits_longer(self) -> None:
        """One window has passed, which would have been enough after a single
        failure. After two it is not."""
        holds = {URL: FailureHold("http_404", 2, NOW - FAILED_RECHECK)}

        assert worth_capturing(_candidate(None), CaptureState({}, holds), NOW) is False

    def test_a_longer_run_is_still_asked_at_the_ceiling(self) -> None:
        holds = {URL: FailureHold("http_404", 99, NOW - FAILED_RECHECK_CEILING)}

        assert worth_capturing(_candidate(None), CaptureState({}, holds), NOW) is True

    def test_the_site_saying_the_url_changed_overrides_the_wait(self) -> None:
        """The strongest reason there is to ask again. Without this override a
        page published the day after its 404 would wait out the window while
        the sitemap said, in public, that it was there."""
        holds = {URL: FailureHold("http_404", 5, datetime(2026, 8, 19, tzinfo=UTC))}

        assert worth_capturing(_candidate("2026-08-20"), CaptureState({}, holds), NOW) is True

    def test_a_claim_older_than_the_failure_does_not_override(self) -> None:
        """The stamp was already there when we asked and got nothing. It says
        the URL has not changed since, which is the case the wait is for."""
        holds = {URL: FailureHold("http_404", 1, NOW - timedelta(minutes=20))}

        assert worth_capturing(_candidate("2026-08-01"), CaptureState({}, holds), NOW) is False

    def test_a_claim_exactly_at_the_failure_asks_again(self) -> None:
        """Failing at the very moment the site says the page changed does not
        establish which came first, so it resolves toward fetching."""
        failed_at = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
        holds = {URL: FailureHold("http_404", 1, failed_at)}

        assert (
            worth_capturing(_candidate("2026-08-18T10:00:00Z"), CaptureState({}, holds), NOW)
            is True
        )

    def test_a_failure_stamped_ahead_of_the_clock_asks_again(self) -> None:
        """An age cannot be computed from it, and a clock that disagrees with
        the archive must not be able to hold a URL back."""
        holds = {URL: FailureHold("http_404", 1, NOW + timedelta(hours=1))}

        assert worth_capturing(_candidate(None), CaptureState({}, holds), NOW) is True

    def test_a_failure_stamped_exactly_now_still_obeys_the_backoff(self) -> None:
        """Equal clocks are usable evidence; only a future failure is suspect."""
        holds = {URL: FailureHold("http_404", 1, NOW)}

        assert worth_capturing(_candidate(None), CaptureState({}, holds), NOW) is False

    def test_an_unreadable_claim_leaves_the_wait_in_force(self) -> None:
        """A stamp we cannot read is not a statement that the URL changed."""
        holds = {URL: FailureHold("http_404", 1, NOW - timedelta(minutes=20))}

        assert worth_capturing(_candidate("whenever"), CaptureState({}, holds), NOW) is False

    def test_a_sighting_outranks_a_hold_that_would_fetch(self) -> None:
        """A URL that has served content is judged on that. The hold is about
        whether the URL exists; the sighting is about whether it changed."""
        state = CaptureState(
            {URL: NOW - timedelta(minutes=20)},
            {URL: FailureHold("http_404", 1, NOW - timedelta(days=10))},
        )

        assert worth_capturing(_candidate(None), state, NOW) is False

    def test_a_sighting_outranks_a_hold_that_would_defer(self) -> None:
        """The same precedence in the direction that costs a request rather
        than an observation, so the rule is pinned from both sides."""
        state = CaptureState(
            {URL: NOW - UNDATED_RECHECK},
            {URL: FailureHold("http_404", 1, NOW - timedelta(minutes=20))},
        )

        assert worth_capturing(_candidate(None), state, NOW) is True
