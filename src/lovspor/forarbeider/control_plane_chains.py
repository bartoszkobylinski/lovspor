"""AR5a: each control-plane family's local chain, read and validated (#738; §9.9 AR5).

A :class:`LocalChain` is what a verifier compares with the witnessed anchors:
the digest of every record, in order, and the bytes of the last one. It is
built only from records whose canonical bytes and hash links verify; anything
else is returned with reasons and no digests, and is never trusted.

- **in the workspace**, read through the existing readers that already refuse
  edited, reordered or truncated-in-the-middle chains:
  ``publication_attempts`` (one transaction's attempt records),
  ``recovery_receipts`` (the workspace's receipts) and ``stop_latch`` (absent,
  or one canonical latch);
- **held by the caller**, given as the exact bytes it holds:
  ``budget_ledger`` (AR1b entries, linked by the digest of the entry before),
  ``control_history`` (owner-signed control records, linked the same way;
  their signatures stay :func:`~lovspor.forarbeider.release_control.verify_control`'s
  to check) and ``quarantine_ledger`` (one canonical AR3 document, a chain of
  at most one: its digest).
"""

import os
from hashlib import sha256
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, ValidationError

from lovspor.forarbeider import publication_attempts, publication_receipts
from lovspor.forarbeider.control_plane_anchor_models import Family
from lovspor.forarbeider.corpus_leaves import directory, read_leaf
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_authorization import SignedRecord
from lovspor.forarbeider.publication_execution_models import StopLatch
from lovspor.forarbeider.release_control_models import ReleaseControl
from lovspor.forarbeider.release_ledger_models import LedgerEntry
from lovspor.forarbeider.release_quarantine_models import QuarantineLedger
from lovspor.forarbeider.version_models import canonical_text

LATCH = "stop-latch.json"


class LocalChain(NamedTuple):
    """The digests of a family's records in order, the last record's bytes, or why not."""

    digests: tuple[str, ...]
    last: bytes | None
    reasons: tuple[str, ...]


def _of(raws: list[bytes]) -> LocalChain:
    return LocalChain(tuple(sha256(r).hexdigest() for r in raws), raws[-1] if raws else None, ())


def _invalid(reason: str) -> LocalChain:
    return LocalChain((), None, (reason,))


def _canonical(model: BaseModel, raw: bytes) -> bool:
    return canonical_text(model).encode() == raw


def _latch(workspace: Path) -> LocalChain:
    if not os.path.lexists(workspace / LATCH):
        return _of([])
    try:
        with directory(workspace, "workspace") as parent:
            raw = read_leaf(parent, LATCH, "stop_latch")
        latch = StopLatch.model_validate_json(raw)
    except (OSError, ValidationError, ForarbeiderRefusedError):
        return _invalid("stop_latch_unreadable")
    return _of([raw]) if _canonical(latch, raw) else _invalid("stop_latch_not_canonical")


def workspace_chain(family: Family, workspace: Path, scope: str) -> LocalChain:
    """The chain of a family the workspace holds; ``scope`` is the transaction for attempts."""
    try:
        if family == "publication_attempts":
            records = publication_attempts.records(workspace, scope)
            return _of([canonical_text(r).encode() for r in records])
        if family == "recovery_receipts":
            receipts = publication_receipts.receipts(workspace)
            return _of([canonical_text(r).encode() for r in receipts])
    except ForarbeiderRefusedError as error:
        return _invalid(f"{family}_invalid:{error.detail}")
    if family == "stop_latch":
        return _latch(workspace)
    return _invalid("not_a_workspace_family")


def _linked(index: int, previous: str | None, sequence: int, link: str | None) -> bool:
    return sequence == index + 1 and link == previous


def ledger_chain(entries: tuple[bytes, ...], ledger_id: str) -> LocalChain:
    """The AR1b budget ledger: canonical entries, one ledger, each naming the one before."""
    previous: str | None = None
    for index, raw in enumerate(entries):
        try:
            entry = LedgerEntry.model_validate_json(raw)
        except ValidationError:
            return _invalid("ledger_entry_unreadable")
        if not _canonical(entry, raw) or entry.ledger_id != ledger_id:
            return _invalid("ledger_entry_not_canonical_or_foreign")
        if not _linked(index, previous, entry.sequence, entry.previous_sha256):
            return _invalid("ledger_chain_broken")
        previous = sha256(raw).hexdigest()
    return _of(list(entries))


def control_chain(history: tuple[SignedRecord, ...], policy_id: str) -> LocalChain:
    """The owner's control records for ``policy_id``, each naming the one before."""
    previous: str | None = None
    for index, item in enumerate(history):
        try:
            record = ReleaseControl.model_validate_json(item.record)
        except ValidationError:
            return _invalid("control_record_unreadable")
        if record.scope.policy_id not in (None, policy_id):
            return _invalid("control_record_foreign")
        if not _linked(index, previous, record.sequence, record.previous_control_sha256):
            return _invalid("control_chain_broken")
        previous = sha256(item.record).hexdigest()
    return _of([item.record for item in history])


def quarantine_chain(ledger: bytes) -> LocalChain:
    """The AR3 quarantine ledger: no bytes is no ledger; otherwise one canonical document."""
    if not ledger:
        return _of([])
    try:
        parsed = QuarantineLedger.model_validate_json(ledger)
    except ValidationError:
        return _invalid("quarantine_ledger_unreadable")
    return _of([ledger]) if _canonical(parsed, ledger) else _invalid("quarantine_not_canonical")
