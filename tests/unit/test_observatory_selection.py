"""Tests for lovspor.observatory.selection — the path-stem capture filter (#348)."""

import pytest

from lovspor.observatory.discovery import Candidate, DiscoveryResult, SkippedLink
from lovspor.observatory.listing import LISTING_METHOD
from lovspor.observatory.selection import (
    ENV_CAPTURE_SELECTION,
    REGULATION_PATH_STEMS,
    choose,
    selection_enabled,
    selects,
)

OWNER_DECIDED_STEMS = [
    "forskrift",
    "reglement",
    "vedtekt",
    "kunngjor",
    "kunngjør",
    "kunngjering",
    "kunngjoering",
    "planbestemmels",
    "lokal-lov",
    "regelverk",
    "retningslinj",
    "regulativ",
    "lover-og-regler",
    "lover_og_regler",
    "kommunale-regler",
    "skoleregler",
    "skulereglar",
    "ordensreglar",
    "foresegn",
    "lovverk",
    "hoyring",
    "hoering",
    "horing",
]

BASE = "https://www.kommune.example"


class TestStemSet:
    def test_the_stems_are_exactly_the_owner_decision_of_2026_09_19(self) -> None:
        assert tuple(OWNER_DECIDED_STEMS) == REGULATION_PATH_STEMS
        assert len(REGULATION_PATH_STEMS) == 23

    def test_every_stem_is_already_lowercase(self) -> None:
        assert all(stem == stem.lower() for stem in REGULATION_PATH_STEMS)

    def test_no_stem_contains_another(self) -> None:
        # A stem inside another stem is dead weight under substring matching.
        for outer in REGULATION_PATH_STEMS:
            for inner in REGULATION_PATH_STEMS:
                assert outer == inner or inner not in outer

    def test_politivedtekt_is_not_listed_because_vedtekt_covers_it(self) -> None:
        assert "politivedtekt" not in REGULATION_PATH_STEMS


class TestSelects:
    @pytest.mark.parametrize("stem", OWNER_DECIDED_STEMS)
    def test_every_stem_as_a_bare_segment_is_selected(self, stem: str) -> None:
        assert selects(f"{BASE}/{stem}/")

    @pytest.mark.parametrize("stem", OWNER_DECIDED_STEMS)
    def test_every_stem_inside_a_longer_slug_is_selected(self, stem: str) -> None:
        assert selects(f"{BASE}/politikk/om-{stem}er-for-kommunen/")

    @pytest.mark.parametrize(
        "path",
        [
            "/forskrift-om-gebyrer-etter-plan-og-bygningsloven-2024/",
            "/delegeringsreglement/",
            "/okonomireglement/",
            "/arkiv-planer-kunngjort/",
            "/politivedtekt-for-kommunen/",
            "/gebyrregulativ/",
            "/kunngjeringar/",
        ],
    )
    def test_substring_matching_covers_the_subsumed_vocabulary(self, path: str) -> None:
        assert selects(BASE + path)

    @pytest.mark.parametrize(
        "path",
        [
            "/hoyring-kommuneplan/",
            "/kunngjering/",
            "/skulereglar-for-barneskulen/",
            "/ordensreglar/",
            "/foresegn-til-reguleringsplan/",
        ],
    )
    def test_nynorsk_paths_are_selected(self, path: str) -> None:
        assert selects(BASE + path)

    @pytest.mark.parametrize(
        "url",
        [
            f"{BASE}/Forskrifter/",
            f"{BASE}/POLITIKK/KUNNGJØRINGER/",
            f"{BASE}/Tjenester/Horing-Av-Plan",
        ],
    )
    def test_case_is_ignored(self, url: str) -> None:
        assert selects(url)

    @pytest.mark.parametrize(
        "url",
        [
            f"{BASE}/kunngj%C3%B8ringer/",
            f"{BASE}/KUNNGJ%C3%98RINGER/",
            f"{BASE}/lokal%2Dlov/",
        ],
    )
    def test_the_path_is_percent_decoded_before_matching(self, url: str) -> None:
        assert selects(url)

    @pytest.mark.parametrize(
        "url",
        [
            f"{BASE}/",
            BASE,
            f"{BASE}/tjenester/vann-og-avlop/",
            f"{BASE}/tenester/barnehage/",
            f"{BASE}/politikk/moteplan/",
            f"{BASE}/lover-og-planer/",
            f"{BASE}/kunngj/",
        ],
    )
    def test_paths_naming_no_regulation_are_not_selected(self, url: str) -> None:
        assert not selects(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://forskrift.kommune.example/tjenester/",
            f"{BASE}/sok?q=forskrift",
            f"{BASE}/tjenester/#forskrift",
        ],
    )
    def test_only_the_path_is_matched_not_host_query_or_fragment(self, url: str) -> None:
        assert not selects(url)


SITEMAP = f"{BASE}/sitemap.xml"
LISTING = f"{BASE}/aktuelt/"
REGULATION = f"{BASE}/tjenester/forskrift-om-renovasjon/"
NEWS = f"{BASE}/nyhetsarkiv/2026/ny-lekeplass/"
CLUB = f"{BASE}/kultur-idrett-fritid/lag-og-foreninger/skiklubben/"


