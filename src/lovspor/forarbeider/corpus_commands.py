"""The bodies of the promotion plan and the synthetic simulator (#738, ADR-0018 PR3B-2).

``plan`` re-runs the PR3B-1 preflight a report was written from, reads the
corpus checkout without writing it, and keeps the planned files privately in
the workspace. ``simulate``, ``sandbox-create`` and ``sandbox-withdraw`` write,
and only to a sandbox this package made, from inputs that are the bundled
synthetic pages. No command here writes a real ``lovverk`` checkout, runs Git
or reaches the network, and none has a ``--publish`` or ``--force``.

Printed: ids, digests, codes and dataset paths. Never chapter text, evidence,
private paths or handles.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict

from lovspor.forarbeider import release_commands
from lovspor.forarbeider.corpus_models import MANIFEST_PATH, DocumentTimes
from lovspor.forarbeider.corpus_plan import (
    Draft,
    PromotionPlan,
    planned_files,
    release_draft,
    withdraw_draft,
)
from lovspor.forarbeider.corpus_render import ReleasedChapter, Rendering
from lovspor.forarbeider.corpus_transaction import (
    Binding,
    apply,
    finish,
    holds,
    locked,
    pending,
    roll_back,
)
from lovspor.forarbeider.corpus_tree import create_sandbox, read_dataset, require_sandbox
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_commands import (
    ChapterScope,
    ReleaseScope,
    Run,
    actor,
    digest_option,
    forbidden_trees,
    open_release_workspace,
    preflight_inputs,
    stored_record,
)
from lovspor.forarbeider.release_models import PreflightReport, digest, record_text
from lovspor.forarbeider.release_preflight import PreflightInputs, evaluate
from lovspor.forarbeider.release_store import read_named, write_preview, write_record
from lovspor.forarbeider.synthetic import is_synthetic, write_fixtures

_NOT_PERMISSION = "publication_authorized=false"
HISTORY_WARNING = (
    "warning: Git history and every clone keep the earlier bytes; a revert does not erase"
    " them. Taking down a real corpus is the owner's decision, outside this command."
)


class PlanRequest(BaseModel):
    """The eligible report to plan from, the checkpoints it was evaluated against, the corpus."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report: str
    checkpoints: tuple[str, ...]
    corpus: Path


class Verified(NamedTuple):
    """A preflight re-run and found to be the report it was asked to be."""

    inputs: PreflightInputs
    report: PreflightReport
    report_sha256: str


def _rerun(scope: ChapterScope, run: Run, expected: str | None) -> Verified:
    """The preflight of ``run`` evaluated again from the private files as they are now.

    Anything changed since the report, even a chapter fewer, denies: the plan
    covers exactly what was verified, or nothing.

    Raises:
        ForarbeiderRefusedError: ``promotion_denied``, naming the new report when
            it denies, ``report_changed`` when it clears something else.
    """
    inputs = preflight_inputs(scope, run)
    report = evaluate(inputs)
    sha = digest(record_text(report).encode())
    if report.outcome != "eligible_preview":
        write_record(open_release_workspace(scope.release), "reports", report)
        raise ForarbeiderRefusedError(RefusalReason.PROMOTION_DENIED, sha)
    if sha != expected:
        raise ForarbeiderRefusedError(RefusalReason.PROMOTION_DENIED, "report_changed")
    return Verified(inputs, report, sha)


def rendering_of(verified: Verified) -> Rendering:
    """The planned chapters of a verified preflight, with their review and owner times."""
    inputs, report = verified.inputs, verified.report
    planned = set(report.planned_chapters)
    chapters = []
    for item in inputs.observed:
        number = item.result.chapter_number
        if number in planned and item.history:
            planned.discard(number)
            chapters.append(ReleasedChapter(item.result, item.history[-1].decision.decided_at))
    if inputs.dossier is None or inputs.authorization is None or planned:
        raise ForarbeiderRefusedError(RefusalReason.PROMOTION_DENIED, "report")
    times = DocumentTimes(
        source_published_at=inputs.metadata.published_at,
        reviewed_at=inputs.dossier.record.reviewed_at,
        owner_decided_at=inputs.authorization.record.decided_at,
        released_at=inputs.at,
        withdrawn_at=None,
        time_basis="operator_supplied_unauthenticated",
        recorded_at="git_commit_time",
    )
    return Rendering(inputs.metadata, tuple(chapters), times)


