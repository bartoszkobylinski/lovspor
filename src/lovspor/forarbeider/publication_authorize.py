"""AR4b-1: fail-closed publication authorization, made fresh for one exact attempt (#738).

:func:`authorize` takes only raw, signed or engine-sealed material and checks
it **itself**, at the time it is given, immediately before a publication:

- ``proposal``: the AR4a clone holds a ``prepared`` journal of exactly this
  transaction and its read-back still proves every byte;
- ``plan``, ``quarantine``, ``trust_root``: the sealed plan and quarantine
  assessment are this transaction's, from the trust root in use;
- ``policy``: the FC7b approvals, verified now against the trust root, with
  their revocations, for exactly the plan's policy;
- ``publisher``: the configured bot principal is a bot key of the root holding
  the release and publication namespaces;
- ``limits``: the owner-signed limits, verified now, are the plan's and keep
  this batch within their caps;
- ``stop_control`` and ``stop_latch``: the owner-signed STOP/RESUME chain,
  verified now, is not stopped, and the persistent latch is not set;
- ``budget``: the ledger, replayed now, holds a reservation for exactly this
  transaction covering this batch and one commit;
- ``remote_head``: ``main`` read now is the expected base;
- ``path_identity``: the configured path is the transaction's, and on path A
  the transport's App identity is the pinned one;
- ``bot_signature`` (path B): the local commit is the proposal from the base,
  signed by the bot in the release namespace.

What no slice implements yet is ``unavailable``, never passed: the AR1 gate
verdict (no passing state until D10, D11 and the lineage anchors),
FC7b's pending blockers (D6, D10, D11, the missing integration and an
unpinned trust root), the AR5 anchors of the control chain and the ledger, the
AR6 manifest signature and the AR7 settings verification. Production needs
every check passed, so it is denied; a simulation may waive exactly those, by
name (:mod:`lovspor.forarbeider.publication_execution_models`).

The evidence is sealed by this verifier, so evidence built or edited by a
caller is refused where it is used. Nothing is sent, written or signed here.
"""

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from lovspor.forarbeider import publication_stage
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_authorization import (
    AuthorizationRequest,
    SignedRecord,
    verify_authorization,
)
from lovspor.forarbeider.policy_authorization_models import PolicyAuthorization
from lovspor.forarbeider.policy_trust import PUBLICATION_NAMESPACE, RELEASE_NAMESPACE, TrustRoot
from lovspor.forarbeider.publication_execution_models import (
    AppIdentity,
    AuthorizationCheck,
    AuthorizationEvidence,
    CheckName,
    Mode,
    PublicationSettings,
    decide,
)
from lovspor.forarbeider.publication_models import ProposalCounts, PublicationTransaction
from lovspor.forarbeider.release_contract_models import (
    BudgetUsage,
    ControlEvidence,
    LedgerEvidence,
    LimitsEvidence,
    PlanEvidence,
)
from lovspor.forarbeider.release_control import ControlRequest, verify_control
from lovspor.forarbeider.release_ledger import LedgerRequest, verify_ledger
from lovspor.forarbeider.release_limits import LimitsMaterial, LimitsRequest, verify_limits
from lovspor.forarbeider.release_limits_models import ReleaseConstraints
from lovspor.forarbeider.release_plan_models import ReleasePlan
from lovspor.forarbeider.release_provenance import authentic, issue_provenance
from lovspor.forarbeider.release_quarantine_models import QuarantineEvidence
from lovspor.forarbeider.version_release_models import PolicyReference, digest_of

#: What stays unavailable until its slice exists; each a separate, named check.
_PENDING: tuple[tuple[CheckName, str], ...] = (
    ("ar1_gate_verdict", "ar1_gate_verdict_has_no_passing_state_before_d10_d11_and_anchors"),
    ("ar5_control_anchor", "control_chain_unanchored_ar5_pending"),
    ("ar5_ledger_anchor", "budget_ledger_unanchored_ar5_pending"),
    ("ar6_release_manifest_signature", "release_manifest_signature_ar6_pending"),
    ("ar7_repository_settings", "repository_settings_unverified_ar7_pending"),
)


class AuthorizationInputs(NamedTuple):
    """What a caller offers; nothing here is trusted until :func:`authorize` verifies it."""

    transaction: PublicationTransaction
    plan: PlanEvidence
    quarantine: QuarantineEvidence
    policy: AuthorizationRequest
    limits: LimitsMaterial
    control: tuple[SignedRecord, ...]
    ledger: tuple[bytes, ...]
    settings: PublicationSettings


