"""The bodies of ``lovspor ops forarbeider-release batch`` (#738, contract Amendment 1).

``batch plan`` reads one private request of at most 25 propositions, the
standing policy, one ``forarbeider-fetch`` run as the current source proof,
and for each document with flagged chapters the owner's stored eligible
preflight report. It evaluates every chapter at one moment and writes one
private plan, ``<workspace>/batch_plans/<sha256>.json``, named by its digest
and never replaced. ``batch show`` prints a stored plan.

Read-only to everything else: no corpus or checkout argument exists, nothing
here starts a process or makes a request, and the plan authorizes, confirms
and applies nothing. Printed: codes, ids, digests, counts, the target branch
and public source URLs. Never chapter text, evidence, handles or a private
path, the request's own paths included.
"""

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lovspor.forarbeider.batch_clearance import Batch, DocumentEvidence, PolicyVerdict, plan_of
from lovspor.forarbeider.batch_models import (
    MAX_DOCUMENTS,
    BatchPlan,
    BatchRequest,
    DocumentRequest,
    PlannedChapter,
    valid_branch,
)
from lovspor.forarbeider.commands import aware_moment
from lovspor.forarbeider.corpus_models import PolicyClearance, ReviewClearance
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_commands import policy_clearance
from lovspor.forarbeider.policy_source import read_source_run
from lovspor.forarbeider.release_commands import (
    ChapterScope,
    ReleaseScope,
    Run,
    digest_option,
    forbidden_trees,
    open_release_workspace,
    preflight_inputs,
    stored_record,
)
from lovspor.forarbeider.release_models import PreflightReport, destination_of, document_of
from lovspor.forarbeider.release_preflight import PreflightInputs, Stored, evaluate
from lovspor.forarbeider.release_store import read_model, refused_as, write_record

_SPOT_CHECK = "(optional spot check, not a gate)"