def _sitemap_candidate(url: str) -> Candidate:
    return Candidate(url=url, discovery_method="sitemap", found_in=SITEMAP)


def _discovered(*candidates: Candidate, skipped: tuple[SkippedLink, ...] = ()) -> DiscoveryResult:
    return DiscoveryResult(
        authority_id="3401", candidates=candidates, skipped=skipped, documents_read=(SITEMAP,)
    )


class TestSelectionSwitch:
    """Global, behind a flag, off until one measured pass (owner, 2026-09-26)."""

    def test_unset_means_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV_CAPTURE_SELECTION, raising=False)

        assert selection_enabled() is False

    @pytest.mark.parametrize("value", ["1", " 1 ", "1\n"])
    def test_one_switches_it_on(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(ENV_CAPTURE_SELECTION, value)

        assert selection_enabled() is True

    @pytest.mark.parametrize("value", ["", "0", "true", "yes", "on", "11"])
    def test_anything_else_leaves_it_off(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        """One spelling, like the pinned-engine switch beside it: a sweep that
        fetched 5% of a site because someone typed "true" for "false" would be
        a decision nobody made."""
        monkeypatch.setenv(ENV_CAPTURE_SELECTION, value)

        assert selection_enabled() is False

    def test_the_variable_name_is_the_documented_one(self) -> None:
        assert ENV_CAPTURE_SELECTION == "LOVSPOR_OBSERVATORY_CAPTURE_SELECTION"


class TestChoose:
    def test_on_only_regulation_paths_are_chosen_in_proposal_order(self) -> None:
        later = _sitemap_candidate(f"{BASE}/politikk/reglement-for-kommunestyret/")
        result = _discovered(
            _sitemap_candidate(NEWS),
            _sitemap_candidate(REGULATION),
            _sitemap_candidate(CLUB),
            later,
        )

        selection = choose(result, (), enabled=True)

        assert [c.url for c in selection.chosen] == [REGULATION, later.url]
        assert (selection.proposed, selection.matching, selection.unselected) == (4, 2, 2)
        assert selection.enabled is True

    def test_off_every_candidate_is_chosen_and_the_match_is_still_counted(self) -> None:
        """Off is today's behaviour, unchanged. The count is what makes the
        first pass a measurement: it says what selection would have kept."""
        candidates = (_sitemap_candidate(NEWS), _sitemap_candidate(REGULATION))

        selection = choose(_discovered(*candidates), (), enabled=False)

        assert selection.chosen == candidates
        assert (selection.proposed, selection.matching, selection.unselected) == (2, 1, 0)
        assert selection.enabled is False

    def test_nothing_proposed_is_nothing_chosen(self) -> None:
        selection = choose(_discovered(), (LISTING,), enabled=True)

        assert (selection.chosen, selection.proposed, selection.matching) == ((), 0, 0)
        assert selection.unselected == 0

    def test_a_proposal_from_a_registered_listing_bypasses_the_path_rule(self) -> None:
        """#348's proposed shape: a listing is curated by a reviewer, so it is
        not guessing — its links are observed whatever their path names."""
        listed = Candidate(url=NEWS, discovery_method=LISTING_METHOD, found_in=LISTING)

        selection = choose(_discovered(listed, _sitemap_candidate(CLUB)), (LISTING,), enabled=True)

        assert selection.chosen == (listed,)
        assert (selection.matching, selection.unselected) == (1, 1)

    def test_a_listing_link_the_sitemap_proposed_first_still_bypasses(self) -> None:
        """Discovery keeps the first proposal of a URL and files the second as
        a duplicate. Sitemaps are read before listings, so on a source with
        both, the listing's proposal of a page is usually that duplicate —
        and it is still a proposal from a registered listing."""
        first = _sitemap_candidate(NEWS)
        duplicate = SkippedLink(url=NEWS, reason="duplicate_candidate", found_in=LISTING)

        selection = choose(_discovered(first, skipped=(duplicate,)), (LISTING,), enabled=True)

        assert selection.chosen == (first,)

    def test_a_duplicate_from_another_sitemap_rescues_nothing(self) -> None:
        other = f"{BASE}/sitemap-2.xml"
        duplicate = SkippedLink(url=NEWS, reason="duplicate_candidate", found_in=other)

        selection = choose(
            _discovered(_sitemap_candidate(NEWS), skipped=(duplicate,)), (LISTING,), enabled=True
        )

        assert selection.chosen == ()

    def test_a_listing_skip_that_is_not_a_duplicate_rescues_nothing(self) -> None:
        refused = SkippedLink(url=NEWS, reason="off_source_host", found_in=LISTING)

        selection = choose(
            _discovered(_sitemap_candidate(NEWS), skipped=(refused,)), (LISTING,), enabled=True
        )

        assert selection.chosen == ()

    def test_a_page_found_in_an_undeclared_listing_does_not_bypass(self) -> None:
        """Only the register's listings are curated; the method name alone is
        not the reviewer's word."""
        listed = Candidate(url=NEWS, discovery_method="sitemap", found_in=LISTING)

        assert choose(_discovered(listed), (), enabled=True).chosen == ()
