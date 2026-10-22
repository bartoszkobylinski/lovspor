"""Regression tests for the exact short-name citation contract (#585)."""

import pytest

from lovspor.mcp import _slug_token_in_citation
from lovspor.slug_index import cited_acts, slugs_named


@pytest.mark.parametrize("name", ["SKATTELOVEN", "SKTL."])
def test_short_names_are_case_insensitive(name: str) -> None:
    assert slugs_named(["skatteloven-sktl", "husleieloven-husll"], name) == ["skatteloven-sktl"]


@pytest.mark.parametrize(
    "citation",
    [
        "presskatteloven § 1",
        "skattelovenx § 1",
        "skatteloven-ekstra § 1",
        "presktl. § 1",
        "sktlx. § 1",
        "sktl § 1",
    ],
)
def test_short_name_citations_do_not_resolve_partial_tokens(citation: str) -> None:
    assert cited_acts(["skatteloven-sktl"], citation, _slug_token_in_citation) == ([], None)


def test_repeated_short_name_and_abbreviation_identify_one_act() -> None:
    assert cited_acts(
        ["skatteloven-sktl"],
        "skatteloven (sktl.) § 1, jf. skatteloven",
        _slug_token_in_citation,
    ) == (["skatteloven-sktl"], None)


def test_shared_abbreviation_refuses_both_acts() -> None:
    slugs = ["testloven-abc", "annenloven-abc"]

    assert slugs_named(slugs, "abc.") == slugs
    assert cited_acts(slugs, "abc. § 1", _slug_token_in_citation) == (
        [],
        "ambiguous citation: 'abc.' names 2 acts: testloven-abc, annenloven-abc; "
        "cite the canonical slug",
    )


def test_canonical_slug_disambiguates_a_shared_abbreviation() -> None:
    assert cited_acts(
        ["testloven-abc", "annenloven-abc"],
        "testloven-abc (abc.) § 1",
        _slug_token_in_citation,
    ) == (["testloven-abc"], None)


def test_shared_abbreviation_includes_a_multiply_claimed_slug() -> None:
    assert cited_acts(
        ["testloven-abc"],
        "abc. § 1",
        _slug_token_in_citation,
        claimed=["annenloven-abc"],
    ) == (
        [],
        "ambiguous citation: 'abc.' names 2 acts: testloven-abc, annenloven-abc; "
        "cite the canonical slug",
    )
