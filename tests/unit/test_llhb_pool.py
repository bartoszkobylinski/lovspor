"""Pool orchestration: determinism, ids, validation, dedup, leaks, queue."""

from pathlib import Path
from typing import Any

import pytest

from lovspor.llhb import templates as tpl
from lovspor.llhb.corpus_pin import CorpusPin
from lovspor.llhb.generation import scan_duplicate_ids
from lovspor.llhb.pool import (
    GenerationRun,
    PoolConfig,
    PoolResult,
    _build_c5,
    _Builder,
    generate_pool,
)
from lovspor.llhb.quotes import normalize_quote_text
from lovspor.llhb.schema import canonical_jsonl, load_schema, validate_case
from lovspor.mcp import CorpusNotFoundError, CorpusReader
from tests.unit.llhb_fixtures import (
    DOBBELTLOVEN_BODY,
    EKKOLOVEN_BODY,
    GENERATED_AT,
    build_corpus,
    rich_corpus,
)
from tests.unit.test_llhb_schema import SCHEMA_PATH

_PIN_SHA = "a" * 40
_RUN = GenerationRun(
    lovspor_commit="b" * 40,
    created="2026-08-05",
    timestamp="2026-08-05T10:00:00+00:00",
)
_TARGETS = {"C1": 3, "C2": 2, "C3": 3, "C4": 2, "C5": 4, "C6": 2, "C7": 3, "C8": 2}


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> PoolResult:
    reader = rich_corpus(tmp_path_factory.mktemp("corpus"))
    return _generate(reader)


def _generate(reader: CorpusReader) -> PoolResult:
    pin = CorpusPin(lovverk_commit=_PIN_SHA, manifest_generated_at=GENERATED_AT)
    config = PoolConfig(
        schema_path=SCHEMA_PATH,
        targets=_TARGETS,
        inventory_size=10,
        per_act_total_cap=20,
    )
    return generate_pool(reader, pin, config, _RUN)


def test_generation_is_deterministic(tmp_path: Path, result: PoolResult) -> None:
    again = _generate(rich_corpus(tmp_path))
    assert canonical_jsonl(result.candidates) == canonical_jsonl(again.candidates)
    assert result.ledger == again.ledger
    assert result.dedup_report == again.dedup_report


def test_all_candidates_valid_and_none_rejected(result: PoolResult) -> None:
    assert result.rejected == []
    assert all(entry["status"] == "pass" for entry in result.ledger)


def test_schema_validates_every_candidate(result: PoolResult) -> None:
    schema = load_schema(SCHEMA_PATH)
    for case in result.candidates:
        assert validate_case(case, schema) == [], case["case_id"]


def test_targets_hit_where_material_allows(result: PoolResult) -> None:
    counts = result.distribution["by_category"]
    assert counts["C1"] == 3
    assert counts["C3"] == 3
    assert counts["C7"] == 3
    # C5 target is 4 but the REAL duplicate population is one document
    # (tombstones no longer qualify — RC1): shortfall reported, never faked.
    assert counts["C5"] == 1
    assert result.generation_manifest["emitted_by_category"]["C5"] == 1


def test_c5_v2_encodes_all_oracle_occurrences(result: PoolResult) -> None:
    (c5,) = [c for c in result.candidates if c["category"] == "C5"]
    assert c5["subcategory"] == "duplicate-section-id"
    assert c5["expected_behaviour"] == "must_disambiguate"
    assert c5["valid_occurrences"] == [1, 2]
    assert c5["citation_exists"] is True
    assert c5["deterministic_criteria"] == ["must-disambiguate", "no-invalid-citations"]
    assert c5["ground_truth_evidence"] == {"duplicate_occurrences": {"occurrences": [1, 2]}}


