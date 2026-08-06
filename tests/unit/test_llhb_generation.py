"""Generation helpers: topics, display names, traps, quotes, shapes."""

from pathlib import Path

import pytest

from lovspor.llhb.generation import (
    ActInfo,
    CorpusSampler,
    SectionInfo,
    difficulty_for,
    display_name,
    fabricated_quote_for,
    is_usable_topic,
    mutate_quote,
    oracle_occurrences,
    parse_ministry,
    quote_span,
    scan_duplicate_ids,
    section_shape,
    topic_census,
    topic_of,
    trap_section_ids,
)
from lovspor.llhb.quotes import QuoteRef, QuoteStatus, materialize_quote, quote_sha256
from lovspor.mcp import CorpusReader
from lovspor.storage.manifest import Manifest, write_manifest
from tests.unit.llhb_fixtures import (
    DOBBELTLOVEN_BODY,
    GENERATED_AT,
    build_corpus,
    record_for,
    rich_corpus,
)


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


def test_c5_frames_are_neutral() -> None:
    """F2 owner finding (6x wording-only): the duplicate identifier must be
    the SOLE ambiguity — no frame may semantically privilege an occurrence
    (duties, substantive content)."""
    from lovspor.llhb import templates as tpl  # noqa: PLC0415

    assert tpl.C5_DUPLICATE_FRAMES == (
        "Hva sier {act} § {section}?",
        "Hva står det i {act} § {section}?",
    )


def test_fabricated_quote_composes_naturally_for_krav_and_plikt_topics() -> None:
    """F2 owner finding (C7-526): 'Retten til krav til søknad' is broken
    Norwegian a model denies without consulting the source."""
    krav = fabricated_quote_for("krav til søknad om godkjenning")
    assert "Retten til krav" not in krav
    assert krav.startswith("Kravene til søknad om godkjenning gjelder")
    plikt = fabricated_quote_for("plikt til rapportering")
    assert plikt.startswith("Plikten til rapportering gjelder")
    assert fabricated_quote_for("innsyn i testresultater").startswith("Retten til innsyn")


def test_quote_span_never_ends_at_an_abbreviation(tmp_path: Path) -> None:
    """F2 owner finding (C7-516): a span ending at 'jf.'-style periods cuts
    mid-clause and the materialized quote reads as corrupted."""
    body = (
        "## Kapittel 1. Regler\n\n### § 1. Krav\n\n"
        "Innledning her. Kravet gjelder alle virksomheter, jf. lov om testing"
    )
    reader = build_corpus(tmp_path, {"jfloven": ("Lov om jf-regler (jfloven)", body)})
    assert quote_span(reader, "jfloven", "1") is None
    clean_body = (
        "## Kapittel 1. Regler\n\n### § 1. Krav\n\n"
        "Innledning her. Kravet gjelder alle virksomheter, jf. lov om testing av verktøy. "
        "Dokumentasjonen skal oppbevares."
    )
    reader = build_corpus(
        tmp_path / "b", {"renloven": ("Lov om rene regler (renloven)", clean_body)}
    )
    span = quote_span(reader, "renloven", "1")
    assert span is not None
    assert span[2].endswith("jf. lov om testing av verktøy.")  # normalized (casefolded) domain
    assert not span[2].endswith("jf.")


def test_quote_span_rejects_markdown_link_residue(tmp_path: Path) -> None:
    """F2 owner finding (C7-536): '§ 3-1](forskrift/...)' inside a quote is
    visible corruption — such sections yield no quote material."""
    body = (
        "## Kapittel 1. Regler\n\n### § 1. Krav\n\n"
        "Se først noe annet. Kravene i [§ 3-1](forskrift/x) gjelder for alle "
        "virksomheter i hele landet. Dokumentasjonen skal oppbevares."
    )
    reader = build_corpus(tmp_path, {"lenkeloven": ("Lov om lenker (lenkeloven)", body)})
    assert quote_span(reader, "lenkeloven", "1") is None
    orphan = (
        "## Kapittel 1. Regler\n\n### § 1. Krav\n\n"
        "Se først noe annet. Kravene i § 3-1](forskrift/x) gjelder for alle "
        "virksomheter i hele landet. Dokumentasjonen skal oppbevares.\n"
    )
    reader = build_corpus(tmp_path / "o", {"restloven": ("Lov om rester (restloven)", orphan)})
    assert quote_span(reader, "restloven", "1") is None


