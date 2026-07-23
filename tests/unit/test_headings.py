"""The section-heading grammar shared by the MCP parser and the embedder.

Every shape below was taken from the production corpus. A shape this
module fails to match is not a degraded result: the structured
accessors and the embedding index are the server's only two retrieval
paths and both start here, so an unmatched heading is a section no tool
can return.
"""

import pytest

from lovspor.headings import (
    SECTION_HEADING,
    SECTION_ID,
    canonical_section_id,
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
    ],
)
def test_section_heading_matches_real_corpus_shapes(
    line: str,
    section_id: str,
    title: str | None,
) -> None:
    match = SECTION_HEADING.match(line)

    assert match is not None
    assert match.group(1) == section_id
    assert match.group(2) == title


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


@pytest.mark.parametrize("real_id", ["5", "5-12", "8-7 a", "2 A-1", "35 a", "10-4-1"])
def test_section_id_fullmatch_accepts_real_ids(real_id: str) -> None:
    assert SECTION_ID.fullmatch(real_id) is not None
