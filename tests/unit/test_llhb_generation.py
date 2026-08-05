"""Generation helpers: topics, display names, traps, quotes, shapes."""

from pathlib import Path

import pytest

from lovspor.llhb.generation import (
    ActInfo,
    CorpusSampler,
    SectionInfo,
    difficulty_for,
    display_name,
    mutate_quote,
    oracle_occurrences,
    parse_ministry,
    quote_span,
    scan_duplicate_ids,
    section_shape,
    topic_of,
    trap_section_ids,
)
from lovspor.llhb.quotes import QuoteRef, QuoteStatus, materialize_quote, quote_sha256
from lovspor.mcp import CorpusReader
from tests.unit.llhb_fixtures import build_corpus, record_for, rich_corpus


@pytest.fixture
def reader(tmp_path: Path) -> CorpusReader:
    return rich_corpus(tmp_path)


def test_topic_of_strips_prefix_and_rejects_unusable() -> None:
    assert topic_of("§ 15-7. Vern mot usaklig oppsigelse") == "vern mot usaklig oppsigelse"
    assert topic_of("§ 14.") is None
    assert topic_of("§ 3. (Opphevet)") is None
    assert topic_of("§ 2. Kort") is None


def test_display_name_prefers_law_name_parenthetical() -> None:
    record = record_for("alfaloven", "Lov om alfa-testing av verktøy (alfaloven)")
    assert display_name(record) == "alfaloven"
    bare = record_for("x", "Forskrift om noe helt annet")
    assert display_name(bare) == "Forskrift om noe helt annet"


def test_parse_ministry_reads_front_matter(reader: CorpusReader) -> None:
    assert parse_ministry(reader.get_law("alfaloven")) == "Testdepartementet"
    assert parse_ministry("---\nid: x\n---\n\nbody") is None


def test_difficulty_and_shape_rules_are_fixed() -> None:
    assert [difficulty_for(n) for n in (10, 100, 500)] == ["easy", "medium", "hard"]
    assert [section_shape(s) for s in ("12", "5-12", "5-12a", "8.1")] == [
        "plain",
        "hyphen",
        "letter",
        "dotted",
    ]


def test_trap_ids_are_absent_and_strategy_labelled(reader: CorpusReader) -> None:
    sampler = CorpusSampler(reader)
    act = next(
        a
        for doc_id in sampler.shuffled_current_doc_ids()
        if (a := sampler.act_info(doc_id)) and a.slug == "alfaloven"
    )
    traps = trap_section_ids(act)
    assert traps
    existing = {s.section_id for s in act.sections}
    for strategy, trap in traps:
        assert trap not in existing
        assert strategy in {"adjacent-gap", "chapter-overrun", "letter-suffix", "flat-overrun"}
    assert ("chapter-overrun", "1-3") in traps


def test_trap_adjacent_gap_found_when_sequence_has_hole() -> None:
    act = ActInfo(
        slug="hull-loven",
        doc_id="nl-x",
        doc_type="lov",
        title="Lov om hull (hulloven)",
        display_name="hulloven",
        ministry=None,
        sections=[
            SectionInfo(section_id="1-1", occurrence=1, heading="§ 1-1. En", kind="section"),
            SectionInfo(section_id="1-3", occurrence=1, heading="§ 1-3. Tre", kind="section"),
        ],
    )
    assert ("adjacent-gap", "1-2") in trap_section_ids(act)


def test_quote_span_round_trips_through_materializer(reader: CorpusReader) -> None:
    span = quote_span(reader, "alfaloven", "1-1")
    assert span is not None
    start, end, text = span
    ref = QuoteRef(
        slug="alfaloven",
        section_id="1-1",
        char_span=(start, end),
        sha256_normalized=quote_sha256(text),
    )
    result = materialize_quote(reader, ref)
    assert result.status is QuoteStatus.OK
    assert result.text == text


