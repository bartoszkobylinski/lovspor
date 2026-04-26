"""Tests for lovspor.rendering.slug."""

import pytest

from lovspor.rendering.slug import derive_slug, resolve_collisions


def test_derive_slug_uses_short_title_when_present() -> None:
    assert derive_slug("Skatteloven", "Lov om skatt", "nl-x") == "skatteloven"


def test_derive_slug_lowercases_short_title() -> None:
    assert derive_slug("Polititjenestepliktloven", "x", "nl-x") == "polititjenestepliktloven"


def test_derive_slug_preserves_norwegian_unicode_characters() -> None:
    assert derive_slug("Opplæringslova", "x", "nl-x") == "opplæringslova"
    assert derive_slug("Fjordlova", "x", "nl-x") == "fjordlova"


def test_derive_slug_replaces_spaces_with_hyphens() -> None:
    assert derive_slug("Forbud paa Vimpel-føring", "x", "nl-x") == "forbud-paa-vimpel-føring"


def test_derive_slug_collapses_consecutive_non_slug_chars() -> None:
    assert derive_slug("foo!!!bar???", "x", "nl-x") == "foo-bar"


def test_derive_slug_strips_leading_trailing_hyphens() -> None:
    assert derive_slug("...skatt...", "x", "nl-x") == "skatt"


def test_derive_slug_falls_back_to_title_when_short_title_missing() -> None:
    assert derive_slug(None, "Lov om noe interessant", "nl-x") == "lov-om-noe-interessant"


def test_derive_slug_strips_bracket_content_from_title_fallback() -> None:
    assert derive_slug(None, "Lov om noe (kortlov)", "nl-x") == "lov-om-noe"


def test_derive_slug_strips_square_bracket_content_from_title_fallback() -> None:
    assert (
        derive_slug(None, "Lov om tjenesteplikt [polititjenestepliktloven]", "nl-x")
        == "lov-om-tjenesteplikt"
    )


def test_derive_slug_falls_back_to_doc_id_when_both_titles_missing() -> None:
    assert derive_slug(None, "", "nl-17410217-000") == "nl-17410217-000"


def test_derive_slug_falls_back_to_doc_id_when_titles_have_no_slug_chars() -> None:
    """Pure punctuation title slugifies to empty string; doc_id is the
    last-resort fallback so the slug is always usable as a filename."""
    assert derive_slug("???", "...", "nl-fallback") == "nl-fallback"


def test_derive_slug_handles_empty_short_title_string() -> None:
    """Empty string for short_title falls through to title."""
    assert derive_slug("", "Skatteloven", "nl-x") == "skatteloven"


def test_resolve_collisions_returns_bare_slug_when_unique() -> None:
    result = resolve_collisions({"a": "skatteloven", "b": "opplæringslova"})
    assert result == {"a": "skatteloven", "b": "opplæringslova"}


def test_resolve_collisions_appends_increment_to_second_occurrence() -> None:
    result = resolve_collisions({"nl-1990-x": "duplikat", "nl-2010-x": "duplikat"})
    # Sorted by doc_id: nl-1990-x < nl-2010-x, so the older keeps bare slug.
    assert result == {"nl-1990-x": "duplikat", "nl-2010-x": "duplikat-2"}


def test_resolve_collisions_handles_three_way_collision() -> None:
    result = resolve_collisions(
        {"nl-c": "x", "nl-a": "x", "nl-b": "x"},
    )
    assert result == {"nl-a": "x", "nl-b": "x-2", "nl-c": "x-3"}


def test_resolve_collisions_is_deterministic_regardless_of_input_order() -> None:
    insertion_a = {"nl-2": "duplikat", "nl-1": "duplikat"}
    insertion_b = {"nl-1": "duplikat", "nl-2": "duplikat"}
    assert resolve_collisions(insertion_a) == resolve_collisions(insertion_b)


def test_resolve_collisions_returns_empty_for_empty_input() -> None:
    assert resolve_collisions({}) == {}


def test_resolve_collisions_does_not_affect_unique_slug_when_others_collide() -> None:
    result = resolve_collisions(
        {"nl-1": "shared", "nl-2": "shared", "nl-3": "lonely"},
    )
    assert result == {"nl-1": "shared", "nl-2": "shared-2", "nl-3": "lonely"}


@pytest.mark.parametrize(
    ("short_title", "title", "expected"),
    [
        ("Skatteloven", "Lov om skatt av formue og inntekt (skatteloven)", "skatteloven"),
        ("Opplæringslova", "Lov om grunnskolen", "opplæringslova"),
        ("Polititjenestepliktloven", "Lov [polititjenestepliktloven]", "polititjenestepliktloven"),
        (None, "Lov om noe", "lov-om-noe"),
    ],
)
def test_derive_slug_matrix_of_realistic_lovdata_inputs(
    short_title: str | None,
    title: str,
    expected: str,
) -> None:
    assert derive_slug(short_title, title, "nl-x") == expected
