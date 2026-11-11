"""Each chapter's one clearance path, below the CLI (#738, A2b, Amendment 1 points 2-4).

Inputs are read by the release commands from synthetic staged files, and the
preflight is evaluated at the plan's moment, exactly as the planner does. A
policy verdict is built directly, so each policy and source fault is shown on
its own. The CLI tests read the same paths end to end.
"""

import hashlib
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider.batch_classify import ChapterClass
from lovspor.forarbeider.batch_clearance import (
    REVIEW_PATH,
    SOURCE_PROOF,
    Batch,
    DocumentEvidence,
    PolicyVerdict,
    _chapter_input,
    _outcome,
    _review_clearance,
    _sha,
    clean_reasons,
    document_input,
    plan_of,
    planned_chapter,
    planned_document,
    review_reasons,
    routed,
)
from lovspor.forarbeider.batch_models import BatchReason
from lovspor.forarbeider.corpus_models import PolicyClearance, ReviewClearance
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_models import PolicyFault
from lovspor.forarbeider.policy_source import SourceFault
from lovspor.forarbeider.release_commands import (
    ChapterScope,
    ReleaseScope,
    Run,
    preflight_inputs,
)
from lovspor.forarbeider.release_models import DenyReason, PreflightReport, digest, record_text
from lovspor.forarbeider.release_preflight import Observed, Stored, evaluate
from lovspor.forarbeider.release_store import read_record
from lovspor.forarbeider.review_models import ReviewStatus
from tests.unit.forarbeider_batch_support import PLAN_AT, BatchDesk
from tests.unit.forarbeider_policy_support import standing_policy
from tests.unit.forarbeider_release_support import (  # noqa: F401
    Desk,
    at_a_terminal,
    moment,
    said_digest,
)

pytestmark = pytest.mark.usefixtures("at_a_terminal")

POLICY_SHA = "b" * 64
PROOF_SHA = "c" * 64
QUOTE = "<blockquote><p>Sitat.</p></blockquote>"
TABLE = "<table><tr><td>x</td></tr></table>"


@pytest.fixture
def batch(tmp_path: Path) -> BatchDesk:
    return BatchDesk(tmp_path)


def _fetched(*desks: Desk) -> frozenset[tuple[str, str]]:
    return frozenset(
        (result.source_page_url, result.source_sha256)
        for desk in desks
        for result in desk.results.values()
    )


def _verdict(*desks: Desk, **changes: Any) -> PolicyVerdict:
    fields: dict[str, Any] = {
        "sha256": POLICY_SHA,
        "policy": standing_policy(),
        "faults": (),
        "proof_sha256": PROOF_SHA,
        "fetched": _fetched(*desks),
    }
    return PolicyVerdict(**{**fields, **changes})


def _evidence(
    desk: Desk, review: tuple[str, list[str]] | None = None, at: str = PLAN_AT
) -> DocumentEvidence:
    release = ReleaseScope(workspace=desk.workspace, ledger=desk.ledger)
    scope = ChapterScope(
        release=release,
        metadata=desk.metadata,
        observations=tuple(desk.observations.values()),
        pages=tuple(desk.pages.values()),
    )
    stored = None
    if review is not None:
        record = read_record(desk.workspace, "reports", review[0], PreflightReport)
        assert record is not None
        stored = Stored(record, review[0])
    authorization = None if stored is None else stored.record.authorization_sha256
    checkpoints = () if review is None else tuple(review[1])
    inputs = preflight_inputs(
        scope, Run(authorization=authorization, checkpoints=checkpoints, at=at)
    )
    return DocumentEvidence(inputs, evaluate(inputs), stored)


def _observed(evidence: DocumentEvidence, number: int) -> Observed:
    return next(item for item in evidence.inputs.observed if item.result.chapter_number == number)


def _chapter(evidence: DocumentEvidence, verdict: PolicyVerdict, number: int) -> Any:
    return planned_chapter(evidence, verdict, _observed(evidence, number))


