"""The read-only apply preflight: a stored batch plan, a release clone, now (#738, B1a).

``batch preflight`` answers one question before a batch release may write:
does this stored plan still hold, exactly, against this checkout, at this
moment? It writes nothing at all (the workspace must already exist), runs
only read-only Git through the checkout guard, makes no request, asks no
confirmation and applies nothing.

- The plan is read by its digest and must include at least one chapter.
- The checkout must pass :func:`~lovspor.forarbeider.release_checkout.verify_checkout`
  on the plan's target branch.
- The A2b evaluation runs again **now**, from the private request and the
  source proof the plan was made from, under the plan's policy: the policy
  state, the proof's freshness, every staged byte, review chain, report,
  authorization and checkpoint. Stamped with the plan's own moment, that
  replay must be the stored plan byte for byte. A stored eligible plan never
  approves by itself, and an excluded or withheld chapter stays out.

:func:`apply_preflight` is the one function a later batch apply calls again,
under its lock, immediately before it writes: the binding digest it returns
is what the owner's confirmation names, and it leaves out the moment, so the
same world gives the same digest. Printed: codes, ids, digests and counts;
never chapter text, a handle or a private path, the checkout's included.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict

from lovspor.forarbeider.batch_clearance import plan_of
from lovspor.forarbeider.batch_commands import BatchPlanRequest, batch_evidence, read_request
from lovspor.forarbeider.batch_models import BatchCounts, BatchPlan
from lovspor.forarbeider.corpus_models import MANIFEST_PATH
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_checkout import CheckoutState, verify_checkout
from lovspor.forarbeider.release_commands import (
    ReleaseScope,
    digest_option,
    forbidden_trees,
    stored_record,
)
from lovspor.forarbeider.release_models import digest, record_text
from lovspor.forarbeider.release_store import refused_as
from lovspor.forarbeider.staging import checked_directory, private_directory_exists

REMOTE: Literal["github.com/bartoszkobylinski/lovverk"] = "github.com/bartoszkobylinski/lovverk"


class ApplyPreflightRequest(BaseModel):
    """The plan's digest, the release clone, and the request and proof the plan was made from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan: str
    checkout: Path
    request: Path
    source_proof: Path


