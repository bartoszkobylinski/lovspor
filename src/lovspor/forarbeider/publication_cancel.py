"""AR5d-3: cancel an abandoned reservation, only on proof that nothing was sent (#738; §9.9 AR5).

A reservation stays open until the ledger settles or cancels it (AR5c), and
while it is open no other release of the ledger is reserved or authorized.
Settlement is automatic (AR4b-2 recovery, AR5c). Cancellation is the
exception: it gives budget back, so it must never follow a publication that
happened or may have happened. :func:`cancel_reservation` is the only way to
record a ``cancel`` (:func:`~lovspor.forarbeider.publication_ledger.record`
refuses one), and it needs both:

- the owner's signed, single-use instruction
  (:class:`~lovspor.forarbeider.publication_cancel_models.ReservationCancellation`)
  naming this exact reservation and the evidence the owner saw; and
- proof, read now under the locks, that **no send was ever begun** for the
  transaction.

**Why "never begun" and not "not published".** A send is begun only after a
``prepared_to_send`` attempt record is anchored, under the workspace and
ledger locks, which the sender holds until the outcome is recorded (AR4b-1,
AR5b, AR5c). An unchanged remote after a begun send proves nothing: the
request may still be in flight, its response lost, or its process dead after
the mutation; a timeout, a lapsed lease or a missing answer is no evidence.
Such a transaction is never cancelled; it is resolved by recovery (which
re-reads the remote) and, if need be, a retried send, which the remote's
expected-head check makes safe. A transaction with no begun send has no
request anywhere, so nothing can publish it later.

**The proof**, every part re-read here, never taken from the caller:

1. the workspace's identity is the one the witness anchored (AR5d-1);
2. the instruction verifies (owner role, control namespace, signer, key valid
   now, signed before now, not expired) and binds this repository, ledger,
   transaction, batch, path, base and identity scope;
3. (the witness) the transaction's attempts are read on their one
   authoritative witness, the ledger's
   (:func:`~lovspor.forarbeider.publication_anchors.attempts_witness`): every
   sender of a reserved transaction, whichever witness keeps its own
   workspace's families, must anchor its ``prepared_to_send`` there before it
   sends, so this history is complete. Nothing a caller names is compared:
   the ledger is verified against that same witness in step 7, so a
   substituted witness has no anchored ledger and refuses;
4. the transaction's attempts verify against that witness and hold no
   ``prepared_to_send``, ``outcome_unknown`` or ``published`` record; their
   head is the one the owner signed. A copy, or another workspace (on any
   witness) that began a send, makes them ``local_rolled_back`` or ``diverged``;
5. no receipt for the transaction is in the workspace's anchored receipt chain;
6. no writer of the identity can be live (the writer chain verifies; its newest
   claim is released or lapsed);
7. the ledger verifies against its witness, its head is the one the owner
   signed, and it holds this transaction's single reservation, open, for this
   batch, with exactly the bytes the owner signed;
8. the remote ``main`` is the transaction's base, read twice around the walk
   (:func:`~lovspor.forarbeider.publication_remote.history`).

**Writes, in order.** (a) a ``refused`` attempt record carrying
:data:`~lovspor.forarbeider.publication_cancel_models.CANCELLED` and the
instruction's digest, anchored for the transaction: the durable decision, and
a fence (an execution that finds it never sends); (b) the AR1b ``cancel``
entry, appended and anchored. A retry with the same instruction completes
whatever a crash left (a pending anchor, a missing entry) and is otherwise
``already_cancelled``; another instruction is refused.

**Locks.** The workspace lock, then the ledger lock, then the witness (a leaf),
held from the first read to the last write, as an execution holds them from
authorization to its last record. A send and a cancellation therefore never
interleave: the second is refused (``corpus_locked``).

The cancellation clears no latch and no STOP, settles nothing and permits no
publication.
"""

from datetime import datetime
from hashlib import sha256
from typing import Literal, NamedTuple