def test_every_deny_reason_is_in_exactly_one_set() -> None:
    assert set(DenyReason) == REVIEW_PATH | SOURCE_PROOF
    assert not REVIEW_PATH & SOURCE_PROOF


def test_clean_chapters_are_cleared_by_a_valid_policy_alone(batch: BatchDesk) -> None:
    desk = batch.add(1)
    evidence = _evidence(desk)

    chapter = _chapter(evidence, _verdict(desk), 2)

    assert chapter.classification is ChapterClass.CLEAN
    assert chapter.included is True
    assert chapter.clearance == PolicyClearance(
        cleared_by="standing_policy",
        policy_sha256=POLICY_SHA,
        source_proof_sha256=PROOF_SHA,
        individually_reviewed=False,
    )


@pytest.mark.parametrize("fault", [*PolicyFault, *SourceFault])
def test_any_policy_or_source_fault_excludes_every_clean_chapter(
    batch: BatchDesk, fault: PolicyFault | SourceFault
) -> None:
    desk = batch.add(1)
    evidence = _evidence(desk)
    verdict = _verdict(desk, faults=(fault,))

    document = planned_document(evidence, verdict)

    assert [chapter.reasons for chapter in document.chapters] == [(fault,), (fault,)]
    assert document.coverage == "none"


def test_a_policy_not_naming_the_document_type_excludes_with_scope_mismatch(
    batch: BatchDesk,
) -> None:
    desk = batch.add(1, letters="ls")
    verdict = _verdict(desk, policy=standing_policy(document_types=("prop-l",)))

    chapter = _chapter(_evidence(desk), verdict, 2)

    assert chapter.reasons == (BatchReason.POLICY_SCOPE_MISMATCH,)


def test_a_page_the_proof_run_did_not_fetch_is_excluded(batch: BatchDesk) -> None:
    desk = batch.add(1)
    result = desk.results[2]
    other_digest = frozenset({(result.source_page_url, "9" * 64)})
    other_url = frozenset({(desk.results[3].source_page_url, result.source_sha256)})

    for fetched in (frozenset(), other_digest, other_url):
        chapter = _chapter(_evidence(desk), _verdict(fetched=fetched), 2)
        assert chapter.reasons == (BatchReason.PAGE_NOT_FETCHED,)


@pytest.mark.parametrize("status", [ReviewStatus.HELD, ReviewStatus.REJECTED])
def test_a_held_or_rejected_decision_vetoes_the_policy(
    batch: BatchDesk, status: ReviewStatus
) -> None:
    desk = batch.add(1)
    desk.decide(2, status)

    chapter = _chapter(_evidence(desk), _verdict(desk), 2)

    assert chapter.reasons == (BatchReason.REVIEW_VETO,)


def test_approved_or_absent_history_does_not_veto_a_clean_chapter(batch: BatchDesk) -> None:
    desk = batch.add(1)
    desk.decide(3, ReviewStatus.APPROVED)

    document = planned_document(_evidence(desk), _verdict(desk))

    assert [chapter.included for chapter in document.chapters] == [True, True]


def test_a_withheld_chapter_is_excluded_whatever_else_holds(batch: BatchDesk) -> None:
    desk = batch.add(1, extras={3: TABLE})

    chapter = _chapter(_evidence(desk), _verdict(desk), 3)

    assert chapter.classification is ChapterClass.WITHHELD
    assert chapter.reasons == (BatchReason.WITHHELD,)
    assert chapter.text_sha256 is None


def test_a_flagged_chapter_without_a_report_is_excluded_though_the_policy_is_valid(
    batch: BatchDesk,
) -> None:
    desk = batch.add(1, extras={3: QUOTE})

    chapter = _chapter(_evidence(desk), _verdict(desk), 3)

    assert chapter.classification is ChapterClass.FLAGGED
    assert chapter.reasons == (BatchReason.PREFLIGHT_MISSING,)


