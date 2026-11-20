"""AR1: pure, deterministic safety gates over release evidence (#738; §9.2, §9.3, D10, D11).

:func:`evaluate_release` reads one :class:`ReleaseEvidence` and nothing else:
no clock, no file, no network, no key. It reports every gate in a fixed
order, each with every reason it fails, by stable code and document. Missing
evidence fails its gate; it never becomes approval.

The gates:

Evidence counts only when :func:`verified`: a trusted source label *and* the
seal of the engine verifier that produced it. A record a caller built with
``status="verified"`` fails its gate as ``<kind>_source_untrusted``, and a
hand-made registry, publisher, approval or reproduction as ``*_unverified``.

- ``inventory``: a verified expected inventory from a trusted source, and a
  verified plan (AR2) bound to it and to the signed limits, of which this
  manifest is exactly one batch,
  selection by selection; every other expected document accounted for by the
  plan, none without evidence. A document the plan did not plan blocks
  (``document_not_planned``); planned documents grouped otherwise than in a
  batch refuse (``batch_not_in_plan``). Without a verified plan the manifest must
  equal the inventory;
- ``lineage_anchor``: every document's FC8a head attested by a trusted anchor
  and equal to it; the lineage verified;
- ``manifest_reproduced``: the FC8b verifier reproduced exactly this manifest,
  and the intent names exactly it;
- ``source_evidence``: no document excluded, no integrity or source blocker;
- ``policy_rules``: no FC7 policy blocker;
- ``authorization``: no FC7b blocker; an engine-verified, engine-pinned trust
  root with an owner key; every root-bound piece of evidence from that root;
- ``legal_assessment``: D10, which nothing can accept today;
- ``automated_publication``: D11 and each policy's verified approvals and
  document limit;
- ``publisher``: the bot, listed with only its namespaces, approving nothing;
- ``privacy_rights``: no denied, unresolved or unassessed span, publication
  complete;
- ``freshness``: no version behind its anchor, none rolled back;
- ``stop_control``: a verified owner-signed control chain with no active stop
  for the policy, anchored outside the runner (AR5); a stop overrides every
  approval;
- ``constraints``: verified signed limits for the policy and a verified,
  anchored ledger (AR5); the release not already spent; batch caps and
  budgets including every open reservation, over the batch size the planner
  counted from the artifacts. The caller's stated size is only compared with
  it; without a verified plan it is ``batch_size_unverified``;
- ``execution_controls``: the controls AR4 to AR7 build, none of which exists.

``execution_controls``, ``legal_assessment``, ``automated_publication``,
``stop_control`` (unanchored) and ``constraints`` (unanchored ledger) fail for
every input today, so no verdict is ever eligible. A verdict is
``refused`` when the evidence contradicts itself or was forged, otherwise
``blocked``.
"""

from collections import Counter
from collections.abc import Callable, Iterable, Iterator

from pydantic import ValidationError

from lovspor.forarbeider.corpus_models import _loaded
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_trust import BOT_NAMESPACES
from lovspor.forarbeider.release_contract_models import (
    EXECUTION_PREREQUISITES,
    GATES,
    REFUSING_CODES,
    AnchorEvidence,
    BatchSize,
    BudgetUsage,
    EvidenceKind,
    GateId,
    GateReason,
    GateResult,
    InventoryEvidence,
    LedgerEvidence,
    ReleaseEvidence,
    ReleaseExecution,
    ReleaseGateVerdict,
    _Evidence,
    verified,
)
from lovspor.forarbeider.release_limits_models import (
    MAX_BATCH_BYTES,
    MAX_BATCH_DOCUMENTS,
    MAX_BATCH_FILES,
    ReleaseConstraints,
)
from lovspor.forarbeider.release_plan_models import PlannedBatch, ReleasePlan
from lovspor.forarbeider.release_provenance import authentic
from lovspor.forarbeider.version_models import canonical_text
from lovspor.forarbeider.version_release_models import (
    DocumentBinding,
    DocumentSelection,
    digest_of,
)

Reasons = Iterator[GateReason]

_ROLLBACK = frozenset({"lineage:version_cycle", "lineage:history_rewritten"})
_PRIVACY = {
    "fc7_deny": "rights_or_privacy_denied",
    "fc7_unresolved": "rights_or_privacy_unresolved",
    "policy_not_evaluated": "rights_or_privacy_not_assessed",
}