from lovspor.forarbeider import (
    publication_anchors,
    publication_attempts,
    publication_ledger,
    workspace_writer,
)
from lovspor.forarbeider.control_plane_anchor import AnchorAuthority, verify
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_authorization import SignedRecord
from lovspor.forarbeider.policy_trust import CONTROL_NAMESPACE
from lovspor.forarbeider.publication_cancel_models import CANCELLED, ReservationCancellation
from lovspor.forarbeider.publication_execute import (
    Environment,
    ExecutionRequest,
    Simulation,
    attempt_id_of,
)
from lovspor.forarbeider.publication_execution_models import AttemptRecord
from lovspor.forarbeider.publication_ledger import BudgetLedger
from lovspor.forarbeider.publication_remote import Reader, history
from lovspor.forarbeider.publication_remote_recovery import reader_of
from lovspor.forarbeider.release_ledger_models import LedgerEntry, LedgerOperation
from lovspor.forarbeider.release_limits import LimitsRequest, verify_limits
from lovspor.forarbeider.release_signed import RecordSpec, verify_owner_record

_SENT = ("prepared_to_send", "outcome_unknown", "published")
_BOUND = ("repository", "ledger_id", "transaction_id", "batch_id", "path", "base_commit")


class CancellationOutcome(NamedTuple):
    status: Literal["cancelled", "already_cancelled"]
    entry: LedgerEntry
    attempt: AttemptRecord


class _Held(NamedTuple):
    """What the cancellation runs on: the request, its ledger and both authorities."""

    request: ExecutionRequest
    ledger: BudgetLedger
    workspace: AnchorAuthority
    budget: AnchorAuthority


def _refused(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, f"cancel:{detail}")


def cancel_reservation(
    request: ExecutionRequest, offered: SignedRecord, environment: Environment
) -> CancellationOutcome:
    """Cancel the transaction's open reservation on the owner's instruction, if nothing was sent.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked`` (``workspace`` or ``ledger``)
            while an execution, a recovery or another cancellation holds either;
            ``release_not_eligible`` with ``cancel:<why>`` for every unproved
            condition (nothing is written); a simulation without a sandboxed
            adapter (``recovery_needs_a_sandboxed_adapter``, ``adapter``).
    """
    ledger, authority = request.ledger, publication_anchors.authority_of(request)
    if ledger is None:
        raise _refused("ledger_store_missing")
    if authority is None:
        raise _refused("anchor_authority_missing")
    reader = reader_of(request, environment)
    if not isinstance(environment, Simulation):  # reader_of proved it is
        raise _refused("adapter")
    held = _Held(request, ledger, authority, publication_ledger.ledger_authority(ledger, authority))
    with (
        publication_attempts.workspace_lock(request.workspace),
        publication_ledger.ledger_lock(ledger),
    ):
        return _cancel(held, offered, reader, environment.clock())


def _cancel(
    held: _Held, offered: SignedRecord, reader: Reader, now: datetime
) -> CancellationOutcome:
    """:func:`cancel_reservation` under the workspace and ledger locks it holds."""
    scope = _scope(held.request)
    order, digest = _instruction(held.request, offered, scope, now)
    record = _recorded(held.request, digest, order)
    if record is None:
        if now >= order.valid_until:
            raise _refused("instruction:expired")
        _prove(held, order, reader, now)
        record = _written(held.request, digest, now)
    return _entered(held, record, now)


# --- the instruction ---------------------------------------------------------------------------


def _scope(request: ExecutionRequest) -> str:
    try:
        return publication_anchors.scope_of(request)
    except ForarbeiderRefusedError as error:
        raise _refused(error.detail) from error