def test_a_flagged_chapter_with_an_eligible_report_is_cleared_by_review(batch: BatchDesk) -> None:
    desk = batch.add(1, extras={3: QUOTE})
    review = batch.flagged_report(1, (3,))
    evidence = _evidence(desk, review)

    chapter = _chapter(evidence, _verdict(desk), 3)

    assert chapter.included is True
    clearance = chapter.clearance
    assert isinstance(clearance, ReviewClearance)
    assert clearance.provenance == "recorded"
    assert clearance.report_sha256 == review[0]
    assert clearance.authorization_sha256 == evidence.fresh.authorization_sha256
    assert clearance.review is not None
    assert clearance.review.revision == 1


def test_the_unknown_class_takes_the_review_path_and_never_the_policy(batch: BatchDesk) -> None:
    desk = batch.add(1)
    evidence = _evidence(desk)
    observed = _observed(evidence, 2)

    reasons, clearance = routed(evidence, _verdict(desk), observed, ChapterClass.UNKNOWN)

    assert (reasons, clearance) == ({BatchReason.PREFLIGHT_MISSING}, None)


def test_a_clean_chapter_with_a_faulted_policy_never_falls_back_to_a_report(
    batch: BatchDesk,
) -> None:
    desk = batch.add(1)
    review = batch.flagged_report(1, (2, 3))
    evidence = _evidence(desk, review)
    assert evidence.fresh.planned_chapters == (2, 3)

    chapter = _chapter(evidence, _verdict(desk, faults=(PolicyFault.POLICY_EXPIRED,)), 2)

    assert chapter.reasons == (PolicyFault.POLICY_EXPIRED,)
    assert chapter.clearance is None


def test_an_approved_flagged_chapter_without_dossier_or_checkpoint_is_excluded(
    batch: BatchDesk,
) -> None:
    desk = batch.add(1, extras={3: QUOTE})
    desk.decide(3, ReviewStatus.APPROVED)
    report_sha = _deny_report(desk)

    chapter = _chapter(_evidence(desk, (report_sha, [])), _verdict(desk), 3)

    assert {
        BatchReason.PREFLIGHT_NOT_ELIGIBLE,
        DenyReason.AUTHORIZATION_MISSING,
        DenyReason.CHECKPOINT_MISSING,
        DenyReason.DOSSIER_MISSING,
    } <= set(chapter.reasons)


def _deny_report(desk: Desk) -> str:
    result = desk.preflight(None)
    assert result.exit_code == 2, result.output
    return said_digest(result.output, "report")


def test_a_flagged_chapter_needs_its_checkpoint_at_plan_time(batch: BatchDesk) -> None:
    desk = batch.add(1, extras={3: QUOTE})
    report, _ = batch.flagged_report(1, (3,))

    chapter = _chapter(_evidence(desk, (report, [])), _verdict(desk), 3)

    assert DenyReason.CHECKPOINT_MISSING in chapter.reasons


def test_a_review_after_the_report_supersedes_it(batch: BatchDesk) -> None:
    desk = batch.add(1, extras={3: QUOTE})
    report, checkpoints = batch.flagged_report(1, (3,))
    desk.decide(3, ReviewStatus.HELD, at="2100-04-09T12:00:00Z")

    chapter = _chapter(_evidence(desk, (report, checkpoints)), _verdict(desk), 3)

    assert DenyReason.REVIEW_NOT_APPROVED in chapter.reasons
    assert chapter.clearance is None


def test_an_authorization_expired_at_plan_time_excludes_the_flagged_chapter(
    batch: BatchDesk,
) -> None:
    desk = batch.add(1, extras={3: QUOTE})
    review = batch.flagged_report(1, (3,))

    chapter = _chapter(_evidence(desk, review, at="2100-05-02T00:00:00Z"), _verdict(desk), 3)

    assert DenyReason.AUTHORIZATION_EXPIRED in chapter.reasons