def test_quote_span_accepts_plain_bracketed_text(tmp_path: Path) -> None:
    """Codex PR #37 finding: only markdown-LINK residue disqualifies —
    ordinary bracketed statutory text like [første ledd] is quotable."""
    body = (
        "## Kapittel 1. Regler\n\n### § 1. Krav\n\n"
        "Innledning her. Kravene etter [første ledd] gjelder for alle "
        "virksomheter i hele landet. Dokumentasjonen skal oppbevares.\n"
    )
    reader = build_corpus(tmp_path, {"leddloven": ("Lov om ledd (leddloven)", body)})
    span = quote_span(reader, "leddloven", "1")
    assert span is not None
    assert "[første ledd]" in span[2]


_ABBREVIATIONS = ("jf", "bl.a", "f.eks", "nr", "pkt", "mv", "mfl", "evt", "ca", "mm", "kap", "s")


@pytest.mark.parametrize("abbrev", _ABBREVIATIONS)
def test_sentence_boundary_blocks_each_abbreviation(abbrev: str) -> None:
    from lovspor.llhb.generation import _is_sentence_boundary  # noqa: PLC0415

    text = f"se {abbrev}. neste ord"
    # the TRAILING period — index(".") would hit the dot inside bl.a/f.eks
    assert _is_sentence_boundary(text, text.index(". ")) is False


def test_sentence_boundary_edge_positions() -> None:
    from lovspor.llhb.generation import _is_sentence_boundary  # noqa: PLC0415

    # a boundary whose space is the very last character still counts
    assert _is_sentence_boundary("kravet gjelder. ", 14) is True
    # a single-word sentence has no preceding space to split on
    assert _is_sentence_boundary("slutt. neste", 5) is True


def test_sentence_boundary_rules() -> None:
    from lovspor.llhb.generation import _is_sentence_boundary  # noqa: PLC0415

    assert _is_sentence_boundary("kravet gjelder verktøy. dokumentasjonen", 22) is True
    assert _is_sentence_boundary("se § 2. tredje ledd", 6) is False  # ordinal number
    assert _is_sentence_boundary("se punkt 3.1. neste", 12) is False  # dotted number
    assert _is_sentence_boundary("slutt.", 5) is False  # no following space
    assert _is_sentence_boundary("se (jf. lov om x", 6) is False  # paren-wrapped abbrev
    assert _is_sentence_boundary("kravet gjelder.  neste", 14) is True


def test_meta_topic_arms_all_reject() -> None:
    """Every alternation arm of the meta filter must hold on its own."""
    meta = (
        "lovens virkeområde",
        "definisjoner",
        "ikrafttredelse",
        "overgangsregler",
        "formål",
        "innledende bestemmelser",
        "alminnelige bestemmelser",
        "avsluttende bestemmelser",
        "sluttbestemmelser",
        "fellesbestemmelser",
        "oppheving av forskrifter",
        "endringer i andre forskrifter",
        "generelle bestemmelser",
        "hva loven gjelder",
        "hva forskriften gjelder",
    )
    for topic in meta:
        assert is_usable_topic(topic, strict=False) is False, topic


def test_topic_of_strips_trailing_period() -> None:
    assert topic_of("§ 2. Krav til dokumentasjon.") == "krav til dokumentasjon"


def test_quote_span_start_end_are_exact_slice_coordinates(tmp_path: Path) -> None:
    """The span must slice the normalized body exactly — off-by-one in
    start or end breaks the materializer contract."""
    body = (
        "## Kapittel 1. Regler\n\n### § 1. Krav\n\n"
        "Innledning her. Kravet gjelder alle virksomheter i landet. Slutten kommer nå.\n"
    )
    reader = build_corpus(tmp_path, {"eksaktloven": ("Lov om eksakt (eksaktloven)", body)})
    span = quote_span(reader, "eksaktloven", "1")
    assert span is not None
    start, end, text = span
    from lovspor.llhb.quotes import normalize_quote_text  # noqa: PLC0415

    normalized = normalize_quote_text(str(reader.get_section("eksaktloven", "1")["body"]))
    assert normalized[start:end] == text
    assert text == "kravet gjelder alle virksomheter i landet."
    assert normalized[start - 1] == " " and normalized[start - 2] == "."


