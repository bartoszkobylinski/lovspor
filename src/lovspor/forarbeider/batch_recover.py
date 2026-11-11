"""``batch recover``: settle what an interrupted apply or withdrawal left (#738, B1b).

The explicit recovery the owner runs when ``batch apply``, ``batch
withdraw`` or the B1a preflight refuses with ``corpus_locked`` or
``checkout_pending_change``. It never publishes anything new:

- The lock is taken over only when no process holds it (its holder died);
  a live apply keeps it and recovery refuses ``corpus_locked``.
- Every identity check of the guard runs (canonical private path, not the
  engine or the sync clone, trusted origin and root commit, no Git operation,
  the named branch); a dirty tree and the journal are what it settles.
- A **release** journal is always rolled back, never completed. Finishing it
  would publish on a confirmation whose world may have moved since; rolling
  back costs one more ``batch apply``, which re-runs every check and asks the
  phrase again. A **withdrawal** journal is finished: a takedown the owner
  confirmed is never undone by recovery.
- Either must name a decision the workspace holds: a confirmation or an intent
  stored under the manifest the journal leads to, with the digest it names.
  Anything else is ``corpus_tree_mismatch: journal`` and nothing is touched.
- A file holding neither its old nor its new bytes (edited by hand since)
  refuses; nothing is overwritten. Then the read-back: status lists exactly
  the change's paths (none after a rollback), HEAD unmoved, dataset exact.
- Staging left without a journal holds bytes no operation names, and is removed.
"""

from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict

from lovspor.forarbeider.apply_preflight import existing_workspace
from lovspor.forarbeider.batch_models import valid_branch
from lovspor.forarbeider.batch_records import (
    BatchConfirmation,
    ReleaseWithdrawalIntent,
)
from lovspor.forarbeider.batch_withdraw import withdrawal_receipt
from lovspor.forarbeider.corpus_transaction import (
    Journal,
    clear_orphaned_staging,
    finish,
    pending,
    roll_back,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_checkout import lockable_checkout, read_back_of, verify_identity
from lovspor.forarbeider.release_commands import ReleaseScope
from lovspor.forarbeider.release_lock import HeldLock, recovered_lock
from lovspor.forarbeider.release_models import digest, record_text
from lovspor.forarbeider.release_store import read_named, write_record


class RecoverRequest(BaseModel):
    """The release clone and the branch it must be on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    checkout: Path
    branch: str


class _Run(NamedTuple):
    workspace: Path
    checkout: Path
    lock: HeldLock
    branch: str
    head: str


def _foreign() -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "journal")


def _roll_back_release(run: _Run, journal: Journal) -> str:
    """Raises: ForarbeiderRefusedError: ``corpus_tree_mismatch: journal`` for no stored decision."""
    found = read_named(
        run.workspace, "batch_confirmations", journal.target_manifest_sha256, BatchConfirmation
    )
    if found is None or digest(record_text(found).encode()) != journal.decision_sha256:
        raise _foreign()
    roll_back(run.checkout, journal, read_back_of(run.branch, run.head, run.lock))
    return (
        f"RECOVERED rolled back release confirmation {journal.decision_sha256}: nothing"
        " of it remains in the working tree; run batch apply again to release"
    )


def _finish_withdrawal(run: _Run, journal: Journal) -> str:
    """Raises: ForarbeiderRefusedError: ``corpus_tree_mismatch: journal`` for no stored intent."""
    intent = read_named(
        run.workspace,
        "release_withdrawal_intents",
        journal.target_manifest_sha256,
        ReleaseWithdrawalIntent,
    )
    if intent is None or digest(record_text(intent).encode()) != journal.decision_sha256:
        raise _foreign()
    finish(run.checkout, journal, read_back_of(run.branch, run.head, run.lock))
    receipt = withdrawal_receipt(intent)
    write_record(run.workspace, "release_withdrawals", receipt, receipt.intent_sha256)
    return f"RECOVERED finished withdrawal of {intent.document_id} intent {journal.decision_sha256}"


def _settle(run: _Run) -> str:
    journal = pending(run.checkout)
    if journal is None:
        removed = clear_orphaned_staging(run.checkout)
        return "RECOVERED orphaned staging removed" if removed else "RECOVERED nothing pending"
    if journal.action == "release":
        return _roll_back_release(run, journal)
    return _finish_withdrawal(run, journal)


def batch_recover_impl(scope: ReleaseScope, ask: RecoverRequest) -> str:
    """Settle an interrupted change in a release clone, under a lock no live process holds.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked`` while a run is alive; the
            guard's identity refusals; a foreign journal; a hand-edited file.
    """
    if not valid_branch(ask.branch):
        raise ForarbeiderRefusedError(RefusalReason.TARGET_BRANCH_INVALID, "branch")
    workspace = existing_workspace(scope)
    checkout = lockable_checkout(ask.checkout)
    with recovered_lock(checkout) as held:
        head = verify_identity(checkout, ask.branch, held)
        line = _settle(_Run(workspace, checkout, held, ask.branch, head))
    return f"{line}\nnot committed: no git add, commit or push ran."