def test_a_report_of_another_document_is_a_changed_preflight(batch: BatchDesk) -> None:
    first = batch.add(1, extras={3: QUOTE})
    batch.add(2, extras={3: QUOTE})
    review = batch.flagged_report(2, (3,))

    chapter = _chapter(_evidence(first, review), _verdict(first), 3)

    assert BatchReason.PREFLIGHT_CHANGED in chapter.reasons
    assert DenyReason.AUTHORIZATION_DOCUMENT_MISMATCH in chapter.reasons


def test_a_chapter_restaged_since_the_report_is_a_changed_preflight(batch: BatchDesk) -> None:
    desk = batch.add(1, extras={3: QUOTE})
    review = batch.flagged_report(1, (3,))
    desk.stage(3, QUOTE + "<p>Endret.</p>", name="ch3-new")

    chapter = _chapter(_evidence(desk, review), _verdict(desk), 3)

    assert BatchReason.PREFLIGHT_CHANGED in chapter.reasons


def test_a_page_changed_since_staging_blocks_a_clean_chapter(batch: BatchDesk) -> None:
    desk = batch.add(1)
    desk.pages[3].write_bytes(desk.pages[3].read_bytes() + b"<!-- changed -->")

    chapter = _chapter(_evidence(desk), _verdict(desk), 3)

    assert chapter.reasons == (DenyReason.SOURCE_PAGE_MISSING,)


def test_a_result_its_page_does_not_reproduce_blocks_a_clean_chapter(
    batch: BatchDesk,
) -> None:
    desk = batch.add(1)
    evidence = _evidence(desk)
    observed = _observed(evidence, 2)
    redated = observed.result.model_copy(update={"ministry": "Et annet departement"})
    swapped = tuple(
        Observed(redated, item.page, item.history) if item is observed else item
        for item in evidence.inputs.observed
    )
    inputs = evidence.inputs._replace(observed=swapped)
    changed = DocumentEvidence(inputs, evaluate(inputs), None)

    chapter = _chapter(changed, _verdict(desk), 2)

    assert chapter.reasons == (DenyReason.OBSERVATION_NOT_REPRODUCIBLE,)


def test_a_changed_metadata_page_blocks_every_clean_chapter(batch: BatchDesk) -> None:
    desk = batch.add(1)
    evidence = _evidence(desk)
    inputs = evidence.inputs._replace(metadata_page=b"<html>other</html>")
    changed = DocumentEvidence(inputs, evaluate(inputs), None)

    document = planned_document(changed, _verdict(desk))

    reasons = {chapter.reasons for chapter in document.chapters}
    assert reasons == {(DenyReason.METADATA_NOT_REPRODUCIBLE,)}


def test_a_corrupt_review_chain_blocks_a_clean_chapter(batch: BatchDesk) -> None:
    desk = batch.add(1)
    evidence = _evidence(desk)
    observed = _observed(evidence, 2)
    broken = tuple(
        Observed(item.result, item.page, None) if item is observed else item
        for item in evidence.inputs.observed
    )
    inputs = evidence.inputs._replace(observed=broken)

    chapter = _chapter(DocumentEvidence(inputs, evaluate(inputs), None), _verdict(desk), 2)

    assert chapter.reasons == (DenyReason.REVIEW_LEDGER_CORRUPT,)


def _batch(batch: BatchDesk, documents: list[DocumentEvidence], at: str = PLAN_AT) -> Batch:
    desks = list(batch.desks.values())
    return Batch("forarbeider/batch-1", moment(at), _verdict(*desks), documents)


def test_the_same_inputs_give_the_same_plan_bytes_in_any_order(batch: BatchDesk) -> None:
    for number in (1, 2, 3):
        batch.add(number)
    documents = [_evidence(desk) for desk in batch.desks.values()]

    first = plan_of(_batch(batch, documents))
    second = plan_of(_batch(batch, documents[::-1]))

    assert record_text(first) == record_text(second)
    assert digest(record_text(first).encode()) == digest(record_text(second).encode())
    assert first.counts.standing_policy == 6


