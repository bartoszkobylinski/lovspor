"""AR5c: the durable, anchored budget ledger and its exactly-once settlement (#738; §9.9 AR5).

**Storage.** One ledger is ``<root>/<ledger_id>/NNNNNN.json``: the AR1b
entries (:class:`~lovspor.forarbeider.release_ledger_models.LedgerEntry`), each
the exact canonical bytes its successor's digest covers, created once and
never replaced or removed. It lives outside every workspace, so transactions of
different workspaces share it. Its head is anchored by the independent witness
(AR5a) as the ``budget_ledger`` family, scope ``<ledger_id>``.

**Consistency model.** Only a ledger whose local chain reproduces exactly the
witnessed head (``verified``) is read or extended. Every write is:

1. under the ledger lock, the chain is read and verified against the witness;
2. :func:`~lovspor.forarbeider.release_ledger.plan_entry` judges the operation
   on that verified chain: the next entry, ``already_applied`` for an exact
   retry, or a refusal (a conflict, a budget or a daily cap exceeded, limits the
   owner no longer signs);
3. the entry is created (``O_EXCL``), read back, and anchored.

A crash between 3's write and its anchor leaves ``pending_anchor``: nothing may
read or extend the ledger until **the same operation** is retried; the retry
proves the pending bytes are exactly what it would append on the anchored
chain and completes the anchor. A pending entry anything else wrote is never
anchored and blocks the ledger (fail closed). A missing, unanchored, truncated,
rewritten, forked or unreadable ledger, or an unreachable witness, refuses.

**Settlement.** :func:`settle` accounts one remote publication once: only a
receipt AR4b-2 classified ``published_authorized``, from the anchored receipts,
for the transaction's single reservation under its batch, with the batch's
exact usage (never above the reservation); the ``commit`` entry names the
verified remote commit and the receipt's digest. A
second settlement naming another commit or receipt is refused. Any other
classification (``published_authorization_unknown``, an integrity failure) is
``withheld``: the reservation stays open, so it keeps counting against the day,
and the workspace's latch stops further publication.

**Locks.** ``<root>/<ledger_id>.lock`` serializes every operation on one
ledger, from whichever workspace. Order: the workspace lock, then the ledger
lock, then the witness lock (a leaf). Nothing holding the ledger lock takes a
workspace lock, and the AR4a clone lock is never taken under either, so no
cycle exists. A second holder is refused at once (``corpus_locked: ledger``).
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple

from pydantic import ValidationError

from lovspor.forarbeider import publication_anchors, publication_attempts, publication_receipts
from lovspor.forarbeider.control_plane_anchor import (
    AnchorAuthority,
    AnchorQuery,
    AnchorVerdict,
    anchor_pending,
    verify,
)
from lovspor.forarbeider.control_plane_chains import LocalChain, ledger_chain
from lovspor.forarbeider.corpus_leaves import directory, read_leaf
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_contract_models import BudgetUsage, LimitsEvidence
from lovspor.forarbeider.release_ledger import entry_bytes, plan_entry
from lovspor.forarbeider.release_ledger_models import LedgerEntry, LedgerOperation
from lovspor.forarbeider.release_limits import LimitsRequest, verify_limits
from lovspor.forarbeider.version_models import canonical_text

if TYPE_CHECKING:
    from lovspor.forarbeider.publication_execute import ExecutionRequest
    from lovspor.forarbeider.publication_models import PublicationTransaction
    from lovspor.forarbeider.publication_recovery_models import RecoveryReceipt

_CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
_SAME = ("operation", "ledger_id", "day", "release_id", "batch_id", "amounts")


class BudgetLedger(NamedTuple):
    """One ledger: ``<root>/<ledger_id>/NNNNNN.json``, its lock ``<root>/<ledger_id>.lock``."""

    root: Path
    ledger_id: str


class LedgerOutcome(NamedTuple):
    status: Literal["appended", "completed", "already_applied"]
    entry: LedgerEntry
    verdict: AnchorVerdict


class Settlement(NamedTuple):
    status: Literal["settled", "already_settled", "withheld", "refused"]
    reasons: tuple[str, ...]
    entry: LedgerEntry | None


def _refused(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, detail)


def _tree(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, detail)


# --- storage -----------------------------------------------------------------------------


def _folder(ledger: BudgetLedger) -> Path:
    return ledger.root / ledger.ledger_id


def _name(sequence: int) -> str:
    return f"{sequence:06d}.json"


def entries(ledger: BudgetLedger) -> tuple[bytes, ...]:
    """The ledger's entries as stored, oldest first; no folder is no entry.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: ledger`` for a gap, a
            stray name or a folder that is not one; ``unsafe_output_path``.
    """
    folder = _folder(ledger)
    if not os.path.lexists(folder):
        return ()
    if folder.is_symlink() or not folder.is_dir():
        raise _tree("ledger")
    names = sorted(item.name for item in folder.iterdir())
    if names != [_name(n) for n in range(1, len(names) + 1)]:
        raise _tree("ledger")
    with directory(folder, "ledger") as parent:
        return tuple(read_leaf(parent, name, "ledger") for name in names)


def append_entry(ledger: BudgetLedger, raw: bytes) -> None:
    """Create the next entry file once; an existing name is a refusal, never overwritten."""
    folder = _folder(ledger)
    if not os.path.lexists(folder):
        folder.mkdir(mode=0o700)
    name = _name(len(entries(ledger)) + 1)
    with directory(folder, "ledger") as parent:
        try:
            descriptor = os.open(name, _CREATE, 0o600, dir_fd=parent)
        except FileExistsError as error:
            raise _tree("ledger") from error
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)


@contextmanager
def ledger_lock(ledger: BudgetLedger) -> Iterator[None]:
    """The ledger, exclusively, across workspaces and processes; a second holder is refused.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked: ledger``; ``checkout_unsafe: ledger_lock``.
    """
    if not os.path.lexists(ledger.root):
        ledger.root.mkdir(mode=0o700)
    with publication_attempts.exclusive(ledger.root, f"{ledger.ledger_id}.lock", "ledger"):
        yield


def query(ledger: BudgetLedger, repository: str) -> AnchorQuery:
    """The ledger as the witness anchors it; an unreadable folder is an invalid chain."""
    try:
        chain = ledger_chain(entries(ledger), ledger.ledger_id)
    except (ForarbeiderRefusedError, OSError) as error:
        detail = getattr(error, "detail", type(error).__name__)
        chain = LocalChain((), None, (f"ledger_unreadable:{detail}",))
    return AnchorQuery("budget_ledger", repository, ledger.ledger_id, chain)


def _parsed(raw: bytes) -> LedgerEntry | None:
    try:
        return LedgerEntry.model_validate_json(raw)
    except (ValidationError, ValueError):
        return None


def parsed(ledger: BudgetLedger) -> list[LedgerEntry]:
    """The entries, parsed; an entry that does not parse is left out (the chain refuses it)."""
    return [entry for raw in entries(ledger) if (entry := _parsed(raw)) is not None]


# --- recording one operation -------------------------------------------------------------


def _same(entry: LedgerEntry, operation: LedgerOperation) -> bool:
    return all(getattr(entry, name) == getattr(operation, name) for name in _SAME)


def _pending_is(
    held: tuple[bytes, ...], operation: LedgerOperation, limits: LimitsEvidence
) -> bool:
    """Whether the one pending entry is exactly what ``operation`` appends on the anchored chain."""
    pending = _parsed(held[-1])
    if pending is None:
        return False
    timed = operation.model_copy(update={"recorded_at": pending.recorded_at})
    planned = plan_entry(held[:-1], timed, limits)
    return planned.entry is not None and entry_bytes(planned.entry) == held[-1]


def _settlement_conflict(held: tuple[bytes, ...], operation: LedgerOperation) -> bool:
    if operation.operation != "commit":
        return False
    found = [e for raw in held if (e := _parsed(raw)) is not None and e.operation == "commit"]
    return any(
        e.release_id == operation.release_id
        and (e.commit, e.receipt_sha256) != (operation.commit, operation.receipt_sha256)
        for e in found
    )


def _existing(held: tuple[bytes, ...], operation: LedgerOperation) -> LedgerEntry:
    found = [e for raw in held if (e := _parsed(raw)) is not None and _same(e, operation)]
    if operation.operation == "commit":
        found = [e for e in found if e.commit == operation.commit]
    if not found:
        raise _refused("ledger:entry_not_found")
    return found[-1]


def _record(
    ledger: BudgetLedger,
    operation: LedgerOperation,
    limits: LimitsEvidence,
    anchoring: tuple[AnchorAuthority, str],
) -> LedgerOutcome:
    """:func:`record` under the ledger lock its caller holds."""
    authority, repository = anchoring
    found = query(ledger, repository)
    verdict = verify(found, authority)
    held = entries(ledger) if not found.local.reasons else ()
    if verdict.status == "pending_anchor" and _pending_is(held, operation, limits):
        entry = LedgerEntry.model_validate_json(held[-1])
        return LedgerOutcome("completed", entry, anchor_pending(found, authority))
    if verdict.status != "verified":
        raise _refused(f"anchor:budget_ledger:{verdict.status}")
    if _settlement_conflict(held, operation):
        raise _refused("ledger:ledger_conflicting_settlement")
    planned = plan_entry(held, operation, limits)
    if planned.outcome == "refused":
        raise _refused(f"ledger:{','.join(planned.reasons)}")
    if planned.entry is None:
        return LedgerOutcome("already_applied", _existing(held, operation), verdict)
    return _appended(ledger, planned.entry, len(held), anchoring)


def _appended(
    ledger: BudgetLedger, entry: LedgerEntry, count: int, anchoring: tuple[AnchorAuthority, str]
) -> LedgerOutcome:
    """Write ``entry`` as the next file, prove it is the next record, then anchor it."""
    authority, repository = anchoring
    raw = entry_bytes(entry)
    append_entry(ledger, raw)
    after = query(ledger, repository)
    if after.local.last != raw or len(after.local.digests) != count + 1:
        raise _refused("record_not_appended")
    return LedgerOutcome("appended", entry, anchor_pending(after, authority))


def record(
    ledger: BudgetLedger,
    operation: LedgerOperation,
    limits: LimitsEvidence,
    anchoring: tuple[AnchorAuthority, str],
) -> LedgerOutcome:
    """Append ``operation`` to the verified ledger and anchor it; idempotent for an exact retry.

    ``anchoring`` is the witness and the repository the ledger is anchored for.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked: ledger``; ``release_not_eligible``
            with ``anchor:budget_ledger:<status>``, ``ledger:<reasons>`` (a conflict,
            a cap, unverified limits, ``ledger_conflicting_settlement``) or
            ``record_not_appended``; the witness's refusals.
    """
    authority = anchoring[0]._replace(workspace=ledger.root)
    with ledger_lock(ledger):
        return _record(ledger, operation, limits, (authority, anchoring[1]))


# --- reservation and settlement ----------------------------------------------------------


def usage_of(transaction: "PublicationTransaction") -> BudgetUsage:
    """What one batch spends: its documents, files and bytes, and one commit."""
    counts = transaction.content.counts
    return BudgetUsage(
        documents=counts.documents, files=counts.files, bytes=counts.bytes, commits=1
    )


def _held(request: "ExecutionRequest") -> tuple[BudgetLedger, AnchorAuthority]:
    ledger, authority = request.ledger, publication_anchors.authority_of(request)
    if ledger is None:
        raise _refused("ledger_store_missing")
    if authority is None:
        raise _refused("anchor_authority_missing")
    return ledger, authority._replace(workspace=ledger.root)


def _limits(request: "ExecutionRequest", at: datetime) -> LimitsEvidence:
    inputs = request.inputs
    return verify_limits(LimitsRequest(inputs.policy.policy, inputs.limits, at), request.root)


def reserve(request: "ExecutionRequest", at: datetime) -> LedgerOutcome:
    """Reserve the transaction's exact usage for ``at``'s day; a retry is ``already_applied``.

    Raises:
        ForarbeiderRefusedError: as :func:`record`; ``ledger_store_missing``,
            ``anchor_authority_missing``.
    """
    ledger, authority = _held(request)
    transaction = request.inputs.transaction
    operation = LedgerOperation(
        operation="reserve",
        ledger_id=ledger.ledger_id,
        day=at.date(),
        release_id=transaction.transaction_id,
        batch_id=transaction.content.batch_id,
        amounts=usage_of(transaction),
        recorded_at=at,
    )
    repository = request.inputs.settings.repository
    return record(ledger, operation, _limits(request, at), (authority, repository))


def _receipts_anchored(request: "ExecutionRequest") -> str | None:
    authority = publication_anchors.authority_of(request)
    if authority is None:
        return "anchor_authority_missing"
    found = publication_anchors.workspace_query(
        request.inputs, request.workspace, "recovery_receipts", "workspace"
    )
    status = verify(found, authority).status
    return None if status == "verified" else f"anchor:recovery_receipts:{status}"


def _reservation(held: tuple[bytes, ...], release_id: str) -> LedgerEntry | None:
    found = [
        e
        for raw in held
        if (e := _parsed(raw)) is not None
        and (e.operation, e.release_id) == ("reserve", release_id)
    ]
    return found[0] if len(found) == 1 else None


def _commit(
    receipt: "RecoveryReceipt", slot: LedgerEntry, usage: BudgetUsage, at: datetime
) -> LedgerOperation:
    """The settlement: the batch's exact usage (never above the reservation, AR1b refuses that)."""
    return LedgerOperation(
        operation="commit",
        ledger_id=slot.ledger_id,
        day=slot.day,
        release_id=slot.release_id,
        batch_id=slot.batch_id,
        amounts=usage,
        recorded_at=max(at, slot.recorded_at),
        commit=receipt.commit,
        receipt_sha256=sha256(canonical_text(receipt).encode()).hexdigest(),
    )