class AdapterFacts(NamedTuple):
    """What the adapter shows: App identity (A), local commit check (B), the stop latch."""

    app: AppIdentity | None = None
    local_commit: str | None = None
    commit_reasons: tuple[str, ...] | None = None
    latched: bool = False


class AuthorizationContext(NamedTuple):
    inputs: AuthorizationInputs
    root: TrustRoot
    clone: Path
    remote: Callable[[], str | None]
    now: datetime
    attempt_id: str
    mode: Mode
    facts: AdapterFacts = AdapterFacts()


class _Found(NamedTuple):
    """The verified evidence the checks rest on, for the record."""

    plan: ReleasePlan | None
    limits: LimitsEvidence
    control: ControlEvidence
    ledger: LedgerEvidence
    remote_head: str | None
    fingerprint: str | None


def _check(name: CheckName, reasons: list[str] | tuple[str, ...]) -> AuthorizationCheck:
    ordered = tuple(sorted(set(reasons)))
    return AuthorizationCheck(name=name, status="failed" if ordered else "passed", reasons=ordered)


def _unavailable(name: CheckName, reason: str) -> AuthorizationCheck:
    return AuthorizationCheck(name=name, status="unavailable", reasons=(reason,))


# --- the checks ----------------------------------------------------------------------------


def _proposal(context: AuthorizationContext) -> AuthorizationCheck:
    try:
        journal = publication_stage.pending_journal(context.clone)
        if journal is None or journal.state != "prepared":
            return _check("proposal", ["proposal_not_prepared"])
        if journal.transaction != context.inputs.transaction:
            return _check("proposal", ["proposal_not_this_transaction"])
        publication_stage.verify_proposal(context.clone, journal.transaction)
    except ForarbeiderRefusedError as error:
        return _check("proposal", [f"proposal:{error.detail}"])
    return _check("proposal", [])


def _plan_of(context: AuthorizationContext) -> ReleasePlan | None:
    evidence = context.inputs.plan
    plan = evidence.plan
    sealed = evidence.status == "verified" and authentic(evidence, "batch_plan")
    if plan is None or not sealed or evidence.plan_sha256 != digest_of(plan):
        return None
    return plan


def _plan(context: AuthorizationContext, plan: ReleasePlan | None) -> AuthorizationCheck:
    content = context.inputs.transaction.content
    if plan is None:
        return _check("plan", ["plan_unverified"])
    batch = next((b for b in plan.batches if b.batch_id == content.batch_id), None)
    documents = tuple(item.document_id for item in content.documents)
    reasons = [] if plan.plan_id == content.plan_id else ["plan_not_the_transactions"]
    if batch is None or batch.document_ids != documents:
        reasons.append("batch_not_the_transactions")
    return _check("plan", reasons)


def _quarantine(context: AuthorizationContext, plan: ReleasePlan | None) -> AuthorizationCheck:
    found = context.inputs.quarantine
    content = context.inputs.transaction.content
    sealed = found.status == "verified" and authentic(found, "quarantine_assessment")
    digest = digest_of(found.quarantine) if found.quarantine is not None else None
    same = digest is not None and digest == found.quarantine_sha256 == content.quarantine_sha256
    honoured = plan is not None and plan.basis.quarantine_sha256 == content.quarantine_sha256
    restricted = (
        {item.document_id for item in found.quarantine.documents} if found.quarantine else set()
    )
    reasons = [] if sealed and same and honoured else ["quarantine_not_the_transactions"]
    if restricted & {item.document_id for item in content.documents}:
        reasons.append("quarantined_document_in_transaction")
    return _check("quarantine", reasons)


def _trust_root(context: AuthorizationContext) -> AuthorizationCheck:
    root = context.root.sha256
    same = (
        context.inputs.plan.trust_root_sha256 == root == context.inputs.quarantine.trust_root_sha256
    )
    return _check(
        "trust_root", [] if same and context.root.entries else ["trust_root_not_the_plans"]
    )


def _approval_reasons(verdict: PolicyAuthorization) -> list[str]:
    found = [
        f"{label}:{reason}"
        for label, check in (
            ("policy_approval", verdict.policy_approval),
            ("automated_publication", verdict.automated_publication),
        )
        if check.status != "verified"
        for reason in check.reasons
    ]
    found += [
        f"revocation:{r}" for c in verdict.revocations if c.status != "verified" for r in c.reasons
    ]
    return found


