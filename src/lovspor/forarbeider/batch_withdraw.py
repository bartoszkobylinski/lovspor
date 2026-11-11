"""``batch withdraw``: take one released document down, forward only (#738, B1b).

Against the same guarded release clone as ``batch apply``, at a terminal,
with no release grant needed: taking down is always allowed, publishing never
is without its checks. In order:

1. The workspace exists, ``--decided`` is not in the future, the branch is a
   ``forarbeider/`` branch, and the clone's lock is taken (a new one).
2. A withdrawal of this document already written to the clone, by an intent
   whose receipt is missing, is completed; one with its receipt is refused:
   a document is withdrawn once.
3. The full B1a guard (only this run's lock accepted, clean tree, the branch).
4. The document must be a released member, and every chapter the owner names
   one of its released chapters. Its file holds all of them, so all are
   withdrawn; the phrase names every one, so the owner confirms the real effect.
5. The intent (reason code, owner, time, chapters, the manifest it leads to)
   is stored first, then the same transaction as a release: the member kept as
   provenance and marked ``withdrawn``, its file removed, index and manifest
   rewritten, then the read-back. Then the receipt.

Git history is never rewritten: earlier commits, forks, clones, caches and
any copy already served keep the bytes. The owner commits the removal and
follows the takedown runbook (docs/operations.md).
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import typer
from pydantic import BaseModel, ConfigDict

from lovspor.forarbeider import release_commands
from lovspor.forarbeider.apply_preflight import existing_workspace
from lovspor.forarbeider.batch_apply import Owner, owner_of
from lovspor.forarbeider.batch_models import valid_branch
from lovspor.forarbeider.batch_records import (
    ReleaseWithdrawal,
    ReleaseWithdrawalIntent,
    WithdrawalReason,
)
from lovspor.forarbeider.batch_release import withdraw_v2_draft
from lovspor.forarbeider.corpus_commands import HISTORY_WARNING
from lovspor.forarbeider.corpus_models import INDEX_PATH, MANIFEST_PATH, ManifestDocumentV2
from lovspor.forarbeider.corpus_plan import Draft
from lovspor.forarbeider.corpus_transaction import Binding, apply
from lovspor.forarbeider.corpus_tree import read_any_dataset_files
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_checkout import (
    CheckoutState,
    current_branch,
    lockable_checkout,
    read_back_of,
    require_git_version,
    require_no_operation,
    verify_checkout,
)
from lovspor.forarbeider.release_commands import ReleaseScope
from lovspor.forarbeider.release_lock import HeldLock, release_lock
from lovspor.forarbeider.release_models import digest, record_text
from lovspor.forarbeider.release_store import read_named, write_record

TAKEDOWN_WARNING = (
    "warning: this removes the document from the working tree only. Earlier commits,"
    " forks, clones, caches and copies already served keep its bytes; Git history is"
    " never rewritten. Commit and push the removal, then follow the takedown runbook."
)


class ReleaseWithdrawRequest(BaseModel):
    """The clone, its branch, the document and chapters, the reason code and the owner's moment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    checkout: Path
    branch: str
    document: str
    chapters: tuple[int, ...]
    reason: WithdrawalReason
    decided: str


class _Run(NamedTuple):
    workspace: Path
    checkout: Path
    lock: HeldLock
    ask: ReleaseWithdrawRequest


def _changed(intent: ReleaseWithdrawalIntent) -> frozenset[str]:
    return frozenset({*intent.removed, INDEX_PATH, MANIFEST_PATH})


def withdrawal_receipt(intent: ReleaseWithdrawalIntent) -> ReleaseWithdrawal:
    return ReleaseWithdrawal(
        schema_version=1,
        record_type="forarbeider_release_withdrawal",
        intent_sha256=digest(record_text(intent).encode()),
        document_id=intent.document_id,
        reason=intent.reason,
        withdrawn_at=intent.withdrawn_at,
        removed=intent.removed,
        result_manifest_sha256=intent.result_manifest_sha256,
        written_to="working_tree_only",
        history_erased=False,
    )


def _completed(run: _Run) -> ReleaseWithdrawalIntent | None:
    """This document's withdrawal already in the clone, with no receipt yet; or ``None``.

    Raises:
        ForarbeiderRefusedError: ``document_not_released`` when it has its
            receipt; a pending change or a clone the guard refuses.
    """
    require_no_operation(run.checkout, run.lock)
    current = read_any_dataset_files(run.checkout)[0].manifest_sha256
    if current is None:
        return None
    intent = read_named(
        run.workspace, "release_withdrawal_intents", current, ReleaseWithdrawalIntent
    )
    if intent is None or intent.document_id != run.ask.document:
        return None
    sha = withdrawal_receipt(intent).intent_sha256
    if read_named(run.workspace, "release_withdrawals", sha, ReleaseWithdrawal) is not None:
        raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
    require_git_version(run.checkout)
    _, head = current_branch(run.checkout)
    changed = _changed(intent) if head == intent.head_commit else frozenset()
    verify_checkout(run.checkout, intent.target_branch, run.lock, changed)
    return intent