def _settle(request: "ExecutionRequest", receipt: "RecoveryReceipt", at: datetime) -> Settlement:
    ledger, authority = _held(request)
    held = entries(ledger)
    transaction = request.inputs.transaction
    slot = _reservation(held, transaction.transaction_id)
    if slot is None:
        return Settlement("refused", ("ledger_reservation_missing",), None)
    if slot.batch_id != transaction.content.batch_id:
        return Settlement("refused", ("ledger_settlement_batch_mismatch",), None)
    operation = _commit(receipt, slot, usage_of(transaction), at)
    repository = request.inputs.settings.repository
    try:
        done = _record(
            ledger, operation, _limits(request, operation.recorded_at), (authority, repository)
        )
    except ForarbeiderRefusedError as error:
        return Settlement("refused", (error.detail,), None)
    status: Literal["settled", "already_settled"] = (
        "already_settled" if done.status == "already_applied" else "settled"
    )
    return Settlement(status, (), done.entry)


def settle(request: "ExecutionRequest", receipt: "RecoveryReceipt", at: datetime) -> Settlement:
    """Account one verified remote publication exactly once, from its anchored receipt.

    Called by AR4b-2 recovery under the workspace lock; takes the ledger lock.
    Anything but ``published_authorized`` is ``withheld``.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked: ledger``; ``ledger_store_missing``.
    """
    if receipt.classification != "published_authorized":
        return Settlement("withheld", (receipt.classification,), None)
    unanchored = _receipts_anchored(request)
    if unanchored is not None:
        return Settlement("refused", (unanchored,), None)
    ledger, _ = _held(request)
    with ledger_lock(ledger):
        return _settle(request, receipt, at)


