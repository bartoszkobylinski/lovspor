"""AR2: a deterministic, bounded release plan from verified evidence (#738; §5.3, §9.2, §9.3).

:func:`plan_release` takes a verified inventory, verified signed limits, the
caller's document offers and the private artifact store, and the trust root.
It takes no count, status, eligibility or selection from the caller:

1. the inventory and the limits must each carry their verifier's seal, bind
   the same policy and come from ``root``; otherwise there is no plan;
2. FC8b (:func:`~lovspor.forarbeider.version_release.build_release`) rebuilds
   every offered document's selection from its verified lineage: the newest
   verified version only, a blocked head never replaced by an older version,
   FC7 and FC7b re-verified. A document offered twice refuses the plan;
3. :func:`build_plan` gives every document of the inventory, and every one
   offered outside it, a disposition and every reason; counts each document's
   files and bytes from the artifact bytes themselves, refusing any artifact
   whose bytes do not hash to its key; and partitions the planned documents,
   in document-id order, into the fewest consecutive batches that keep the
   lower of §9.3's hard caps and the signed batch limits.

An AR3 quarantine assessment (:mod:`lovspor.forarbeider.release_quarantine_run`)
may come with the request. It is honoured only when it carries the quarantine
verifier's seal, comes from ``root``, binds the same inventory and limits, and
was made for exactly the plan these inputs give without it; otherwise there is
no plan. A document it restricts is ``quarantined``: visible, with its version,
binding, artifacts and every reason, and never in a batch. The plan names the
quarantine set's digest, so a change of membership gives new plan and batch ids.

The result is sealed as ``batch_plan``. It is not an authorization: every
batch is still judged by the AR1 gates, which fail on the release-wide
blockers the plan names. :func:`verify_plan` tells whether a stored plan is
still the one the current evidence gives; a changed version, policy, limit or
inventory makes it ``stale``.

Daily budgets are not planned here: they depend on what was already spent,
which only the budget ledger knows (AR1b, AR5), so the constraints gate checks
each batch against them when it is reserved.

Nothing here writes, signs or publishes.
"""

from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import NamedTuple

from pydantic import ValidationError

from lovspor.forarbeider.corpus_models import _loaded
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_trust import TrustRoot
from lovspor.forarbeider.release_contract_models import (
    DocumentInventory,
    InventoryEvidence,
    LimitsEvidence,
    PlanEvidence,
    verified,
)
from lovspor.forarbeider.release_limits_models import (
    MAX_BATCH_BYTES,
    MAX_BATCH_DOCUMENTS,
    MAX_BATCH_FILES,
    ReleaseConstraints,
)
from lovspor.forarbeider.release_plan_models import (
    RELEASE_WIDE_BLOCKERS,
    BatchCaps,
    Disposition,
    PlanBasis,
    PlannedBatch,
    PlannedDocument,
    PlanVerification,
    ReleasePlan,
    coverage_of,
    plan_digest,
)
from lovspor.forarbeider.release_provenance import issue_provenance
from lovspor.forarbeider.release_quarantine_models import (
    NO_QUARANTINE,
    QuarantineEvidence,
    QuarantineSet,
)
from lovspor.forarbeider.version_models import Frozen, NonBlank, canonical_text
from lovspor.forarbeider.version_release import DocumentOffer, build_release
from lovspor.forarbeider.version_release_models import (
    DocumentBinding,
    DocumentSelection,
    PolicyReference,
    ReleaseManifest,
    digest_of,
)

#: The binding digests that name bytes in the artifact store. The lineage entry
#: itself travels with the offer, not the store, and is not a release file.
_BOUND = (
    "manifest_sha256",
    "assessment_sha256",
    "policy_decision_sha256",
    "authorization_sha256",
)


class PlanRequest(NamedTuple):
    """What a plan is made from; none of it is trusted until verified."""

    inventory: InventoryEvidence
    limits: LimitsEvidence
    offers: tuple[DocumentOffer, ...]
    artifacts: Mapping[str, bytes]
    quarantine: QuarantineEvidence = NO_QUARANTINE


class PlanContext(NamedTuple):
    """The verified inputs of :func:`build_plan`, by value."""

    inventory: DocumentInventory
    inventory_version: int
    inventory_sha256: str
    policy: PolicyReference
    limits_sha256: str
    constraints: ReleaseConstraints
    selection_release_id: str
    selection_bindings_sha256: str


class Row(NamedTuple):
    """One document's FC8b selection and the artifact digests it is bound to."""

    selection: DocumentSelection
    binding: DocumentBinding