def evaluate_release(evidence: ReleaseEvidence) -> ReleaseGateVerdict:
    """Every gate's result over ``evidence``; always blocked or refused, never eligible."""
    gates = tuple(_result(gate, _CHECKS[gate](evidence)) for gate in GATES)
    refusing = any(r.code in REFUSING_CODES for g in gates for r in g.reasons)
    return ReleaseGateVerdict(
        intent_sha256=digest_of(evidence.intent),
        evidence_sha256=digest_of(evidence),
        release_id=evidence.manifest.release_id,
        status="refused" if refusing else "blocked",
        gates=gates,
        failed_gates=tuple(g.gate for g in gates if g.status == "failed"),
    )


def verdict_text(verdict: ReleaseGateVerdict) -> str:
    return canonical_text(verdict)


def parse_verdict(raw: bytes) -> ReleaseGateVerdict:
    """The verdict in ``raw``, which must be its canonical bytes.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid`` for anything else, including a
            verdict claiming eligibility or hiding a failed gate.
    """
    try:
        verdict = ReleaseGateVerdict.model_validate(_loaded(raw))
    except (ValidationError, ForarbeiderRefusedError) as error:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "gate verdict") from error
    if verdict_text(verdict).encode() != raw:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "gate verdict")
    return verdict


def not_executed(verdict: ReleaseGateVerdict) -> ReleaseExecution:
    """The execution record of ``verdict``: nothing was executed, because nothing can be."""
    return ReleaseExecution(verdict_sha256=digest_of(verdict))


def _result(gate: GateId, reasons: Iterable[GateReason]) -> GateResult:
    ordered = tuple(sorted(set(reasons), key=lambda r: (r.document_id or "", r.code)))
    return GateResult(gate=gate, status="failed" if ordered else "passed", reasons=ordered)


def _evidence(prefix: str, kind: EvidenceKind, found: _Evidence) -> Iterator[str]:
    if found.status != "verified":
        yield f"{prefix}_{found.status}"
    elif not verified(kind, found):
        yield f"{prefix}_source_untrusted"


def _ids(evidence: ReleaseEvidence) -> tuple[str, ...]:
    return tuple(item.document_id for item in evidence.manifest.content.documents)


# --- inventory, anchors, manifest ----------------------------------------------------


def _inventory(evidence: ReleaseEvidence) -> Reasons:
    found = evidence.inventory
    for code in _evidence("inventory", "document_inventory", found):
        yield GateReason(code=code)
    yield from (GateReason(code=c) for c in _evidence("plan", "batch_plan", evidence.plan))
    if found.inventory is None:
        return
    expected, present = set(found.inventory.document_ids), set(_ids(evidence))
    for document in sorted(present - expected):
        yield GateReason(code="document_outside_inventory", document_id=document)
    plan = _plan(evidence)
    if plan is None:
        for document in sorted(expected - present):
            yield GateReason(code="inventory_document_missing", document_id=document)
        return
    yield from _in_plan(evidence, found, plan)


def _plan(evidence: ReleaseEvidence) -> ReleasePlan | None:
    """The plan, only when the engine's planner sealed it."""
    return evidence.plan.plan if verified("batch_plan", evidence.plan) else None


def _batch_of(evidence: ReleaseEvidence) -> PlannedBatch | None:
    """The batch of the verified plan that holds exactly this manifest's documents."""
    plan, ids = _plan(evidence), _ids(evidence)
    if plan is None:
        return None
    return next((batch for batch in plan.batches if batch.document_ids == ids), None)


def _in_plan(evidence: ReleaseEvidence, found: InventoryEvidence, plan: ReleasePlan) -> Reasons:
    if plan.basis.inventory_sha256 != found.inventory_sha256:
        yield GateReason(code="plan_inventory_mismatch")
    if plan.basis.limits_sha256 != evidence.limits.limits_sha256:
        yield GateReason(code="plan_limits_mismatch")
    for item in plan.documents:
        if item.disposition == "missing":
            yield GateReason(code="inventory_document_missing", document_id=item.document_id)
    if _batch_of(evidence) is None:
        yield from _unplanned(evidence, plan)
        return
    planned = {item.document_id: item.selection_sha256 for item in plan.documents}
    for selection in evidence.manifest.content.documents:
        if planned.get(selection.document_id) != digest_of(selection):
            yield GateReason(code="batch_selection_mismatch", document_id=selection.document_id)