def test_mutate_quote_changes_text_deterministically() -> None:
    text = "virksomheten skal dokumentere alle resultater"
    mutated = mutate_quote(text)
    assert mutated == "virksomheten kan dokumentere alle resultater"
    assert mutate_quote("ingen mutasjonsord her") is None


def test_mutate_quote_never_touches_the_tail() -> None:
    """RC6: a mutation near the end produces a visibly ragged quote."""
    text = "detta er en lang setning som slutter med at noen skal x"
    assert mutate_quote(text) is None  # only mutation site is within the tail guard


def test_quote_span_ends_at_sentence_boundary(reader: CorpusReader) -> None:
    span = quote_span(reader, "alfaloven", "1-1")
    assert span is not None
    assert span[2].endswith(".")


def test_trap_ids_exclude_near_miss_siblings() -> None:
    """RC7: § 1 is no trap when § 1-1 or § 1a exists."""
    from lovspor.llhb.generation import trap_has_sibling  # noqa: PLC0415

    assert trap_has_sibling({"1-1", "2"}, "1") is True
    assert trap_has_sibling({"1a", "2"}, "1") is True
    assert trap_has_sibling({"12", "2"}, "1") is False
    assert trap_has_sibling({"2", "3"}, "1") is False


def test_topic_filter_rejects_meta_and_short_topics() -> None:
    from lovspor.llhb.generation import is_usable_topic  # noqa: PLC0415

    assert is_usable_topic("lovens virkeområde", strict=False) is False
    assert is_usable_topic("overgangsbestemmelser for gamle avtaler", strict=True) is False
    assert is_usable_topic("hvem kan søke", strict=True) is True
    assert is_usable_topic("krav til dokumentasjon ved testing", strict=True) is True
    assert is_usable_topic("registreringsplikt", strict=True) is False  # too short
    assert is_usable_topic("registreringsplikt", strict=False) is True


def test_scan_finds_real_duplicates_only(reader: CorpusReader) -> None:
    """RC3: the fixture's veileder echo of § 6-2 must not count as occurrence 3."""
    findings = scan_duplicate_ids(reader)
    assert [f["slug"] for f in findings] == ["dobbeltloven"]
    assert findings[0]["duplicates"] == {"6-2": 2}


LAGDELT_BODY = """## Kapittel 1. Krav

### § 2. Krav til utstyr

Utstyret skal være egnet for formålet og vedlikeholdes forsvarlig.

### § 3. Krav til bruk

Bruken skal skje i samsvar med produsentens anvisninger.

## Vedlegg 1. Tekniske krav

### § 2. Tekniske spesifikasjoner

Spesifikasjonene i dette vedlegget gjelder som forskrift.

## Veileder til forskriften

### § 3. Krav til bruk

Kommentar som speiler forskriftens paragraf uten å være en bestemmelse.
"""


def test_oracle_counts_vedlegg_and_ignores_veileder(tmp_path: Path) -> None:
    """RC3 ruling: normative vedlegg duplicates are real ambiguity; a veileder
    echo is commentary and never an occurrence the oracle acknowledges."""
    reader = build_corpus(
        tmp_path,
        {"lagdeltforskriften": ("Forskrift om lagdelte krav (lagdeltforskriften)", LAGDELT_BODY)},
    )
    assert oracle_occurrences(reader, "lagdeltforskriften", "2") == [1, 2]
    assert oracle_occurrences(reader, "lagdeltforskriften", "3") == [1]
    findings = scan_duplicate_ids(reader)
    assert [f["slug"] for f in findings] == ["lagdeltforskriften"]
    assert findings[0]["duplicates"] == {"2": 2}


def test_sampler_is_seed_deterministic(reader: CorpusReader) -> None:
    first = CorpusSampler(reader, 42).shuffled_current_doc_ids()
    second = CorpusSampler(reader, 42).shuffled_current_doc_ids()
    third = CorpusSampler(reader, 43).shuffled_current_doc_ids()
    assert first == second
    assert sorted(first) == sorted(third)
