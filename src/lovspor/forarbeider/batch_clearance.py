"""Each chapter's one clearance path in a batch, from inputs already read (#738, A2b).

No I/O and no clock, like :mod:`lovspor.forarbeider.release_preflight`, whose
verdict it reuses: the preflight is evaluated once per document at the plan's
moment, and its reasons are split between the two paths.

- A ``withheld`` chapter is excluded, always.
- A ``clean`` chapter may be cleared only by the standing policy. It ignores
  exactly the :data:`REVIEW_PATH` reasons (dossier, authorization, evidence,
  review status and checkpoint), and every other reason blocks it, so a
  preflight reason this module has not classified blocks too. The policy and
  the current source proof must have no fault, the policy must name the
  document type, the chapter's page must be one the proof run fetched, and a
  person's held or rejected decision vetoes it.
- A ``flagged`` or ``unknown`` chapter may be cleared only by the per-chapter
  review: the owner's stored eligible report must cover it, and the preflight
  evaluated again now must still find it eligible with the same identity.

There is no fallback either way: a clean chapter never takes the review path
when the policy fails, and a flagged one never takes the policy.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime
from typing import NamedTuple

from lovspor.forarbeider.batch_classify import ChapterClass, classify_result, needs_review
from lovspor.forarbeider.batch_models import (
    BatchInputs,
    BatchPlan,
    BatchReason,
    ChapterInput,
    DocumentInput,
    ExclusionReason,
    PlannedChapter,
    PlannedDocument,
    counts_of,
    coverage_of,
    sample_of,
)
from lovspor.forarbeider.chapter_models import result_text
from lovspor.forarbeider.corpus_models import ChapterClearance, PolicyClearance, ReviewClearance
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.models import metadata_text
from lovspor.forarbeider.policy_models import PolicyFault, StandingPolicy
from lovspor.forarbeider.policy_source import SourceFault
from lovspor.forarbeider.release_models import (
    ChapterOutcome,
    DenyReason,
    PreflightReport,
    destination_of,
    digest,
    record_text,
)
from lovspor.forarbeider.release_preflight import Observed, PreflightInputs, Stored, current_pin
from lovspor.forarbeider.review_models import ReviewStatus

#: What only the per-chapter path needs. A clean chapter ignores these and nothing else.
REVIEW_PATH: frozenset[DenyReason] = frozenset(
    {
        DenyReason.AUTHORIZATION_CHAPTER_MISMATCH,
        DenyReason.AUTHORIZATION_DOCUMENT_MISMATCH,
        DenyReason.AUTHORIZATION_EXPIRED,
        DenyReason.AUTHORIZATION_MISSING,
        DenyReason.AUTHORIZATION_NOT_YET_VALID,
        DenyReason.AUTHORIZATION_REVOKED,
        DenyReason.AUTHORIZED_BEFORE_APPROVAL,
        DenyReason.AUTHORIZED_BEFORE_DOSSIER,
        DenyReason.CHECKPOINT_MISMATCH,
        DenyReason.CHECKPOINT_MISSING,
        DenyReason.DOSSIER_CHAPTER_MISMATCH,
        DenyReason.DOSSIER_CHAPTER_MISSING,
        DenyReason.DOSSIER_DOCUMENT_MISMATCH,
        DenyReason.DOSSIER_MISSING,
        DenyReason.EVALUATED_BEFORE_APPROVAL,
        DenyReason.EVALUATED_BEFORE_DECISION,
        DenyReason.EVIDENCE_DIGEST_MISMATCH,
        DenyReason.EVIDENCE_INCOMPLETE,
        DenyReason.EVIDENCE_UNAVAILABLE,
        DenyReason.NOT_AUTHORIZED,
        DenyReason.OBSERVATION_MISSING,
        DenyReason.PRIVACY_FINDING_CONTRADICTED,
        DenyReason.PURPOSE_EVIDENCE_MISSING,
        DenyReason.PURPOSE_NOT_AUTHORIZED,
        DenyReason.REVIEW_MISSING,
        DenyReason.REVIEW_NOT_APPROVED,
        DenyReason.REVIEW_SUPERSEDED,
    }
)
#: The source proofs both paths need; listed so a test can pin the partition total.
SOURCE_PROOF: frozenset[DenyReason] = frozenset(
    {
        DenyReason.BODY_WITHHELD,
        DenyReason.CHAPTER_DUPLICATE,
        DenyReason.CHAPTER_FOREIGN,
        DenyReason.CHAPTER_NOT_IN_INVENTORY,
        DenyReason.METADATA_NOT_REPRODUCIBLE,
        DenyReason.METADATA_PAGE_MISSING,
        DenyReason.OBSERVATION_NOT_REPRODUCIBLE,
        DenyReason.REVIEW_LEDGER_CORRUPT,
        DenyReason.SOURCE_PAGE_MISSING,
    }
)
_VETO = frozenset({ReviewStatus.HELD, ReviewStatus.REJECTED})


class PolicyVerdict(NamedTuple):
    """The policy at the plan's moment and the source proof it was compared against."""

    sha256: str
    policy: StandingPolicy
    faults: tuple[PolicyFault | SourceFault, ...]
    proof_sha256: str
    fetched: frozenset[tuple[str, str]]