class BatchPlanRequest(BaseModel):
    """What one ``batch plan`` is asked: the request file, the policy, the proof, the moment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: Path
    policy: str
    source_proof: Path
    at: str


def _request(path: Path) -> BatchRequest:
    """The private request, refused before any staged file is read when it cannot be a batch.

    Raises:
        ForarbeiderRefusedError: unsafe, unreadable or not the schema; ``batch_empty``,
            ``batch_too_large`` or ``target_branch_invalid``.
    """
    with refused_as("request"):
        batch = read_model(path, forbidden_trees(), BatchRequest)
    if not batch.documents:
        raise ForarbeiderRefusedError(RefusalReason.BATCH_EMPTY, "request")
    if len(batch.documents) > MAX_DOCUMENTS:
        raise ForarbeiderRefusedError(RefusalReason.BATCH_TOO_LARGE, "request")
    if not valid_branch(batch.target_branch):
        raise ForarbeiderRefusedError(RefusalReason.TARGET_BRANCH_INVALID, "target_branch")
    return batch


def _policy_verdict(workspace: Path, ask: BatchPlanRequest, at: datetime) -> PolicyVerdict:
    """The policy at ``at``, compared with the one fetch run named as current source proof.

    Raises:
        ForarbeiderRefusedError: an unknown or edited policy, or an unreadable,
            malformed or edited source proof.
    """
    sha = digest_option(ask.policy, "policy")
    run = read_source_run(ask.source_proof)
    policy, faults, sourced = policy_clearance(workspace, sha, run.source, at)
    return PolicyVerdict(sha, policy, (*faults, *sourced), run.sha256, run.fetched)


def _report(workspace: Path, item: DocumentRequest) -> Stored[PreflightReport] | None:
    if item.review is None:
        return None
    sha = item.review.report
    return Stored(stored_record(workspace, "reports", sha, PreflightReport), sha)


def _document(
    scope: ReleaseScope, workspace: Path, item: DocumentRequest, at: str
) -> DocumentEvidence:
    """One proposition read, and its preflight evaluated at ``at`` under the report's authorization.

    Raises:
        ForarbeiderRefusedError: an unsafe or unreadable file, a malformed checkpoint,
            or a named report that is not stored.
    """
    report = _report(workspace, item)
    staged = ChapterScope(
        release=scope, metadata=item.metadata, observations=item.observations, pages=item.pages
    )
    run = Run(
        authorization=None if report is None else report.record.authorization_sha256,
        checkpoints=() if item.review is None else item.review.checkpoints,
        at=at,
    )
    inputs = preflight_inputs(staged, run)
    return DocumentEvidence(inputs, evaluate(inputs), report)


def _require_one_document(inputs: PreflightInputs) -> None:
    """Every chapter is this proposition's, once each.

    Raises:
        ForarbeiderRefusedError: ``chapter_foreign`` or ``chapter_duplicate``.
    """
    document = document_of(inputs.metadata)
    for item in inputs.observed:
        if document_of(item.result) != document:
            raise ForarbeiderRefusedError(RefusalReason.CHAPTER_FOREIGN, item.result.id)
    numbers = [item.result.chapter_number for item in inputs.observed]
    if len(set(numbers)) != len(numbers):
        raise ForarbeiderRefusedError(RefusalReason.CHAPTER_DUPLICATE, "observation")


def _require_one_batch(documents: Sequence[DocumentEvidence]) -> None:
    """Each proposition once: by id, by source document and by destination.

    Raises:
        ForarbeiderRefusedError: ``chapter_foreign``, ``chapter_duplicate`` or
            ``document_duplicate``.
    """
    seen: set[object] = set()
    for evidence in documents:
        _require_one_document(evidence.inputs)
        document = evidence.fresh.document
        keys = {document.id, document.source_document_id, destination_of(document)}
        if keys & seen:
            raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_DUPLICATE, document.id)
        seen |= keys


def batch_plan_impl(scope: ReleaseScope, ask: BatchPlanRequest) -> tuple[str, bool]:
    """Write the plan; return its lines and whether any chapter is included.

    Raises:
        ForarbeiderRefusedError: an input could not be read safely or is not one
            batch; nothing was written.
    """
    batch = _request(ask.request)
    with refused_as("at"):
        at = aware_moment(ask.at)
    workspace = open_release_workspace(scope)
    verdict = _policy_verdict(workspace, ask, at)
    documents = tuple(_document(scope, workspace, item, ask.at) for item in batch.documents)
    _require_one_batch(documents)
    plan = plan_of(Batch(batch.target_branch, at, verdict, documents))
    sha = write_record(workspace, "batch_plans", plan)
    counts = plan.counts
    return plan_lines(plan, sha), counts.standing_policy + counts.owner_review > 0


def batch_show_impl(scope: ReleaseScope, sha: str) -> str:
    """A stored plan as ``batch plan`` printed it.

    Raises:
        ForarbeiderRefusedError: an unknown or edited plan, or a malformed digest.
    """
    workspace = open_release_workspace(scope)
    sha = digest_option(sha, "digest")
    return plan_lines(stored_record(workspace, "batch_plans", sha, BatchPlan), sha)


def _chapter_line(chapter: PlannedChapter) -> str:
    head = f"chapter {chapter.chapter_number} {chapter.chapter_id} {chapter.classification}"
    clearance = chapter.clearance
    if isinstance(clearance, PolicyClearance):
        return f"{head} included standing_policy policy {clearance.policy_sha256[:12]}"
    if isinstance(clearance, ReviewClearance) and clearance.review is not None:
        pin = f"{clearance.review.subject_key[:12]}@{clearance.review.revision}"
        report = (clearance.report_sha256 or "")[:12]
        return f"{head} included owner_review report {report} pin {pin}"
    return f"{head} excluded {','.join(chapter.reasons)}"


def plan_lines(plan: BatchPlan, sha: str) -> str:
    """The plan in codes, ids, digests and public source URLs only."""
    counts = plan.counts
    included = counts.standing_policy + counts.owner_review
    lines = [
        f"BATCH PLAN {sha} documents {counts.documents} included {included} "
        f"(standing_policy {counts.standing_policy}, owner_review {counts.owner_review}) "
        f"excluded {counts.excluded}",
        f"target {plan.target_branch} publication_authorized=false applies=false "
        f"owner_confirmation=required_not_given",
        f"policy {plan.policy_sha256} source_proof {plan.source_proof_sha256} "
        f"inputs {plan.inputs_sha256}",
    ]
    for document in plan.documents:
        lines.append(
            f"document {document.document.id} {document.destination} coverage {document.coverage}"
        )
        lines += [_chapter_line(chapter) for chapter in document.chapters]
    lines += [
        f"sample {item.chapter_id} {item.source_page_url} {_SPOT_CHECK}" for item in plan.sample
    ]
    return "\n".join(lines)