def test_a_changed_input_changes_the_inputs_digest_and_reseeds_the_sample(
    batch: BatchDesk,
) -> None:
    for number in range(1, 5):
        batch.add(number)
    documents = [_evidence(desk) for desk in batch.desks.values()]

    before = plan_of(_batch(batch, documents))
    later = plan_of(_batch(batch, documents, at="2100-04-10T12:00:01Z"))
    fewer = plan_of(_batch(batch, documents[:3]))

    assert len({before.inputs_sha256, later.inputs_sha256, fewer.inputs_sha256}) == 3
    assert before.sample != later.sample
    assert len(before.sample) == 5


def test_outcome_is_none_for_a_chapter_the_report_does_not_list(batch: BatchDesk) -> None:
    desk = batch.add(1)
    evidence = _evidence(desk)

    assert _outcome(evidence.fresh, 999) is None


def test_clean_reasons_handles_a_chapter_the_fresh_report_does_not_list(
    batch: BatchDesk,
) -> None:
    desk = batch.add(1)
    evidence = _evidence(desk)
    observed = _observed(evidence, 2)
    forged = DocumentEvidence(
        evidence.inputs, evidence.fresh.model_copy(update={"chapters": ()}), evidence.report
    )

    reasons = clean_reasons(forged, _verdict(desk), observed)

    # B1b: a missing fresh outcome excludes; it was silently cleared before.
    expected = set(evidence.fresh.document_reasons) - REVIEW_PATH
    assert reasons == expected | {BatchReason.PREFLIGHT_OUTCOME_MISSING}


def test_review_reasons_handles_a_chapter_the_fresh_report_does_not_list(
    batch: BatchDesk,
) -> None:
    desk = batch.add(1, extras={3: QUOTE})
    review = batch.flagged_report(1, (3,))
    evidence = _evidence(desk, review)
    observed = _observed(evidence, 3)
    forged = DocumentEvidence(
        evidence.inputs, evidence.fresh.model_copy(update={"chapters": ()}), evidence.report
    )

    reasons = review_reasons(forged, observed)

    assert BatchReason.PREFLIGHT_NOT_ELIGIBLE in reasons


def test_review_reasons_or_not_and_for_a_stale_report_still_planned(batch: BatchDesk) -> None:
    desk = batch.add(1, extras={3: QUOTE})
    review = batch.flagged_report(1, (3,))
    evidence = _evidence(desk, review)
    observed = _observed(evidence, 3)
    assert evidence.report is not None
    forged_record = evidence.report.record.model_copy(
        update={"outcome": "deny", "document_reasons": (DenyReason.REVIEW_MISSING,)}
    )
    forged = DocumentEvidence(
        evidence.inputs, evidence.fresh, Stored(forged_record, evidence.report.sha256)
    )

    reasons = review_reasons(forged, observed)

    assert BatchReason.PREFLIGHT_NOT_ELIGIBLE in reasons


def test_review_clearance_or_and_precedence_and_refusal_fields(batch: BatchDesk) -> None:
    desk = batch.add(1)
    desk.decide(2, ReviewStatus.APPROVED)
    evidence = _evidence(desk)
    observed = _observed(evidence, 2)
    assert observed.history
    assert evidence.report is None

    with pytest.raises(ForarbeiderRefusedError) as caught:
        _review_clearance(evidence, observed)

    assert caught.value.reason is RefusalReason.RELEASE_INVALID
    assert caught.value.detail == observed.result.id


def test_a_withheld_chapters_hold_reasons_are_the_actual_values(batch: BatchDesk) -> None:
    desk = batch.add(1, extras={3: TABLE})

    chapter = _chapter(_evidence(desk), _verdict(desk), 3)

    assert chapter.hold_reasons == tuple(str(item) for item in desk.results[3].hold_reasons)
    assert "table" in chapter.hold_reasons