class Prepared(NamedTuple):
    """The verified context and FC8b rows a plan is built from."""

    context: PlanContext
    rows: tuple[Row, ...]


class _Draft(NamedTuple):
    document_id: str
    disposition: Disposition
    version_id: str | None
    selection_sha256: str | None
    binding_sha256: str | None
    artifact_sha256s: tuple[str, ...]
    bytes: int
    reasons: tuple[str, ...]


class _Inputs(NamedTuple):
    context: PlanContext
    basis: PlanBasis
    artifacts: Mapping[str, bytes]


class _BatchKey(Frozen):
    """What a batch id is the digest of: the plan basis and its documents' exact state."""

    basis: PlanBasis
    members: tuple[NonBlank, ...]


# --- from verified evidence ----------------------------------------------------------------


def plan_release(request: PlanRequest, root: TrustRoot) -> PlanEvidence:
    """The sealed plan for ``request`` under ``root``; ``invalid`` without verified inputs.

    With an AR3 quarantine assessment, the plan honours it only when it carries
    the quarantine verifier's seal, comes from ``root``, binds the same
    inventory and limits and was made for exactly the plan these inputs give
    without it; otherwise there is no plan.
    """
    prepared = prepare_plan(request, root)
    if not isinstance(prepared, Prepared):
        return _invalid(list(prepared))
    quarantine = _quarantine_of(request, prepared, root)
    if not isinstance(quarantine, QuarantineSet) and quarantine is not None:
        return _invalid(list(quarantine))
    plan = build_plan(prepared.context, prepared.rows, request.artifacts, quarantine)
    evidence = PlanEvidence(
        status="verified",
        source="engine_release_planner",
        reasons=(),
        plan=plan,
        plan_sha256=digest_of(plan),
        trust_root_sha256=root.sha256,
    )
    return issue_provenance(evidence, "batch_plan")


def prepare_plan(request: PlanRequest, root: TrustRoot) -> Prepared | tuple[str, ...]:
    """The verified context and FC8b rows of ``request``, or why there are none."""
    refused = _input_reasons(request, root)
    if refused:
        return tuple(refused)
    try:
        manifest = build_release(request.offers, request.artifacts, root)
    except ForarbeiderRefusedError:
        return ("plan_offers_refused",)
    context = _context(request, manifest)
    if context is None:
        return ("plan_inputs_incomplete",)
    return Prepared(context, tuple(_rows(manifest)))


def _quarantine_of(
    request: PlanRequest, prepared: Prepared, root: TrustRoot
) -> QuarantineSet | tuple[str, ...] | None:
    found = request.quarantine
    if found.status == "missing":
        return None
    if not verified("quarantine_assessment", found) or found.quarantine is None:
        return ("plan_quarantine_unverified",)
    context = prepared.context
    bound = (found.trust_root_sha256, found.inventory_sha256, found.limits_sha256)
    if bound != (root.sha256, context.inventory_sha256, context.limits_sha256):
        return ("plan_quarantine_mismatch",)
    base = build_plan(context, prepared.rows, request.artifacts)
    if base.plan_id != found.base_plan_id:
        return ("plan_quarantine_stale",)
    return found.quarantine


def _input_reasons(request: PlanRequest, root: TrustRoot) -> list[str]:
    inventory, limits = request.inventory, request.limits
    found = []
    if not verified("document_inventory", inventory) or inventory.inventory is None:
        found.append("plan_inventory_unverified")
    if not verified("publication_limits", limits) or limits.constraints is None:
        found.append("plan_limits_unverified")
    if found:
        return found
    if inventory.policy is None or inventory.policy != limits.policy:
        found.append("plan_policy_mismatch")
    if {inventory.trust_root_sha256, limits.trust_root_sha256} != {root.sha256}:
        found.append("plan_trust_root_conflict")
    return found


def _invalid(reasons: list[str]) -> PlanEvidence:
    return PlanEvidence(status="invalid", source=None, reasons=tuple(sorted(set(reasons))))


def _context(request: PlanRequest, manifest: ReleaseManifest) -> PlanContext | None:
    inventory, limits = request.inventory, request.limits
    record, version, digest = (
        inventory.inventory,
        inventory.inventory_version,
        inventory.inventory_sha256,
    )
    if record is None or version is None or digest is None or inventory.policy is None:
        return None
    if limits.constraints is None or limits.limits_sha256 is None:
        return None
    return PlanContext(
        inventory=record,
        inventory_version=version,
        inventory_sha256=digest,
        policy=inventory.policy,
        limits_sha256=limits.limits_sha256,
        constraints=limits.constraints,
        selection_release_id=manifest.release_id,
        selection_bindings_sha256=manifest.bindings_sha256,
    )


