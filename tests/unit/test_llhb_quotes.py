"""Quote-reference materialization: fail-closed on every mismatch class."""

from pathlib import Path

import pytest

from lovspor.llhb.quotes import (
    MaterializedQuote,
    QuoteRef,
    QuoteStatus,
    drift_or_invalid,
    materialize_quote,
    normalize_quote_text,
    quote_sha256,
)
from lovspor.mcp import CorpusReader
from tests.unit.llhb_fixtures import TESTLOVEN_BODY, standard_corpus


@pytest.fixture
def reader(tmp_path: Path) -> CorpusReader:
    return standard_corpus(tmp_path)


def _span_ref(reader: CorpusReader, needle: str) -> QuoteRef:
    """Build a correct reference to ``needle`` inside testloven § 5-12."""
    section = reader.get_section("testloven", "5-12")
    normalized = normalize_quote_text(str(section["body"]))
    start = normalized.find(normalize_quote_text(needle))
    assert start != -1
    end = start + len(normalize_quote_text(needle))
    return QuoteRef(
        slug="testloven",
        section_id="5-12",
        char_span=(start, end),
        sha256_normalized=quote_sha256(normalized[start:end]),
    )


def test_valid_span_reference_materializes(reader: CorpusReader) -> None:
    ref = _span_ref(reader, "fradrag for kostnader")
    result = materialize_quote(reader, ref)
    assert result.status is QuoteStatus.OK
    assert result.text == "fradrag for kostnader"


def test_materialized_quote_passes_production_verify_quote(reader: CorpusReader) -> None:
    ref = _span_ref(reader, "fradrag for kostnader til testing")
    result = materialize_quote(reader, ref)
    assert result.status is QuoteStatus.OK and result.text is not None
    verdict = reader.verify_quote("testloven", "5-12", result.text)
    assert verdict["verified"] is True


def test_whole_section_reference_when_span_omitted(reader: CorpusReader) -> None:
    section = reader.get_section("testloven", "1")
    normalized = normalize_quote_text(str(section["body"]))
    ref = QuoteRef(slug="testloven", section_id="1", sha256_normalized=quote_sha256(normalized))
    result = materialize_quote(reader, ref)
    assert result.status is QuoteStatus.OK
    assert result.text == normalized


def test_wrong_hash_fails_closed(reader: CorpusReader) -> None:
    ref = _span_ref(reader, "fradrag for kostnader")
    tampered = ref.model_copy(update={"sha256_normalized": "0" * 64})
    result = materialize_quote(reader, tampered)
    assert result.status is QuoteStatus.HASH_MISMATCH
    assert result.text is None


def test_out_of_bounds_span_is_invalid_not_clamped(reader: CorpusReader) -> None:
    ref = _span_ref(reader, "fradrag for kostnader")
    broken = ref.model_copy(update={"char_span": (0, 100_000)})
    result = materialize_quote(reader, broken)
    assert result.status is QuoteStatus.SPAN_INVALID


def test_unknown_section_is_not_found(reader: CorpusReader) -> None:
    ref = QuoteRef(slug="testloven", section_id="99", sha256_normalized="0" * 64)
    assert materialize_quote(reader, ref).status is QuoteStatus.NOT_FOUND


def test_duplicate_id_without_occurrence_is_ambiguous(reader: CorpusReader) -> None:
    ref = QuoteRef(slug="dobbeltloven", section_id="6-2", sha256_normalized="0" * 64)
    assert materialize_quote(reader, ref).status is QuoteStatus.AMBIGUOUS


def test_occurrence_pins_one_duplicate_only(reader: CorpusReader) -> None:
    first = normalize_quote_text(str(reader.get_section("dobbeltloven", "6-2", 1)["body"]))
    ref = QuoteRef(
        slug="dobbeltloven",
        section_id="6-2",
        occurrence=2,
        sha256_normalized=quote_sha256(first),
    )
    # Occurrence 2's text hashes differently from occurrence 1's reference:
    # the quote lives in the OTHER duplicate, so this must fail closed.
    assert materialize_quote(reader, ref).status is QuoteStatus.HASH_MISMATCH


def test_source_drift_surfaces_as_hash_mismatch(
    tmp_path: Path,
    reader: CorpusReader,
) -> None:
    ref = _span_ref(reader, "fradrag for kostnader")
    drifted = TESTLOVEN_BODY.replace("fradrag for kostnader", "fradrag for utgifter")
    path = tmp_path / "lover" / "testloven.md"
    path.write_text(
        "---\nid: nl-0\ntitle: Lov om testing av verktøy (testloven)\n---\n\n" + drifted,
        encoding="utf-8",
    )
    fresh = CorpusReader(tmp_path)
    result = materialize_quote(fresh, ref)
    assert result.status is QuoteStatus.HASH_MISMATCH
    assert drift_or_invalid(pin_matches=False) == "source-drift"
    assert drift_or_invalid(pin_matches=True) == "invalid-case-definition"


def test_empty_span_is_invalid(reader: CorpusReader) -> None:
    result = MaterializedQuote(status=QuoteStatus.SPAN_INVALID)
    assert result.text is None
    ref = QuoteRef(
        slug="testloven",
        section_id="1",
        char_span=(3, 3),
        sha256_normalized="0" * 64,
    )
    assert materialize_quote(reader, ref).status is QuoteStatus.SPAN_INVALID