def _unplanned(evidence: ReleaseEvidence, plan: ReleasePlan) -> Reasons:
    """A document the plan did not plan blocks; planned ones regrouped refuse."""
    disposition = {item.document_id: item.disposition for item in plan.documents}
    ids = _ids(evidence)
    for document in ids:
        if disposition.get(document) != "planned":
            yield GateReason(code="document_not_planned", document_id=document)
    if all(disposition.get(document) == "planned" for document in ids):
        yield GateReason(code="batch_not_in_plan")


def _anchors(evidence: ReleaseEvidence) -> dict[str, AnchorEvidence]:
    return {item.document_id: item for item in evidence.anchors}


def _lineage_anchor(evidence: ReleaseEvidence) -> Reasons:
    counted = Counter(item.document_id for item in evidence.anchors)
    known = set(_ids(evidence))
    for document, count in sorted(counted.items()):
        if count > 1:
            yield GateReason(code="anchor_conflict", document_id=document)
        if document not in known:
            yield GateReason(code="anchor_for_unknown_document", document_id=document)
    anchors = _anchors(evidence)
    for binding in evidence.manifest.bindings:
        document = binding.document_id
        if binding.lineage_status != "verified":
            yield GateReason(code=f"lineage_{binding.lineage_status}", document_id=document)
        yield from _anchored(anchors.get(document), binding)


def _anchored(found: AnchorEvidence | None, binding: DocumentBinding) -> Reasons:
    document, head = binding.document_id, binding.lineage_head_sha256
    if found is None:
        yield GateReason(code="anchor_missing", document_id=document)
        return
    for code in _evidence("anchor", "lineage_head_anchor", found):
        yield GateReason(code=code, document_id=document)
    record = found.anchor
    if record is None or head is None or record.head_sha256 == head:
        return
    if binding.lineage_entries == record.lineage_entries:
        yield GateReason(code="anchor_head_mismatch", document_id=document)
    elif binding.lineage_entries > record.lineage_entries:
        yield GateReason(code="head_ahead_of_anchor", document_id=document)


def _manifest_reproduced(evidence: ReleaseEvidence) -> Reasons:
    manifest, intent = evidence.manifest, evidence.intent
    reproduction = evidence.reproduction.verification
    if not authentic(evidence.reproduction, "manifest_reproduction"):
        yield GateReason(code="reproduction_unverified")
    if reproduction.status != "reproduced":
        yield GateReason(code="reproduction_invalid")
    for finding in reproduction.findings:
        yield GateReason(code=f"reproduction:{finding.code}", document_id=finding.document_id)
    offered = (reproduction.offered_release_id, reproduction.rebuilt_release_id)
    if offered != (manifest.release_id, manifest.release_id):
        yield GateReason(code="reproduction_of_other_manifest")
    if intent.release_id != manifest.release_id:
        yield GateReason(code="intent_release_mismatch")
    if intent.bindings_sha256 != manifest.bindings_sha256:
        yield GateReason(code="intent_bindings_mismatch")
    if intent.document_ids != _ids(evidence):
        yield GateReason(code="intent_documents_mismatch")


# --- the FC8b selections -------------------------------------------------------------


def _carried(
    evidence: ReleaseEvidence, pick: Callable[[DocumentSelection], Iterable[str]]
) -> Reasons:
    for selection in evidence.manifest.content.documents:
        for code in pick(selection):
            yield GateReason(code=code, document_id=selection.document_id)


def _of(selection: DocumentSelection, *categories: str) -> list[str]:
    return [item.code for item in selection.blockers if item.category in categories]


def _source_evidence(evidence: ReleaseEvidence) -> Reasons:
    def pick(selection: DocumentSelection) -> list[str]:
        excluded = ["document_excluded"] if selection.status == "excluded" else []
        return excluded + _of(selection, "integrity", "source")

    return _carried(evidence, pick)


def _policy_rules(evidence: ReleaseEvidence) -> Reasons:
    return _carried(evidence, lambda selection: _of(selection, "policy"))


def _authorization(evidence: ReleaseEvidence) -> Reasons:
    registry = evidence.registry
    if not authentic(registry, "signer_registry"):
        yield GateReason(code="trust_root_unverified")
    if registry.origin != "engine_pinned":
        yield GateReason(code="trust_root_not_engine_pinned")
    if registry.owner_keys == 0:
        yield GateReason(code="trust_root_has_no_owner_key")
    if _roots(evidence) - {registry.trust_root_sha256}:
        yield GateReason(code="trust_root_conflict")
    yield from _carried(
        evidence,
        lambda s: (
            _of(s, "authorization") + [c for c in _of(s, "integrity") if c.startswith("fc7b_")]
        ),
    )


