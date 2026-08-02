"""The section-heading grammar shared by the MCP parser and the embedder.

Every shape below was taken from the production corpus. A shape this
module fails to match is not a degraded result: the structured
accessors and the embedding index are the server's only two retrieval
paths and both start here, so an unmatched heading is a section no tool
can return.
"""

import pytest

from lovspor.headings import (
    _MAX_BLOCK_ID_CHARS,
    SECTION_HEADING,
    SECTION_ID,
    block_id,
    canonical_section_id,
    parse_section_heading,
    raw_section_id,
)


@pytest.mark.parametrize(
    ("line", "section_id", "title"),
    [
        # shapes the pre-fix adjacent-letter grammar already matched
        ("## § 1. Formål", "1", "Formål"),
        ("## § 14.", "14", None),
        ("## § 13. (Opphevet)", "13", "(Opphevet)"),
        ("### § 5-12. Title", "5-12", "Title"),
        ("### § 5", "5", None),
        ("### § 5a. Bokstavparagraf", "5a", "Bokstavparagraf"),
        ("### § 5-10a. Behandling hos ortoptist", "5-10a", "Behandling hos ortoptist"),
        # shapes it silently dropped: 2 347 headings across 301 documents
        (
            "### § 8-7 a. Oppfølging mv. i regi av Arbeids- og velferdsetaten",
            "8-7 a",
            "Oppfølging mv. i regi av Arbeids- og velferdsetaten",
        ),
        ("### § 35 a", "35 a", None),
        ("### § 21 a. (Opphevet)", "21 a", "(Opphevet)"),
        (
            "### § 2 A-1. Rett til å varsle om kritikkverdige forhold i virksomheten",
            "2 A-1",
            "Rett til å varsle om kritikkverdige forhold i virksomheten",
        ),
        (
            "### § 3-4 A. Skatt på grunnrenteinntekt i hav",
            "3-4 A",
            "Skatt på grunnrenteinntekt i hav",
        ),
        ("###### § 10-4-1. Beløpsgrense for betaling", "10-4-1", "Beløpsgrense for betaling"),
        ("#### § 7-3. Fradrag", "7-3", "Fradrag"),
        # multi-letter adjacent suffixes (sanksjonsforskrift-ukraina)
        (
            "### § 17aa. Forbud mot å tilby lagringskapasitet",
            "17aa",
            "Forbud mot å tilby lagringskapasitet",
        ),
        ("### § 8cd. Forbud mot å koble seg til SPFS", "8cd", "Forbud mot å koble seg til SPFS"),
        ("### § 20-7ca. Minstekrav til summen", "20-7ca", "Minstekrav til summen"),
        # dot sub-numbering
        ("## § 8.1", "8.1", None),
        ("## § 21.1 Straff", "21.1", "Straff"),
        ("## § 9 a.1 Utvidet adgang", "9 a.1", "Utvidet adgang"),
        # title separated by a space alone (byggeforskrift-for-longyearbyen)
        ("### § 2 Plan og bygningslovens anvendelse", "2", "Plan og bygningslovens anvendelse"),
        # letter-led id under a Roman-numbered chapter
        # (forskrift-om-kulturhistoriske-eiendommer, Kapittel X -> § x-1)
        ("### § x-1. Ikraftsetting", "x-1", "Ikraftsetting"),
    ],
)
def test_section_heading_matches_real_corpus_shapes(
    line: str,
    section_id: str,
    title: str | None,
) -> None:
    assert parse_section_heading(line) == (section_id, title)


@pytest.mark.parametrize(
    "line",
    [
        "## Kapittel 1.",
        "### Merknad",
        "### Takster fra 1. juli 2026",
        "# Lov om folketrygd",
        "Vanlig avsnittstekst med § 5-12 nevnt.",
        "##### Vedlegg 1",
    ],
)
def test_section_heading_rejects_non_section_lines(line: str) -> None:
    assert SECTION_HEADING.match(line) is None