def _instruction(
    request: ExecutionRequest, offered: SignedRecord, scope: str, now: datetime
) -> tuple[ReservationCancellation, str]:
    """The owner's verified instruction and its digest, bound to this request."""
    spec = RecordSpec(ReservationCancellation, CONTROL_NAMESPACE)
    check = verify_owner_record(request.root, offered, spec, now)
    order = check.record
    if check.status != "verified" or not isinstance(order, ReservationCancellation):
        raise _refused(f"instruction:{','.join(check.reasons) or check.status}")
    content = request.inputs.transaction.content
    wanted = {
        "repository": request.inputs.settings.repository,
        "ledger_id": request.ledger.ledger_id if request.ledger else None,
        "transaction_id": request.inputs.transaction.transaction_id,
        "batch_id": content.batch_id,
        "path": content.publication.path,
        "base_commit": content.publication.base_commit,
    }
    wrong = [name for name in _BOUND if getattr(order, name) != wanted[name]]
    if order.workspace_scope != scope:
        wrong.append("workspace_scope")
    if wrong:
        raise _refused(f"instruction_{','.join(wrong)}_mismatch")
    return order, sha256(offered.record).hexdigest()


# --- the proof that no send was ever begun -----------------------------------------------------


def _prove(held: _Held, order: ReservationCancellation, reader: Reader, now: datetime) -> None:
    request = held.request
    _never_sent(held, order)
    _no_receipt(request)
    try:
        workspace_writer.require_idle(request, now)
    except ForarbeiderRefusedError as error:
        raise _refused(error.detail) from error
    _reservation_open(held, order)
    found = history(reader, request.inputs.transaction.content.publication.base_commit)
    if found.broken is not None:
        raise _refused(f"remote:{found.broken}")
    if found.commits:
        raise _refused("remote_advanced")


def _never_sent(held: _Held, order: ReservationCancellation) -> None:
    request = held.request
    tid = request.inputs.transaction.transaction_id
    query = publication_anchors.workspace_query(
        request.inputs, request.workspace, "publication_attempts", tid
    )
    authoritative = publication_anchors.attempts_witness(held.workspace, held.ledger)
    status = verify(query, authoritative).status
    if status != "verified":
        raise _refused(f"anchor:publication_attempts:{status}")
    sent = [
        r.state for r in publication_attempts.records(request.workspace, tid) if r.state in _SENT
    ]
    if sent:
        raise _refused(f"send_initiated:{sent[0]}")
    if publication_attempts.head(request.workspace, tid)[1] != order.attempts_head_sha256:
        raise _refused("instruction_stale:attempts_head")


def _no_receipt(request: ExecutionRequest) -> None:
    chain = publication_ledger.anchored_receipts(request)
    if isinstance(chain, str):
        raise _refused(chain)
    tid = request.inputs.transaction.transaction_id
    if any(receipt.transaction_id == tid for receipt, _ in chain):
        raise _refused("receipt_on_record")


def _reservation_open(held: _Held, order: ReservationCancellation) -> None:
    request, ledger = held.request, held.ledger
    query = publication_ledger.query(ledger, request.inputs.settings.repository)
    status = verify(query, held.budget).status
    if status != "verified":
        raise _refused(f"anchor:budget_ledger:{status}")
    stored = publication_ledger.entries(ledger)
    if not stored or sha256(stored[-1]).hexdigest() != order.ledger_head_sha256:
        raise _refused("instruction_stale:ledger_head")
    tid = request.inputs.transaction.transaction_id
    found = _reservation_bytes(stored, tid)
    if found is None:
        raise _refused("reservation_missing")
    if sha256(found).hexdigest() != order.reservation_sha256:
        raise _refused("instruction_reservation_mismatch")
    if tid not in publication_ledger.outstanding(stored, None):
        raise _refused("reservation_not_open")
    batch = request.inputs.transaction.content.batch_id
    if LedgerEntry.model_validate_json(found).batch_id != batch:
        raise _refused("reservation_not_this_batch")


def _reservation_bytes(stored: tuple[bytes, ...], tid: str) -> bytes | None:
    found = [
        raw
        for raw in stored
        if (entry := _entry(raw)) is not None
        and (entry.operation, entry.release_id) == ("reserve", tid)
    ]
    return found[0] if len(found) == 1 else None