def _rows(manifest: ReleaseManifest) -> list[Row]:
    pairs = zip(manifest.content.documents, manifest.bindings, strict=True)
    return [Row(selection, binding) for selection, binding in pairs]


# --- the pure planner ------------------------------------------------------------------------


def build_plan(
    context: PlanContext,
    rows: Sequence[Row],
    artifacts: Mapping[str, bytes],
    quarantine: QuarantineSet | None = None,
) -> ReleasePlan:
    """Every document's disposition and the bounded batches; the same inputs, the same bytes.

    A document ``quarantine`` restricts keeps its version, binding, artifacts and
    every reason, and is ``quarantined``: never in a batch. The others keep
    their order and are partitioned again within the same caps.
    """
    basis = _basis(context, quarantine)
    by_id = {item.selection.document_id: item for item in rows}
    expected = frozenset(context.inventory.document_ids)
    inputs = _Inputs(context, basis, artifacts)
    drafts = [
        _restricted(_draft(document, by_id.get(document), expected, inputs), quarantine)
        for document in sorted(expected | by_id.keys())
    ]
    batches, placed = _partition(basis, [d for d in drafts if d.disposition == "planned"])
    documents = tuple(_document(draft, placed.get(draft.document_id)) for draft in drafts)
    return ReleasePlan(
        plan_id=plan_digest(basis, documents, batches),
        basis=basis,
        documents=documents,
        batches=batches,
        coverage=coverage_of(documents),
    )


def _basis(context: PlanContext, quarantine: QuarantineSet | None) -> PlanBasis:
    limits = context.constraints
    return PlanBasis(
        quarantine_sha256=None if quarantine is None else digest_of(quarantine),
        inventory_id=context.inventory.inventory_id,
        inventory_version=context.inventory_version,
        inventory_sha256=context.inventory_sha256,
        policy=context.policy,
        limits_sha256=context.limits_sha256,
        caps=BatchCaps(
            documents=min(MAX_BATCH_DOCUMENTS, limits.max_documents_per_batch),
            files=min(MAX_BATCH_FILES, limits.max_files_per_batch),
            bytes=min(MAX_BATCH_BYTES, limits.max_bytes_per_batch),
        ),
        selection_release_id=context.selection_release_id,
        selection_bindings_sha256=context.selection_bindings_sha256,
    )


def _draft(
    document: str,
    found: Row | None,
    expected: frozenset[str],
    inputs: _Inputs,
) -> _Draft:
    if found is None:
        return _unsized(document, "missing", None, ("inventory_document_without_evidence",))
    if document not in expected:
        return _unsized(document, "outside_inventory", found, ("document_outside_inventory",))
    selection = found.selection
    if selection.status == "excluded":
        return _unsized(document, "excluded", found, _own_blockers(selection))
    keys = _artifact_keys(found)
    damaged = _damaged(keys, inputs.artifacts)
    if damaged:
        return _unsized(document, "excluded", found, damaged)
    size = sum(len(inputs.artifacts[key]) for key in keys)
    reasons = _blocking(inputs, found, len(keys), size)
    disposition: Disposition = "blocked" if reasons else "planned"
    return _unsized(document, disposition, found, reasons)._replace(
        artifact_sha256s=keys, bytes=size
    )


def _restricted(draft: _Draft, quarantine: QuarantineSet | None) -> _Draft:
    restriction = quarantine.reasons_of(draft.document_id) if quarantine else ()
    if not restriction:
        return draft
    kept = draft.disposition in ("missing", "outside_inventory")
    disposition: Disposition = draft.disposition if kept else "quarantined"
    reasons = tuple(sorted(set(draft.reasons) | set(restriction)))
    return draft._replace(disposition=disposition, reasons=reasons)


def _unsized(
    document: str, disposition: Disposition, found: Row | None, reasons: tuple[str, ...]
) -> _Draft:
    if found is None:
        return _Draft(document, disposition, None, None, None, (), 0, reasons)
    selection, binding = digest_of(found.selection), digest_of(found.binding)
    version = found.selection.version_id
    ordered = tuple(sorted(set(reasons)))
    return _Draft(document, disposition, version, selection, binding, (), 0, ordered)


