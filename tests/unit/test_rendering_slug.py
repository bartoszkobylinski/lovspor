"""Tests for lovspor.rendering.slug."""

import pytest

from lovspor.rendering.slug import _cap_length, _slugify, derive_slug, resolve_collisions


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


def test_derive_slug_collapses_consecutive_hyphens() -> None:
    assert derive_slug("foo---bar", "x", "nl-x") == "foo-bar"


def test_derive_slug_strips_leading_trailing_hyphens() -> None:
    assert derive_slug("...skatt...", "x", "nl-x") == "skatt"
    assert derive_slug("---skatt---", "x", "nl-x") == "skatt"


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


def test_derive_slug_caps_long_title_at_200_utf8_bytes() -> None:
    """Production regression 2026-04-27: an EU-implementation forskrift
    with no short_title and a 290-character title crashed sync with
    ``OSError: File name too long``. Cap at 200 UTF-8 bytes leaves
    headroom under POSIX NAME_MAX (255) for ``.md`` + collision suffix.
    """
    monster_title = (
        "Forskrift om gjennomføring av kommisjonsforordning (EU) "
        "2020/420 av 16. mars 2020 om endring av den tyske språkversjonen "
        "av forordning (EU) 2016/919 om den tekniske spesifikasjonen for "
        "samtrafikkevne for delsystemet styring, kontroll og signal i "
        "Den europeiske unions jernbanesystem"
    )
    slug = derive_slug(None, monster_title, "sf-fallback")
    assert len(slug.encode("utf-8")) <= 200


def test_derive_slug_truncates_capped_slug_at_hyphen_boundary() -> None:
    """A truncated slug should not end mid-word — cut back to the last
    hyphen so the visible filename remains a clean prefix of the title."""
    long = "lang-tittel-" * 50
    slug = derive_slug(None, long, "x")
    assert len(slug.encode("utf-8")) <= 200
    assert "-" in slug
    assert not slug.endswith("lan")
    assert not slug.endswith("titt")


def test_derive_slug_norwegian_chars_counted_as_bytes_not_chars() -> None:
    """``ø`` (and other Norwegian Unicode) is 2 UTF-8 bytes. The cap
    is bytes, not characters, so a 250-character all-``ø`` title still
    fits under the 200-byte budget after truncation."""
    long_norwegian_title = "ø" * 250  # 500 UTF-8 bytes
    slug = derive_slug(None, long_norwegian_title, "x")
    assert len(slug.encode("utf-8")) <= 200


def test_derive_slug_short_title_under_cap_is_unchanged() -> None:
    """Sanity: the cap is not over-eager. Sub-cap slugs pass through."""
    assert derive_slug("Skatteloven", "x", "nl-x") == "skatteloven"
    assert len(b"skatteloven") < 200


def test_derive_slug_exactly_at_byte_cap_is_unchanged() -> None:
    slug = derive_slug("a" * 200, "", "x")

    assert slug == "a" * 200
    assert len(slug.encode("utf-8")) == 200


def test_derive_slug_no_hyphen_in_prefix_falls_back_to_byte_truncation() -> None:
    """Documented behavior: when the byte-truncated prefix has no hyphen
    at position > 0 (theoretical single-token slug ≥ 200 bytes), the
    raw byte-truncated form is returned. Real Lovdata titles always
    contain spaces that become hyphens during slugify, so this branch
    is never hit in production — but the contract must hold."""
    monolithic = "a" * 500
    slug = derive_slug(monolithic, "", "x")
    assert len(slug.encode("utf-8")) == 200
    assert slug == "a" * 200


def test_derive_slug_ignores_split_multibyte_character_at_byte_cap() -> None:
    slug = derive_slug(("a" * 199) + "ø" + "suffix", "", "x")

    assert slug == "a" * 199
    assert len(slug.encode("utf-8")) == 199


def test_derive_slug_keeps_single_long_token_even_when_hyphen_after_cap() -> None:
    slug = derive_slug(("a" * 200) + "-after", "", "x")

    assert slug == "a" * 200


def test_slugify_strips_literal_hyphens_after_replacement() -> None:
    assert _slugify("---alpha---") == "alpha"


def test_cap_length_trims_at_last_literal_hyphen_boundary() -> None:
    slug = ("a" * 150) + "-" + ("b" * 150)

    assert _cap_length(slug) == "a" * 150


def test_cap_length_does_not_trim_for_leading_hyphen_boundary() -> None:
    slug = "-" + ("a" * 250)

    assert _cap_length(slug) == "a" * 199


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
