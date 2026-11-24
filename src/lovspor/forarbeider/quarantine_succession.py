"""AR5d-4: anchored, independently re-provable successors of the AR3 quarantine ledger (#738).

AR5a anchors a quarantine ledger as one document (``quarantine_ledger``) and
refuses every transition, because none is proven: a newer document is never
trusted because an anchor over it is signed. This module adds the proof.

**Storage.** One chain of
:class:`~lovspor.forarbeider.quarantine_succession_models.QuarantineSuccession`
entries, ``<root>/<ledger_id>/NNNNNN.json``, created once each and never
replaced (the AR5c ledger primitives), anchored by the witness as the
``quarantine_succession`` family in the scope ``<ledger_id>``. Each entry
embeds its successor document and the exact signed owner exceptions it relies
on, so the proof is durable, anchored and needs nothing the caller asserts.

**The proof of one transition** (:func:`transition_reasons`), replayed by
every reader and by the writer before it writes, from the predecessor (the
previous entry's successor; for the first entry the empty ledger, or the
document anchored under ``quarantine_ledger`` that it continues):

1. the predecessor's digest is the one the entry names; both documents are
   canonical AR3 ledgers (the entry's own validation);
2. :func:`~lovspor.forarbeider.release_quarantine.successor_findings` is
   empty: no record dropped, no history rewritten, no exclusion dropped. AR3
   has no justified removal, so none is accepted;
3. the policy the exceptions were judged under is one the reader holds, by
   digest;
4. the offered exceptions and revocations are verified now by
   :func:`~lovspor.forarbeider.release_quarantine_run.verify_exceptions`
   against the reader's trust root (current owner authority), at the entry's
   judgement time; any signature failure fails the proof;
5. every new ``cleared`` event names a verified clearance that
   :func:`~lovspor.forarbeider.release_quarantine.clears` exactly the record
   as it stood (policy, version, evidence digests, target), and every new
   exclusion names a verified exclusion of that document;
6. the successor is a fixed point of
   :func:`~lovspor.forarbeider.release_quarantine.settle` under exactly those
   exceptions: no active record a verified clearance matches, no cleared
   record without one;
7. time does not run backwards: the judgement is not before the predecessor's
   newest event, and no event of the successor is after it.

A stored ``cleared`` state, an anchor or a writer's verdict is never taken
as proof. Nothing clears a quarantine automatically: only an owner-signed
clearance, re-verified here, does.

**Reading** (:func:`read_quarantine`): the chain must be ``verified`` against
the witness (a pending entry is untrusted), its genesis must agree with the
``quarantine_ledger`` anchor of the same scope (the document it continues is
verified there; an empty genesis requires that no document was anchored),
and every transition is replayed. Anything else refuses.

**Writing** (:func:`append_successor`), under the store lock (a leaf above
the witness lock; no workspace or budget-ledger lock is taken):

1. the chain is read and verified; an unanchored, empty chain is anchored
   empty first;
2. the caller's expected predecessor must be the verified head: one
   successor per anchored predecessor. The same successor again is
   ``already_applied``; another one for a predecessor already succeeded is
   refused;
3. the entry is proved, created (``O_EXCL``) and anchored; the witness's
   compare-and-set on its head refuses a competing anchor;
4. a crash after the create leaves ``pending_anchor``, untrusted by readers;
   only a retry whose entry has exactly the pending bytes completes it.
"""

from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import ValidationError

from lovspor.forarbeider import publication_ledger
from lovspor.forarbeider.control_plane_anchor import (
    AnchorAuthority,
    AnchorQuery,
    AnchorVerdict,
    anchor_pending,
    initialize,
    verify,
)
from lovspor.forarbeider.control_plane_chains import quarantine_chain, succession_chain
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_authorization import SignedRecord
from lovspor.forarbeider.policy_trust import TrustRoot
from lovspor.forarbeider.publication_ledger import BudgetLedger
from lovspor.forarbeider.quarantine_succession_models import (
    Basis,
    QuarantineSuccession,
    SignedMaterial,
    document_sha256,
    succession_bytes,
)
from lovspor.forarbeider.release_quarantine import (
    EMPTY_LEDGER,
    VerifiedException,
    clears,
    latest_event,
    parse_ledger,
    settle,
    successor_findings,
)
from lovspor.forarbeider.release_quarantine_models import QuarantineLedger, QuarantineRecord
from lovspor.forarbeider.release_quarantine_run import ExceptionMaterial, verify_exceptions
from lovspor.forarbeider.version_models import canonical_text

_EMPTY_GENESIS = ("verified", "unanchored")