def _roots(evidence: ReleaseEvidence) -> set[str]:
    """The trust roots every root-bound piece of evidence names."""
    named = (
        evidence.limits.trust_root_sha256,
        evidence.control.trust_root_sha256,
        evidence.inventory.trust_root_sha256,
        evidence.plan.trust_root_sha256,
    )
    return {root for root in named if root is not None}


def _privacy_rights(evidence: ReleaseEvidence) -> Reasons:
    def pick(selection: DocumentSelection) -> list[str]:
        mapped = [_PRIVACY[c] for c in _of(selection, "policy") if c in _PRIVACY]
        return mapped + _of(selection, "publication")

    return _carried(evidence, pick)


def _freshness(evidence: ReleaseEvidence) -> Reasons:
    anchors = _anchors(evidence)
    for selection, binding in zip(
        evidence.manifest.content.documents, evidence.manifest.bindings, strict=True
    ):
        document = selection.document_id
        if {item.code for item in selection.blockers} & _ROLLBACK:
            yield GateReason(code="version_rolled_back", document_id=document)
        found = anchors.get(document)
        record = None if found is None else found.anchor
        if record is None:
            yield GateReason(code="freshness_unverifiable", document_id=document)
        elif binding.lineage_entries < record.lineage_entries:
            yield GateReason(code="version_stale", document_id=document)


# --- owner decisions, publisher ------------------------------------------------------


def _legal_assessment(evidence: ReleaseEvidence) -> Reasons:
    legal = evidence.legal
    yield GateReason(code="d10_not_independently_accepted")
    for code in _evidence("legal_assessment", "legal_assessment", legal):
        yield GateReason(code=code)
    if any(item.legal_reference_stated is None for item in evidence.approvals):
        yield GateReason(code="legal_reference_missing")


def _policies(evidence: ReleaseEvidence) -> set[str]:
    documents = evidence.manifest.content.documents
    return {item.policy.policy_sha256 for item in documents if item.policy is not None}


def _automated_publication(evidence: ReleaseEvidence) -> Reasons:
    yield GateReason(code="d11_not_accepted")
    counted = Counter(item.policy.policy_sha256 for item in evidence.approvals)
    policies = _policies(evidence)
    for policy, count in sorted(counted.items()):
        if count > 1:
            yield GateReason(code="approval_conflict")
        if policy not in policies:
            yield GateReason(code="approval_for_unknown_policy")
    if policies - set(counted):
        yield GateReason(code="d11_approval_missing")
    yield from _approvals(evidence)


def _approvals(evidence: ReleaseEvidence) -> Reasons:
    documents = len(evidence.manifest.content.documents)
    for item in evidence.approvals:
        if not authentic(item, "policy_approval"):
            yield GateReason(code="approval_unverified")
        if item.automated_publication != "verified":
            yield GateReason(code="d11_approval_missing")
        if item.policy_approval != "verified":
            yield GateReason(code="policy_approval_missing")
        if item.max_documents_per_batch is None or documents > item.max_documents_per_batch:
            yield GateReason(code="batch_documents_exceeded")


def _publisher(evidence: ReleaseEvidence) -> Reasons:
    found = evidence.publisher
    checks = (
        (found.principal != evidence.intent.publisher_principal, "publisher_mismatch"),
        (found.role is None, "publisher_not_in_registry"),
        (found.role == "owner_policy", "publisher_holds_owner_key"),
        (not set(found.namespaces) <= BOT_NAMESPACES, "publisher_scope_exceeded"),
        (found.key_fingerprint is None, "publisher_key_missing"),
        (evidence.registry.origin != "engine_pinned", "publisher_registry_unpinned"),
        (not authentic(found, "publisher_identity"), "publisher_unverified"),
    )
    yield from (GateReason(code=code) for failed, code in checks if failed)
    for item in evidence.approvals:
        if (
            found.key_fingerprint is not None
            and found.key_fingerprint in item.approver_fingerprints
        ):
            yield GateReason(code="publisher_self_approval")
        if "bot_execution" in item.approver_roles:
            yield GateReason(code="approval_signed_by_bot")


# --- stop control, constraints, execution --------------------------------------------


def _stop_control(evidence: ReleaseEvidence) -> Reasons:
    control = evidence.control
    yield from (GateReason(code=c) for c in _evidence("control", "release_control", control))
    yield GateReason(code="control_history_unanchored")
    if control.state != "not_stopped":
        yield GateReason(code="release_stopped")
    policies = {d.policy.policy_id for d in evidence.manifest.content.documents if d.policy}
    if control.state == "not_stopped" and policies != {control.policy_id}:
        yield GateReason(code="control_scope_mismatch")


