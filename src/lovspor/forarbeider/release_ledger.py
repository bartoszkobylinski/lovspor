"""AR1b: deterministic budget accounting over the ledger chain (#738; §9.9 AR1, AR5).

Two pure functions over the exact bytes of the ledger entries:

- :func:`verify_ledger` replays the chain and reports one day's settled and
  reserved usage and the state of one release, or every reason the chain is
  invalid: a malformed or non-canonical entry, a gap, a broken link, another
  ledger's entry, time running backwards, a duplicated reservation or
  settlement, a settlement without or above its reservation, a reservation
  after a settlement or cancellation, a reconciliation that disagrees, or a
  reservation over the signed limits;
- :func:`plan_entry` answers what to record for one operation: the next entry
  to append, ``already_applied`` for an exact retry (nothing is appended, so a
  retry is never counted twice), or a refusal with reasons (a conflicting or
  over-budget operation, an invalid chain, unverified limits).

A release is accounted once: it is reserved under one batch, then settled or
cancelled, and never reserved again. A retry under a new batch id, a second
settlement or a reservation after the release was spent is refused, so no
retry can reopen a budget.

Nothing here writes. **A local hash chain proves neither append-only storage
nor concurrency safety**: whoever holds the file can truncate or rewrite it,
and two writers can fork it. The evidence is therefore ``unanchored`` and the
``constraints`` gate never passes it; AR5 must add the persistence and the
external anchor listed in :data:`LEDGER_REQUIREMENTS`.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from hashlib import sha256
from typing import Literal, NamedTuple

from pydantic import ValidationError

from lovspor.forarbeider.corpus_models import _loaded
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.release_contract_models import (
    BudgetUsage,
    LedgerEvidence,
    LimitsEvidence,
    ReleaseState,
    verified,
)
from lovspor.forarbeider.release_ledger_models import LedgerEntry, LedgerOperation, LedgerPlan
from lovspor.forarbeider.release_limits_models import ReleaseConstraints
from lovspor.forarbeider.release_provenance import issue_provenance
from lovspor.forarbeider.version_models import canonical_text

_ZERO = BudgetUsage(documents=0, files=0, bytes=0, commits=0)
_RESOURCES = ("documents", "files", "bytes", "commits")
_DUPLICATE = {
    "reserve": "ledger_duplicate_reservation",
    "commit": "ledger_duplicate_settlement",
    "cancel": "ledger_duplicate_cancellation",
}


class LedgerRequest(NamedTuple):
    entries: tuple[bytes, ...]
    limits: LimitsEvidence
    day: date
    release_id: str


class _Judgement(NamedTuple):
    outcome: Literal["new", "repeat", "refused"]
    reasons: tuple[str, ...] = ()


@dataclass
class _Slot:
    batch_id: str
    day: date
    amounts: BudgetUsage
    state: ReleaseState
    settled: BudgetUsage | None = None


@dataclass
class _Books:
    """The replayed state of a chain: slots per release, settled usage per day."""

    limits: LimitsEvidence
    slots: dict[str, _Slot] = field(default_factory=dict)
    settled: dict[date, BudgetUsage] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    head: str | None = None
    count: int = 0
    ledger_id: str | None = None
    last_time: datetime | None = None


def entry_bytes(entry: LedgerEntry) -> bytes:
    """An entry's exact canonical bytes, the ones its successor's digest covers."""
    return canonical_text(entry).encode()


def verify_ledger(request: LedgerRequest) -> LedgerEvidence:
    """One day's accounting from the chain; missing when empty, invalid on any defect."""
    if not request.entries:
        return LedgerEvidence(status="missing", source=None, reasons=("budget_ledger_empty",))
    books = _replay(request.entries, request.limits)
    if books.reasons:
        return LedgerEvidence(status="invalid", source=None, reasons=_sorted(books.reasons))
    slot = books.slots.get(request.release_id)
    evidence = LedgerEvidence(
        status="verified",
        source="runner_budget_ledger",
        reasons=(),
        day=request.day,
        limits_sha256=request.limits.limits_sha256,
        committed=books.settled.get(request.day, _ZERO),
        reserved=_reserved(books, request.day),
        release_state="none" if slot is None else slot.state,
        release_reserved=slot.amounts if slot and slot.state == "reserved" else None,
        entries=books.count,
        head_sha256=books.head,
    )
    return issue_provenance(evidence, "budget_ledger")


