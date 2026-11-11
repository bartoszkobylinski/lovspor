"""``batch apply``: one confirmed batch written to a release clone, all or nothing (#738, B1b).

The only command that writes a lovverk checkout, and only ``forarbeider/`` of
a dedicated release clone, in its working tree. It runs no ``git add``,
commit or push; the owner commits, pushes and opens the lovverk PR, and
merging that PR is the publication. In order, the first failure refusing:

1. The workspace exists, the ``--decided`` moment is not in the future, and
   the path is safe to lock (canonical, not forbidden, private, its own
   ``.git``). The clone's lock is taken: a new one, never one another run left.
2. A batch this plan already wrote is recognised and reported ``UNCHANGED``.
3. :func:`~lovspor.forarbeider.apply_preflight.apply_preflight` runs **under
   the lock**: the whole B1a guard (only this run's own lock accepted) and the
   plan evaluated again now, which must reproduce byte for byte.
4. The files are drafted (only included chapters, deterministic renderer,
   manifest schema 2); a document the manifest already lists refuses.
5. The owner types one phrase naming the plan and binding digest prefixes,
   the document and chapter counts and the target branch. The confirmation
   is stored, bound to those inputs and to the exact files and manifest.
6. The preflight runs **again**, immediately before staging: its binding and
   the redrafted files must be the confirmed ones.
7. The transaction: journal, staged bytes inside ``.git``, documents then
   index then manifest, then the read-back (status lists exactly those
   paths, HEAD unmoved, dataset exactly the new manifest), then the journal
   is cleared and the guard runs once more on the result.

A run that stops after its journal is written leaves the clone recoverable,
never silently half-published: B1a, this command and the withdrawal all
refuse a clone with a journal, and ``batch recover`` settles it.

Printed: codes, ids, digests, counts, dataset paths and the branch. Never
chapter text, a handle, evidence or a private path.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import typer
from pydantic import BaseModel, ConfigDict

from lovspor.forarbeider import release_commands
from lovspor.forarbeider.apply_preflight import (
    ApplyPreflightRequest,
    ApplyVerdict,
    apply_preflight,
    existing_workspace,
    preflight_lines,
    stored_plan,
)
from lovspor.forarbeider.batch_clearance import DocumentEvidence
from lovspor.forarbeider.batch_models import PlannedChapter
from lovspor.forarbeider.batch_records import BatchConfirmation, BatchReleaseReceipt
from lovspor.forarbeider.batch_release import (
    ChapterRelease,
    Decision,
    DocumentRelease,
    release_batch_draft,
)
from lovspor.forarbeider.chapter_models import ChapterResult
from lovspor.forarbeider.corpus_models import MANIFEST_PATH
from lovspor.forarbeider.corpus_plan import Draft, planned_files
from lovspor.forarbeider.corpus_transaction import Binding, apply
from lovspor.forarbeider.corpus_tree import read_any_dataset_files
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_checkout import (
    current_branch,
    lockable_checkout,
    read_back_of,
    require_git_version,
    require_no_operation,
    verify_checkout,
)
from lovspor.forarbeider.release_commands import ReleaseScope, actor
from lovspor.forarbeider.release_lock import HeldLock, release_lock
from lovspor.forarbeider.release_models import digest, record_text
from lovspor.forarbeider.release_store import read_named, write_record

NOT_COMMITTED = (
    "not committed: no git add, commit or push ran. The owner reviews the diff, commits,"
    " pushes and opens the lovverk PR; merging it is the publication."
)
NOT_AUTHENTICATED = (
    "identity_authenticated=false: the typed phrase is the owner's act at a terminal,"
    " not an authenticated identity."
)


class BatchApplyRequest(BaseModel):
    """The plan, the release clone, the files the plan was made from, and the owner's moment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan: str
    checkout: Path
    branch: str
    request: Path
    source_proof: Path
    decided: str