class QuarantineStore(NamedTuple):
    """One ledger's successions: ``<root>/<ledger_id>/NNNNNN.json``, lock ``<ledger_id>.lock``."""

    root: Path
    ledger_id: str


class SuccessionRequest(NamedTuple):
    """One proposed successor; nothing in it is trusted until proved."""

    repository: str
    expected_predecessor_sha256: str
    successor: QuarantineLedger
    exceptions: ExceptionMaterial
    policy: bytes | None
    at: datetime
    #: For a first entry only: the document anchored under ``quarantine_ledger`` it continues.
    predecessor_document: bytes | None = None
    #: Policies the chain's earlier entries name, to replay them; matched by digest only.
    policies: tuple[bytes, ...] = ()


class SuccessionOutcome(NamedTuple):
    status: Literal["appended", "completed", "already_applied"]
    entry: QuarantineSuccession
    verdict: AnchorVerdict


class VerifiedQuarantine(NamedTuple):
    """The ledger a reader proved: the head of a verified, replayed chain."""

    ledger: QuarantineLedger
    ledger_sha256: str
    sequence: int


class _Context(NamedTuple):
    store: QuarantineStore
    repository: str
    authority: AnchorAuthority
    policies: Mapping[str, bytes]


def _refused(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(
        RefusalReason.RELEASE_NOT_ELIGIBLE, f"quarantine_succession:{detail}"
    )


def _handle(store: QuarantineStore) -> BudgetLedger:
    """The AR5c append-only storage and lock primitives, reused for this chain."""
    return BudgetLedger(store.root, store.ledger_id)


def policies_of(*policies: bytes) -> dict[str, bytes]:
    return {sha256(item).hexdigest(): item for item in policies}


# --- the proof of one transition ---------------------------------------------------------------


def transition_reasons(
    previous: QuarantineLedger,
    entry: QuarantineSuccession,
    policies: Mapping[str, bytes],
    root: TrustRoot,
) -> tuple[str, ...]:
    """Why ``entry`` does not prove its successor from ``previous``; empty when it does."""
    reasons = list(successor_findings(previous, entry.successor))
    if document_sha256(previous) != entry.predecessor_sha256:
        reasons.append("predecessor_mismatch")
    reasons += _clock_reasons(previous, entry)
    policy = None if entry.policy_sha256 is None else policies.get(entry.policy_sha256)
    if entry.policy_sha256 is not None and policy is None:
        return (*sorted(set(reasons)), "policy_unavailable")
    material = ExceptionMaterial(_signed(entry.exceptions), _signed(entry.revocations))
    verdict = verify_exceptions(material, policy, entry.recorded_at, root)
    reasons += [f"exception:{halt.code}" for halt in verdict.halts]
    if settle(entry.successor, verdict.verified, entry.recorded_at) != entry.successor:
        reasons.append("not_settled_under_the_verified_exceptions")
    reasons += _unjustified(previous, entry.successor, verdict.verified)
    return tuple(sorted(set(reasons)))


def _signed(items: tuple[SignedMaterial, ...]) -> tuple[SignedRecord, ...]:
    return tuple(item.signed() for item in items)


def _clock_reasons(previous: QuarantineLedger, entry: QuarantineSuccession) -> list[str]:
    reasons = []
    before, after = latest_event(previous), latest_event(entry.successor)
    if before is not None and entry.recorded_at < before:
        reasons.append("clock_regressed")
    if after is not None and after > entry.recorded_at:
        reasons.append("event_after_the_judgement")
    return reasons


def _unjustified(
    previous: QuarantineLedger,
    successor: QuarantineLedger,
    verified: tuple[VerifiedException, ...],
) -> list[str]:
    known = {item.quarantine_id: len(item.history) for item in previous.records}
    reasons = [
        "unjustified_clearance"
        for record in successor.records
        if not _clearances_justified(record, known.get(record.quarantine_id, 0), verified)
    ]
    held = set(previous.exclusions)
    excluded = {
        (item.sha256, item.record.document_id, item.record.reason_code)
        for item in verified
        if item.record.action == "exclusion"
    }
    for item in successor.exclusions:
        if item not in held and (item.exception_sha256, item.document_id, item.reason_code) not in (
            excluded
        ):
            reasons.append("unjustified_exclusion")
    return reasons


def _clearances_justified(
    record: QuarantineRecord, known: int, verified: tuple[VerifiedException, ...]
) -> bool:
    """Every new ``cleared`` event names a verified clearance of the record as it then stood."""
    for event in record.history[known:]:
        if event.event != "cleared":
            continue
        then = record.model_copy(
            update={"evidence_sha256s": event.evidence_sha256s, "policy": event.policy}
        )
        if not any(
            item.sha256 == event.exception_sha256 and clears(item.record, then) for item in verified
        ):
            return False
    return True


# --- the chain, as stored and as witnessed -----------------------------------------------------


def _entries(store: QuarantineStore) -> tuple[bytes, ...]:
    try:
        return publication_ledger.entries(_handle(store))
    except ForarbeiderRefusedError as error:
        raise _refused(f"store:{error.detail}") from error


def _query(context: _Context, held: tuple[bytes, ...]) -> AnchorQuery:
    chain = succession_chain(held, context.repository, context.store.ledger_id)
    return AnchorQuery("quarantine_succession", context.repository, context.store.ledger_id, chain)


def _legacy(context: _Context, document: bytes) -> AnchorQuery:
    chain = quarantine_chain(document)
    return AnchorQuery("quarantine_ledger", context.repository, context.store.ledger_id, chain)


def _parsed(held: tuple[bytes, ...]) -> list[QuarantineSuccession]:
    return [QuarantineSuccession.model_validate_json(raw) for raw in held]


def _genesis(context: _Context, first: QuarantineSuccession | None) -> QuarantineLedger:
    """The predecessor of the first entry, agreed with the ``quarantine_ledger`` anchor."""
    return _origin(context, None if first is None else first.predecessor)


def _origin(context: _Context, predecessor: QuarantineLedger | None) -> QuarantineLedger:
    """The empty ledger, when no document was anchored; else the anchored document itself."""
    if predecessor is None:
        status = verify(_legacy(context, b""), context.authority).status
        if status not in _EMPTY_GENESIS:
            raise _refused(f"genesis:quarantine_ledger:{status}")
        return EMPTY_LEDGER
    document = canonical_text(predecessor).encode()
    status = verify(_legacy(context, document), context.authority).status
    if status != "verified":
        raise _refused(f"genesis:quarantine_ledger:{status}")
    return predecessor


def _replayed(context: _Context, held: tuple[bytes, ...]) -> QuarantineLedger:
    """The head of a chain the witness verified, every transition proved again."""
    found = _parsed(held)
    previous = _genesis(context, found[0] if found else None)
    for entry in found:
        reasons = transition_reasons(previous, entry, context.policies, context.authority.root)
        if reasons:
            raise _refused(f"entry_{entry.sequence}:{','.join(reasons)}")
        previous = entry.successor
    return previous


def _witnessed(context: _Context, held: tuple[bytes, ...]) -> None:
    status = verify(_query(context, held), context.authority).status
    if status != "verified":
        raise _refused(f"anchor:{status}")


def read_quarantine(
    store: QuarantineStore,
    repository: str,
    authority: AnchorAuthority,
    policies: Mapping[str, bytes],
) -> VerifiedQuarantine:
    """The quarantine ledger a reader may rely on, proved from the anchored chain alone.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible: quarantine_succession:<why>``.
    """
    context = _Context(store, repository, authority, policies)
    held = _entries(store)
    _witnessed(context, held)
    head = _replayed(context, held)
    return VerifiedQuarantine(head, document_sha256(head), len(held))


# --- writing -------------------------------------------------------------------------------------


def append_successor(
    store: QuarantineStore, request: SuccessionRequest, authority: AnchorAuthority
) -> SuccessionOutcome:
    """Append, prove and anchor one successor of the verified head; a retry completes it.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked: ledger`` while another writer
            holds the store; ``release_not_eligible: quarantine_succession:<why>``
            for anything unproved (nothing is written); the witness's
            ``anchor_head_moved`` when a competing anchor won.
    """
    offered = (*request.policies, *(() if request.policy is None else (request.policy,)))
    policies = policies_of(*offered)
    context = _Context(store, request.repository, authority, policies)
    with publication_ledger.ledger_lock(_handle(store)):
        return _append(context, request)


def _append(context: _Context, request: SuccessionRequest) -> SuccessionOutcome:
    held = _entries(context.store)
    verdict = verify(_query(context, held), context.authority)
    if verdict.status == "unanchored" and not held:
        verdict = initialize(_query(context, held), context.authority)
    if verdict.status == "pending_anchor":
        return _complete(context, request, held)
    if verdict.status != "verified":
        raise _refused(f"anchor:{verdict.status}")
    if held and _planned_bytes(context, request, held[:-1]) == held[-1]:
        return SuccessionOutcome("already_applied", _parsed(held)[-1], verdict)
    head = _head(context, request, held)
    _expected(request, head)
    raw = _proved(context, request, held, head)
    publication_ledger.append_entry(_handle(context.store), raw)
    done = anchor_pending(_query(context, (*held, raw)), context.authority)
    return SuccessionOutcome("appended", QuarantineSuccession.model_validate_json(raw), done)


def _complete(
    context: _Context, request: SuccessionRequest, held: tuple[bytes, ...]
) -> SuccessionOutcome:
    """A crash's pending entry, anchored only if this retry plans exactly its bytes."""
    before = held[:-1]  # ``pending_anchor`` proved the witness anchored exactly these
    head = _head(context, request, before)
    _expected(request, head)
    raw = _proved(context, request, before, head)
    if raw != held[-1]:
        raise _refused("pending_entry_is_another_successor")
    done = anchor_pending(_query(context, held), context.authority)
    return SuccessionOutcome("completed", QuarantineSuccession.model_validate_json(raw), done)


def _planned_bytes(
    context: _Context, request: SuccessionRequest, before: tuple[bytes, ...]
) -> bytes | None:
    """The entry this request makes on ``before``, if it can be built at all."""
    try:
        head = _head(context, request, before)
        _expected(request, head)
        return succession_bytes(_entry(context, request, before, head))
    except (ForarbeiderRefusedError, ValueError):
        return None


def _expected(request: SuccessionRequest, head: QuarantineLedger) -> None:
    if request.expected_predecessor_sha256 != document_sha256(head):
        raise _refused("predecessor_not_the_anchored_head")


def _head(
    context: _Context, request: SuccessionRequest, before: tuple[bytes, ...]
) -> QuarantineLedger:
    """The predecessor the request's entry on ``before`` succeeds, proved."""
    if before or request.predecessor_document is None:
        return _replayed(context, before)
    try:
        document = parse_ledger(request.predecessor_document)
    except ForarbeiderRefusedError as error:
        raise _refused("predecessor_document_unreadable") from error
    return _origin(context, document)


def _proved(
    context: _Context,
    request: SuccessionRequest,
    before: tuple[bytes, ...],
    head: QuarantineLedger,
) -> bytes:
    try:
        entry = _entry(context, request, before, head)
    except ValueError as error:
        raise _refused("entry_invalid") from error
    reasons = transition_reasons(head, entry, context.policies, context.authority.root)
    if reasons:
        raise _refused(",".join(reasons))
    return succession_bytes(entry)


def _entry(
    context: _Context,
    request: SuccessionRequest,
    before: tuple[bytes, ...],
    head: QuarantineLedger,
) -> QuarantineSuccession:
    """The next entry on ``before``.

    Raises:
        ValueError: material that cannot be carried, or an entry that cannot exist.
    """
    first = not before
    basis: Basis = "succession" if not first else "empty"
    predecessor = None
    if first and request.predecessor_document is not None:
        basis, predecessor = "anchored_document", _document(request.predecessor_document, head)
    fields = _fields(context, request, before, head)
    try:
        return QuarantineSuccession.model_validate(
            {**fields, "basis": basis, "predecessor": predecessor}
        )
    except ValidationError as error:
        raise ValueError(str(error)) from error


def _document(raw: bytes, head: QuarantineLedger) -> QuarantineLedger:
    try:
        found = parse_ledger(raw)
    except ForarbeiderRefusedError as error:
        raise ValueError("predecessor_document_unreadable") from error
    if found != head:
        raise ValueError("predecessor_document_not_the_genesis")
    return found


def _fields(
    context: _Context,
    request: SuccessionRequest,
    before: tuple[bytes, ...],
    head: QuarantineLedger,
) -> dict[str, object]:
    return {
        "repository": request.repository,
        "ledger_id": context.store.ledger_id,
        "sequence": len(before) + 1,
        "previous_succession_sha256": sha256(before[-1]).hexdigest() if before else None,
        "predecessor_sha256": document_sha256(head),
        "successor_sha256": document_sha256(request.successor),
        "successor": request.successor,
        "policy_sha256": None if request.policy is None else sha256(request.policy).hexdigest(),
        "exceptions": _material(request.exceptions.records),
        "revocations": _material(request.exceptions.revocations),
        "recorded_at": request.at,
    }


def _material(records: tuple[SignedRecord, ...]) -> tuple[SignedMaterial, ...]:
    """The offered records, carried exactly; bytes that are not text cannot be carried."""
    try:
        return tuple(
            SignedMaterial(
                record=item.record.decode(),
                signature=None if item.signature is None else item.signature.decode(),
            )
            for item in records
        )
    except UnicodeDecodeError as error:
        raise ValueError("exception_material_not_text") from error