def test_raw_section_id_returns_the_id_as_written() -> None:
    assert raw_section_id("### § 8-7 a. Oppfølging") == "8-7 a"


def test_raw_section_id_returns_none_for_non_section() -> None:
    assert raw_section_id("### Merknad") is None


@pytest.mark.parametrize(
    ("written", "canonical"),
    [
        ("5-12", "5-12"),
        ("§ 5-12", "5-12"),
        ("§5-12", "5-12"),
        ("5-12.", "5-12"),
        ("  5-12  ", "5-12"),
        # the corpus writes headings spaced and link targets closed up;
        # both must land on one key or the reference dangles
        ("8-7 a", "8-7a"),
        ("8-7a", "8-7a"),
        ("§ 8-7 a", "8-7a"),
        ("§8-7A", "8-7a"),
        # arbeidsmiljøloven kapittel 2 A: the corpus's own links disagree
        # with each other (§2a-1 alongside §2A-6)
        ("2 A-1", "2a-1"),
        ("2a-1", "2a-1"),
        ("2A-1", "2a-1"),
        ("§ 2 A-1", "2a-1"),
        # a trailing letter is part of the id, not punctuation to strip
        ("5-12X", "5-12x"),
        ("20-7CA", "20-7ca"),
        # letter-led id: the heading writes lowercase x, the chapter Roman X
        ("x-1", "x-1"),
        ("§ X-1", "x-1"),
        ("X-1", "x-1"),
    ],
)
def test_canonical_section_id_folds_every_spelling_to_one_key(
    written: str,
    canonical: str,
) -> None:
    assert canonical_section_id(written) == canonical


@pytest.mark.parametrize(
    "not_an_id",
    ["5-12 ledd 2", "kapittel 5", "femte paragraf", ""],
)
def test_canonical_section_id_leaves_non_ids_alone(not_an_id: str) -> None:
    """A mangled key would fail the lookup with a confusing message; an
    untouched one fails with the available-ids recovery message."""
    assert canonical_section_id(not_an_id) == not_an_id.strip()


@pytest.mark.parametrize("prose", ["5 og", "12 i skatteloven", "5 og 7", "1 første ledd"])
def test_section_id_fullmatch_rejects_legal_prose(prose: str) -> None:
    """Admitting a space before the letter suffix makes the pattern ambiguous
    against ordinary Norwegian (``§ 5 og``, ``§ 12 i skatteloven``) unless it is
    anchored. Anchoring is the contract: ``canonical_section_id`` uses
    ``fullmatch`` and ``SECTION_HEADING`` pins both ends of the line."""
    assert SECTION_ID.fullmatch(prose) is None


@pytest.mark.parametrize("real_id", ["5", "5-12", "8-7 a", "2 A-1", "35 a", "10-4-1", "x-1"])
def test_section_id_fullmatch_accepts_real_ids(real_id: str) -> None:
    assert SECTION_ID.fullmatch(real_id) is not None


@pytest.mark.parametrize(
    "not_an_id",
    [
        # two letters is a word, not an id — "§ og-1" must stay prose
        "og-1",
        # a bare letter, letter-dot, or letter-dash-letter never forms an id:
        # the letter lead is only legal directly before "-<digit>"
        "x",
        "x.",
        "x-a",
        "i-",
    ],
)
def test_section_id_letter_lead_stays_narrow(not_an_id: str) -> None:
    """The single corpus-attested letter-led shape (``x-1``) must not open
    the grammar to arbitrary alphabetic ids. A fabricated section is worse
    than a missed one — see :data:`lovspor.headings._TITLE`."""
    assert SECTION_ID.fullmatch(not_an_id) is None


@pytest.mark.parametrize(
    "heading",
    [
        "### § og-1. Tittel",
        "### § x. Tittel",
        "### § x-a. Tittel",
        "### § xx-1. Tittel",
    ],
)
def test_section_heading_rejects_prose_like_letter_leads(heading: str) -> None:
    assert parse_section_heading(heading) is None