class DocumentEvidence(NamedTuple):
    """One proposition's inputs, its preflight evaluated now, and the owner's stored report."""

    inputs: PreflightInputs
    fresh: PreflightReport
    report: Stored[PreflightReport] | None


def _outcome(report: PreflightReport, number: int) -> ChapterOutcome | None:
    return next((item for item in report.chapters if item.chapter_number == number), None)


def _vetoed(observed: Observed) -> bool:
    history = observed.history
    return bool(history) and history is not None and history[-1].decision.status in _VETO


def clean_reasons(
    evidence: DocumentEvidence, verdict: PolicyVerdict, observed: Observed
) -> set[ExclusionReason]:
    """Every reason the standing policy cannot clear this clean chapter; empty when it can."""
    result = observed.result
    outcome = _outcome(evidence.fresh, result.chapter_number)
    found = set(evidence.fresh.document_reasons) | set(() if outcome is None else outcome.reasons)
    reasons: set[ExclusionReason] = set(found - REVIEW_PATH)
    reasons |= set(verdict.faults)
    if result.document_type not in verdict.policy.document_types:
        reasons.add(BatchReason.POLICY_SCOPE_MISMATCH)
    if (result.source_page_url, result.source_sha256) not in verdict.fetched:
        reasons.add(BatchReason.PAGE_NOT_FETCHED)
    if _vetoed(observed):
        reasons.add(BatchReason.REVIEW_VETO)
    return reasons


def _identity(report: PreflightReport, number: int) -> tuple[str, str | None, str | None] | None:
    outcome = _outcome(report, number)
    if outcome is None:
        return None
    return (outcome.chapter_id, outcome.source_sha256, outcome.text_sha256)


def review_reasons(evidence: DocumentEvidence, observed: Observed) -> set[ExclusionReason]:
    """Every reason the per-chapter path cannot clear this chapter now; empty when it can."""
    stored = evidence.report
    if stored is None:
        return {BatchReason.PREFLIGHT_MISSING}
    number, fresh, report = observed.result.chapter_number, evidence.fresh, stored.record
    outcome = _outcome(fresh, number)
    reasons: set[ExclusionReason] = set(fresh.document_reasons)
    reasons |= set(() if outcome is None else outcome.reasons)
    if outcome is None or not outcome.eligible:
        reasons.add(BatchReason.PREFLIGHT_NOT_ELIGIBLE)
    if report.outcome != "eligible_preview" or number not in report.planned_chapters:
        reasons.add(BatchReason.PREFLIGHT_NOT_ELIGIBLE)
    if report.document != fresh.document or _identity(report, number) != _identity(fresh, number):
        reasons.add(BatchReason.PREFLIGHT_CHANGED)
    return reasons


def _review_clearance(evidence: DocumentEvidence, observed: Observed) -> ReviewClearance:
    """The recorded references of a chapter ``review_reasons`` found none against.

    Raises:
        ForarbeiderRefusedError: ``release_invalid``: the eligible preflight
            ``review_reasons`` accepted lacks a chain or an authorization, which
            :func:`~lovspor.forarbeider.release_preflight.evaluate` never allows.
    """
    history, stored = observed.history, evidence.report
    if not history or stored is None or stored.record.authorization_sha256 is None:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_INVALID, observed.result.id)
    return ReviewClearance(
        cleared_by="owner_review",
        review_decided_at=history[-1].decision.decided_at,
        review=current_pin(observed.result, history),
        authorization_sha256=stored.record.authorization_sha256,
        report_sha256=stored.sha256,
        provenance="recorded",
        individually_reviewed=True,
    )


def _policy_clearance(verdict: PolicyVerdict) -> PolicyClearance:
    return PolicyClearance(
        cleared_by="standing_policy",
        policy_sha256=verdict.sha256,
        source_proof_sha256=verdict.proof_sha256,
        individually_reviewed=False,
    )


def routed(
    evidence: DocumentEvidence, verdict: PolicyVerdict, observed: Observed, kind: ChapterClass
) -> tuple[set[ExclusionReason], ChapterClearance | None]:
    """The one path ``kind`` may take: its reasons, and its clearance when there are none."""
    if kind is ChapterClass.WITHHELD:
        return {BatchReason.WITHHELD}, None
    if needs_review(kind):
        reasons = review_reasons(evidence, observed)
        return reasons, None if reasons else _review_clearance(evidence, observed)
    reasons = clean_reasons(evidence, verdict, observed)
    return reasons, None if reasons else _policy_clearance(verdict)