def _echo_corpus(root: Path) -> CorpusReader:
    return build_corpus(
        root,
        {
            "ekkoloven": ("Lov om ekko-regler (ekkoloven)", EKKOLOVEN_BODY),
            "solidloven": (
                "Lov om solide regler (solidloven)",
                "## Kapittel 1. Alminnelige bestemmelser\n\n"
                "### § 1. Krav til solid dokumentasjon\n\n"
                "Virksomheten skal dokumentere alle solide vurderinger.\n",
            ),
        },
    )


def test_id_offset_shifts_case_ids_into_a_fresh_range(tmp_path: Path) -> None:
    """Stage 3.6-E: replacement generations must never reuse Stage 3 case
    ids — offset counters keep the id space disjoint (owner ruling: new
    material gets new ids, review history never mixes)."""
    pin = CorpusPin(lovverk_commit=_PIN_SHA, manifest_generated_at=GENERATED_AT)
    config = PoolConfig(
        schema_path=SCHEMA_PATH,
        targets=_TARGETS,
        inventory_size=10,
        per_act_total_cap=20,
        id_offset=500,
    )
    result = generate_pool(rich_corpus(tmp_path), pin, config, _RUN)
    ids = [str(c["case_id"]) for c in result.candidates]
    assert ids
    assert all(int(i.rsplit("-", 1)[1]) > 500 for i in ids)
    assert any(i.endswith("-501") for i in ids)


def test_veileder_only_duplicate_yields_no_c5(tmp_path: Path) -> None:
    """RC3 end-to-end: a corpus whose only duplication is a veileder echo
    produces zero C5 cases and an empty ambiguity scan."""
    result = _generate(_echo_corpus(tmp_path))
    assert result.generation_manifest["emitted_by_category"].get("C5", 0) == 0
    assert all(c["category"] != "C5" for c in result.candidates)
    assert result.ambiguity_scan == []


def _c5_builder(reader: CorpusReader, c5_target: int) -> _Builder:
    pin = CorpusPin(lovverk_commit=_PIN_SHA, manifest_generated_at=GENERATED_AT)
    targets = {**_TARGETS, "C5": c5_target}
    config = PoolConfig(schema_path=SCHEMA_PATH, targets=targets)
    return _Builder(reader, pin, config, _RUN)


def test_build_c5_ignores_scan_finding_the_oracle_disowns(tmp_path: Path) -> None:
    """Defense in depth: a stale (pre-RC3) scan finding whose duplication
    the layer-aware oracle disowns emits nothing."""
    builder = _c5_builder(_echo_corpus(tmp_path), _TARGETS["C5"])
    stale = [{"slug": "ekkoloven", "doc_id": "nl-0", "duplicates": {"2": 2}}]
    _build_c5(builder, stale)
    assert builder.cases == []
    assert builder.counters["C5"] == 0


TO_DUP_BODY = """## Kapittel 1. En

### § 7. Krav til første tema

Første versjon av kravet til første tema.

### § 8. Krav til andre tema

Første versjon av kravet til andre tema.

## Kapittel 2. To

### § 7. Krav til første tema

Andre versjon av kravet til første tema.

### § 8. Krav til andre tema

Andre versjon av kravet til andre tema.
"""


MIKS_BODY = """## Kapittel 1. En

### § 2. Krav til utstyr

Kravet til utstyr i hovedteksten.

### § 7. Krav til bruk

Første versjon av kravet til bruk.

## Kapittel 2. To

### § 7. Krav til bruk

Andre versjon av kravet til bruk.

## Veileder til loven

### § 2. Krav til utstyr

Kommentar som speiler paragrafen uten å være en bestemmelse.
"""


def test_build_c5_skips_disowned_id_and_still_emits_the_next(tmp_path: Path) -> None:
    """A stale finding mixing a veileder-echo id with a real duplicate must
    skip the echo and still emit the real one — never abort the id loop."""
    reader = build_corpus(
        tmp_path,
        {"miksloven": ("Lov om blandede paragrafer (miksloven)", MIKS_BODY)},
    )
    builder = _c5_builder(reader, _TARGETS["C5"])
    stale = [{"slug": "miksloven", "doc_id": "nl-0", "duplicates": {"2": 2, "7": 2}}]
    _build_c5(builder, stale)
    assert [c["expected_section_id"] for c in builder.cases] == ["7"]