def _own_blockers(selection: DocumentSelection) -> tuple[str, ...]:
    """The selection's blockers that depend on the document, as ``category:code``."""
    return tuple(
        f"{item.category}:{item.code}"
        for item in selection.blockers
        if (item.category, item.code) not in RELEASE_WIDE_BLOCKERS
    )


def _artifact_keys(found: Row) -> tuple[str, ...]:
    bound = {getattr(found.binding, name) for name in _BOUND} - {None}
    return tuple(sorted(set(found.selection.source_sha256s) | bound))


def _damaged(keys: tuple[str, ...], artifacts: Mapping[str, bytes]) -> tuple[str, ...]:
    found = set()
    for key in keys:
        data = artifacts.get(key)
        if data is None:
            found.add("artifact_missing")
        elif sha256(data).hexdigest() != key:
            found.add("artifact_altered")
    return tuple(sorted(found))


def _blocking(inputs: _Inputs, found: Row, files: int, size: int) -> tuple[str, ...]:
    reasons = list(_own_blockers(found.selection))
    if found.selection.policy != inputs.context.policy:
        reasons.append("policy_mismatch")
    caps = inputs.basis.caps
    if files > caps.files or size > caps.bytes:
        reasons.append("document_exceeds_batch_cap")
    return tuple(sorted(set(reasons)))


def _partition(
    basis: PlanBasis, planned: list[_Draft]
) -> tuple[tuple[PlannedBatch, ...], dict[str, str]]:
    """The fewest consecutive batches, in id order, each within the caps."""
    groups: list[list[_Draft]] = []
    for draft in planned:
        if groups and _fits(basis.caps, [*groups[-1], draft]):
            groups[-1].append(draft)
        else:
            groups.append([draft])
    batches = tuple(_batch(basis, index, group) for index, group in enumerate(groups, 1))
    placed = {d: b.batch_id for b in batches for d in b.document_ids}
    return batches, placed


def _fits(caps: BatchCaps, group: list[_Draft]) -> bool:
    files = sum(len(item.artifact_sha256s) for item in group)
    size = sum(item.bytes for item in group)
    return len(group) <= caps.documents and files <= caps.files and size <= caps.bytes


def _batch(basis: PlanBasis, sequence: int, group: list[_Draft]) -> PlannedBatch:
    members = tuple(
        f"{d.document_id}:{d.version_id}:{d.selection_sha256}:{d.binding_sha256}:"
        + ",".join(d.artifact_sha256s)
        for d in group
    )
    key = digest_of(_BatchKey(basis=basis, members=members))
    return PlannedBatch(
        batch_id=f"batch-{key[:56]}",
        sequence=sequence,
        document_ids=tuple(d.document_id for d in group),
        documents=len(group),
        files=sum(len(d.artifact_sha256s) for d in group),
        bytes=sum(d.bytes for d in group),
    )


def _document(draft: _Draft, batch_id: str | None) -> PlannedDocument:
    return PlannedDocument(
        document_id=draft.document_id,
        disposition=draft.disposition,
        version_id=draft.version_id,
        selection_sha256=draft.selection_sha256,
        binding_sha256=draft.binding_sha256,
        artifact_sha256s=draft.artifact_sha256s,
        files=len(draft.artifact_sha256s),
        bytes=draft.bytes,
        reasons=draft.reasons,
        batch_id=batch_id,
    )


# --- stored plans ----------------------------------------------------------------------------


def plan_text(plan: ReleasePlan) -> str:
    return canonical_text(plan)


def parse_plan(raw: bytes) -> ReleasePlan:
    """The plan in ``raw``, which must be its canonical bytes.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid`` for anything else, including a
            plan with a forged id, a document in two batches or an oversized batch.
    """
    try:
        plan = ReleasePlan.model_validate(_loaded(raw))
    except (ValidationError, ForarbeiderRefusedError) as error:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release plan") from error
    if plan_text(plan).encode() != raw:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release plan")
    return plan


def verify_plan(raw: bytes, request: PlanRequest, root: TrustRoot) -> PlanVerification:
    """Whether the stored plan in ``raw`` is exactly the one the current evidence gives."""
    try:
        offered: ReleasePlan | None = parse_plan(raw)
    except ForarbeiderRefusedError:
        offered = None
    rebuilt = plan_release(request, root).plan
    if offered is None or rebuilt is None:
        found = [] if offered else ["plan_malformed"]
        found += [] if rebuilt else ["plan_unverifiable"]
        return PlanVerification(status="invalid", findings=tuple(found))
    if offered.plan_id != rebuilt.plan_id:
        return PlanVerification(status="stale", findings=("plan_stale",))
    return PlanVerification(status="current", findings=())