def planned_chapter(
    evidence: DocumentEvidence, verdict: PolicyVerdict, observed: Observed
) -> PlannedChapter:
    result = observed.result
    kind = classify_result(result)
    reasons, clearance = routed(evidence, verdict, observed, kind)
    return PlannedChapter(
        chapter_number=result.chapter_number,
        chapter_id=result.id,
        source_page_url=result.source_page_url,
        source_sha256=result.source_sha256,
        text_sha256=result.text_sha256,
        extractor_version=result.extractor_version,
        normalizer_version=result.normalizer_version,
        hold_reasons=tuple(str(item) for item in result.hold_reasons),
        classification=kind,
        included=clearance is not None,
        clearance=clearance,
        reasons=tuple(sorted(reasons)),
    )


def planned_document(evidence: DocumentEvidence, verdict: PolicyVerdict) -> PlannedDocument:
    ordered = sorted(evidence.inputs.observed, key=lambda item: item.result.chapter_number)
    chapters = tuple(planned_chapter(evidence, verdict, item) for item in ordered)
    listed = tuple(chapter.chapter_number for chapter in evidence.inputs.metadata.chapters)
    document = evidence.fresh.document
    return PlannedDocument(
        document=document,
        destination=destination_of(document),
        listed_chapters=listed,
        report_sha256=None if evidence.report is None else evidence.report.sha256,
        chapters=chapters,
        coverage=coverage_of(listed, chapters),
    )


def _sha(raw: bytes | None) -> str | None:
    return None if raw is None else hashlib.sha256(raw).hexdigest()


def _chapter_input(observed: Observed) -> ChapterInput:
    history = observed.history or ()
    return ChapterInput(
        result_sha256=digest(result_text(observed.result).encode()),
        page_sha256=_sha(observed.page),
        review_revision=len(history) if observed.history is not None else -1,
        review_head_sha256=history[-1].sha256 if history else None,
    )


def document_input(evidence: DocumentEvidence) -> DocumentInput:
    inputs = evidence.inputs
    ordered = sorted(inputs.observed, key=lambda item: item.result.chapter_number)
    pinned = (f"{key}@{rev}:{head}" for key, (rev, head) in inputs.checkpoints.items())
    return DocumentInput(
        document_id=evidence.fresh.document.id,
        metadata_sha256=digest(metadata_text(inputs.metadata).encode()),
        metadata_page_sha256=_sha(inputs.metadata_page),
        chapters=tuple(_chapter_input(item) for item in ordered),
        report_sha256=None if evidence.report is None else evidence.report.sha256,
        checkpoints=tuple(sorted(pinned)),
    )


class Batch(NamedTuple):
    """Everything a plan is made of, already read and evaluated at ``at``."""

    target_branch: str
    at: datetime
    verdict: PolicyVerdict
    documents: Sequence[DocumentEvidence]


#: What every plan states, whatever it found: a plan permits nothing.
_FIXED_PLAN: dict[str, object] = {
    "schema_version": 1,
    "record_type": "forarbeider_batch_plan",
    "publication_authorized": False,
    "writes_corpus": False,
    "applies": False,
    "owner_confirmation": "required_not_given",
    "legal_sufficiency_decided_by_code": False,
    "sample_is_gate": False,
}


def inputs_of(batch: Batch, ordered: Sequence[DocumentEvidence]) -> BatchInputs:
    """What the plan's seed digests: every staged byte and named record, never a path."""
    return BatchInputs(
        at=batch.at,
        target_branch=batch.target_branch,
        policy_sha256=batch.verdict.sha256,
        source_proof_sha256=batch.verdict.proof_sha256,
        documents=tuple(document_input(item) for item in ordered),
    )


def plan_of(batch: Batch) -> BatchPlan:
    """The deterministic plan: the same inputs give the same bytes, and so the same digest."""
    verdict = batch.verdict
    ordered = sorted(batch.documents, key=lambda item: item.fresh.document.id)
    seed = digest(record_text(inputs_of(batch, ordered)).encode())
    documents = tuple(planned_document(item, verdict) for item in ordered)
    found = {
        "inputs_sha256": seed,
        "at": batch.at,
        "target_branch": batch.target_branch,
        "policy_sha256": verdict.sha256,
        "source_proof_sha256": verdict.proof_sha256,
        "documents": documents,
        "sample": sample_of(documents, seed),
        "counts": counts_of(documents),
    }
    return BatchPlan.model_validate({**_FIXED_PLAN, **found})