TRE_DUP_BODY = """## Kapittel 1. En

### § 7. Krav til første tema

Første versjon av kravet til første tema.

### § 8. Krav til andre tema

Første versjon av kravet til andre tema.

### § 9. Krav til tredje tema

Første versjon av kravet til tredje tema.

## Kapittel 2. To

### § 7. Krav til første tema

Andre versjon av kravet til første tema.

### § 8. Krav til andre tema

Andre versjon av kravet til andre tema.

### § 9. Krav til tredje tema

Andre versjon av kravet til tredje tema.
"""


def test_build_c5_respects_per_act_category_cap_and_rotates_frames(tmp_path: Path) -> None:
    """The per-(category, act) cap stops the third duplicate id of one act,
    and the two emitted cases use different question frames."""
    reader = build_corpus(
        tmp_path,
        {"treloven": ("Lov om tre doble paragrafer (treloven)", TRE_DUP_BODY)},
    )
    pin = CorpusPin(lovverk_commit=_PIN_SHA, manifest_generated_at=GENERATED_AT)
    config = PoolConfig(
        schema_path=SCHEMA_PATH,
        targets={**_TARGETS, "C5": 10},
        per_act_category_caps={},
    )
    builder = _Builder(reader, pin, config, _RUN)
    _build_c5(builder, scan_duplicate_ids(reader))
    assert [c["expected_section_id"] for c in builder.cases] == ["7", "8"]
    frames = [
        tpl.fill(tpl.C5_DUPLICATE_FRAMES, n, act="treloven", section=s)
        for n, s in ((0, "7"), (1, "8"))
    ]
    assert [c["question"] for c in builder.cases] == frames


def test_generation_manifest_records_category_cap_overrides(result: PoolResult) -> None:
    """Provenance: the manifest must record every cap that shaped the pool —
    a future regeneration relies on it (Codex finding, PR #33)."""
    caps = result.generation_manifest["caps"]
    assert caps["per_act_category_cap"] == 2
    assert caps["per_act_category_caps"] == {"C5": 3}


FIRE_DUP_BODY = """## Kapittel 1. En

### § 7. Krav til første tema

Første versjon av kravet til første tema.

### § 8. Krav til andre tema

Første versjon av kravet til andre tema.

### § 9. Krav til tredje tema

Første versjon av kravet til tredje tema.

### § 10. Krav til fjerde tema

Første versjon av kravet til fjerde tema.

## Kapittel 2. To

### § 7. Krav til første tema

Andre versjon av kravet til første tema.

### § 8. Krav til andre tema

Andre versjon av kravet til andre tema.

### § 9. Krav til tredje tema

Andre versjon av kravet til tredje tema.

### § 10. Krav til fjerde tema

Andre versjon av kravet til fjerde tema.
"""


def test_c5_cap_three_is_a_ceiling(tmp_path: Path) -> None:
    """Four duplicate ids in one act: exactly three C5 cases emit (mutation
    survivor from the PR #33 review — cap 4 must not pass)."""
    reader = build_corpus(
        tmp_path,
        {"fireloven": ("Lov om fire doble paragrafer (fireloven)", FIRE_DUP_BODY)},
    )
    builder = _c5_builder(reader, 10)
    _build_c5(builder, scan_duplicate_ids(reader))
    assert [c["expected_section_id"] for c in builder.cases] == ["10", "7", "8"]


def test_c5_cap_override_allows_three_per_act(tmp_path: Path) -> None:
    """Owner ruling #21 (Stage 3.6-G): C5's per-act cap is 3 by default —
    the real ambiguity population is concentrated in few documents, and
    cap 2 makes the frozen target of 15 structurally unreachable."""
    reader = build_corpus(
        tmp_path,
        {"treloven": ("Lov om tre doble paragrafer (treloven)", TRE_DUP_BODY)},
    )
    builder = _c5_builder(reader, 10)
    _build_c5(builder, scan_duplicate_ids(reader))
    assert [c["expected_section_id"] for c in builder.cases] == ["7", "8", "9"]


