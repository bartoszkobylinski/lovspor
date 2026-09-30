"""Tests for lovspor.observatory.selection — the path-stem capture filter (#348)."""

import pytest

from lovspor.observatory.selection import REGULATION_PATH_STEMS, selects

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