def test_planned_document_report_sha256_is_populated_from_evidence(batch: BatchDesk) -> None:
    desk = batch.add(1, extras={3: QUOTE})
    review = batch.flagged_report(1, (3,))
    evidence = _evidence(desk, review)

    document = planned_document(evidence, _verdict(desk))

    assert document.report_sha256 == evidence.report.sha256
    assert document.report_sha256 is not None


def test_sha_is_none_only_for_a_none_input() -> None:
    assert _sha(None) is None
    assert _sha(b"hello") == hashlib.sha256(b"hello").hexdigest()


def test_chapter_input_reflects_the_actual_page_and_review_history(batch: BatchDesk) -> None:
    desk = batch.add(1)
    desk.decide(2, ReviewStatus.APPROVED)
    evidence = _evidence(desk)
    observed = _observed(evidence, 2)
    assert observed.page is not None
    assert observed.history

    built = _chapter_input(observed)

    assert built.page_sha256 == hashlib.sha256(observed.page).hexdigest()
    assert built.review_revision == len(observed.history)
    assert built.review_head_sha256 == observed.history[-1].sha256


def test_chapter_input_with_no_review_chain_has_a_sentinel_revision(batch: BatchDesk) -> None:
    desk = batch.add(1)
    evidence = _evidence(desk)
    observed = _observed(evidence, 2)
    no_history = Observed(observed.result, observed.page, None)

    built = _chapter_input(no_history)

    assert built.review_revision == -1
    assert built.review_head_sha256 is None


def test_chapter_input_with_an_empty_review_chain_has_zero_revision(batch: BatchDesk) -> None:
    desk = batch.add(1)
    evidence = _evidence(desk)
    observed = _observed(evidence, 2)
    empty_history = Observed(observed.result, observed.page, ())

    built = _chapter_input(empty_history)

    assert built.review_revision == 0
    assert built.review_head_sha256 is None


def test_document_input_reflects_the_metadata_page_and_report(batch: BatchDesk) -> None:
    desk = batch.add(1, extras={3: QUOTE})
    review = batch.flagged_report(1, (3,))
    evidence = _evidence(desk, review)
    assert evidence.inputs.metadata_page is not None

    built = document_input(evidence)

    assert built.metadata_page_sha256 == hashlib.sha256(evidence.inputs.metadata_page).hexdigest()
    assert built.report_sha256 == evidence.report.sha256
    assert built.report_sha256 is not None


def test_a_clean_chapter_the_fresh_preflight_has_no_outcome_for_is_excluded(
    batch: BatchDesk,
) -> None:
    """B1b regression: a missing fresh outcome is not silence; the policy clears nothing then."""
    desk = batch.add(1)
    evidence = _evidence(desk)
    fresh = evidence.fresh.model_copy(
        update={"chapters": tuple(i for i in evidence.fresh.chapters if i.chapter_number != 2)}
    )
    missing = evidence._replace(fresh=fresh)

    chapter = _chapter(missing, _verdict(desk), 2)

    assert chapter.included is False
    assert chapter.clearance is None
    assert BatchReason.PREFLIGHT_OUTCOME_MISSING in chapter.reasons
    assert _chapter(evidence, _verdict(desk), 2).included is True


def test_a_flagged_chapter_the_fresh_preflight_has_no_outcome_for_is_excluded(
    batch: BatchDesk,
) -> None:
    desk = batch.add(2, extras={3: QUOTE})
    review = batch.flagged_report(2, (3,))
    evidence = _evidence(desk, review)
    fresh = evidence.fresh.model_copy(
        update={"chapters": tuple(i for i in evidence.fresh.chapters if i.chapter_number != 3)}
    )

    chapter = _chapter(evidence._replace(fresh=fresh), _verdict(desk), 3)

    assert chapter.included is False
    assert BatchReason.PREFLIGHT_OUTCOME_MISSING in chapter.reasons