class Owner(BaseModel):
    """Who confirmed, by handle, and when: the operator's word, never authenticated."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    handle: str
    at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def owner_of(decided: str, now: datetime) -> Owner:
    """``HANDLE@ISO-8601``, never a moment after the machine clock.

    Raises:
        ForarbeiderRefusedError: ``release_invalid: actor``, ``invalid_timestamp``.
    """
    handle, at = actor(decided)
    if at > now:
        raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "decided")
    return Owner(handle=handle, at=at)


def _preflight_ask(ask: BatchApplyRequest, checkout: Path) -> ApplyPreflightRequest:
    return ApplyPreflightRequest(
        plan=ask.plan, checkout=checkout, request=ask.request, source_proof=ask.source_proof
    )


def _not_reproduced(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.PLAN_NOT_REPRODUCED, detail)


def _chapter_release(results: dict[str, ChapterResult], planned: PlannedChapter) -> ChapterRelease:
    """The fresh result for an included chapter, which must be the one the plan states.

    Raises:
        ForarbeiderRefusedError: ``plan_not_reproduced: <chapter id>: outcome_missing``.
    """
    result = results.get(planned.chapter_id)
    if result is None or planned.clearance is None or result.text_sha256 != planned.text_sha256:
        raise _not_reproduced(f"{planned.chapter_id}: outcome_missing")
    return ChapterRelease(result, planned.clearance)


def _document_release(
    evidence: DocumentEvidence | None, included: list[PlannedChapter], document: str
) -> DocumentRelease:
    if evidence is None:
        raise _not_reproduced(f"{document}: outcome_missing")
    results = {item.result.id: item.result for item in evidence.inputs.observed}
    chapters = tuple(_chapter_release(results, planned) for planned in included)
    return DocumentRelease(evidence.inputs.metadata, chapters)


def releases_of(verdict: ApplyVerdict) -> tuple[DocumentRelease, ...]:
    """Every planned document with an included chapter, from the evidence evaluated now.

    Raises:
        ForarbeiderRefusedError: ``plan_not_reproduced`` for an included chapter
            the fresh evaluation has no result for.
    """
    evidence = {item.fresh.document.id: item for item in verdict.batch.documents}
    releases = []
    for document in verdict.plan.documents:
        included = [chapter for chapter in document.chapters if chapter.included]
        if included:
            found = evidence.get(document.document.id)
            releases.append(_document_release(found, included, document.document.id))
    return tuple(releases)


def batch_draft(verdict: ApplyVerdict, checkout: Path, at: datetime) -> Draft:
    """The files this batch writes on the dataset as the guard just read it.

    Raises:
        ForarbeiderRefusedError: ``plan_not_reproduced: base_manifest``,
            ``release_conflict``, ``render_unsafe`` or ``release_invalid``.
    """
    base = read_any_dataset_files(checkout)[0]
    if base.manifest_sha256 != verdict.state.base_manifest_sha256:
        raise _not_reproduced("base_manifest")
    decision = Decision(verdict.batch.verdict.policy.decided_at, at)
    return release_batch_draft(base.manifest, releases_of(verdict), decision)


def phrase_of(verdict: ApplyVerdict, documents: int) -> str:
    """What the owner types: digest prefixes, counts and the branch, so any change changes it."""
    counts = verdict.plan.counts
    chapters = counts.standing_policy + counts.owner_review
    return (
        f"apply {verdict.plan_sha256[:12]} binding {verdict.binding_sha256[:12]} "
        f"documents {documents} chapters {chapters} to {verdict.state.branch}"
    )


#: What every confirmation states: an act at a terminal that commits, pushes nothing.
_FIXED_CONFIRMATION: dict[str, object] = {
    "schema_version": 1,
    "record_type": "forarbeider_batch_confirmation",
    "identity_authenticated": False,
    "git_add": False,
    "commits": False,
    "pushes": False,
}


def confirmation_of(verdict: ApplyVerdict, draft: Draft, owner: Owner) -> BatchConfirmation:
    documents = tuple(item.metadata.id for item in releases_of(verdict))
    counts = verdict.plan.counts
    return BatchConfirmation.model_validate(
        {
            **_FIXED_CONFIRMATION,
            "plan_sha256": verdict.plan_sha256,
            "binding_sha256": verdict.binding_sha256,
            "binding": verdict.binding,
            "target_branch": verdict.state.branch,
            "documents": documents,
            "chapters": counts.standing_policy + counts.owner_review,
            "phrase": phrase_of(verdict, len(documents)),
            "owner": owner.handle,
            "decided_at": owner.at,
            "result_manifest_sha256": digest(draft.writes[MANIFEST_PATH]),
            "writes": planned_files(draft),
        }
    )


def _summary(verdict: ApplyVerdict, confirmation: BatchConfirmation) -> str:
    lines = [preflight_lines(verdict), f"would write ({len(confirmation.writes)} files):"]
    lines += [f"write {item.path} {item.sha256} {item.size_bytes}" for item in confirmation.writes]
    lines.append(f"result {MANIFEST_PATH} {confirmation.result_manifest_sha256}")
    return "\n".join(lines)


def receipt_of(confirmation: BatchConfirmation, sha: str) -> BatchReleaseReceipt:
    binding = confirmation.binding
    return BatchReleaseReceipt(
        schema_version=1,
        record_type="forarbeider_batch_release",
        confirmation_sha256=sha,
        plan_sha256=confirmation.plan_sha256,
        binding_sha256=confirmation.binding_sha256,
        target_branch=confirmation.target_branch,
        head_commit=binding.head_commit,
        base_manifest_sha256=binding.base_manifest_sha256,
        result_manifest_sha256=confirmation.result_manifest_sha256,
        writes=confirmation.writes,
        documents=confirmation.documents,
        written_to="working_tree_only",
        publication="owner_merge_of_lovverk_pr",
    )


def written_paths(confirmation: BatchConfirmation) -> frozenset[str]:
    return frozenset(item.path for item in confirmation.writes)


class _Run(NamedTuple):
    """One apply: its scope, request, workspace, locked clone, lock and owner."""

    scope: ReleaseScope
    ask: BatchApplyRequest
    workspace: Path
    checkout: Path
    lock: HeldLock
    owner: Owner


def _completed(run: _Run) -> BatchConfirmation | None:
    """The confirmation of this plan that left the clone as it is now, or ``None``.

    Uncommitted: status lists exactly its files. Committed by the owner: HEAD
    moved and the tree is clean. Either way the dataset is its manifest.

    Raises:
        ForarbeiderRefusedError: a pending change, or a clone the guard refuses.
    """
    require_no_operation(run.checkout, run.lock)
    current = read_any_dataset_files(run.checkout)[0].manifest_sha256
    if current is None:
        return None
    found = read_named(run.workspace, "batch_confirmations", current, BatchConfirmation)
    if found is None or found.plan_sha256 != stored_plan(run.workspace, run.ask.plan)[0]:
        return None
    require_git_version(run.checkout)
    _, head = current_branch(run.checkout)
    changed = written_paths(found) if head == found.binding.head_commit else frozenset()
    verify_checkout(run.checkout, found.target_branch, run.lock, changed)
    return found


def _confirmed(run: _Run) -> tuple[str, BatchConfirmation]:
    """The preflight under the lock, the draft, the owner's phrase, the stored confirmation.

    Raises:
        ForarbeiderRefusedError: any preflight refusal, a conflict, or no phrase.
    """
    verdict = apply_preflight(run.scope, _preflight_ask(run.ask, run.checkout), _now(), run.lock)
    draft = batch_draft(verdict, run.checkout, run.owner.at)
    confirmation = confirmation_of(verdict, draft, run.owner)
    typer.echo(_summary(verdict, confirmation))
    release_commands.require_a_person(confirmation.phrase, "batch_confirmation")
    name = confirmation.result_manifest_sha256
    return write_record(run.workspace, "batch_confirmations", confirmation, name), confirmation


def _write(run: _Run, sha: str, confirmation: BatchConfirmation) -> None:
    """The preflight once more, immediately before staging, then the transaction.

    Raises:
        ForarbeiderRefusedError: ``confirmation_mismatch: binding|files`` when the
            world moved since the phrase; the transaction's own refusals.
    """
    verdict = apply_preflight(run.scope, _preflight_ask(run.ask, run.checkout), _now(), run.lock)
    if verdict.binding_sha256 != confirmation.binding_sha256:
        raise ForarbeiderRefusedError(RefusalReason.CONFIRMATION_MISMATCH, "binding")
    draft = batch_draft(verdict, run.checkout, confirmation.decided_at)
    if planned_files(draft) != confirmation.writes:
        raise ForarbeiderRefusedError(RefusalReason.CONFIRMATION_MISMATCH, "files")
    state = verdict.state
    read_back = read_back_of(state.branch, state.head_commit, run.lock)
    apply(run.checkout, draft, Binding("release", sha), read_back)
    verify_checkout(run.checkout, state.branch, run.lock, written_paths(confirmation))


def _locked_run(scope: ReleaseScope, ask: BatchApplyRequest, owner: Owner) -> str:
    workspace = existing_workspace(scope)
    if stored_plan(workspace, ask.plan)[1].target_branch != ask.branch:
        raise ForarbeiderRefusedError(RefusalReason.TARGET_BRANCH_MISMATCH, "branch")
    checkout = lockable_checkout(ask.checkout)
    with release_lock(checkout) as held:
        run = _Run(scope, ask, workspace, checkout, held, owner)
        done = _completed(run)
        if done is not None:
            return _finished(run, done, "UNCHANGED")
        sha, confirmation = _confirmed(run)
        _write(run, sha, confirmation)
        return _finished(run, confirmation, "BATCH APPLIED")


def _finished(run: _Run, confirmation: BatchConfirmation, verdict: str) -> str:
    sha = digest(record_text(confirmation).encode())
    receipt = receipt_of(confirmation, sha)
    stored = write_record(run.workspace, "batch_releases", receipt, sha)
    lines = [
        f"{verdict} plan {confirmation.plan_sha256} confirmation {sha} receipt {stored}",
        f"target {confirmation.target_branch} head {receipt.head_commit} "
        f"base {MANIFEST_PATH} {receipt.base_manifest_sha256 or 'absent'} "
        f"result {receipt.result_manifest_sha256}",
        f"documents {len(confirmation.documents)} chapters {confirmation.chapters}",
    ]
    lines += [f"wrote {item.path} {item.sha256}" for item in confirmation.writes]
    return "\n".join([*lines, NOT_COMMITTED, NOT_AUTHENTICATED])


def batch_apply_impl(scope: ReleaseScope, ask: BatchApplyRequest) -> str:
    """Apply a stored batch plan to a release clone's working tree, after the owner's phrase.

    Raises:
        ForarbeiderRefusedError: the first condition that does not hold. Before
            the journal is written nothing in the clone changed; after it, the
            clone is left for ``batch recover``.
    """
    owner = owner_of(ask.decided, _now())
    return _locked_run(scope, ask, owner)