def _report_run(workspace: Path, request: PlanRequest) -> tuple[str, Run]:
    sha = digest_option(request.report, "report")
    report = stored_record(workspace, "reports", sha, PreflightReport)
    if report.outcome != "eligible_preview":
        raise ForarbeiderRefusedError(RefusalReason.PROMOTION_DENIED, sha)
    at = report.evaluated_at.isoformat()
    return sha, Run(
        authorization=report.authorization_sha256, checkpoints=request.checkpoints, at=at
    )


def _plan(verified: Verified, run: Run, base: str | None, draft: Draft) -> PromotionPlan:
    inputs = verified.inputs
    results = [item.result for item in inputs.observed]
    return PromotionPlan(
        schema_version=1,
        record_type="forarbeider_promotion_plan",
        action="release",
        document=verified.report.document,
        report_sha256=verified.report_sha256,
        authorization_sha256=run.authorization,
        checkpoints=run.checkpoints,
        at=inputs.at,
        base_manifest_sha256=base,
        writes=planned_files(draft),
        removes=draft.removes,
        renderer_version=1,
        synthetic_inputs=is_synthetic(inputs.metadata, results),
        publication_authorized=False,
        writes_corpus=False,
        legal_sufficiency_decided_by_code=False,
    )


def plan_impl(scope: ChapterScope, request: PlanRequest) -> str:
    """Re-verify an eligible preflight and record the plan it supports; write no checkout.

    Raises:
        ForarbeiderRefusedError: ``promotion_denied``, ``plan_stale``, an unsafe
            or inconsistent corpus, or an input that could not be read safely.
    """
    workspace = open_release_workspace(scope.release)
    sha, run = _report_run(workspace, request)
    verified = _rerun(scope, run, sha)
    dataset = read_dataset(request.corpus)
    draft = release_draft(dataset, rendering_of(verified))
    for payload in draft.writes.values():
        write_preview(workspace, payload.decode())
    plan = _plan(verified, run, dataset.manifest_sha256, draft)
    return plan_lines(plan, write_record(workspace, "plans", plan))


def plan_lines(plan: PromotionPlan, sha: str) -> str:
    """The plan in ids, digests and dataset paths only."""
    head = f"PLAN {plan.document.id} plan {sha} action {plan.action} {_NOT_PERMISSION}"
    lines = [head, f"base {MANIFEST_PATH} {plan.base_manifest_sha256 or 'absent'}"]
    lines += [f"write {item.path} {item.sha256} {item.size_bytes}" for item in plan.writes]
    lines += [f"remove {path}" for path in plan.removes]
    lines.append(f"synthetic_inputs={str(plan.synthetic_inputs).lower()} writes_corpus=false")
    return "\n".join(lines)


class SimulateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    plan: str
    sandbox: Path


def _now() -> datetime:
    return datetime.now(UTC)


def _require_unexpired(verified: Verified) -> None:
    """``--at`` is the operator's word; the machine clock still ends an authorization.

    Raises:
        ForarbeiderRefusedError: ``promotion_denied``.
    """
    authorization = verified.inputs.authorization
    if authorization is None or authorization.record.valid_until <= _now():
        raise ForarbeiderRefusedError(RefusalReason.PROMOTION_DENIED, "authorization_expired")


def _authorized(scope: ChapterScope, plan: PromotionPlan) -> Verified:
    """The plan's preflight re-run from the private inputs as they are now, synthetic, unexpired.

    Raises:
        ForarbeiderRefusedError: denied, expired or not synthetic.
    """
    run = Run(
        authorization=plan.authorization_sha256,
        checkpoints=plan.checkpoints,
        at=plan.at.isoformat(),
    )
    verified = _rerun(scope, run, plan.report_sha256)
    results = [item.result for item in verified.inputs.observed]
    if not is_synthetic(verified.inputs.metadata, results):
        raise ForarbeiderRefusedError(RefusalReason.INPUT_NOT_SYNTHETIC, "observation")
    _require_unexpired(verified)
    return verified


def _simulated_draft(verified: Verified, plan: PromotionPlan, sandbox: Path) -> Draft:
    """The plan's change, re-derived from its verified preflight and the sandbox as it is now.

    Raises:
        ForarbeiderRefusedError: ``plan_stale``, or an unsafe or inconsistent sandbox.
    """
    dataset = read_dataset(sandbox)
    draft = release_draft(dataset, rendering_of(verified))
    if dataset.manifest_sha256 != plan.base_manifest_sha256 or planned_files(draft) != plan.writes:
        raise ForarbeiderRefusedError(RefusalReason.PLAN_STALE, "plan")
    return draft


