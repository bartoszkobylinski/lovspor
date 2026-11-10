"""The batch plan record refuses every state a planner could not have written (#738, A2b).

A plan states its own limits by type and recomputes what it derives: the
sample, the counts and the coverage. An edited plan, a 26th document, a
second path for one chapter or a target branch outside ``forarbeider/`` is
not a valid plan.
"""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider.batch_classify import ChapterClass
from lovspor.forarbeider.batch_models import (
    MAX_DOCUMENTS,
    SAMPLE_SIZE,
    BatchPlan,
    BatchReason,
    PlannedChapter,
    PlannedDocument,
    counts_of,
    sample_of,
    valid_branch,
)
from lovspor.forarbeider.policy_models import PolicyFault
from lovspor.forarbeider.policy_source import SourceFault
from lovspor.forarbeider.release_models import DenyReason, DocumentIdentity, destination_of

POLICY_SHA = "b" * 64
PROOF_SHA = "c" * 64
INPUTS_SHA = "1" * 64
POLICY = {
    "cleared_by": "standing_policy",
    "policy_sha256": POLICY_SHA,
    "source_proof_sha256": PROOF_SHA,
    "individually_reviewed": False,
}
REVIEW = {
    "cleared_by": "owner_review",
    "review_decided_at": "2100-04-02T12:00:00Z",
    "review": {"subject_key": "d" * 64, "revision": 1, "head_sha256": "e" * 64},
    "authorization_sha256": "f" * 64,
    "report_sha256": "0" * 64,
    "provenance": "recorded",
    "individually_reviewed": True,
}


def _document(number: int) -> dict[str, Any]:
    return {
        "id": f"regjeringen-{9000100 + number}",
        "document_type": "prop-l",
        "session": "2099-2100",
        "proposition_number": 900 + number,
        "source_document_id": 9000100 + number,
        "source_url": (
            f"https://www.regjeringen.no/no/dokumenter/prop.-{900 + number}-l-20992100/"
            f"id{9000100 + number}/"
        ),
    }


def _chapter(number: int, chapter: int, **changes: Any) -> dict[str, Any]:
    document = _document(number)
    fields = {
        "chapter_number": chapter,
        "chapter_id": f"{document['id']}-ch{chapter}",
        "source_page_url": f"{document['source_url']}?ch={chapter}",
        "source_sha256": "2" * 64,
        "text_sha256": "3" * 64,
        "extractor_version": 1,
        "normalizer_version": 1,
        "hold_reasons": ["review_required"],
        "classification": "clean",
        "included": True,
        "clearance": POLICY,
        "reasons": [],
    }
    return {**fields, **changes}