def test_build_c5_stops_at_target(tmp_path: Path) -> None:
    """Target saturation skips later duplicate ids and later findings."""
    reader = build_corpus(
        tmp_path,
        {
            "toloven": ("Lov om to doble paragrafer (toloven)", TO_DUP_BODY),
            "dobbeltloven": ("Lov om doble paragrafer (dobbeltloven)", DOBBELTLOVEN_BODY),
        },
    )
    builder = _c5_builder(reader, 1)
    findings = scan_duplicate_ids(reader)
    assert len(findings) == 2
    _build_c5(builder, findings)
    assert builder.counters["C5"] == 1
    assert len(builder.cases) == 1


def test_no_tombstone_subcategory_is_ever_emitted(result: PoolResult) -> None:
    assert all(c["subcategory"] != "repealed-as-current" for c in result.candidates)


def test_c3_traps_are_truly_nonexistent(
    result: PoolResult,
    tmp_path: Path,
) -> None:
    reader = rich_corpus(tmp_path)
    for case in result.candidates:
        if case["category"] != "C3":
            continue
        with pytest.raises(CorpusNotFoundError):
            reader.get_section(str(case["claimed_act_slug"]), str(case["claimed_section_id"]))


def test_c4_trap_differs_from_ground_truth(result: PoolResult) -> None:
    c4 = [c for c in result.candidates if c["category"] == "C4"]
    assert c4
    for case in c4:
        assert case["claimed_act_slug"] != case["expected_act_slug"]


def test_ids_are_sequential_unique_and_category_visible(result: PoolResult) -> None:
    ids = [str(c["case_id"]) for c in result.candidates]
    assert len(ids) == len(set(ids))
    for category, count in result.generation_manifest["emitted_by_category"].items():
        emitted = [i for i in ids if f"-{category}-" in i]
        assert len(emitted) == count  # nothing rejected in this corpus
        assert emitted == [f"llhb-v1-{category}-{n:03d}" for n in range(1, count + 1)]


def test_every_case_carries_the_corpus_pin(result: PoolResult) -> None:
    for case in result.candidates:
        assert case["corpus_pin"]["lovverk_commit"] == _PIN_SHA


def test_c2_questions_leak_nothing(result: PoolResult) -> None:
    for case in result.candidates:
        if case["category"] == "C2":
            question = str(case["question"]).casefold()
            assert "§" not in question
            assert str(case["expected_act_slug"]).casefold() not in question


def test_c8_cases_are_structural_only(result: PoolResult) -> None:
    c8 = [c for c in result.candidates if c["category"] == "C8"]
    assert c8
    for case in c8:
        for field in ("expected_act_slug", "claimed_act_slug", "quote_ref", "citation_exists"):
            assert case[field] is None, (case["case_id"], field)


def test_no_authentic_statutory_text_leaks(result: PoolResult, tmp_path: Path) -> None:
    """Questions and refs must not embed verbatim section text. The only
    sanctioned near-verbatim content is the C7 'modified' trap text, which
    must differ from every authentic normalized body."""
    reader = rich_corpus(tmp_path)
    bodies = [
        normalize_quote_text(str(reader.get_section(slug, sid)["body"]))
        for slug, sid in (("alfaloven", "1-1"), ("betaloven", "1"), ("gammaloven", "5-1"))
    ]
    for case in result.candidates:
        if case["category"] == "C7":
            _assert_c7_quote_free(case, bodies)
            continue
        question = " ".join(str(case["question"]).casefold().split())
        for body in bodies:
            probe = body[:60]
            assert probe not in question, case["case_id"]