def _settled(
    scope: ChapterScope, plan: PromotionPlan, sha: str, sandbox: Path
) -> tuple[Verified, bool]:
    """The plan authorized now, with an interrupted change of its own finished or rolled back.

    The authorization is checked before any journaled operation runs: a release
    whose plan no longer holds is put back, never completed.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: journal`` for another
            decision's journal, left as it is; denied, expired or not synthetic.
    """
    journal = pending(sandbox)
    if journal is not None and journal.binding != Binding("release", sha):
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "journal")
    try:
        verified = _authorized(scope, plan)
    except ForarbeiderRefusedError:
        if journal is not None:
            roll_back(sandbox, journal)
        raise
    if journal is not None:
        finish(sandbox, journal)
    return verified, journal is not None


def simulate_impl(scope: ChapterScope, request: SimulateRequest) -> str:
    """Apply a release plan to the synthetic sandbox, re-validated at this moment.

    Every run, a retry of an applied plan included, re-checks the plan before
    it reports anything.

    Raises:
        ForarbeiderRefusedError: not a sandbox, not synthetic, denied, stale,
            locked, another decision's journal, or a write that failed with its
            journal kept.
    """
    workspace = open_release_workspace(scope.release)
    sha = digest_option(request.plan, "plan")
    plan = stored_record(workspace, "plans", sha, PromotionPlan)
    if plan.action != "release" or not plan.synthetic_inputs:
        raise ForarbeiderRefusedError(RefusalReason.INPUT_NOT_SYNTHETIC, "plan")
    sandbox = require_sandbox(request.sandbox, forbidden_trees())
    with locked(sandbox):
        verified, recovered = _settled(scope, plan, sha, sandbox)
        if _applied(sandbox, plan):
            return _result_line("UNCHANGED", plan, sha, recovered)
        apply(sandbox, _simulated_draft(verified, plan, sandbox), Binding("release", sha))
    return _result_line("APPLIED", plan, sha, recovered)


def _applied(sandbox: Path, plan: PromotionPlan) -> bool:
    """A retry of an applied plan: every planned file already holds its bytes."""
    dataset = read_dataset(sandbox)
    manifest = next(item for item in plan.writes if item.path == MANIFEST_PATH)
    if dataset.manifest_sha256 != manifest.sha256:
        return False
    expected: dict[str, str | None] = {item.path: item.sha256 for item in plan.writes}
    return holds(sandbox, {**expected, **dict.fromkeys(plan.removes)})


def _result_line(verdict: str, plan: PromotionPlan, sha: str, recovered: bool) -> str:
    manifest = next(item for item in plan.writes if item.path == MANIFEST_PATH)
    note = " recovered_interrupted_change" if recovered else ""
    return (
        f"{verdict} {plan.document.id} plan {sha} in the synthetic sandbox: {MANIFEST_PATH}"
        f" {manifest.sha256}{note} ({_NOT_PERMISSION}, no git add, commit or push)"
    )


def sandbox_create_impl(scope: ReleaseScope, raw: Path) -> str:
    """Make a new disposable sandbox checkout.

    Raises:
        ForarbeiderRefusedError: ``unsafe_output_path``.
    """
    open_release_workspace(scope)
    create_sandbox(raw, forbidden_trees())
    return "SANDBOX created: a new empty Git checkout with a synthetic baseline, no remote"


def fixtures_impl(scope: ReleaseScope, raw: Path) -> str:
    """Write the bundled synthetic propositions' staged files for the operator to review.

    Raises:
        ForarbeiderRefusedError: an unsafe directory, or a file there with other bytes.
    """
    open_release_workspace(scope)
    names = write_fixtures(raw, forbidden_trees())
    return "\n".join(["FIXTURES synthetic, invented text only:", *names])


class WithdrawRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sandbox: Path
    document: str
    decided: str


class WithdrawalIntent(BaseModel):
    """The owner's decision to withdraw one document, stored before the sandbox changes.

    Named by the manifest digest it leads to, so a retry that finds the sandbox
    in that state finds the decision that put it there.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    record_type: Literal["forarbeider_sandbox_withdrawal_intent"]
    document_id: str
    owner: str
    withdrawn_at: datetime
    removed: tuple[str, ...]
    base_manifest_sha256: str | None
    result_manifest_sha256: str
    history_erased: Literal[False]


class Withdrawal(BaseModel):
    """The owner withdrew one document from the sandbox: what changed, by path and digest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    record_type: Literal["forarbeider_sandbox_withdrawal"]
    document_id: str
    owner: str
    withdrawn_at: datetime
    removed: tuple[str, ...]
    manifest_sha256: str
    intent_sha256: str
    history_erased: Literal[False]