def _planned(number: int, chapters: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    document = _document(number)
    planned = chapters if chapters is not None else [_chapter(number, 2), _chapter(number, 3)]
    included = [item["chapter_number"] for item in planned if item["included"]]
    coverage = "none" if not included else "partial_curated_excerpt"
    if included == [1, 2, 3, 4]:
        coverage = "every_listed_chapter"
    return {
        "document": document,
        "destination": destination_of(DocumentIdentity.model_validate(document)),
        "listed_chapters": [1, 2, 3, 4],
        "report_sha256": None,
        "chapters": planned,
        "coverage": coverage,
    }


def _plan(documents: list[dict[str, Any]], **changes: Any) -> dict[str, Any]:
    models = [PlannedDocument.model_validate(item) for item in documents]
    fields = {
        "schema_version": 1,
        "record_type": "forarbeider_batch_plan",
        "inputs_sha256": INPUTS_SHA,
        "at": "2100-04-10T12:00:00Z",
        "target_branch": "forarbeider/batch-1",
        "policy_sha256": POLICY_SHA,
        "source_proof_sha256": PROOF_SHA,
        "documents": documents,
        "sample": [item.model_dump(mode="json") for item in sample_of(models, INPUTS_SHA)],
        "counts": counts_of(models).model_dump(mode="json"),
        "publication_authorized": False,
        "writes_corpus": False,
        "applies": False,
        "owner_confirmation": "required_not_given",
        "legal_sufficiency_decided_by_code": False,
        "sample_is_gate": False,
    }
    return {**fields, **changes}


def test_exclusion_reason_values_are_disjoint_across_their_four_sources() -> None:
    families = [DenyReason, PolicyFault, SourceFault, BatchReason]
    values = [member.value for family in families for member in family]

    assert len(values) == len(set(values))


def test_a_valid_plan_states_that_it_authorizes_and_applies_nothing() -> None:
    plan = BatchPlan.model_validate(_plan([_planned(1)]))

    assert plan.publication_authorized is False
    assert plan.writes_corpus is False
    assert plan.applies is False
    assert plan.owner_confirmation == "required_not_given"
    assert plan.sample_is_gate is False
    assert plan.counts.standing_policy == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("publication_authorized", True),
        ("writes_corpus", True),
        ("applies", True),
        ("owner_confirmation", "given"),
        ("legal_sufficiency_decided_by_code", True),
        ("sample_is_gate", True),
        ("schema_version", 2),
    ],
)
def test_a_plan_cannot_state_that_it_permits_anything(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        BatchPlan.model_validate(_plan([_planned(1)], **{field: value}))


def test_twenty_five_documents_are_a_plan_and_twenty_six_are_not() -> None:
    BatchPlan.model_validate(_plan([_planned(n) for n in range(1, MAX_DOCUMENTS + 1)]))

    with pytest.raises(ValidationError):
        BatchPlan.model_validate(_plan([_planned(n) for n in range(1, MAX_DOCUMENTS + 2)]))


def test_a_document_planned_twice_or_out_of_order_is_invalid() -> None:
    with pytest.raises(ValidationError):
        BatchPlan.model_validate(_plan([_planned(1), _planned(1)]))
    with pytest.raises(ValidationError):
        BatchPlan.model_validate(_plan([_planned(2), _planned(1)]))


def test_an_edited_sample_or_count_is_invalid() -> None:
    payload = _plan([_planned(n) for n in range(1, 5)])
    reordered = {**payload, "sample": payload["sample"][::-1]}
    shortened = {**payload, "sample": payload["sample"][:-1]}
    recounted = {**payload, "counts": {**payload["counts"], "excluded": 1}}

    for edited in (reordered, shortened, recounted):
        with pytest.raises(ValidationError):
            BatchPlan.model_validate(edited)


def test_the_sample_is_the_lowest_keys_of_policy_cleared_chapters_only() -> None:
    review = _chapter(1, 4, classification="flagged", clearance=REVIEW)
    documents = [_planned(1, [_chapter(1, 2), _chapter(1, 3), review]), _planned(2), _planned(3)]
    plan = BatchPlan.model_validate(_plan(documents))

    assert len(plan.sample) == SAMPLE_SIZE
    keys = [item.key_sha256 for item in plan.sample]
    assert keys == sorted(keys)
    assert "regjeringen-9000101-ch4" not in {item.chapter_id for item in plan.sample}
    assert plan.sample == sample_of(plan.documents, INPUTS_SHA)
    assert sample_of(plan.documents, "2" * 64) != plan.sample


def test_fewer_policy_cleared_chapters_than_the_sample_size_are_all_sampled() -> None:
    plan = BatchPlan.model_validate(_plan([_planned(1)]))

    assert {item.chapter_id for item in plan.sample} == {
        "regjeringen-9000101-ch2",
        "regjeringen-9000101-ch3",
    }


@pytest.mark.parametrize(
    "branch",
    [
        "main",
        "master",
        "HEAD",
        "refs/heads/x",
        "forarbeider/",
        "forarbeider/a..b",
        "forarbeider/Batch",
        "forarbeider/a b",
        "forarbeider/-a",
        "forarbeider/a/b",
        "forarbeider/" + "a" * 64,
        "xforarbeider/a",
    ],
)
def test_a_branch_outside_the_grammar_is_refused(branch: str) -> None:
    assert not valid_branch(branch)
    with pytest.raises(ValidationError):
        BatchPlan.model_validate(_plan([_planned(1)], target_branch=branch))


@pytest.mark.parametrize(
    "branch", ["forarbeider/batch-2026-11-10", "forarbeider/a", "forarbeider/" + "a" * 63]
)
def test_a_branch_inside_the_grammar_is_accepted(branch: str) -> None:
    assert valid_branch(branch)


@pytest.mark.parametrize(
    "changes",
    [
        {"included": False},
        {"clearance": None},
        {"reasons": ["withheld"]},
        {"classification": "flagged"},
        {"classification": "unknown"},
        {"classification": "withheld"},
        {
            "clearance": {
                **REVIEW,
                "provenance": "manifest_v1",
                "review": None,
                "authorization_sha256": None,
                "report_sha256": None,
            }
        },
        {"text_sha256": None},
        {"hold_reasons": ["review_required", "table"]},
    ],
)
def test_an_included_chapter_has_exactly_the_one_clearance_its_class_allows(
    changes: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        PlannedChapter.model_validate(_chapter(1, 2, **changes))


def test_a_flagged_chapter_may_carry_only_a_recorded_review_clearance() -> None:
    PlannedChapter.model_validate(_chapter(1, 2, classification="flagged", clearance=REVIEW))
    PlannedChapter.model_validate(_chapter(1, 2, classification="unknown", clearance=REVIEW))

    with pytest.raises(ValidationError):
        PlannedChapter.model_validate(_chapter(1, 2, classification="clean", clearance=REVIEW))


def test_an_excluded_chapter_has_sorted_reasons_from_any_source() -> None:
    reasons = ["page_not_fetched", "policy_expired", "source_page_missing", "terms_changed"]
    excluded = _chapter(1, 2, included=False, clearance=None, reasons=reasons)

    chapter = PlannedChapter.model_validate(excluded)

    assert [str(item) for item in chapter.reasons] == reasons
    with pytest.raises(ValidationError):
        PlannedChapter.model_validate({**excluded, "reasons": reasons[::-1]})
    with pytest.raises(ValidationError):
        PlannedChapter.model_validate({**excluded, "reasons": ["not_a_reason"]})


def test_a_chapter_of_another_document_or_a_wrong_coverage_is_invalid() -> None:
    foreign = _planned(1, [_chapter(1, 2), _chapter(2, 3)])
    wrong = {**_planned(1), "coverage": "every_listed_chapter"}

    for payload in (foreign, wrong):
        with pytest.raises(ValidationError):
            PlannedDocument.model_validate(payload)


def test_a_plan_round_trips_through_its_json() -> None:
    plan = BatchPlan.model_validate(_plan([_planned(1)]))

    again = BatchPlan.model_validate_json(json.dumps(plan.model_dump(mode="json")))

    assert again == plan
    assert ChapterClass.CLEAN in {chapter.classification for chapter in again.documents[0].chapters}