def _constraints(evidence: ReleaseEvidence) -> Reasons:
    limits, ledger = evidence.limits, evidence.ledger
    batch, found = _batch_size(evidence)
    found += _evidence("limits", "publication_limits", limits)
    found += _evidence("budget_ledger", "budget_ledger", ledger)
    found.append("budget_ledger_not_anchored")
    if limits.policy is not None and limits.policy.policy_sha256 not in _policies(evidence):
        found.append("limits_policy_mismatch")
    if evidence.intent.batch.documents != len(evidence.intent.document_ids):
        found.append("batch_size_contradicts_intent")
    found += _batch(batch, limits.constraints)
    found += _ledger(evidence, ledger, batch)
    yield from (GateReason(code=code) for code in found)


def _batch_size(evidence: ReleaseEvidence) -> tuple[BatchSize, list[str]]:
    """The planner's count for this batch; the caller's statement only without a plan."""
    stated, batch = evidence.intent.batch, _batch_of(evidence)
    if batch is None:
        return stated, ["batch_size_unverified"]
    counted = BatchSize(documents=batch.documents, files=batch.files, bytes=batch.bytes)
    return counted, [] if counted == stated else ["batch_size_contradicts_plan"]


def _ledger(evidence: ReleaseEvidence, ledger: LedgerEvidence, batch: BatchSize) -> list[str]:
    limits = evidence.limits
    found = []
    if ledger.limits_sha256 not in (None, limits.limits_sha256):
        found.append("ledger_limits_mismatch")
    if ledger.release_state in ("settled", "cancelled"):
        found.append("release_already_settled")
    held = ledger.release_reserved
    if held is not None and (held.documents, held.files, held.bytes) != (
        batch.documents,
        batch.files,
        batch.bytes,
    ):
        found.append("reservation_contradicts_batch")
    if limits.constraints is not None and ledger.committed and ledger.reserved:
        planned = _planned(ledger.committed, ledger.reserved, None if held else batch)
        found += _budgets(planned, limits.constraints)
    return found


def _batch(batch: BatchSize, limits: ReleaseConstraints | None) -> list[str]:
    caps = (
        ("documents", batch.documents, MAX_BATCH_DOCUMENTS, "max_documents_per_batch"),
        ("files", batch.files, MAX_BATCH_FILES, "max_files_per_batch"),
        ("bytes", batch.bytes, MAX_BATCH_BYTES, "max_bytes_per_batch"),
    )
    return [
        f"batch_{name}_exceeded"
        for name, amount, hard, field in caps
        if amount > min(hard, getattr(limits, field, hard))
    ]


def _planned(committed: BudgetUsage, reserved: BudgetUsage, batch: BatchSize | None) -> BudgetUsage:
    """Settled plus reserved usage, plus this batch unless it is already reserved."""
    extra = (0, 0, 0, 0) if batch is None else (batch.documents, batch.files, batch.bytes, 1)
    return BudgetUsage(
        documents=committed.documents + reserved.documents + extra[0],
        files=committed.files + reserved.files + extra[1],
        bytes=committed.bytes + reserved.bytes + extra[2],
        commits=committed.commits + reserved.commits + extra[3],
    )


def _budgets(planned: BudgetUsage, limits: ReleaseConstraints) -> list[str]:
    names = ("documents", "files", "bytes", "commits")
    return [
        f"budget_{name}_exceeded"
        for name in names
        if getattr(planned, name) > getattr(limits, f"max_{name}_per_day")
    ]


def _execution_controls(_: ReleaseEvidence) -> Reasons:
    yield from (GateReason(code=code) for code in EXECUTION_PREREQUISITES)


_CHECKS: dict[GateId, Callable[[ReleaseEvidence], Iterable[GateReason]]] = {
    "inventory": _inventory,
    "lineage_anchor": _lineage_anchor,
    "manifest_reproduced": _manifest_reproduced,
    "source_evidence": _source_evidence,
    "policy_rules": _policy_rules,
    "authorization": _authorization,
    "legal_assessment": _legal_assessment,
    "automated_publication": _automated_publication,
    "publisher": _publisher,
    "privacy_rights": _privacy_rights,
    "freshness": _freshness,
    "stop_control": _stop_control,
    "constraints": _constraints,
    "execution_controls": _execution_controls,
}