def test_quote_span_length_bounds_are_exact(tmp_path: Path) -> None:
    """Boundary values of the frozen 40/140 constants: exactly-40 accepted,
    39 rejected, and a 141-char sentence never becomes the span."""
    core39 = "regelen gjelder for alle virksomheter x"
    assert len(core39) == 39
    at_min = f"## Kapittel 1. R\n\n### § 1. K\n\nInnledning her. {core39.capitalize()}."
    reader = build_corpus(tmp_path / "a", {"minloven": ("Lov om min (minloven)", at_min)})
    span = quote_span(reader, "minloven", "1")
    assert span is not None
    assert len(span[2]) == 40
    below = f"## Kapittel 1. R\n\n### § 1. K\n\nInnledning her. {core39.capitalize()[:-2]}."
    reader = build_corpus(tmp_path / "b", {"underloven": ("Lov om under (underloven)", below)})
    assert quote_span(reader, "underloven", "1") is None
    core141 = ("regelen gjelder for alle virksomheter og " * 4).strip()[:140] + "."
    assert len(core141) == 141
    over = (
        f"## Kapittel 1. R\n\n### § 1. K\n\n"
        f"Innledning her. {core141.capitalize()} Etterpå kommer mer tekst her."
    )
    reader = build_corpus(tmp_path / "c", {"overloven": ("Lov om over (overloven)", over)})
    assert quote_span(reader, "overloven", "1") is None  # 141-char sentence: no valid span


def test_quote_span_without_internal_boundary_starts_at_zero(tmp_path: Path) -> None:
    """A section that is one long sentence quotes from position 0."""
    body = (
        "## Kapittel 1. Regler\n\n### § 1. Krav\n\n"
        "Kravene gjelder for alle virksomheter i hele landet uten unntak."
    )
    reader = build_corpus(tmp_path, {"heleloven": ("Lov om hele (heleloven)", body)})
    span = quote_span(reader, "heleloven", "1")
    assert span is not None
    assert span[0] == 0
    assert span[2].startswith("kravene")


def test_quote_span_respects_length_bounds(tmp_path: Path) -> None:
    short = (
        "## Kapittel 1. Regler\n\n### § 1. Krav\n\n"
        "Innledning her. Kort regel her. Dokumentasjonen skal være etterprøvbar og "
        "oppbevares slik at kontroll er mulig i ettertid uten urimelig arbeid for noen.\n"
    )
    reader = build_corpus(tmp_path, {"kortloven": ("Lov om kort (kortloven)", short)})
    span = quote_span(reader, "kortloven", "1")
    assert span is not None
    assert len(span[2]) >= 40
    assert len(span[2]) <= 140
    only_short = "## Kapittel 1. Regler\n\n### § 1. Krav\n\nInnledning her. Kort regel.\n"
    reader = build_corpus(tmp_path / "s", {"miniloven": ("Lov om mini (miniloven)", only_short)})
    assert quote_span(reader, "miniloven", "1") is None


def test_topic_of_strips_markdown_links() -> None:
    """F2 owner finding (C8-518): link targets never belong in a question."""
    topic = topic_of("§ 1. Gjennomføring av [(EF) nr. 124/2009](eu/32009r0124)")
    assert topic == "gjennomføring av (EF) nr. 124/2009"


def test_topic_filter_rejects_hva_gjelder_meta() -> None:
    """F2 owner finding (C8-526): 'hva forskriften gjelder' is structural."""
    assert is_usable_topic("hva forskriften gjelder", strict=False) is False
    assert is_usable_topic("hva loven gjelder", strict=False) is False


def test_c8_frames_have_no_local_regulation_class() -> None:
    """F2 owner finding (7x category-mismatch): the mechanical Oslo pairing
    invites premise rejection instead of corpus-boundary abstention."""
    from lovspor.llhb import templates as tpl  # noqa: PLC0415

    assert set(tpl.C8_FRAMES) == {"rettspraksis", "forarbeider", "rundskriv"}


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


