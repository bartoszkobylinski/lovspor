"""The append-only store of review decisions (#738, ADR-0018 PR3A).

``<ledger>/<subject key>/<revision>.json``. Each subject has its own chain, so
changed source bytes or text start from nothing, and each record states the
digest of the one before, so an edit or a removal inside a chain is found on the
next read. What this cannot find is a chain cut at its end, or one rebuilt whole
by someone who can write here: the digests live beside what they protect.

A revision is a name taken by ``os.link``, so two reviewers cannot both write
one: the second finds it taken. Retrying a decision that landed changes nothing.
"""

import hashlib
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.review_models import (
    Attestation,
    ReviewDecision,
    ReviewReason,
    ReviewStatus,
    ReviewSubject,
    may_follow,
    review_text,
    subject_key,
)
from lovspor.forarbeider.staging import (
    ensure_private_directory,
    private_directory_exists,
    read_private,
    write_observation,
)

#: Far above a record of a few hundred bytes per evidence reference.
MAX_RECORD_BYTES = 1024 * 1024

_SUBJECT_KEY = re.compile(r"[0-9a-f]{64}")


class StoredDecision(BaseModel):
    """A decision as read back, with the digest of its exact bytes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: ReviewDecision
    sha256: str


class DecisionRequest(BaseModel):
    """What a reviewer asks to record. ``None`` is a value left out, refused on recording."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: ReviewSubject
    reviewer: str | None
    decided_at: str | None
    status: ReviewStatus
    reason_codes: tuple[ReviewReason, ...]
    evidence: tuple[str, ...]
    attestations: tuple[Attestation, ...]
    expected_revision: int


class RecordOutcome(BaseModel):
    """The decision now in place; ``written`` is ``False`` when a retry found it there."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stored: StoredDecision
    written: bool


def current_status(history: tuple[StoredDecision, ...]) -> ReviewStatus:
    return history[-1].decision.status if history else ReviewStatus.PENDING


def load_history(ledger: Path, subject: ReviewSubject) -> tuple[StoredDecision, ...]:
    """The verified chain of ``subject``, empty when nothing was decided.

    Raises:
        ForarbeiderRefusedError: an unsafe path or a broken chain.
    """
    key = subject_key(subject)
    if not private_directory_exists(ledger) or not private_directory_exists(ledger / key):
        return ()
    return _chain(ledger / key)


def verify_ledger(ledger: Path) -> tuple[tuple[StoredDecision, ...], ...]:
    """Every chain in ``ledger`` in key order, or a refusal naming the first broken one.

    Raises:
        ForarbeiderRefusedError: an unsafe path, a foreign entry or a broken chain.
    """
    if not private_directory_exists(ledger):
        return ()
    chains = []
    for directory in sorted(ledger.iterdir()):
        if _SUBJECT_KEY.fullmatch(directory.name) is None:
            raise ForarbeiderRefusedError(RefusalReason.REVIEW_LEDGER_CORRUPT, str(directory))
        private_directory_exists(directory)
        chains.append(_chain(directory))
    return tuple(chain for chain in chains if chain)


def record_decision(ledger: Path, request: DecisionRequest) -> RecordOutcome:
    """Append one decision, or find that this very decision already landed.

    Raises:
        ForarbeiderRefusedError: ``review_conflict`` for a reviewer who has not
            seen the latest revision, ``review_transition``, ``review_invalid``,
            or an unsafe path or broken chain.
    """
    history = load_history(ledger, request.subject)
    landed = _landed(request, history)
    if landed is not None:
        return RecordOutcome(stored=landed, written=False)
    if request.expected_revision != len(history):
        raise ForarbeiderRefusedError(RefusalReason.REVIEW_CONFLICT, "expected revision")
    decision = _decision(request, history)
    if not _may_extend(history, decision):
        raise ForarbeiderRefusedError(RefusalReason.REVIEW_TRANSITION, decision.status.value)
    return _append(ledger, decision)


def _landed(request: DecisionRequest, history: tuple[StoredDecision, ...]) -> StoredDecision | None:
    if not history or request.expected_revision != len(history) - 1:
        return None
    retried = _decision(request, history[:-1])
    return history[-1] if retried == history[-1].decision else None


def _decision(request: DecisionRequest, history: tuple[StoredDecision, ...]) -> ReviewDecision:
    try:
        return ReviewDecision.model_validate(
            {
                "schema_version": 1,
                "subject": request.subject,
                "revision": len(history) + 1,
                "previous_decision_sha256": history[-1].sha256 if history else None,
                "reviewer": request.reviewer,
                "decided_at": request.decided_at,
                "status": request.status,
                "reason_codes": request.reason_codes,
                "evidence": request.evidence,
                "attestations": request.attestations,
                "grants_publication": False,
                "grants_crawling": False,
            }
        )
    except ValidationError as error:
        raise ForarbeiderRefusedError(RefusalReason.REVIEW_INVALID, _fields(error)) from error


def _fields(error: ValidationError) -> str:
    """Field names only: the input may be the very name the grammar refused."""
    fields = sorted({str(entry["loc"][0]) for entry in error.errors() if entry["loc"]})
    return ",".join(fields) or "decision"


def _append(ledger: Path, decision: ReviewDecision) -> RecordOutcome:
    ensure_private_directory(ledger)
    directory = ledger / subject_key(decision.subject)
    ensure_private_directory(directory)
    text = review_text(decision)
    try:
        written = write_observation(directory / _record_name(decision.revision), text)
    except ForarbeiderRefusedError as error:
        if error.reason is RefusalReason.OBSERVATION_EXISTS:
            raise ForarbeiderRefusedError(RefusalReason.REVIEW_CONFLICT, error.detail) from error
        raise
    stored = StoredDecision(decision=decision, sha256=_digest(text.encode()))
    return RecordOutcome(stored=stored, written=written)


def _chain(directory: Path) -> tuple[StoredDecision, ...]:
    chain: list[StoredDecision] = []
    names = sorted(path.name for path in directory.iterdir() if not path.name.startswith("."))
    for position, name in enumerate(names, start=1):
        stored = _read_record(directory / name)
        extends = _may_extend(tuple(chain), stored.decision)
        linked = (
            stored.decision.revision == position
            and name == _record_name(position)
            and stored.decision.previous_decision_sha256 == (chain[-1].sha256 if chain else None)
            and subject_key(stored.decision.subject) == directory.name
        )
        if not (linked and extends):
            raise ForarbeiderRefusedError(RefusalReason.REVIEW_LEDGER_CORRUPT, str(directory))
        chain.append(stored)
    return tuple(chain)


def _read_record(path: Path) -> StoredDecision:
    raw = read_private(path, MAX_RECORD_BYTES)
    try:
        decision = ReviewDecision.model_validate_json(raw)
    except ValidationError as error:
        raise ForarbeiderRefusedError(RefusalReason.REVIEW_LEDGER_CORRUPT, str(path)) from error
    if review_text(decision).encode() != raw:
        raise ForarbeiderRefusedError(RefusalReason.REVIEW_LEDGER_CORRUPT, str(path))
    return StoredDecision(decision=decision, sha256=_digest(raw))


def _may_extend(history: tuple[StoredDecision, ...], decision: ReviewDecision) -> bool:
    if not may_follow(current_status(history), decision.status):
        return False
    return not history or history[-1].decision.decided_at <= decision.decided_at


def _record_name(revision: int) -> str:
    return f"{revision:06d}.json"


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