def _policy(
    context: AuthorizationContext, plan: ReleasePlan | None
) -> tuple[AuthorizationCheck, AuthorizationCheck]:
    request = replace(context.inputs.policy, checked_at=context.now)
    try:
        verdict = verify_authorization(request, context.root)
    except ForarbeiderRefusedError as error:
        pending = _unavailable("fc7b_pending_blockers", "policy_unreadable")
        return _check("policy", [f"policy:{error.detail}"]), pending
    reasons = _approval_reasons(verdict)
    ours = PolicyReference(
        policy_id=verdict.policy_id,
        policy_version=verdict.policy_version,
        policy_sha256=verdict.policy_sha256,
    )
    if plan is None or plan.basis.policy != ours:
        reasons.append("policy_not_the_plans")
    pending = _unavailable("fc7b_pending_blockers", ",".join(verdict.blockers))
    return _check("policy", reasons), pending


def _publisher(context: AuthorizationContext) -> tuple[AuthorizationCheck, str | None]:
    principal = context.inputs.settings.bot_principal
    entry = next((e for e in context.root.entries if e.principal == principal), None)
    wanted = {RELEASE_NAMESPACE, PUBLICATION_NAMESPACE}
    good = entry is not None and entry.role == "bot_execution" and wanted <= set(entry.namespaces)
    revoked = entry is not None and entry.key_blob in context.root.revoked_blobs
    reasons = [] if good and not revoked else ["publisher_not_a_listed_bot_key"]
    return _check("publisher", reasons), None if entry is None else entry.fingerprint


def _within(counts: ProposalCounts, caps: ReleaseConstraints) -> bool:
    return (
        counts.documents <= caps.max_documents_per_batch
        and counts.files <= caps.max_files_per_batch
        and counts.bytes <= caps.max_bytes_per_batch
    )


def _limits(
    context: AuthorizationContext, plan: ReleasePlan | None
) -> tuple[AuthorizationCheck, LimitsEvidence]:
    policy = context.inputs.policy.policy
    request = LimitsRequest(policy, context.inputs.limits, context.now)
    evidence = verify_limits(request, context.root)
    reasons = [f"limits:{r}" for r in evidence.reasons]
    if evidence.status != "verified" or evidence.constraints is None:
        return _check("limits", reasons or ["limits_unverified"]), evidence
    if plan is None or evidence.limits_sha256 != plan.basis.limits_sha256:
        reasons.append("limits_not_the_plans")
    if not _within(context.inputs.transaction.content.counts, evidence.constraints):
        reasons.append("batch_over_limits")
    return _check("limits", reasons), evidence


def _control(
    context: AuthorizationContext, plan: ReleasePlan | None
) -> tuple[AuthorizationCheck, ControlEvidence]:
    policy_id = plan.basis.policy.policy_id if plan else None
    request = ControlRequest(context.inputs.control, policy_id, context.now)
    evidence = verify_control(request, context.root)
    if evidence.state == "not_stopped":
        return _check("stop_control", []), evidence
    reasons = [f"control:{r}" for r in evidence.reasons]
    reasons += [f"active_stop:{s}" for s in evidence.active_stops]
    return _check("stop_control", reasons or ["stopped"]), evidence


def batch_usage(transaction: PublicationTransaction) -> BudgetUsage:
    counts = transaction.content.counts
    return BudgetUsage(
        documents=counts.documents, files=counts.files, bytes=counts.bytes, commits=1
    )


def _covers(reserved: BudgetUsage | None, wanted: BudgetUsage) -> bool:
    names = ("documents", "files", "bytes", "commits")
    return reserved is not None and all(getattr(reserved, n) >= getattr(wanted, n) for n in names)


def _budget(
    context: AuthorizationContext, limits: LimitsEvidence
) -> tuple[AuthorizationCheck, LedgerEvidence]:
    transaction = context.inputs.transaction
    day, release = context.now.date(), transaction.transaction_id
    evidence = verify_ledger(LedgerRequest(context.inputs.ledger, limits, day, release))
    reasons = [f"ledger:{r}" for r in evidence.reasons]
    if evidence.status != "verified" or evidence.release_state != "reserved":
        reasons.append("budget_not_reserved_for_this_transaction")
    elif not _covers(evidence.release_reserved, batch_usage(transaction)):
        reasons.append("reservation_smaller_than_the_batch")
    return _check("budget", reasons), evidence


