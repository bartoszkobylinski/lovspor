"""What a fetch outcome means, and what the archive is made of.

Two questions over one vocabulary, with deliberately opposite safe defaults:
an unclassified outcome counts as a lost document, and counts as nothing at
all when deciding whether a URL may be left alone. Getting either default the
other way round is a bug the tests here are meant to catch.
"""

from datetime import UTC, datetime

from lovspor.observatory.model import (
    ArtifactObservation,
    FetchFailure,
    RetrievalProvenance,
    Tombstone,
)
from lovspor.observatory.outcomes import (
    REDIRECT_FOLLOWED,
    ArchiveComposition,
    collect_composition,
    is_redirect_hop,
    is_url_property,
    lost_the_document,
)

URL = "https://www.baerum.kommune.no/tjenester/forskrift"
WHEN = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _provenance() -> RetrievalProvenance:
    return RetrievalProvenance(
        adapter="http",
        channel="http",
        discovery_method="sitemap",
        user_agent="lovspor-observatory/0.1",
        rate_limit_seconds=7.0,
    )


def _artifact() -> ArtifactObservation:
    return ArtifactObservation(
        authority_id="3201",
        url=URL,
        observed_at=WHEN,
        provenance=_provenance(),
        sha256="a" * 64,
        content_type="text/html",
        http_status=200,
    )


def _failure(outcome: str) -> FetchFailure:
    return FetchFailure(
        authority_id="3201",
        url=URL,
        observed_at=WHEN,
        provenance=_provenance(),
        outcome=outcome,
    )


class TestIsRedirectHop:
    def test_a_followed_redirect_is_a_hop(self) -> None:
        assert is_redirect_hop(REDIRECT_FOLLOWED) is True

    def test_a_declined_redirect_is_not(self) -> None:
        """It carries a 301 or a 302 like the followed one, and it is the
        opposite outcome: the fetcher stopped, and no document arrived."""
        assert is_redirect_hop("redirect_not_followed") is False
        assert is_redirect_hop("redirect_limit_exceeded") is False

    def test_nothing_else_is(self) -> None:
        assert is_redirect_hop("http_404") is False
        assert is_redirect_hop("timeout") is False
        assert is_redirect_hop("") is False


class TestLostTheDocument:
    def test_a_followed_redirect_lost_nothing(self) -> None:
        """The whole of issue #188 in one assertion: 316,109 records of the
        current archive, three quarters of everything it calls a failure."""
        assert lost_the_document(REDIRECT_FOLLOWED) is False

    def test_every_other_failure_lost_the_document(self) -> None:
        for outcome in (
            "http_404",
            "http_500",
            "redirect_not_followed",
            "redirect_limit_exceeded",
            "robots_disallowed",
            "response_exceeded_max_bytes",
            "timeout",
            "transport_error: ConnectError",
        ):
            assert lost_the_document(outcome) is True, outcome

    def test_an_unrecognised_outcome_counts_as_a_loss(self) -> None:
        """The opposite default from `is_url_property`, and the reason the two
        are separate functions. A failure rate that quietly omits what it does
        not recognise is the number that gets quoted."""
        assert lost_the_document("something_new") is True


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


class TestTheTwoDefaultsDisagreeOnPurpose:
    def test_an_unknown_outcome_is_a_loss_but_never_a_hold(self) -> None:
        """Erring the same way in both would be wrong in one of them: an
        unclassified failure must stay in the failure count, and must not be
        able to hold a page back."""
        assert lost_the_document("something_new") is True
        assert is_url_property("something_new") is False

    def test_a_followed_hop_is_neither_a_loss_nor_a_hold(self) -> None:
        """A hop must never start a backoff. The fetcher is already asking the
        target, so deferring the requested URL over the hop it took would hold
        back a page that is being fetched at that moment. It reads as False
        today because no rule matches it — this pins it as a decision."""
        assert lost_the_document(REDIRECT_FOLLOWED) is False
        assert is_url_property(REDIRECT_FOLLOWED) is False


class TestArchiveComposition:
    def test_an_empty_archive_reports_no_rates_rather_than_raising(self) -> None:
        """The caller is a report. One that divides by zero tells an operator
        less than one that says nothing was lost out of nothing."""
        empty = ArchiveComposition()

        assert (empty.records, empty.loss_rate, empty.naive_failure_rate) == (0, 0.0, 0.0)

    def test_records_is_every_kind_folded(self) -> None:
        found = ArchiveComposition(artifacts=3, hops=5, lost=2, tombstones=1)

        assert found.records == 11

    def test_the_two_rates_differ_by_exactly_the_hops(self) -> None:
        """The gap between them is issue #188, so it is asserted rather than
        left to be inferred from two independent numbers."""
        found = ArchiveComposition(artifacts=1, hops=7, lost=2)

        assert found.loss_rate == 0.2
        assert found.naive_failure_rate == 0.9

    def test_a_hop_and_a_loss_are_folded_apart(self) -> None:
        found = ArchiveComposition()
        collect = collect_composition(found)

        collect(_artifact())
        collect(_failure(REDIRECT_FOLLOWED))
        collect(_failure("http_404"))

        assert (found.artifacts, found.hops, found.lost) == (1, 1, 1)

    def test_every_failure_is_counted_by_outcome_including_the_hops(self) -> None:
        """The breakdown is the evidence for the headline number, so a hop
        must appear in it rather than be quietly dropped once it stops
        counting as a failure."""
        found = ArchiveComposition()
        collect = collect_composition(found)

        collect(_failure(REDIRECT_FOLLOWED))
        collect(_failure(REDIRECT_FOLLOWED))
        collect(_failure("http_404"))

        assert found.by_outcome == {REDIRECT_FOLLOWED: 2, "http_404": 1}

    def test_a_tombstone_is_its_own_kind(self) -> None:
        """It is neither an observation nor a failure, and folding it into
        either would move a number an audit reads."""
        found = ArchiveComposition()

        collect_composition(found)(
            Tombstone(
                sha256="b" * 64,
                removed_at=WHEN,
                basis="test",
                authorised_by="test",
            )
        )

        assert (found.tombstones, found.artifacts, found.lost, found.hops) == (1, 0, 0, 0)

    def test_an_artifact_is_never_a_failure(self) -> None:
        found = ArchiveComposition()

        collect_composition(found)(_artifact())

        assert (found.artifacts, found.by_outcome) == (1, {})