def plan_entry(
    entries: tuple[bytes, ...], operation: LedgerOperation, limits: LimitsEvidence
) -> LedgerPlan:
    """What to record for ``operation``: an entry, nothing (a retry) or a refusal; no write."""
    books = _replay(entries, limits)
    reasons = list(books.reasons) or _order(books, operation)
    limits_sha256 = limits.limits_sha256 or ""
    if not verified("publication_limits", limits) or not limits_sha256:
        reasons.append("limits_unverified")
    judged = _judge(books, operation) if not reasons else _Judgement("refused", ())
    if judged.outcome == "repeat":
        return LedgerPlan(outcome="already_applied", reasons=(), entry=None)
    reasons += judged.reasons
    if reasons:
        return LedgerPlan(outcome="refused", reasons=_sorted(reasons), entry=None)
    entry = LedgerEntry(
        **operation.model_dump(),
        sequence=books.count + 1,
        previous_sha256=books.head,
        limits_sha256=limits_sha256,
    )
    return LedgerPlan(outcome="append", reasons=(), entry=entry)


def _sorted(reasons: list[str]) -> tuple[str, ...]:
    return tuple(sorted(set(reasons)))


# --- replay ------------------------------------------------------------------------------


def _replay(entries: tuple[bytes, ...], limits: LimitsEvidence) -> _Books:
    books = _Books(limits)
    for raw in entries:
        entry = _parsed(raw)
        if entry is None:
            books.reasons[:] = ["ledger_entry_malformed"]
            return books
        _take(books, entry, raw)
    return books


def _parsed(raw: bytes) -> LedgerEntry | None:
    try:
        entry = LedgerEntry.model_validate(_loaded(raw))
    except (ValidationError, ForarbeiderRefusedError):
        return None
    return entry if entry_bytes(entry) == raw else None


def _take(books: _Books, entry: LedgerEntry, raw: bytes) -> None:
    if entry.sequence != books.count + 1:
        books.reasons.append("ledger_sequence_gap")
    if entry.previous_sha256 != books.head:
        books.reasons.append("ledger_chain_broken")
    books.reasons += _order(books, entry)
    books.reasons += _limits_reasons(books, entry)
    judged = _judge(books, entry)
    if judged.outcome == "repeat":
        books.reasons.append(_DUPLICATE.get(entry.operation, "ledger_duplicate_entry"))
    books.reasons += judged.reasons
    if judged.outcome == "new":
        _record(books, entry)
    books.ledger_id = books.ledger_id or entry.ledger_id
    books.last_time, books.head, books.count = (
        entry.recorded_at,
        sha256(raw).hexdigest(),
        books.count + 1,
    )


def _order(books: _Books, operation: LedgerOperation) -> list[str]:
    reasons = []
    if books.ledger_id is not None and operation.ledger_id != books.ledger_id:
        reasons.append("ledger_id_mismatch")
    if books.last_time is not None and operation.recorded_at < books.last_time:
        reasons.append("ledger_time_reordered")
    return reasons


def _limits_reasons(books: _Books, entry: LedgerEntry) -> list[str]:
    known = books.limits.limits_sha256
    if verified("publication_limits", books.limits) and entry.limits_sha256 != known:
        return ["ledger_limits_mismatch"]
    return []


def _record(books: _Books, entry: LedgerOperation) -> None:
    if entry.operation == "reconcile" or entry.release_id is None or entry.batch_id is None:
        return
    if entry.operation == "reserve":
        books.slots[entry.release_id] = _Slot(entry.batch_id, entry.day, entry.amounts, "reserved")
        return
    slot = books.slots[entry.release_id]
    if entry.operation == "commit":
        books.settled[slot.day] = _plus(books.settled.get(slot.day, _ZERO), entry.amounts)
        slot.state, slot.settled = "settled", entry.amounts
    else:
        slot.state = "cancelled"


# --- judging one operation ---------------------------------------------------------------


