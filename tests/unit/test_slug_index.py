"""Regression tests for the exact short-name citation contract (#585)."""

import json
from pathlib import Path

import pytest

from lovspor.mcp import CorpusReader, _slug_token_in_citation
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


@pytest.mark.parametrize("name", ["lex", "LEX", "lex.", "LEX."])
def test_three_character_name_requires_abbreviation_dot(name: str) -> None:
    expected = ["lex"] if name.endswith(".") else []
    assert slugs_named(["lex"], name) == expected


@pytest.mark.parametrize(
    ("name", "slug"),
    [("LEX.", "lex"), ("lex..", "lex"), ("appendix", "appendix"), ("APPENDIX.", "appendix")],
)
def test_short_name_preserves_terminal_letters_when_stripping_dots(
    name: str,
    slug: str,
) -> None:
    assert slugs_named([slug], name) == [slug]


@pytest.mark.parametrize("name", ["APPENDIXX", "APPENDIXX.", "APPENDIXX..", "appendiXX."])
def test_short_name_keeps_repeated_terminal_x_after_case_normalization(name: str) -> None:
    assert slugs_named(["appendi", "appendix", "appendixx"], name) == ["appendixx"]


@pytest.mark.parametrize(
    "citation",
    [
        "testloven-abc pretestloven-abc",
        "pretestloven-abc testloven-abc pretestloven-abc",
    ],
)
def test_slug_token_finds_valid_occurrence_before_invalid_last(citation: str) -> None:
    assert _slug_token_in_citation("testloven-abc", citation)


@pytest.mark.parametrize(
    ("slug", "citation", "expected"),
    [("a", "aa a", True), ("aaa", "aaaa aaa", True), ("aaa", "aaaa", False)],
)
def test_overlapping_slug_occurrences_respect_word_boundaries(
    slug: str,
    citation: str,
    expected: bool,
) -> None:
    assert _slug_token_in_citation(slug, citation) is expected


def test_citation_checks_adjacent_occurrences_of_manifest_slug(tmp_path: Path) -> None:
    # Manifests accept string slugs without an alphabet restriction. An underscore
    # is a non-word boundary under the citation parser's documented alphabet.
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "generated_at": "2026-05-01T04:00:00Z",
                "documents": {
                    "doc-a": {
                        "doc_type": "lov",
                        "xml_hash": "hash-a",
                        "markdown_path": "lover/_.md",
                        "source_dataset": "gjeldende-lover",
                        "last_seen": "2026-05-01T04:00:00Z",
                        "status": "current",
                        "slug": "_",
                    },
                },
            },
        ),
    )
    verdict = CorpusReader(tmp_path).validate_citation("a__")
    assert verdict["valid"] is True
    assert verdict["slug"] == "_"


@pytest.mark.parametrize(
    ("slug", "citation", "expected"),
    [
        ("", "", True),
        ("", "x", False),
        ("", "x y", False),
        ("lov", "lov", True),
        ("lov", "", False),
        ("lov", "blov lovb lov-x xlov", False),
        ("lov", "blov lovb lov-x xlov lov", True),
        ("lov", "lovlovlov lov", True),
        ("æøå-loven", "xæøå-loven, æøå-loven.", True),
        ("æøå-loven", "xæøå-lovenå", False),
        ("a.b", "a.b", True),
        ("a.b", "axb", False),
    ],
)
def test_slug_token_every_occurrence_is_tried_and_both_ends_are_boundaries(
    slug: str,
    citation: str,
    expected: bool,
) -> None:
    # Guards #674: a bounded iteration must still visit every occurrence,
    # including overlapping ones, and judge both boundaries of each.
    assert _slug_token_in_citation(slug, citation) is expected


def test_slug_token_search_is_bounded_by_the_citation_length() -> None:
    # Guards #674: the search over a citation that repeats a non-token
    # occurrence many times ends after a number of steps proportional to
    # the citation, so a broken advance cannot loop forever.
    citation = "x" + "presskatteloven-sktl " * 2_000
    assert not _slug_token_in_citation("skatteloven-sktl", citation)
    assert _slug_token_in_citation("skatteloven-sktl", citation + "skatteloven-sktl")