class _Decided(NamedTuple):
    owner: str
    at: datetime


def _intent(
    request: WithdrawRequest, decided: _Decided, base: str | None, draft: Draft
) -> WithdrawalIntent:
    return WithdrawalIntent(
        schema_version=1,
        record_type="forarbeider_sandbox_withdrawal_intent",
        document_id=request.document,
        owner=decided.owner,
        withdrawn_at=decided.at,
        removed=draft.removes,
        base_manifest_sha256=base,
        result_manifest_sha256=digest(draft.writes[MANIFEST_PATH]),
        history_erased=False,
    )


def _unrecorded_intent(workspace: Path, sandbox: Path, document: str) -> WithdrawalIntent | None:
    """The intent that left the sandbox as it is now, withdrawing ``document``, with no receipt yet.

    Raises:
        ForarbeiderRefusedError: ``document_not_released`` when that withdrawal
            already has its receipt: it is done, and done once.
    """
    current = read_dataset(sandbox).manifest_sha256
    if current is None:
        return None
    intent = read_named(workspace, "withdrawal_intents", current, WithdrawalIntent)
    if intent is None or intent.document_id != document:
        return None
    if read_named(workspace, "withdrawals", _receipt(intent).intent_sha256, Withdrawal):
        raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
    return intent


def _bound_journal(workspace: Path, sandbox: Path) -> None:
    """Settle an interrupted change before a withdrawal; no grant is needed to take down.

    A release is rolled back, whether or not its plan still holds; a withdrawal
    is finished. Either must name a decision the workspace holds.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: journal`` for one
            naming no stored plan or intent.
    """
    journal = pending(sandbox)
    if journal is None:
        return
    if journal.action == "release":
        if read_named(workspace, "plans", journal.decision_sha256, PromotionPlan) is None:
            raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "journal")
        roll_back(sandbox, journal)
        return
    intent = read_named(
        workspace, "withdrawal_intents", journal.target_manifest_sha256, WithdrawalIntent
    )
    if intent is None or _receipt(intent).intent_sha256 != journal.decision_sha256:
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "journal")
    finish(sandbox, journal)


def _withdraw(
    workspace: Path, sandbox: Path, request: WithdrawRequest, decided: _Decided
) -> WithdrawalIntent:
    """The intent carried out: an earlier one this state records, or a new one, stored first."""
    with locked(sandbox):
        _bound_journal(workspace, sandbox)
        intent = _unrecorded_intent(workspace, sandbox, request.document)
        release_commands.require_a_person(f"withdraw {request.document}")
        if intent is not None:
            return intent
        dataset = read_dataset(sandbox)
        draft = withdraw_draft(dataset, request.document, decided.at)
        intent = _intent(request, decided, dataset.manifest_sha256, draft)
        sha = write_record(workspace, "withdrawal_intents", intent, intent.result_manifest_sha256)
        apply(sandbox, draft, Binding("withdraw", sha))
    return intent


def _receipt(intent: WithdrawalIntent) -> Withdrawal:
    return Withdrawal(
        schema_version=1,
        record_type="forarbeider_sandbox_withdrawal",
        document_id=intent.document_id,
        owner=intent.owner,
        withdrawn_at=intent.withdrawn_at,
        removed=intent.removed,
        manifest_sha256=intent.result_manifest_sha256,
        intent_sha256=digest(record_text(intent).encode()),
        history_erased=False,
    )


def sandbox_withdraw_impl(scope: ReleaseScope, request: WithdrawRequest) -> str:
    """Withdraw one document from the sandbox, forward only, at a terminal.

    The intent is stored before the sandbox changes and the receipt after, under
    the intent's digest: a run that stopped between them is completed by the
    next, which writes the same receipt and never undoes the withdrawal.

    Raises:
        ForarbeiderRefusedError: not a sandbox, no such released document,
            not at a terminal, locked, or a write that failed.
    """
    workspace = open_release_workspace(scope)
    sandbox = require_sandbox(request.sandbox, forbidden_trees())
    intent = _withdraw(workspace, sandbox, request, _Decided(*actor(request.decided)))
    receipt = _receipt(intent)
    stored = write_record(workspace, "withdrawals", receipt, receipt.intent_sha256)
    lines = [f"WITHDRAWN {request.document} record {stored}"]
    lines += [f"removed {path}" for path in intent.removed]
    lines += [f"rewritten {MANIFEST_PATH} {intent.result_manifest_sha256}", HISTORY_WARNING]
    return "\n".join(lines)