def _judge(books: _Books, operation: LedgerOperation) -> _Judgement:
    if operation.operation == "reconcile":
        settled = books.settled.get(operation.day, _ZERO)
        mismatch = ("ledger_reconciliation_mismatch",) if operation.amounts != settled else ()
        return _Judgement("refused" if mismatch else "new", mismatch)
    slot = books.slots.get(operation.release_id or "")
    if operation.operation == "reserve":
        return _reserve(books, slot, operation)
    if operation.operation == "commit":
        return _settle(slot, operation)
    return _cancel(slot, operation)


def _same(slot: _Slot, operation: LedgerOperation) -> bool:
    return (slot.batch_id, slot.day, slot.amounts) == (
        operation.batch_id,
        operation.day,
        operation.amounts,
    )


def _reserve(books: _Books, slot: _Slot | None, operation: LedgerOperation) -> _Judgement:
    if slot is not None:
        spent = {
            "settled": "ledger_reservation_after_commit",
            "cancelled": "ledger_reservation_after_cancel",
        }
        if slot.state in spent:
            return _Judgement("refused", (spent[slot.state],))
        if _same(slot, operation):
            return _Judgement("repeat")
        return _Judgement("refused", ("ledger_conflicting_reservation",))
    over = _over_budget(books, operation)
    return _Judgement("refused", over) if over else _Judgement("new")


def _settle(slot: _Slot | None, operation: LedgerOperation) -> _Judgement:
    if slot is None or slot.state == "cancelled":
        return _Judgement("refused", ("ledger_commit_without_reservation",))
    if slot.state == "settled" and _same_settlement(slot, operation):
        return _Judgement("repeat")
    checks = (
        (slot.batch_id != operation.batch_id, "ledger_conflicting_reservation"),
        (slot.day != operation.day, "ledger_day_mismatch"),
        (slot.state == "settled", "ledger_duplicate_settlement"),
        (not _within(operation.amounts, slot.amounts), "ledger_settlement_exceeds_reservation"),
    )
    reasons = tuple(code for failed, code in checks if failed)
    return _Judgement("refused", reasons) if reasons else _Judgement("new")


def _same_settlement(slot: _Slot, operation: LedgerOperation) -> bool:
    return (slot.batch_id, slot.day, slot.settled) == (
        operation.batch_id,
        operation.day,
        operation.amounts,
    )


def _cancel(slot: _Slot | None, operation: LedgerOperation) -> _Judgement:
    if slot is not None and slot.state == "cancelled" and _same(slot, operation):
        return _Judgement("repeat")
    if slot is None or slot.state != "reserved":
        return _Judgement("refused", ("ledger_cancel_without_reservation",))
    if not _same(slot, operation):
        return _Judgement("refused", ("ledger_conflicting_reservation",))
    return _Judgement("new")


# --- budgets -----------------------------------------------------------------------------


def _plus(left: BudgetUsage, right: BudgetUsage) -> BudgetUsage:
    return BudgetUsage(**{name: getattr(left, name) + getattr(right, name) for name in _RESOURCES})


def _within(amounts: BudgetUsage, cap: BudgetUsage) -> bool:
    return all(getattr(amounts, name) <= getattr(cap, name) for name in _RESOURCES)


def _reserved(books: _Books, day: date) -> BudgetUsage:
    total = _ZERO
    for slot in books.slots.values():
        if slot.state == "reserved" and slot.day == day:
            total = _plus(total, slot.amounts)
    return total


def _over_budget(books: _Books, operation: LedgerOperation) -> tuple[str, ...]:
    constraints = books.limits.constraints
    if constraints is None or not verified("publication_limits", books.limits):
        return ()
    day = operation.day
    planned = _plus(_plus(books.settled.get(day, _ZERO), _reserved(books, day)), operation.amounts)
    return _budget_codes(planned, constraints) + _batch_codes(operation.amounts, constraints)


def _budget_codes(planned: BudgetUsage, limits: ReleaseConstraints) -> tuple[str, ...]:
    return tuple(
        f"budget_{name}_exceeded"
        for name in _RESOURCES
        if getattr(planned, name) > getattr(limits, f"max_{name}_per_day")
    )


def _batch_codes(amounts: BudgetUsage, limits: ReleaseConstraints) -> tuple[str, ...]:
    return tuple(
        f"batch_{name}_exceeded"
        for name in ("documents", "files", "bytes")
        if getattr(amounts, name) > getattr(limits, f"max_{name}_per_batch")
    )