def _assert_c7_quote_free(case: dict[str, Any], bodies: list[str]) -> None:
    if case["subcategory"] == "authentic":
        assert case["fabricated_quote_text"] is None
        assert case["quote_ref"] is not None
        assert "[SITAT]" in str(case["question"])
    else:
        text = normalize_quote_text(str(case["fabricated_quote_text"]))
        assert all(text not in body for body in bodies), case["case_id"]


def test_review_queue_contains_all_c5_and_c8(result: PoolResult) -> None:
    queued = {entry["case_id"]: entry["reasons"] for entry in result.review_queue}
    for case in result.candidates:
        if case["category"] in ("C5", "C8"):
            case_id = str(case["case_id"])
            assert any("mandatory-manual-review" in r for r in queued[case_id])


def test_review_queue_never_references_removed_cases(result: PoolResult) -> None:
    """Codex PR #17 finding 1: dedup-removed cases surfaced as queue orphans."""
    kept = {str(case["case_id"]) for case in result.candidates}
    assert all(entry["case_id"] in kept for entry in result.review_queue)


def test_review_queue_drops_warnings_for_deduped_cases() -> None:
    from lovspor.llhb.pool import _review_queue  # noqa: PLC0415

    kept_case = {"case_id": "llhb-v1-C8-001", "category": "C8"}
    warning = {"code": "c8-requires-manual-review", "severity": "warning", "message": "x"}
    ledger = [
        {"case_id": "llhb-v1-C8-001", "issues": [warning]},
        {"case_id": "llhb-v1-C8-002", "issues": [warning]},  # dedup-removed
    ]
    queue = _review_queue([kept_case], ledger, [])
    assert [entry["case_id"] for entry in queue] == ["llhb-v1-C8-001"]


def test_c6_misattribution_evidence_mirrors_real_oracle_heading(
    result: PoolResult,
    tmp_path: Path,
) -> None:
    """Codex PR #17 finding 2: evidence stored the derived topic, not the
    heading get_section actually returns."""
    reader = rich_corpus(tmp_path)
    checked = 0
    for case in result.candidates:
        if case["category"] != "C6" or case["subcategory"] != "attribution-mismatch":
            continue
        recorded = case["ground_truth_evidence"]["get_section"]["heading"]
        actual = reader.get_section(
            str(case["expected_act_slug"]),
            str(case["expected_section_id"]),
        )["heading"]
        assert recorded == actual, case["case_id"]
        checked += 1
    assert checked > 0


def test_quarantine_retains_id_and_reason(tmp_path: Path) -> None:
    """A candidate that fails validation lands in rejected with its id kept."""
    from lovspor.llhb.pool import _Builder, _validate_all  # noqa: PLC0415
    from lovspor.llhb.validation import CandidateValidator  # noqa: PLC0415
    from tests.unit.test_llhb_schema import make_case  # noqa: PLC0415

    reader = rich_corpus(tmp_path)
    pin = CorpusPin(lovverk_commit=_PIN_SHA, manifest_generated_at=GENERATED_AT)
    builder = _Builder(reader, pin, PoolConfig(schema_path=SCHEMA_PATH), _RUN)
    builder.validator = CandidateValidator(reader, load_schema(SCHEMA_PATH), pin)
    broken = make_case(
        case_id=None,
        category="C3",
        expected_behaviour="reject_citation",
        expected_act_slug=None,
        expected_section_id=None,
        claimed_act_slug="alfaloven",
        claimed_section_id="1-1",  # exists — not a trap
        citation_exists=False,
        corpus_pin={"lovverk_commit": _PIN_SHA, "manifest_generated_at": "2026-08-05T06:00:00Z"},
    )
    del broken["case_id"]
    builder.emit("C3", broken)
    valid, rejected, ledger = _validate_all(builder)
    assert valid == []
    (entry,) = rejected
    assert entry["case"]["case_id"] == "llhb-v1-C3-001"
    assert entry["case"]["validation"]["status"] == "quarantined"
    assert [i["code"] for i in entry["issues"]] == ["trap-section-exists"]
    assert ledger[0]["status"] == "fail"


