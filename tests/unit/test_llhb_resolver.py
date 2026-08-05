"""Citation resolver: typed verdicts with production validate_citation parity."""

from pathlib import Path

import pytest

from lovspor.llhb.citations import extract_citations
from lovspor.llhb.names import ActNameIndex
from lovspor.llhb.resolver import CitationResolver, ResolutionStatus, ResolvedCitation
from lovspor.mcp import CorpusReader
from tests.unit.llhb_fixtures import build_corpus, standard_corpus


@pytest.fixture
def reader(tmp_path: Path) -> CorpusReader:
    return standard_corpus(tmp_path)


def _resolve_one(reader: CorpusReader, answer: str) -> ResolvedCitation:
    index = ActNameIndex.from_manifest(reader.manifest)
    extraction = extract_citations(answer, index)
    assert len(extraction.citations) == 1, extraction
    return CitationResolver(reader, index).resolve(extraction.citations[0])


def test_valid_citation_resolves_with_heading(reader: CorpusReader) -> None:
    resolved = _resolve_one(reader, "Etter testloven § 1 gjelder dette.")
    assert resolved.status is ResolutionStatus.VALID
    assert resolved.slug == "testloven"
    assert resolved.section_id == "1"
    assert resolved.heading == "§ 1. Formål"


def test_preposition_tail_strips_like_production(reader: CorpusReader) -> None:
    resolved = _resolve_one(reader, "Det følger av § 5-12 i testloven.")
    assert resolved.status is ResolutionStatus.VALID
    assert resolved.section_id == "5-12"


def test_genuine_i_suffix_section_resolves(reader: CorpusReader) -> None:
    resolved = _resolve_one(reader, "Se § 33 i testloven om spesialregelen.")
    # Longest read finds the real "33 i" section — no tail strip needed.
    assert resolved.status is ResolutionStatus.VALID
    assert resolved.section_id == "33 i"


def test_nonexistent_section_fails_closed(reader: CorpusReader) -> None:
    resolved = _resolve_one(reader, "Etter testloven § 15-99 kan avtalen heves.")
    assert resolved.status is ResolutionStatus.NONEXISTENT_SECTION
    assert resolved.slug == "testloven"
    assert resolved.reason is not None and "15-99" in resolved.reason


def test_duplicate_section_id_is_ambiguous_occurrence(reader: CorpusReader) -> None:
    resolved = _resolve_one(reader, "Etter dobbeltloven § 6-2 gjelder dette.")
    assert resolved.status is ResolutionStatus.AMBIGUOUS_OCCURRENCE
    assert resolved.slug == "dobbeltloven"


def test_missing_act_never_resolves(reader: CorpusReader) -> None:
    resolved = _resolve_one(reader, "Det følger av § 1 at dette gjelder.")
    assert resolved.status is ResolutionStatus.MISSING_ACT
    assert resolved.slug is None


def test_unknown_act_via_abbreviation(reader: CorpusReader) -> None:
    # "tvl." expands to tvisteloven, which this corpus does not contain.
    resolved = _resolve_one(reader, "Kravet følger av tvl. § 1-3 om søksmål.")
    assert resolved.status is ResolutionStatus.UNKNOWN_ACT


def test_ambiguous_act_name_lists_candidates(tmp_path: Path) -> None:
    reader = build_corpus(
        tmp_path,
        {
            "a-loven": ("Lov om A (samleloven)", "## § 1. En\n\nTekst.\n"),
            "b-loven": ("Lov om B (samleloven)", "## § 1. To\n\nTekst.\n"),
        },
    )
    index = ActNameIndex.from_manifest(reader.manifest)
    extraction = extract_citations("Etter samleloven § 1 gjelder dette.", index)
    resolved = CitationResolver(reader, index).resolve(extraction.citations[0])
    assert resolved.status is ResolutionStatus.AMBIGUOUS_ACT
    assert resolved.reason is not None
    assert "a-loven" in resolved.reason and "b-loven" in resolved.reason


def test_repealed_only_act_name(tmp_path: Path) -> None:
    reader = build_corpus(
        tmp_path,
        {"nyloven": ("Lov om nytt (nyloven)", "## § 1. En\n\nTekst.\n")},
        removed={"gammelloven": "Lov om gammelt (gammelloven)"},
    )
    index = ActNameIndex.from_manifest(reader.manifest)
    extraction = extract_citations("Etter gammelloven § 3 gjelder dette.", index)
    resolved = CitationResolver(reader, index).resolve(extraction.citations[0])
    assert resolved.status is ResolutionStatus.REPEALED_ACT
    assert resolved.slug == "gammelloven"


def test_validity_parity_with_production_validate_citation(reader: CorpusReader) -> None:
    """The resolver's VALID verdict must equal validate_citation's, case by case."""
    index = ActNameIndex.from_manifest(reader.manifest)
    resolver = CitationResolver(reader, index)
    cases = [
        "testloven § 1",
        "testloven § 5-12",
        "§ 5-12 i testloven",
        "§ 33 i testloven",
        "testloven § 15-99",
        "dobbeltloven § 6-2",
    ]
    for text in cases:
        extraction = extract_citations(f"Se {text}.", index)
        assert len(extraction.citations) == 1, text
        resolved = resolver.resolve(extraction.citations[0])
        production = reader.validate_citation(text)
        assert (resolved.status is ResolutionStatus.VALID) == production["valid"], text
        if resolved.status is ResolutionStatus.VALID:
            assert resolved.slug == production["slug"], text
            assert resolved.section_id == production["section_id"], text
