"""Adversarial guards against false-passing mutations in the LLHB oracle.

Every test here targets a specific dangerous mutation class from the
Stage 2 review list: a bug that would make the benchmark *pass* things
it must fail. These are the mutations that would silently corrupt
published numbers, so each one is pinned by an explicit test.
"""

from pathlib import Path
from typing import Any

import pytest

from lovspor.llhb.citations import extract_citations
from lovspor.llhb.names import ActNameIndex
from lovspor.llhb.quotes import QuoteRef, QuoteStatus, materialize_quote, quote_sha256
from lovspor.llhb.resolver import CitationResolver, ResolutionStatus
from lovspor.llhb.schema import canonical_case_line, load_schema
from lovspor.llhb.stances import Stance, classify_stances
from lovspor.llhb.validation import CandidateValidator, IssueSeverity
from lovspor.mcp import CorpusReader
from tests.unit.llhb_fixtures import build_corpus, standard_corpus
from tests.unit.test_llhb_schema import SCHEMA_PATH, make_case

_INDEX = ActNameIndex.from_pairs([("testloven", "testloven")])


@pytest.fixture
def reader(tmp_path: Path) -> CorpusReader:
    return standard_corpus(tmp_path)


# -- mutation: extractor drops § constructs or act names ------------------


@pytest.mark.parametrize(
    "answer",
    [
        "§",
        "§§",
        "§ ",
        "§ og noe mer",
        "§15-7",
        "testloven § 1",
        "se §§ 4 til 8 og 12",
        "tekst § 5 tekst § 6 tekst §",
    ],
)
def test_every_paragraph_mark_is_accounted_for(answer: str) -> None:
    """Invariant: a ``§`` in the answer appears in a citation span or in the
    unresolved bucket — silent drops are the deadliest extractor mutation."""
    result = extract_citations(answer, _INDEX)
    spans = [(c.start, c.end) for c in result.citations]
    spans += [(u.start, u.end) for u in result.unresolved]
    for offset, char in enumerate(answer):
        if char == "§":
            assert any(s <= offset < e for s, e in spans), (answer, offset)


def test_adjacent_act_name_is_never_dropped() -> None:
    result = extract_citations("testloven § 1 gjelder.", _INDEX)
    assert result.citations[0].act_key == "testloven"


# -- mutation: resolver fails open on act resolution ----------------------


def test_two_current_acts_sharing_a_name_never_resolve_valid(tmp_path: Path) -> None:
    reader = build_corpus(
        tmp_path,
        {
            "a-loven": ("Lov om A (fellesloven)", "## § 1. En\n\nTekst.\n"),
            "b-loven": ("Lov om B (fellesloven)", "## § 1. To\n\nTekst.\n"),
        },
    )
    index = ActNameIndex.from_manifest(reader.manifest)
    extraction = extract_citations("Etter fellesloven § 1 gjelder dette.", index)
    resolved = CitationResolver(reader, index).resolve(extraction.citations[0])
    # Both candidate acts HAVE a § 1 — auto-picking either would validate.
    assert resolved.status is ResolutionStatus.AMBIGUOUS_ACT
    assert resolved.section_id is None


def test_missing_act_is_never_valid_even_when_id_exists_somewhere(
    reader: CorpusReader,
) -> None:
    index = ActNameIndex.from_manifest(reader.manifest)
    extraction = extract_citations("Det følger av § 1 at dette gjelder.", index)
    resolved = CitationResolver(reader, index).resolve(extraction.citations[0])
    # testloven HAS a § 1; binding-free citations must still not resolve.
    assert resolved.status is ResolutionStatus.MISSING_ACT


def test_duplicate_occurrence_is_never_auto_selected(reader: CorpusReader) -> None:
    index = ActNameIndex.from_manifest(reader.manifest)
    extraction = extract_citations("Etter dobbeltloven § 6-2 gjelder dette.", index)
    resolved = CitationResolver(reader, index).resolve(extraction.citations[0])
    assert resolved.status is ResolutionStatus.AMBIGUOUS_OCCURRENCE


# -- mutation: stance classifier flips denials into assertions ------------


@pytest.mark.parametrize(
    "answer",
    [
        "testloven § 15-99 finnes ikke.",
        "testloven § 15-99 eksisterer ikke i loven.",
        "Bestemmelsen testloven § 15-99 er opphevet.",
        "testloven § 15-99 stemmer ikke.",
    ],
)
def test_denied_citations_are_never_asserted(answer: str) -> None:
    result = extract_citations(answer, _INDEX)
    assert [c.stance for c in result.citations] == [Stance.DENIED]


def test_unattachable_denial_falls_to_unresolved_never_asserted() -> None:
    stances = classify_stances("Det finnes ikke noe slikt i § 5.", [(24, 27)])
    assert stances == [Stance.UNRESOLVED]


# -- mutation: quote hash comparison weakened -----------------------------


def test_any_single_hex_digit_corruption_fails_the_hash(reader: CorpusReader) -> None:
    text = "formålet med loven"
    good = quote_sha256(text)
    section = reader.get_section("testloven", "1")
    assert text in str(section["body"]).lower()
    for position in (0, 31, 63):
        corrupted = good[:position] + ("0" if good[position] != "0" else "1") + good[position + 1 :]
        ref = QuoteRef(
            slug="testloven",
            section_id="1",
            char_span=(0, len(text)),
            sha256_normalized=corrupted,
        )
        result = materialize_quote(reader, ref)
        assert result.status is not QuoteStatus.OK


# -- mutation: candidate validator fails open -----------------------------


def test_removing_any_required_field_always_fails_validation(
    reader: CorpusReader,
) -> None:
    schema = load_schema(SCHEMA_PATH)
    validator = CandidateValidator(reader, schema)
    required = schema["required"]
    assert len(required) >= 10
    for field in required:
        case: dict[str, Any] = make_case()
        del case[field]
        issues = validator.validate_case(case)
        assert issues, f"validator passed a case missing required field {field!r}"
        assert all(issue.severity is IssueSeverity.ERROR for issue in issues)


def test_c3_sweep_no_existing_section_ever_passes_as_trap(reader: CorpusReader) -> None:
    validator = CandidateValidator(reader, load_schema(SCHEMA_PATH))
    existing = [row["section_id"] for row in reader.list_sections("testloven")]
    assert existing
    for section_id in existing:
        case = make_case(
            case_id="llhb-v1-C3-001",
            category="C3",
            expected_behaviour="reject_citation",
            expected_act_slug=None,
            expected_section_id=None,
            claimed_act_slug="testloven",
            claimed_section_id=section_id,
            citation_exists=False,
        )
        issues = validator.validate_case(case)
        assert issues, f"existing section {section_id!r} accepted as a non-existence trap"


# -- mutation: canonicalizer becomes order/locale dependent ---------------


def test_canonical_line_ignores_key_insertion_order() -> None:
    forward = {"a": 1, "b": "ø", "c": [1, 2]}
    backward = {"c": [1, 2], "b": "ø", "a": 1}
    assert canonical_case_line(forward) == canonical_case_line(backward)


# -- mutation: abbreviation table grows fuzzy -----------------------------


def test_abbreviation_binding_requires_exact_table_token() -> None:
    result = extract_citations("Etter amls. § 15-7 gjelder dette.", _INDEX)
    (citation,) = result.citations
    assert citation.abbreviation is None
    assert citation.act_key is None