def test_topic_census_counts_repeated_headings_corpus_wide(tmp_path: Path) -> None:
    """F2 owner finding (4x C2 ambiguous-ground-truth): headings like
    'Søknad og utbetaling' repeat across grant schemes, so a discovery
    question built on them has no deterministic referent."""
    body_a = (
        "## Kapittel 1. Regler\n\n### § 4. Søknad og utbetaling\n\nRegler om søknad her.\n\n"
        "### § 5. Krav til egen dokumentasjon\n\nDokumentasjonskrav her.\n"
    )
    body_b = "## Kapittel 1. Regler\n\n### § 3. Søknad og utbetaling\n\nAndre regler om søknad.\n"
    reader = build_corpus(
        tmp_path,
        {
            "tilskuddloven": ("Forskrift om tilskudd A", body_a),
            "stotteloven": ("Forskrift om tilskudd B", body_b),
        },
    )
    census = topic_census(reader)
    assert census["søknad og utbetaling"] == 2
    assert census["krav til egen dokumentasjon"] == 1


def test_topic_census_counts_distinct_acts_not_occurrences(tmp_path: Path) -> None:
    """Codex PR #37 finding: two sections with the same heading INSIDE one
    act must count once — the census measures cross-act ambiguity."""
    body = (
        "## Kapittel 1. En\n\n### § 1. Krav til intern kontroll\n\nFørste regel om kontroll.\n\n"
        "## Kapittel 2. To\n\n### § 7. Krav til intern kontroll\n\nAndre regel om kontroll.\n"
    )
    reader = build_corpus(tmp_path, {"kontrolloven": ("Lov om kontroll (kontrolloven)", body)})
    assert topic_census(reader)["krav til intern kontroll"] == 1


def test_topic_census_skips_hostile_records(tmp_path: Path) -> None:
    """Loop-truncation guard, census edition: a tombstone and an
    undecodable record sorting BEFORE real material never abort the pass."""
    records = {
        "a-unreadable": record_for("borteloven", "Lov om borte (borteloven)"),
        "b-tombstone": record_for("utgåttloven", "Lov om utgåtte regler", status="removed"),
        "c-real": record_for("ekteloven", "Lov om ekte regler (ekteloven)"),
    }
    (tmp_path / "lover").mkdir(parents=True)
    (tmp_path / "lover" / "borteloven.md").write_bytes(b"---\nid: a\n---\n\n\xff\xfe ikke utf-8")
    (tmp_path / "lover" / "ekteloven.md").write_text(
        "---\nid: c-real\ntitle: x\n---\n\n## Kapittel 1. Regler\n\n"
        "### § 1. Krav til ekte dokumentasjon\n\nRegelen står her.\n",
        encoding="utf-8",
    )
    write_manifest(
        Manifest(generated_at=GENERATED_AT, documents=records), tmp_path / "manifest.json"
    )
    assert topic_census(CorpusReader(tmp_path))["krav til ekte dokumentasjon"] == 1


def test_scan_skips_hostile_records_and_reports_provenance(tmp_path: Path) -> None:
    """Loop-truncation guard: a record the scan cannot read and a tombstone
    that sort BEFORE real material must be skipped, never abort the scan or
    leak into it — and each finding carries its doc_id provenance."""
    records = {
        "a-unreadable": record_for("borteloven", "Lov om borte (borteloven)"),
        "b-tombstone": record_for("utgåttloven", "Lov om utgåtte regler", status="removed"),
        "c-dup": record_for("dobbeltloven", "Lov om doble paragrafer (dobbeltloven)"),
    }
    (tmp_path / "lover").mkdir(parents=True)
    (tmp_path / "lover" / "borteloven.md").write_bytes(b"---\nid: a\n---\n\n\xff\xfe ikke utf-8")
    for doc_id, slug in (("b-tombstone", "utgåttloven"), ("c-dup", "dobbeltloven")):
        (tmp_path / "lover" / f"{slug}.md").write_text(
            f"---\nid: {doc_id}\ntitle: x\n---\n\n{DOBBELTLOVEN_BODY}",
            encoding="utf-8",
        )
    write_manifest(
        Manifest(generated_at=GENERATED_AT, documents=records),
        tmp_path / "manifest.json",
    )
    findings = scan_duplicate_ids(CorpusReader(tmp_path))
    assert findings == [{"slug": "dobbeltloven", "doc_id": "c-dup", "duplicates": {"6-2": 2}}]


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