def _remote(context: AuthorizationContext) -> tuple[AuthorizationCheck, str | None]:
    try:
        head = context.remote()
    except Exception:
        return _check("remote_head", ["remote_unreadable"]), None
    base = context.inputs.transaction.content.publication.base_commit
    return _check("remote_head", [] if head == base else ["remote_head_not_the_base"]), head


def _path_identity(context: AuthorizationContext) -> AuthorizationCheck:
    settings = context.inputs.settings
    path = context.inputs.transaction.content.publication.path
    reasons = [] if settings.path == path else ["configured_path_not_the_transactions"]
    if path == "A" and (context.facts.app is None or context.facts.app != settings.app):
        reasons.append("app_identity_not_the_pinned_one")
    if path == "B" and context.facts.app is not None:
        reasons.append("path_b_has_no_app")
    return _check("path_identity", reasons)


def _bot_signature(context: AuthorizationContext) -> Iterator[AuthorizationCheck]:
    if context.inputs.transaction.content.publication.path != "B":
        return
    facts = context.facts
    if facts.local_commit is None or facts.commit_reasons is None:
        yield _check("bot_signature", ["local_commit_missing"])
    else:
        yield _check("bot_signature", list(facts.commit_reasons))


# --- the evidence --------------------------------------------------------------------------


def _checks(context: AuthorizationContext) -> tuple[list[AuthorizationCheck], _Found]:
    plan = _plan_of(context)
    policy, pending = _policy(context, plan)
    publisher, fingerprint = _publisher(context)
    limits_check, limits = _limits(context, plan)
    control_check, control = _control(context, plan)
    budget_check, ledger = _budget(context, limits)
    remote_check, head = _remote(context)
    latch = _check("stop_latch", ["stop_latch_set"] if context.facts.latched else [])
    found = [policy, pending, publisher, limits_check, control_check, latch, budget_check]
    return [*_bindings(context, plan), *found, remote_check, *_execution(context)], _Found(
        plan, limits, control, ledger, head, fingerprint
    )


def _bindings(context: AuthorizationContext, plan: ReleasePlan | None) -> list[AuthorizationCheck]:
    return [
        _proposal(context),
        _plan(context, plan),
        _quarantine(context, plan),
        _trust_root(context),
    ]


def _execution(context: AuthorizationContext) -> list[AuthorizationCheck]:
    return [
        _path_identity(context),
        *_bot_signature(context),
        *(_unavailable(name, reason) for name, reason in _PENDING),
    ]


def _bound(context: AuthorizationContext, found: _Found) -> dict[str, object]:
    """What the evidence binds besides the checks: the attempt, proposal, path and evidence."""
    transaction = context.inputs.transaction
    content = transaction.content
    return {
        "mode": context.mode,
        "attempt_id": context.attempt_id,
        "transaction_id": transaction.transaction_id,
        "plan_id": content.plan_id,
        "batch_id": content.batch_id,
        "release_manifest_path": content.release_manifest_path,
        "release_manifest_sha256": content.release_manifest_sha256,
        "files": content.files,
        "proposal_sha256": digest_of(*content.files),
        "path": content.publication.path,
        "base_commit": content.publication.base_commit,
        "remote_head": found.remote_head,
        "local_commit": context.facts.local_commit,
        "checked_at": context.now,
    }


def _verified(context: AuthorizationContext, found: _Found) -> dict[str, object]:
    """The verified state behind the checks: policy, root, publisher, control, budget."""
    return {
        "policy": found.plan.basis.policy if found.plan else None,
        "trust_root_sha256": context.root.sha256,
        "publisher_principal": context.inputs.settings.bot_principal,
        "publisher_fingerprint": found.fingerprint,
        "control_state": found.control.state,
        "control_head_sha256": found.control.head_sha256,
        "quarantine_sha256": context.inputs.quarantine.quarantine_sha256,
        "limits_sha256": found.limits.limits_sha256,
        "budget_state": found.ledger.release_state,
        "budget_reserved": found.ledger.release_reserved,
    }


def authorize(context: AuthorizationContext) -> AuthorizationEvidence:
    """Every check for one exact attempt, made now; the decision follows from them alone."""
    checks, found = _checks(context)
    ordered = tuple(sorted(checks, key=lambda item: item.name))
    decision, denied_by, waived = decide(ordered, context.mode)
    evidence = AuthorizationEvidence.model_validate(
        {
            **_bound(context, found),
            **_verified(context, found),
            "checks": ordered,
            "decision": decision,
            "denied_by": denied_by,
            "waived": waived,
        }
    )
    return issue_provenance(evidence, "publication_authorization")