@pytest.mark.parametrize(
    "prose_heading",
    [
        "### § 5 og andre bestemmelser",
        "### § 12 i skatteloven",
        "### § 5 og § 7 oppheves",
        "### § 3 jf. tidligere bestemmelser",
    ],
)
def test_section_heading_does_not_manufacture_a_section_from_prose(prose_heading: str) -> None:
    """Codex review of PR #144. Admitting a title after a bare space — needed for
    the seven real headings like `### § 2 Plan og bygningslovens anvendelse` —
    also matched running prose: `### § 5 og andre bestemmelser` parsed as a real
    § 5 titled "og andre bestemmelser", and `### § 12 i skatteloven` as a § "12 i"
    that exists in no act.

    A fabricated section is strictly worse than a missed one. It hides the content
    block that heading actually names, and it makes a citation to a paragraph
    nobody wrote validate as real."""
    assert parse_section_heading(prose_heading) is None
    assert SECTION_HEADING.match(prose_heading) is None


def test_a_dotless_title_must_be_capitalised_but_a_dotted_one_need_not_be() -> None:
    """The discriminator, stated once. Every one of the seven corpus headings that
    needs the dotless form is capitalised, because Norwegian legal titles are;
    a continuation word in a reference is not. After a dot, the dot is itself the
    evidence and anything may follow."""
    assert parse_section_heading("### § 5 Formål") == ("5", "Formål")
    assert parse_section_heading("### § 5 formål") is None
    assert parse_section_heading("### § 5. formål") == ("5", "formål")


@pytest.mark.parametrize(
    ("heading_text", "expected"),
    [
        ("Takster fra 1. juli 2026", "#takster-fra-1-juli-2026"),
        ("a" * _MAX_BLOCK_ID_CHARS, "#" + "a" * _MAX_BLOCK_ID_CHARS),
        # the 61st character is dropped, the 60th is not
        ("a" * _MAX_BLOCK_ID_CHARS + "b", "#" + "a" * _MAX_BLOCK_ID_CHARS),
        ("a" * (_MAX_BLOCK_ID_CHARS - 1) + "b", "#" + "a" * (_MAX_BLOCK_ID_CHARS - 1) + "b"),
        # truncation landing on a separator must not leave a trailing hyphen
        ("a" * (_MAX_BLOCK_ID_CHARS - 1) + " bcd", "#" + "a" * (_MAX_BLOCK_ID_CHARS - 1)),
    ],
)
def test_block_id_truncates_without_leaving_a_dangling_separator(
    heading_text: str,
    expected: str,
) -> None:
    assert block_id(heading_text) == expected


@pytest.mark.parametrize("unsluggable", ["", "   ", "///", "— —", "###"])
def test_block_id_is_empty_when_the_heading_has_nothing_to_slug(unsluggable: str) -> None:
    """Callers read the empty string as "no block": the heading stays a plain
    boundary, which is the pre-block behaviour."""
    assert block_id(unsluggable) == ""


@pytest.mark.parametrize(
    ("candidate", "matches"),
    [
        ("5-12X", True),
        ("20-7ca", True),
        ("8.1.2", True),
        ("5-12-", False),
        ("-5", False),
        ("5..1", False),
        # class B repair: the letter-led corpus shape is an id since 2026-08-02
        ("x-1", True),
    ],
)
def test_section_id_fullmatch_boundaries(candidate: str, matches: bool) -> None:
    assert (SECTION_ID.fullmatch(candidate) is not None) is matches


def test_block_id_cap_is_sixty_characters() -> None:
    """Pinned as a literal, not via the constant. Every other test here refers
    to _MAX_BLOCK_ID_CHARS symbolically, so re-pointing the constant moves the
    assertion with it and the boundary goes unguarded — mutmut mutant 3844
    (60 -> 61) survived the symbolic tests for exactly that reason."""
    assert _MAX_BLOCK_ID_CHARS == 60
    assert len(block_id("a" * 200)) == 61  # "#" + 60
