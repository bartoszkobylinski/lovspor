"""Manual-review support: decisions contract, gate, clusters, evidence items."""

from pathlib import Path
from typing import Any

import pytest

from lovspor.llhb.review import (
    DROP_REASONS,
    KEEP_REASONS,
    NEEDS_FIX_REASONS,
    ReviewDecision,
    ReviewError,
    build_review_items,
    completeness,
    decision_problems,
    init_decisions,
    load_decisions,
    near_duplicate_clusters,
    save_decisions,
)

_QUEUE = [
    {"case_id": "llhb-v1-C5-001", "reasons": ["C5-mandatory-manual-review"]},
    {"case_id": "llhb-v1-C8-001", "reasons": ["C8-mandatory-manual-review"]},
    {"case_id": "llhb-v1-C1-001", "reasons": ["stratified-10pct-sample"]},
]


def _decided(case_id: str, decision: str, reason: str) -> ReviewDecision:
    return ReviewDecision(
        case_id=case_id,
        category=case_id.split("-")[2],
        queue_reasons=["x"],
        decision=decision,
        reviewer="owner",
        reviewed_at="2026-08-06T10:00:00Z",
        reason_code=reason,
    )


def test_reason_codes_are_the_frozen_closed_lists() -> None:
    assert KEEP_REASONS == (
        "valid",
        "useful-ambiguity",
        "good-adversarial-case",
        "acceptable-near-duplicate",
        "scope-boundary-clear",
    )
    assert "corpus-evidence-insufficient" in DROP_REASONS
    assert NEEDS_FIX_REASONS == ("wording-only", "metadata-error", "classification-review")


def test_init_decisions_is_nulls_plus_immutable_metadata() -> None:
    decisions = init_decisions(_QUEUE)
    assert [d.case_id for d in decisions] == [str(e["case_id"]) for e in _QUEUE]
    assert all(d.decision is None and d.reviewer is None for d in decisions)
    assert decisions[0].category == "C5"