def test_dedup_report_is_stable_and_empty_on_template_pool(result: PoolResult) -> None:
    assert result.dedup_report["exact_removed"] == []
    assert isinstance(result.dedup_report["near_duplicate_flags"], list)


def test_name_calibration_reports_collisions_and_coverage(result: PoolResult) -> None:
    calibration = result.name_calibration
    assert calibration["documents"] == 6  # 5 current + 1 tombstone
    assert calibration["keys"] > 0
    assert "collision_count" in calibration


def test_exhausted_corpus_reports_shortfall_not_padding(tmp_path: Path) -> None:
    """Targets far above the material: every builder must run dry honestly."""
    reader = rich_corpus(tmp_path)
    pin = CorpusPin(lovverk_commit=_PIN_SHA, manifest_generated_at=GENERATED_AT)
    config = PoolConfig(
        schema_path=SCHEMA_PATH,
        targets=dict.fromkeys(("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"), 50),
        inventory_size=10,
        per_act_total_cap=50,
        per_act_category_cap=3,
    )
    result = generate_pool(reader, pin, config, _RUN)
    counts = result.distribution["by_category"]
    assert all(count < 50 for count in counts.values())
    assert result.rejected == []
    # Shortfalls are visible in the manifest, never silently padded:
    assert result.generation_manifest["targets"]["C1"] == 50
    assert result.generation_manifest["emitted_by_category"]["C1"] == counts["C1"]


def test_exact_duplicate_questions_are_removed_and_recorded() -> None:
    from lovspor.llhb.pool import _dedup  # noqa: PLC0415

    first = {"case_id": "llhb-v1-C1-001", "category": "C1", "question": "Hva gjelder her?"}
    second = {"case_id": "llhb-v1-C1-002", "category": "C1", "question": "hva  gjelder her?"}
    kept, report = _dedup([first, second])
    assert [c["case_id"] for c in kept] == ["llhb-v1-C1-001"]
    assert report["exact_removed"] == [
        {"case_id": "llhb-v1-C1-002", "duplicate_of": "llhb-v1-C1-001"},
    ]


def test_validate_all_without_validator_fails_closed(tmp_path: Path) -> None:
    from lovspor.llhb.pool import _Builder, _validate_all  # noqa: PLC0415

    reader = rich_corpus(tmp_path)
    pin = CorpusPin(lovverk_commit=_PIN_SHA, manifest_generated_at=GENERATED_AT)
    builder = _Builder(reader, pin, PoolConfig(schema_path=SCHEMA_PATH), _RUN)
    with pytest.raises(RuntimeError, match="no validator"):
        _validate_all(builder)


def test_claimed_exists_skips_ambiguous_pairs(tmp_path: Path) -> None:
    from lovspor.llhb.pool import _claimed_exists  # noqa: PLC0415

    reader = rich_corpus(tmp_path)
    assert _claimed_exists(reader, "dobbeltloven", "6-2") is None
    assert _claimed_exists(reader, "alfaloven", "1-1") is True
    assert _claimed_exists(reader, "alfaloven", "9-9") is False


def test_pair_wrong_act_falls_back_to_doc_type(tmp_path: Path) -> None:
    from lovspor.llhb.generation import CorpusSampler  # noqa: PLC0415
    from lovspor.llhb.pool import _pair_wrong_act  # noqa: PLC0415

    reader = rich_corpus(tmp_path)
    sampler = CorpusSampler(reader)
    acts = [a for i in sampler.shuffled_current_doc_ids() if (a := sampler.act_info(i))]
    alfa = next(a for a in acts if a.slug == "alfaloven")
    no_ministry = alfa.model_copy(update={"ministry": None})
    paired = _pair_wrong_act(acts, no_ministry, 0)
    assert paired is not None and paired.slug != "alfaloven"
    assert _pair_wrong_act([alfa], alfa, 0) is None