def _entry(raw: bytes) -> LedgerEntry | None:
    try:
        return LedgerEntry.model_validate_json(raw)
    except ValueError:
        return None


# --- the writes ----------------------------------------------------------------------------------


def _recorded(
    request: ExecutionRequest, digest: str, order: ReservationCancellation
) -> AttemptRecord | None:
    """The decision a crash left on record for this instruction, its anchor completed."""
    tid = request.inputs.transaction.transaction_id
    found = [
        r for r in publication_attempts.records(request.workspace, tid) if CANCELLED in r.reasons
    ]
    if not found:
        return None
    record = found[-1]
    if len(found) > 1 or record.authorization_sha256 != digest:
        raise _refused("already_cancelled_by_another_instruction")
    # The decision is written right after the attempts head the owner signed, with
    # nothing sent before it; anything else was not written by this path (#1028).
    every = publication_attempts.records(request.workspace, tid)
    before = every[: every.index(record)]
    if record.previous_sha256 != order.attempts_head_sha256 or any(
        r.state in _SENT for r in before
    ):
        raise _refused("cancellation_record_not_the_owners")
    if record.recorded_at >= order.valid_until:
        raise _refused("instruction:expired")
    _anchored(request)
    return record


def _anchored(request: ExecutionRequest) -> None:
    tid = request.inputs.transaction.transaction_id
    verdict = publication_anchors.settle(request, "publication_attempts", tid)
    if verdict is None or verdict.status != "verified":
        status = "unavailable" if verdict is None else verdict.status
        raise _refused(f"anchor:publication_attempts:{status}")


def _written(request: ExecutionRequest, digest: str, now: datetime) -> AttemptRecord:
    """The durable decision: a ``refused`` attempt naming the instruction, anchored."""
    tid = request.inputs.transaction.transaction_id
    content = request.inputs.transaction.content
    sequence, previous = publication_attempts.head(request.workspace, tid)
    record = AttemptRecord(
        transaction_id=tid,
        release_manifest_sha256=content.release_manifest_sha256,
        base_commit=content.publication.base_commit,
        path=content.publication.path,
        attempt_id=attempt_id_of(tid, sequence),
        sequence=sequence,
        state="refused",
        recorded_at=now,
        authorization_sha256=digest,
        commit=None,
        reasons=(CANCELLED,),
        previous_sha256=previous,
    )
    publication_attempts.append(request.workspace, record)
    _anchored(request)
    return record


def _operation(held: _Held, now: datetime) -> LedgerOperation:
    """The AR1b cancel of the transaction's single reservation: same batch, day and amounts."""
    tid = held.request.inputs.transaction.transaction_id
    found = _reservation_bytes(publication_ledger.entries(held.ledger), tid)
    if found is None:
        raise _refused("reservation_missing")
    slot = LedgerEntry.model_validate_json(found)
    return LedgerOperation(
        operation="cancel",
        ledger_id=slot.ledger_id,
        day=slot.day,
        release_id=tid,
        batch_id=slot.batch_id,
        amounts=slot.amounts,
        recorded_at=now,
    )


def _entered(held: _Held, record: AttemptRecord, now: datetime) -> CancellationOutcome:
    """The AR1b ``cancel`` entry, appended and anchored; a retry completes or finds it."""
    request = held.request
    operation = _operation(held, now)
    limits = verify_limits(
        LimitsRequest(request.inputs.policy.policy, request.inputs.limits, now), request.root
    )
    anchoring = (held.budget, request.inputs.settings.repository)
    try:
        done = publication_ledger._record(held.ledger, operation, limits, anchoring)
    except ForarbeiderRefusedError as error:
        raise _refused(error.detail) from error
    status: Literal["cancelled", "already_cancelled"] = (
        "already_cancelled" if done.status == "already_applied" else "cancelled"
    )
    return CancellationOutcome(status, done.entry, record)