def test_save_load_round_trip_and_invalid_line(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    decisions = init_decisions(_QUEUE)
    save_decisions(path, decisions)
    assert load_decisions(path) == decisions
    path.write_text('{"case_id": 42}\n', encoding="utf-8")
    with pytest.raises(ReviewError, match=r"decisions\.jsonl:1"):
        load_decisions(path)


def test_decision_problems_enforce_the_contract() -> None:
    assert decision_problems(init_decisions(_QUEUE)[0]) == []
    assert decision_problems(_decided("llhb-v1-C5-001", "keep", "useful-ambiguity")) == []
    wrong_code = _decided("llhb-v1-C5-001", "keep", "redundant")
    assert any("not valid for keep" in p for p in decision_problems(wrong_code))
    unknown = _decided("llhb-v1-C5-001", "maybe", "valid")
    assert any("unknown decision" in p for p in decision_problems(unknown))
    incomplete = _decided("llhb-v1-C5-001", "drop", "redundant").model_copy(
        update={"reviewer": None, "reviewed_at": None},
    )
    problems = decision_problems(incomplete)
    assert "reviewer missing" in problems and "reviewed_at missing" in problems


def test_completeness_blocks_until_every_disposition_is_final() -> None:
    pending = completeness(_QUEUE, init_decisions(_QUEUE))
    assert pending.total_queued == 3 and pending.remaining == 3
    assert pending.c5_remaining == ["llhb-v1-C5-001"]
    assert pending.c8_remaining == ["llhb-v1-C8-001"]
    assert pending.stratified_remaining == ["llhb-v1-C1-001"]
    assert pending.stage4_unblocked is False

    done = [
        _decided("llhb-v1-C5-001", "keep", "useful-ambiguity"),
        _decided("llhb-v1-C8-001", "drop", "scope-boundary-unclear"),
        _decided("llhb-v1-C1-001", "keep", "valid"),
    ]
    complete = completeness(_QUEUE, done)
    assert complete.remaining == 0 and complete.keep == 2 and complete.drop == 1
    assert complete.stage4_unblocked is True


def test_needs_fix_and_missing_records_block_stage4() -> None:
    with_fix = [
        _decided("llhb-v1-C5-001", "needs_fix", "wording-only"),
        _decided("llhb-v1-C8-001", "keep", "scope-boundary-clear"),
        _decided("llhb-v1-C1-001", "keep", "valid"),
    ]
    report = completeness(_QUEUE, with_fix)
    assert report.needs_fix == 1 and report.stage4_unblocked is False

    missing = [_decided("llhb-v1-C5-001", "keep", "valid")]
    report = completeness(_QUEUE, missing)
    assert report.stage4_unblocked is False
    assert any("missing from decisions file" in i for i in report.invalid_records)


def test_invalid_decided_record_blocks_even_when_all_reviewed() -> None:
    decisions = [
        _decided("llhb-v1-C5-001", "keep", "redundant"),  # wrong code for keep
        _decided("llhb-v1-C8-001", "keep", "scope-boundary-clear"),
        _decided("llhb-v1-C1-001", "keep", "valid"),
    ]
    report = completeness(_QUEUE, decisions)
    assert report.remaining == 0
    assert report.stage4_unblocked is False


def test_duplicate_decision_rows_block_stage4() -> None:
    """Codex PR #18 finding 1a: same queued case twice with conflicting
    final decisions must never unblock."""
    decisions = [
        _decided("llhb-v1-C5-001", "keep", "useful-ambiguity"),
        _decided("llhb-v1-C5-001", "drop", "redundant"),
        _decided("llhb-v1-C8-001", "keep", "scope-boundary-clear"),
        _decided("llhb-v1-C1-001", "keep", "valid"),
    ]
    report = completeness(_QUEUE, decisions)
    assert report.stage4_unblocked is False
    assert any("appears 2 times" in i for i in report.invalid_records)


def test_stray_decision_rows_block_stage4() -> None:
    """Codex PR #18 finding 1b: a decided non-queued extra row must block."""
    decisions = [
        _decided("llhb-v1-C5-001", "keep", "useful-ambiguity"),
        _decided("llhb-v1-C8-001", "keep", "scope-boundary-clear"),
        _decided("llhb-v1-C1-001", "keep", "valid"),
        _decided("llhb-v1-C9-999", "keep", "valid"),
    ]
    report = completeness(_QUEUE, decisions)
    assert report.stage4_unblocked is False
    assert any("not in the review queue" in i for i in report.invalid_records)


def test_sanitize_evidence_drops_free_text_keeps_identifiers() -> None:
    from lovspor.llhb.review import sanitize_evidence  # noqa: PLC0415

    evidence = {
        "get_section": {"slug": "testloven", "section_id": "1", "heading": "§ 1. Formål"},
        "validate_citation": {
            "valid": False,
            "slug": "testloven",
            "reason": "section '9' not found; available: § 1, § 2",
        },
        "duplicate_occurrences": {"count": 2},
        "scope": {"source_class": "rettspraksis", "in_corpus": False, "authority": "docs"},
    }
    sanitized = sanitize_evidence(evidence)
    assert sanitized == {
        "get_section": {"slug": "testloven", "section_id": "1"},
        "validate_citation": {"valid": False, "slug": "testloven"},
        "duplicate_occurrences": {"count": 2},
        "scope": {"source_class": "rettspraksis", "in_corpus": False, "authority": "docs"},
    }


def test_near_duplicate_clusters_union_transitively() -> None:
    flags = [
        {"a": "x-1", "b": "x-2", "jaccard": 0.9},
        {"a": "x-2", "b": "x-3", "jaccard": 0.85},
        {"a": "y-1", "b": "y-2", "jaccard": 0.8},
    ]
    assert near_duplicate_clusters(flags) == [["x-1", "x-2", "x-3"], ["y-1", "y-2"]]


def _case(case_id: str, **overrides: Any) -> dict[str, Any]:
    case: dict[str, Any] = {
        "case_id": case_id,
        "category": case_id.split("-")[2],
        "subcategory": "factual",
        "difficulty": "easy",
        "question": "Hva sier testloven om formålet med loven her?",
        "expected_behaviour": "answer_with_citation",
        "expected_act_slug": "testloven",
        "expected_section_id": "1",
        "expected_occurrence": None,
        "claimed_act_slug": None,
        "claimed_section_id": None,
        "citation_exists": True,
        "quote_ref": None,
        "fabricated_quote_text": None,
        "ground_truth_evidence": {"get_section": {"heading": "§ 1. Formål"}},
        "provenance": {"method": "corpus-selected-template"},
    }
    case.update(overrides)
    return case


def test_review_items_carry_evidence_and_structural_notes() -> None:
    queue = [
        {"case_id": "llhb-v1-C5-001", "reasons": ["C5-mandatory-manual-review"]},
        {"case_id": "llhb-v1-C8-001", "reasons": ["C8-mandatory-manual-review"]},
        {"case_id": "llhb-v1-C7-001", "reasons": ["stratified-10pct-sample"]},
    ]
    candidates = [
        _case(
            "llhb-v1-C5-001",
            subcategory="duplicate-section-id",
            expected_act_slug="dobbeltloven",
            expected_section_id="6-2",
            ground_truth_evidence={"duplicate_occurrences": {"count": 2}},
        ),
        _case(
            "llhb-v1-C8-001",
            subcategory="rettspraksis",
            expected_act_slug=None,
            expected_section_id=None,
            citation_exists=None,
            expected_behaviour="abstain",
            question="Hvilke dommer fra Høyesterett gjelder virkeområde, og hva ble resultatet?",
            ground_truth_evidence={"scope": {"source_class": "rettspraksis", "authority": "docs"}},
        ),
        _case(
            "llhb-v1-C7-001",
            subcategory="authentic",
            expected_behaviour="verify_quote",
            quote_ref={
                "slug": "testloven",
                "section_id": "1",
                "char_span": [10, 60],
                "sha256_normalized": "ab" * 32,
            },
        ),
    ]
    ledger = [{"case_id": c["case_id"], "status": "pass", "issues": []} for c in candidates]
    flags = [{"a": "llhb-v1-C5-001", "b": "llhb-v1-C7-001", "jaccard": 0.81}]
    items = build_review_items(queue, candidates, ledger, flags)

    c5, c8, c7 = items
    # Sanitized evidence: identifiers/counts survive, free text does not.
    assert c5.ground_truth_evidence == {"duplicate_occurrences": {"count": 2}}
    assert "heading" not in str(items)
    assert any("duplicate section id" in n for n in c5.structural_notes)
    assert any("DEBATABLE" in n for n in c5.structural_notes)
    assert c5.near_duplicates == ["llhb-v1-C7-001"]
    assert any("rettspraksis" in n for n in c8.structural_notes)
    assert any("very generic topic" in n for n in c8.structural_notes)
    assert any("materialize locally" in n for n in c7.structural_notes)
    # Quote-free: the item exposes coordinates and hash prefix, never text.
    assert c7.quote_ref is not None and "text" not in c7.quote_ref


def test_queued_case_missing_from_pool_fails_closed() -> None:
    queue = [{"case_id": "llhb-v1-C1-999", "reasons": ["stratified-10pct-sample"]}]
    with pytest.raises(ReviewError, match="llhb-v1-C1-999"):
        build_review_items(queue, [], [], [])