def released_member(members: tuple[ManifestDocumentV2, ...], document: str) -> ManifestDocumentV2:
    """Raises: ForarbeiderRefusedError: ``document_not_released: document``."""
    found = next((item for item in members if item.document.id == document), None)
    if found is None or found.release_status != "released":
        raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
    return found


def require_named(member: ManifestDocumentV2, named: tuple[int, ...]) -> tuple[int, ...]:
    """The member's released chapters, refused unless the owner named some of them, once each.

    Raises:
        ForarbeiderRefusedError: ``chapter_not_released: chapters``.
    """
    released = tuple(item.chapter_number for item in member.chapters)
    if not named or len(set(named)) != len(named) or not set(named) <= set(released):
        raise ForarbeiderRefusedError(RefusalReason.CHAPTER_NOT_RELEASED, "chapters")
    return released


def _now() -> datetime:
    return datetime.now(UTC)


def intent_of(
    run: _Run, state: CheckoutState, decided: Owner, draft: tuple[Draft, tuple[int, ...]]
) -> ReleaseWithdrawalIntent:
    files, withdrawn = draft
    ask = run.ask
    return ReleaseWithdrawalIntent(
        schema_version=1,
        record_type="forarbeider_release_withdrawal_intent",
        document_id=ask.document,
        chapters_named=tuple(sorted(ask.chapters)),
        chapters_withdrawn=withdrawn,
        reason=ask.reason,
        owner=decided.handle,
        withdrawn_at=decided.at,
        target_branch=state.branch,
        head_commit=state.head_commit,
        base_manifest_sha256=_base(state),
        result_manifest_sha256=digest(files.writes[MANIFEST_PATH]),
        removed=files.removes,
        phrase=_phrase(ask, withdrawn),
        identity_authenticated=False,
        history_erased=False,
    )


def _base(state: CheckoutState) -> str:
    """A released member exists, so the manifest does; a missing one is a guard defect."""
    if state.base_manifest_sha256 is None:
        raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
    return state.base_manifest_sha256


def _phrase(ask: ReleaseWithdrawRequest, withdrawn: tuple[int, ...]) -> str:
    chapters = ",".join(str(number) for number in withdrawn)
    return f"withdraw {ask.document} chapters {chapters} from {ask.branch}"


def _withdraw(run: _Run, decided: Owner) -> ReleaseWithdrawalIntent:
    """The guard, the draft, the owner's phrase, the stored intent, then the transaction.

    Raises:
        ForarbeiderRefusedError: the guard's refusal, no such released document or
            chapter, no phrase, or the transaction's own refusals.
    """
    state = verify_checkout(run.checkout, run.ask.branch, run.lock)
    dataset = read_any_dataset_files(run.checkout)[0]
    member = released_member(dataset.manifest.documents, run.ask.document)
    withdrawn = require_named(member, run.ask.chapters)
    draft = withdraw_v2_draft(dataset.manifest, run.ask.document, decided.at)
    intent = intent_of(run, state, decided, (draft, withdrawn))
    typer.echo(_preview(intent))
    release_commands.require_a_person(intent.phrase, "withdrawal")
    name = intent.result_manifest_sha256
    sha = write_record(run.workspace, "release_withdrawal_intents", intent, name)
    read_back = read_back_of(state.branch, state.head_commit, run.lock)
    apply(run.checkout, draft, Binding("withdraw", sha), read_back)
    verify_checkout(run.checkout, state.branch, run.lock, _changed(intent))
    return intent


def _preview(intent: ReleaseWithdrawalIntent) -> str:
    chapters = ",".join(str(number) for number in intent.chapters_withdrawn)
    lines = [
        f"WITHDRAWAL {intent.document_id} reason {intent.reason} chapters {chapters} "
        f"target {intent.target_branch} head {intent.head_commit}",
        *(f"remove {path}" for path in intent.removed),
        f"result {MANIFEST_PATH} {intent.result_manifest_sha256}",
    ]
    return "\n".join([*lines, TAKEDOWN_WARNING])


def release_withdraw_impl(scope: ReleaseScope, ask: ReleaseWithdrawRequest) -> str:
    """Withdraw one released document from a release clone's working tree, at a terminal.

    Raises:
        ForarbeiderRefusedError: the first condition that does not hold.
    """
    owner = owner_of(ask.decided, _now())
    if not valid_branch(ask.branch):
        raise ForarbeiderRefusedError(RefusalReason.TARGET_BRANCH_INVALID, "branch")
    workspace = existing_workspace(scope)
    checkout = lockable_checkout(ask.checkout)
    with release_lock(checkout) as held:
        run = _Run(workspace, checkout, held, ask)
        intent = _completed(run) or _withdraw(run, owner)
    receipt = withdrawal_receipt(intent)
    stored = write_record(workspace, "release_withdrawals", receipt, receipt.intent_sha256)
    lines = [
        f"WITHDRAWN {intent.document_id} record {stored} reason {intent.reason}",
        *(f"removed {path}" for path in intent.removed),
        f"rewritten {MANIFEST_PATH} {intent.result_manifest_sha256}",
    ]
    return "\n".join([*lines, HISTORY_WARNING, TAKEDOWN_WARNING])
