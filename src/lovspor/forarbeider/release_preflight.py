"""The release preflight verdict, from inputs already read (#738, ADR-0018 PR3B-1).

No I/O and no clock: the same inputs give the same report. Every check that
fails adds a reason, and any reason denies, so absent or ambiguous evidence is
``deny`` by construction. A PR3A approval is one input among many: it never
clears a chapter without a dossier, an authorization and a matching checkpoint.

The page bytes are parsed again here, so a chapter result, its text digest and
its extractor version are proved by the saved page, not taken from the file
that states them.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import NamedTuple

from lovspor.forarbeider.chapter import build_chapter_result
from lovspor.forarbeider.chapter_models import ChapterResult, HoldReason
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.models import ForarbeidRecord
from lovspor.forarbeider.page import build_record
from lovspor.forarbeider.release_models import (
    GATE_KINDS,
    AuthorizedChapter,
    ChapterOutcome,
    Coverage,
    DenyReason,
    DocumentIdentity,
    EvidenceArtifact,
    EvidenceKind,
    PreflightReport,
    ReleaseAuthorization,
    ReleaseDossier,
    ReleaseGate,
    ReviewPin,
    chapter_identity,
    destination_of,
    document_of,
)
from lovspor.forarbeider.review_ledger import StoredDecision
from lovspor.forarbeider.review_models import ReviewStatus, subject_key, subject_of

_PERSONAL_CANDIDATES = frozenset(
    {HoldReason.HEARING_PARTICIPANTS, HoldReason.PERSONAL_DATA, HoldReason.SENSITIVE_DATA}
)


class Observed(NamedTuple):
    """A staged chapter, its saved page if one was given, and its review chain.

    ``history`` is ``None`` when the chain did not verify.
    """

    result: ChapterResult
    page: bytes | None
    history: tuple[StoredDecision, ...] | None


class Stored[T](NamedTuple):
    record: T
    sha256: str


class PreflightInputs(NamedTuple):
    """Everything a verdict depends on. ``evidence`` maps a path to its actual digest."""

    metadata: ForarbeidRecord
    metadata_page: bytes | None
    observed: tuple[Observed, ...]
    dossier: Stored[ReleaseDossier] | None
    evidence: Mapping[str, str | None]
    authorization: Stored[ReleaseAuthorization] | None
    revoked: bool
    checkpoints: Mapping[str, tuple[int, str]]
    at: datetime


#: What every report states, whatever it found: it is a plan, and a plan permits nothing.
_FIXED_REPORT: dict[str, object] = {
    "schema_version": 1,
    "record_type": "forarbeider_release_preflight",
    "publication_authorized": False,
    "writes_corpus": False,
    "asserts_complete_proposition": False,
    "legal_sufficiency_decided_by_code": False,
    "checkpoint_basis": "operator_supplied_unauthenticated",
}


def evaluate(inputs: PreflightInputs) -> PreflightReport:
    """The deterministic verdict on every observed or authorized chapter."""
    document = document_of(inputs.metadata)
    blocking = tuple(sorted(_document_reasons(inputs, document)))
    outcomes = _outcomes(inputs, bool(blocking))
    planned = tuple(item.chapter_number for item in outcomes if item.eligible)
    preview = not blocking and bool(planned)
    found = {
        "outcome": "eligible_preview" if preview else "deny",
        "evaluated_at": inputs.at,
        "document": document,
        "listed_chapters": _listed(inputs.metadata),
        **_digests(inputs),
        "coverage": _coverage(inputs.metadata, planned, preview),
        "proposed_destination": destination_of(document) if preview else None,
        "planned_chapters": planned,
        "document_reasons": blocking,
        "chapters": outcomes,
    }
    return PreflightReport.model_validate({**_FIXED_REPORT, **found})


def _digests(inputs: PreflightInputs) -> dict[str, str | None]:
    dossier, authorization = inputs.dossier, inputs.authorization
    return {
        "dossier_sha256": None if dossier is None else dossier.sha256,
        "authorization_sha256": None if authorization is None else authorization.sha256,
    }


def _listed(metadata: ForarbeidRecord) -> tuple[int, ...]:
    return tuple(chapter.chapter_number for chapter in metadata.chapters)


def _coverage(metadata: ForarbeidRecord, planned: tuple[int, ...], preview: bool) -> Coverage:
    """Never "complete": the page lists what it lists, not what the proposition holds."""
    if not preview:
        return "none"
    return "every_listed_chapter" if planned == _listed(metadata) else "partial_curated_excerpt"


def _outcomes(inputs: PreflightInputs, blocked: bool) -> tuple[ChapterOutcome, ...]:
    """One per observed chapter, the first of a duplicate, and one per authorized but unobserved."""
    observed: dict[int, Observed] = {}
    for item in inputs.observed:
        observed.setdefault(item.result.chapter_number, item)
    outcomes = {number: _outcome(inputs, item, blocked) for number, item in observed.items()}
    authorized = () if inputs.authorization is None else inputs.authorization.record.chapters
    for chapter in authorized:
        outcomes.setdefault(chapter.chapter.chapter_number, _unobserved(chapter))
    return tuple(outcomes[number] for number in sorted(outcomes))


def _document_reasons(inputs: PreflightInputs, document: DocumentIdentity) -> set[DenyReason]:
    return (
        _metadata_reasons(inputs)
        | _scope_reasons(inputs, document)
        | _dossier_reasons(inputs, document)
        | _authorization_reasons(inputs, document)
    )


def _metadata_reasons(inputs: PreflightInputs) -> set[DenyReason]:
    metadata = inputs.metadata
    if inputs.metadata_page is None:
        return {DenyReason.METADATA_PAGE_MISSING}
    try:
        rebuilt = build_record(
            inputs.metadata_page, metadata.source_page_url, metadata.retrieved_at
        )
    except ForarbeiderRefusedError:
        return {DenyReason.METADATA_NOT_REPRODUCIBLE}
    return set() if rebuilt == metadata else {DenyReason.METADATA_NOT_REPRODUCIBLE}


def _scope_reasons(inputs: PreflightInputs, document: DocumentIdentity) -> set[DenyReason]:
    reasons: set[DenyReason] = set()
    numbers = [item.result.chapter_number for item in inputs.observed]
    if len(set(numbers)) != len(numbers):
        reasons.add(DenyReason.CHAPTER_DUPLICATE)
    if any(document_of(item.result) != document for item in inputs.observed):
        reasons.add(DenyReason.CHAPTER_FOREIGN)
    return reasons


def _dossier_reasons(inputs: PreflightInputs, document: DocumentIdentity) -> set[DenyReason]:
    if inputs.dossier is None:
        return {DenyReason.DOSSIER_MISSING}
    dossier = inputs.dossier.record
    reasons = set() if dossier.document == document else {DenyReason.DOSSIER_DOCUMENT_MISMATCH}
    if inputs.at < dossier.reviewed_at:
        reasons.add(DenyReason.EVALUATED_BEFORE_DECISION)
    return reasons


def _authorization_reasons(inputs: PreflightInputs, document: DocumentIdentity) -> set[DenyReason]:
    if inputs.authorization is None:
        return {DenyReason.AUTHORIZATION_MISSING}
    authorization = inputs.authorization.record
    reasons = _validity_reasons(authorization, inputs.at)
    if inputs.revoked:
        reasons.add(DenyReason.AUTHORIZATION_REVOKED)
    if authorization.document != document:
        reasons.add(DenyReason.AUTHORIZATION_DOCUMENT_MISMATCH)
    dossier = inputs.dossier
    if dossier is not None and dossier.sha256 != authorization.dossier_sha256:
        reasons.add(DenyReason.DOSSIER_MISSING)
    if not _purpose_verified(inputs, authorization.purpose_sha256):
        reasons.add(DenyReason.PURPOSE_EVIDENCE_MISSING)
    return reasons


def _validity_reasons(authorization: ReleaseAuthorization, at: datetime) -> set[DenyReason]:
    reasons: set[DenyReason] = set()
    if at < authorization.decided_at:
        reasons.add(DenyReason.EVALUATED_BEFORE_DECISION)
    if at < authorization.valid_from:
        reasons.add(DenyReason.AUTHORIZATION_NOT_YET_VALID)
    if at >= authorization.valid_until:
        reasons.add(DenyReason.AUTHORIZATION_EXPIRED)
    return reasons


def _purpose_verified(inputs: PreflightInputs, sha: str) -> bool:
    if inputs.dossier is None:
        return False
    return any(
        item.kind is EvidenceKind.PUBLICATION_PURPOSE
        and item.sha256 == sha
        and _intact(inputs, item)
        for item in inputs.dossier.record.evidence
    )


def _intact(inputs: PreflightInputs, artifact: EvidenceArtifact) -> bool:
    return inputs.evidence.get(artifact.path) == artifact.sha256


def _outcome(inputs: PreflightInputs, observed: Observed, blocked: bool) -> ChapterOutcome:
    number = observed.result.chapter_number
    authorized = _authorized_chapter(inputs, number)
    missing, evidence_reasons = _evidence(inputs, observed.result)
    reasons = (
        _source_reasons(inputs.metadata, observed)
        | _review_reasons(inputs, observed, authorized)
        | _binding_reasons(inputs, observed.result, authorized)
        | evidence_reasons
    )
    result = observed.result
    return ChapterOutcome(
        chapter_number=number,
        chapter_id=result.id,
        source_sha256=result.source_sha256,
        text_sha256=result.text_sha256,
        hold_reasons=tuple(result.hold_reasons),
        missing_evidence=missing,
        reasons=tuple(sorted(reasons)),
        eligible=not (reasons or missing or blocked),
    )


def _authorized_chapter(inputs: PreflightInputs, number: int) -> AuthorizedChapter | None:
    if inputs.authorization is None:
        return None
    chapters = inputs.authorization.record.chapters
    return next((item for item in chapters if item.chapter.chapter_number == number), None)


def _unobserved(authorized: AuthorizedChapter) -> ChapterOutcome:
    chapter = authorized.chapter
    return ChapterOutcome(
        chapter_number=chapter.chapter_number,
        chapter_id=chapter.chapter_id,
        source_sha256=None,
        text_sha256=None,
        hold_reasons=(),
        missing_evidence=(),
        reasons=(DenyReason.OBSERVATION_MISSING,),
        eligible=False,
    )


def _source_reasons(metadata: ForarbeidRecord, observed: Observed) -> set[DenyReason]:
    result = observed.result
    reasons: set[DenyReason] = set()
    listed = {(item.chapter_number, item.url, item.title) for item in metadata.chapters}
    if (result.chapter_number, result.source_page_url, result.chapter_title) not in listed:
        reasons.add(DenyReason.CHAPTER_NOT_IN_INVENTORY)
    if result.blocks is None:
        reasons.add(DenyReason.BODY_WITHHELD)
    if observed.page is None:
        return reasons | {DenyReason.SOURCE_PAGE_MISSING}
    if not _reproduced(observed.page, result):
        reasons.add(DenyReason.OBSERVATION_NOT_REPRODUCIBLE)
    return reasons


def _reproduced(page: bytes, result: ChapterResult) -> bool:
    try:
        rebuilt = build_chapter_result(page, result.source_page_url, result.retrieved_at)
    except ForarbeiderRefusedError:
        return False
    return rebuilt == result


def _review_reasons(
    inputs: PreflightInputs, observed: Observed, authorized: AuthorizedChapter | None
) -> set[DenyReason]:
    history = observed.history
    if history is None:
        return {DenyReason.REVIEW_LEDGER_CORRUPT}
    if not history:
        return {DenyReason.REVIEW_MISSING}
    reasons: set[DenyReason] = set()
    if history[-1].decision.status is not ReviewStatus.APPROVED:
        reasons.add(DenyReason.REVIEW_NOT_APPROVED)
    pin = current_pin(observed.result, history)
    checkpoint = inputs.checkpoints.get(pin.subject_key)
    if checkpoint is None:
        reasons.add(DenyReason.CHECKPOINT_MISSING)
    elif checkpoint != (pin.revision, pin.head_sha256):
        reasons.add(DenyReason.CHECKPOINT_MISMATCH)
    if authorized is not None and authorized.review != pin:
        reasons.add(DenyReason.REVIEW_SUPERSEDED)
    return reasons


def current_pin(result: ChapterResult, history: tuple[StoredDecision, ...]) -> ReviewPin:
    """The chain head of ``result`` as it stands; ``history`` must not be empty."""
    return ReviewPin(
        subject_key=subject_key(subject_of(result)),
        revision=len(history),
        head_sha256=history[-1].sha256,
    )


def _binding_reasons(
    inputs: PreflightInputs, result: ChapterResult, authorized: AuthorizedChapter | None
) -> set[DenyReason]:
    identity = chapter_identity(result)
    reasons: set[DenyReason] = set()
    if inputs.authorization is not None and authorized is None:
        reasons.add(DenyReason.NOT_AUTHORIZED)
    elif authorized is not None and authorized.chapter != identity:
        reasons.add(DenyReason.AUTHORIZATION_CHAPTER_MISMATCH)
    if inputs.dossier is None:
        return reasons
    held = [
        item
        for item in inputs.dossier.record.chapters
        if item.chapter_number == result.chapter_number
    ]
    if not held:
        reasons.add(DenyReason.DOSSIER_CHAPTER_MISSING)
    elif held[0] != identity:
        reasons.add(DenyReason.DOSSIER_CHAPTER_MISMATCH)
    return reasons


def _evidence(
    inputs: PreflightInputs, result: ChapterResult
) -> tuple[tuple[ReleaseGate, ...], set[DenyReason]]:
    """The gates this chapter lacks verified evidence for, and what was wrong with the rest."""
    if inputs.dossier is None:
        return tuple(ReleaseGate), {DenyReason.EVIDENCE_INCOMPLETE}
    bound = [
        item for item in inputs.dossier.record.evidence if result.chapter_number in item.chapters
    ]
    faults = (_artifact_fault(inputs, item) for item in bound)
    reasons = {fault for fault in faults if fault is not None}
    verified = {item.kind for item in bound if _intact(inputs, item)}
    missing = tuple(gate for gate, kinds in GATE_KINDS.items() if not kinds & verified)
    if missing:
        reasons.add(DenyReason.EVIDENCE_INCOMPLETE)
    if _contradicted(verified, result):
        reasons.add(DenyReason.PRIVACY_FINDING_CONTRADICTED)
    return missing, reasons


def _artifact_fault(inputs: PreflightInputs, artifact: EvidenceArtifact) -> DenyReason | None:
    actual = inputs.evidence.get(artifact.path)
    if actual is None:
        return DenyReason.EVIDENCE_UNAVAILABLE
    return None if actual == artifact.sha256 else DenyReason.EVIDENCE_DIGEST_MISMATCH


def _contradicted(verified: set[EvidenceKind], result: ChapterResult) -> bool:
    """A no-personal-data finding cannot stand alone where the extractor flagged candidates."""
    if EvidenceKind.PRIVACY_PROCESSING_BASIS in verified:
        return False
    flagged = _PERSONAL_CANDIDATES.intersection(result.hold_reasons)
    return EvidenceKind.PRIVACY_NO_PERSONAL_DATA in verified and bool(flagged)