class ApplyBinding(BaseModel):
    """What a batch confirmation names: the plan, the checkout state and the chapters it carries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    record_type: Literal["forarbeider_apply_preflight"]
    plan_sha256: str
    target_branch: str
    head_commit: str
    base_manifest_sha256: str | None
    remote: Literal["github.com/bartoszkobylinski/lovverk"]
    included: tuple[str, ...]
    counts: BatchCounts
    writes_corpus: Literal[False]
    applies: Literal[False]
    owner_confirmation: Literal["required_not_given"]


class ApplyVerdict(NamedTuple):
    """A preflight that passed: the plan replayed now, the checkout, the binding and its digest."""

    plan_sha256: str
    plan: BatchPlan
    state: CheckoutState
    binding: ApplyBinding
    binding_sha256: str
    evaluated_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def existing_workspace(scope: ReleaseScope) -> Path:
    """The private workspace, which must already be there: a preflight creates nothing.

    Raises:
        ForarbeiderRefusedError: ``release_record_missing: workspace``, or an unsafe one.
    """
    with refused_as("workspace"):
        path = checked_directory(scope.workspace, forbidden_trees())
        present = private_directory_exists(path)
    if not present:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_RECORD_MISSING, "workspace")
    return path


def stored_plan(workspace: Path, text: str) -> tuple[str, BatchPlan]:
    """The stored, intact plan of digest ``text``, with at least one chapter included.

    Raises:
        ForarbeiderRefusedError: a malformed digest, an unknown or edited plan,
            or ``batch_nothing_included``.
    """
    sha = digest_option(text, "plan")
    plan = stored_record(workspace, "batch_plans", sha, BatchPlan)
    if plan.counts.standing_policy + plan.counts.owner_review == 0:
        raise ForarbeiderRefusedError(RefusalReason.BATCH_NOTHING_INCLUDED, sha)
    return sha, plan


def _difference(replayed: BatchPlan, plan: BatchPlan) -> str:
    """Where a replay parts from the stored plan, in codes: what changed, never text."""
    if replayed.inputs_sha256 != plan.inputs_sha256:
        return "inputs"
    stored = {item.chapter_id: item for document in plan.documents for item in document.chapters}
    for chapter in (item for document in replayed.documents for item in document.chapters):
        if stored.get(chapter.chapter_id) != chapter:
            return f"{chapter.chapter_id}: {','.join(chapter.reasons) or 'included'}"
    return "plan"


def reproduce(
    scope: ReleaseScope, ask: ApplyPreflightRequest, stored: tuple[str, BatchPlan], now: datetime
) -> BatchPlan:
    """The plan evaluated again at ``now`` and stamped with its own moment: it must be the plan.

    Raises:
        ForarbeiderRefusedError: ``target_branch_mismatch: request``, ``plan_not_reproduced``,
            or any refusal the planner raises for an input.
    """
    sha, plan = stored
    batch = read_request(ask.request)
    if batch.target_branch != plan.target_branch:
        raise ForarbeiderRefusedError(RefusalReason.TARGET_BRANCH_MISMATCH, "request")
    evaluation = BatchPlanRequest(
        request=ask.request,
        policy=plan.policy_sha256,
        source_proof=ask.source_proof,
        at=now.isoformat(),
    )
    replayed = plan_of(batch_evidence(scope, evaluation, batch)._replace(at=plan.at))
    if replayed.source_proof_sha256 != plan.source_proof_sha256:
        raise ForarbeiderRefusedError(RefusalReason.PLAN_NOT_REPRODUCED, "source_proof")
    if digest(record_text(replayed).encode()) != sha:
        raise ForarbeiderRefusedError(
            RefusalReason.PLAN_NOT_REPRODUCED, _difference(replayed, plan)
        )
    return replayed


def binding_of(sha: str, plan: BatchPlan, state: CheckoutState) -> ApplyBinding:
    """The binding of an included-only chapter list to this plan and this checkout state."""
    included = tuple(
        f"{chapter.chapter_id}@{chapter.text_sha256}"
        for document in plan.documents
        for chapter in document.chapters
        if chapter.included
    )
    return ApplyBinding(
        schema_version=1,
        record_type="forarbeider_apply_preflight",
        plan_sha256=sha,
        target_branch=plan.target_branch,
        head_commit=state.head_commit,
        base_manifest_sha256=state.base_manifest_sha256,
        remote=REMOTE,
        included=included,
        counts=plan.counts,
        writes_corpus=False,
        applies=False,
        owner_confirmation="required_not_given",
    )


def apply_preflight(scope: ReleaseScope, ask: ApplyPreflightRequest, now: datetime) -> ApplyVerdict:
    """The whole preflight, read-only: the one function a batch apply must call before it writes.

    Raises:
        ForarbeiderRefusedError: the first condition that does not hold; nothing was written.
    """
    workspace = existing_workspace(scope)
    sha, plan = stored_plan(workspace, ask.plan)
    state = verify_checkout(ask.checkout, plan.target_branch)
    replayed = reproduce(scope, ask, (sha, plan), now)
    binding = binding_of(sha, replayed, state)
    bound = digest(record_text(binding).encode())
    return ApplyVerdict(sha, replayed, state, binding, bound, now)


def apply_preflight_impl(scope: ReleaseScope, ask: ApplyPreflightRequest) -> str:
    """``batch preflight``: the preflight at the machine clock, as printed lines."""
    return preflight_lines(apply_preflight(scope, ask, _now()))


def _document_lines(plan: BatchPlan) -> list[str]:
    lines = []
    for document in plan.documents:
        numbers = [str(item.chapter_number) for item in document.chapters if item.included]
        lines.append(
            f"document {document.document.id} {document.destination} "
            f"coverage {document.coverage} chapters {','.join(numbers) or '-'}"
        )
    return lines


def preflight_lines(verdict: ApplyVerdict) -> str:
    """The verdict in codes, ids, digests and counts only."""
    state, counts = verdict.state, verdict.plan.counts
    included = counts.standing_policy + counts.owner_review
    return "\n".join(
        [
            f"APPLY PREFLIGHT PASS plan {verdict.plan_sha256} binding {verdict.binding_sha256}",
            f"target {state.branch} head {state.head_commit} "
            f"base {MANIFEST_PATH} {state.base_manifest_sha256 or 'absent'}",
            f"included {included} (standing_policy {counts.standing_policy}, "
            f"owner_review {counts.owner_review}) excluded {counts.excluded} "
            f"documents {counts.documents}",
            *_document_lines(verdict.plan),
            "writes_corpus=false applies=false owner_confirmation=required_not_given "
            f"evaluated_at {verdict.evaluated_at.isoformat()}",
        ]
    )