# --- what authorization reads ------------------------------------------------------------


def anchor_reasons(
    ledger: BudgetLedger | None, authority: AnchorAuthority | None, repository: str
) -> tuple[str, ...]:
    """Why the ledger is not verified against its witness; empty when it is."""
    if ledger is None:
        return ("ledger_store_missing",)
    if authority is None:
        return ("anchor_authority_missing",)
    status = verify(query(ledger, repository), authority._replace(workspace=ledger.root)).status
    return () if status == "verified" else (f"budget_ledger:{status}",)


def reservation_batch(held: tuple[bytes, ...], release_id: str) -> str | None:
    """The batch the release's single reservation names; ``None`` without exactly one."""
    slot = _reservation(held, release_id)
    return None if slot is None else slot.batch_id


def unsettled(workspace: Path, held: tuple[bytes, ...], current: str) -> tuple[str, ...]:
    """Releases reserved in this ledger, published by this workspace, that it has not settled.

    A publication the ledger has not accounted for is an unresolved outcome of
    this budget: until AR4b-2 recovery settles it, nothing more is spent from it.
    """
    parsed_entries = [e for raw in held if (e := _parsed(raw)) is not None]
    reserved = {e.release_id for e in parsed_entries if e.operation == "reserve"}
    settled = {e.release_id for e in parsed_entries if e.operation == "commit"}
    published = {r.transaction_id for r in publication_receipts.receipts(workspace) if r.published}
    for tid in publication_attempts.transactions(workspace):
        if any(r.state == "published" for r in publication_attempts.records(workspace, tid)):
            published.add(tid)
    return tuple(sorted((published & reserved) - settled - {current}))
