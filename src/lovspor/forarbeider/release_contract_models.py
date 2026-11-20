"""AR1 records: what an automatic release must prove before any execution (#738; §9, D10, D11).

Three things are kept apart, each its own record:

- the **desired release** (``ReleaseIntent``): the caller's request. It names
  an exact FC8b manifest (``release_id`` and ``bindings_sha256``), its
  documents, the publisher it expects and the batch size it plans. It has no
  field for eligibility, approval or publication; extra fields are refused;
- the **verified authorization** (``ReleaseGateVerdict``): the gates'
  verdict over ``ReleaseEvidence``. ``authorized``, ``release_eligible`` and
  ``published`` have the single value ``False``;
- the **executed release** (``ReleaseExecution``): its only state is
  ``not_executed``. No execution path exists (AR4 adds one).

**Trust boundaries.** Every piece of evidence names the source it came from.
``TRUST_BOUNDARIES`` states, for each kind, the independent sources that may
vouch for it, and ``VERIFIED_SOURCES`` (derived from it) the sources whose
verifier exists in the engine today. A source label is only a claim, so
evidence counts as verified (:func:`verified`) only when its status says so,
its source is accepted for its kind and verified, *and* it carries the seal
of the engine verifier that produced it
(:mod:`lovspor.forarbeider.release_provenance`). ``api_request`` is never
accepted: a head, an inventory or an approval is not trusted because a caller
sent it, and a record built by hand with ``status="verified"`` has no seal.
The lineage-head anchors and the D10 legal assessment have no verifier, so
their evidence is never trusted whatever it claims (AR6/AR8 and D10 must each
prove source authenticity before they can be). A kind may have several
boundaries, one per source: the document inventory is verified only when the
owner signed it (AR2); an official catalogue (FC11, D7) has no verifier yet.

**AR1b** adds the owner-signed limits (:mod:`release_limits_models`), the
budget ledger (:mod:`release_ledger_models`) and the owner-signed stop/resume
chain (:mod:`release_control_models`), each with its verifier and the
``stop_control`` gate. The ledger and the control chain are local hash
chains: neither proves append-only storage, completeness or concurrency
safety, so both stay ``unanchored`` and their gates fail until AR5 provides
:data:`LEDGER_REQUIREMENTS` and an external anchor for the control head.

**AR2** adds the owner-signed document inventory
(:mod:`release_inventory_models`) and the batch plan
(:mod:`release_plan_models`), each with its verifier. A release is one batch
of a plan: the inventory gate accounts for every expected document through
the plan, and the constraints gate counts the batch the planner computed
from the artifacts, never the caller's numbers.

**Owner decisions.** D10 cannot be recorded as accepted
(``independently_accepted`` is ``Literal[False]``) and D11 governance is the
FC7b ``Governance`` record, which knows only ``proposed_not_accepted``.
Nothing here states a legal conclusion.
"""

from datetime import date
from typing import Final, Literal, Self

from pydantic import Field, model_validator

from lovspor.forarbeider.identity import DocumentType
from lovspor.forarbeider.policy_authorization_models import CheckStatus, Governance
from lovspor.forarbeider.policy_trust import Origin, Role
from lovspor.forarbeider.release_limits_models import ReleaseConstraints
from lovspor.forarbeider.release_plan_models import ReleasePlan
from lovspor.forarbeider.release_provenance import VERIFIERS, Provenance, authentic
from lovspor.forarbeider.version_models import Digest, Frozen, NonBlank
from lovspor.forarbeider.version_release_models import (
    PolicyReference,
    ReleaseManifest,
    ReleaseVerification,
)

CONTRACT_VERSION: Final = "release-contract/2"

GateId = Literal[
    "inventory",
    "lineage_anchor",
    "manifest_reproduced",
    "source_evidence",
    "policy_rules",
    "authorization",
    "legal_assessment",
    "automated_publication",
    "publisher",
    "privacy_rights",
    "freshness",
    "stop_control",
    "constraints",
    "execution_controls",
]
GATES: Final[tuple[GateId, ...]] = (
    "inventory",
    "lineage_anchor",
    "manifest_reproduced",
    "source_evidence",
    "policy_rules",
    "authorization",
    "legal_assessment",
    "automated_publication",
    "publisher",
    "privacy_rights",
    "freshness",
    "stop_control",
    "constraints",
    "execution_controls",
)

#: What no evidence can supply today: the execution controls AR4 to AR7 build.
EXECUTION_PREREQUISITES: Final = (
    "base_commit_not_implemented",
    "kill_switch_not_implemented",
    "publication_path_not_implemented",
    "release_signing_not_implemented",
    "settings_verifier_not_implemented",
)

#: The questions D10's independent assessment must answer (§3, owner decisions §5).
LEGAL_QUESTIONS: Final = (
    "attachments",
    "privacy",
    "quotations",
    "rights_section_14",
    "rights_section_24",
    "rights_section_29",
)

#: What AR5 must provide before ledger evidence can pass a gate: a local hash
#: chain alone proves neither append-only storage nor concurrency safety.
LEDGER_REQUIREMENTS: Final = (
    "atomic_append_with_compare_and_swap_on_the_head",
    "durable_before_the_publication_call",
    "external_anchor_of_the_head_outside_the_runner",
    "single_writer_lock_per_ledger",
    "reserve_before_publication_settle_after_verification",
    "reconcile_against_the_published_history",
)

SourceKind = Literal[
    "api_request",
    "engine_trust_root",
    "owner_signed_record",
    "owner_signed_inventory",
    "official_catalogue",
    "published_release_attestation",
    "owner_signed_anchor",
    "independent_legal_assessment",
    "owner_signed_policy_limits",
    "owner_signed_control",
    "runner_budget_ledger",
    "engine_release_verifier",
    "engine_release_planner",
    "engine_quarantine",
]
EvidenceKind = Literal[
    "lineage_head_anchor",
    "document_inventory",
    "signer_registry",
    "publisher_identity",
    "policy_approval",
    "manifest_reproduction",
    "legal_assessment",
    "publication_limits",
    "release_control",
    "budget_ledger",
    "batch_plan",
    "quarantine_assessment",
]
EvidenceStatus = Literal["missing", "invalid", "verified"]


class TrustBoundary(Frozen):
    """Who may vouch for one kind of evidence, and which slice builds the verifier."""

    evidence: EvidenceKind
    accepted_sources: tuple[SourceKind, ...] = Field(min_length=1)
    verifier: NonBlank
    implemented_by: NonBlank


TRUST_BOUNDARIES: Final = (
    TrustBoundary(
        evidence="lineage_head_anchor",
        accepted_sources=("published_release_attestation", "owner_signed_anchor"),
        verifier="not_implemented",
        implemented_by="AR6 (attestation reader), AR8 (auditor)",
    ),
    TrustBoundary(
        evidence="document_inventory",
        accepted_sources=("owner_signed_inventory",),
        verifier="lovspor.forarbeider.release_inventory.verify_inventory",
        implemented_by="AR2; owner key by owner-merged change (D6)",
    ),
    TrustBoundary(
        evidence="document_inventory",
        accepted_sources=("official_catalogue",),
        verifier="not_implemented",
        implemented_by="FC11 discovery after D7, with a catalogue authenticity verifier",
    ),
    TrustBoundary(
        evidence="batch_plan",
        accepted_sources=("engine_release_planner",),
        verifier="lovspor.forarbeider.release_plan.plan_release",
        implemented_by="AR2",
    ),
    TrustBoundary(
        evidence="signer_registry",
        accepted_sources=("engine_trust_root",),
        verifier="lovspor.forarbeider.policy_trust.load_trust_root",
        implemented_by="FC7b; keys by owner-merged change (D6)",
    ),
    TrustBoundary(
        evidence="publisher_identity",
        accepted_sources=("engine_trust_root",),
        verifier="lovspor.forarbeider.release_sources.publisher_of",
        implemented_by="FC7b trust root; the bot key by owner-merged change (D6)",
    ),
    TrustBoundary(
        evidence="policy_approval",
        accepted_sources=("owner_signed_record",),
        verifier="lovspor.forarbeider.policy_authorization.verify_authorization",
        implemented_by="FC7b",
    ),
    TrustBoundary(
        evidence="manifest_reproduction",
        accepted_sources=("engine_release_verifier",),
        verifier="lovspor.forarbeider.version_release.verify_release",
        implemented_by="FC8b",
    ),
    TrustBoundary(
        evidence="quarantine_assessment",
        accepted_sources=("engine_quarantine",),
        verifier="lovspor.forarbeider.release_quarantine_run.run_quarantine",
        implemented_by="AR3; atomic persistence and an anchor of the ledger are AR5",
    ),
    TrustBoundary(
        evidence="legal_assessment",
        accepted_sources=("independent_legal_assessment",),
        verifier="not_implemented",
        implemented_by="D10 decision, then a signed assessment binding",
    ),
    TrustBoundary(
        evidence="publication_limits",
        accepted_sources=("owner_signed_policy_limits",),
        verifier="lovspor.forarbeider.release_limits.verify_limits",
        implemented_by="AR1b; owner key by owner-merged change (D6)",
    ),
    TrustBoundary(
        evidence="release_control",
        accepted_sources=("owner_signed_control",),
        verifier="lovspor.forarbeider.release_control.verify_control",
        implemented_by="AR1b chain verifier; the external head anchor is AR5",
    ),
    TrustBoundary(
        evidence="budget_ledger",
        accepted_sources=("runner_budget_ledger",),
        verifier="lovspor.forarbeider.release_ledger.verify_ledger",
        implemented_by="AR1b chain verifier; atomic persistence and anchor are AR5",
    ),
)
#: Sources with a verifier in the engine today, derived from the boundaries.
VERIFIED_SOURCES: Final[frozenset[SourceKind]] = frozenset(
    source
    for boundary in TRUST_BOUNDARIES
    if boundary.verifier != "not_implemented"
    for source in boundary.accepted_sources
)


def trusted(kind: EvidenceKind, status: EvidenceStatus, source: SourceKind | None) -> bool:
    """Whether a label could vouch for ``kind``: status, accepted source, verifier. Not enough.

    A label is a claim; :func:`verified` also demands the verifier's seal.
    """
    if status != "verified" or source not in VERIFIED_SOURCES:
        return False
    return any(
        item.evidence == kind
        and source in item.accepted_sources
        and item.verifier != "not_implemented"
        for item in TRUST_BOUNDARIES
    )


class _Evidence(Frozen):
    """Evidence from one source; ``verified`` needs a named source."""

    status: EvidenceStatus
    source: SourceKind | None
    reasons: tuple[NonBlank, ...]
    provenance: Provenance | None = None

    def _require(self, record: object) -> None:
        if self.status == "verified" and (self.source is None or record is None):
            raise ValueError("verified evidence names its source and carries its record")


# --- inventory and anchors -----------------------------------------------------------


class InventoryScope(Frozen):
    source_provider: Literal["regjeringen.no"] = "regjeringen.no"
    document_types: tuple[DocumentType, ...] = Field(min_length=1)


def _sorted_once(ids: tuple[str, ...]) -> tuple[str, ...]:
    if list(ids) != sorted(set(ids)):
        raise ValueError("document ids are sorted, once each")
    return ids


class DocumentInventory(Frozen):
    """The documents a release scope is expected to contain."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_document_inventory"] = "forarbeider_document_inventory"
    inventory_id: NonBlank
    scope: InventoryScope
    document_ids: tuple[NonBlank, ...]

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        _sorted_once(self.document_ids)
        return self


class InventoryEvidence(_Evidence):
    """The expected documents as verified: the inventory, its policy, version and signer."""

    inventory: DocumentInventory | None
    inventory_sha256: Digest | None
    inventory_version: int | None = Field(default=None, ge=1)
    policy: PolicyReference | None = None
    discovery_sha256s: tuple[Digest, ...] = ()
    trust_root_sha256: Digest | None = None
    signer_fingerprint: NonBlank | None = None
    signer_role: Role | None = None

    @model_validator(mode="after")
    def _complete(self) -> Self:
        self._require(self.inventory)
        return self


class PlanEvidence(_Evidence):
    """The batch plan as the engine's planner computed it from verified evidence."""

    plan: ReleasePlan | None = None
    plan_sha256: Digest | None = None
    trust_root_sha256: Digest | None = None

    @model_validator(mode="after")
    def _complete(self) -> Self:
        self._require(self.plan)
        self._require(self.plan_sha256)
        return self


NO_PLAN: Final = PlanEvidence(status="missing", source=None, reasons=("plan_not_collected",))


class HeadAnchor(Frozen):
    """A lineage head attested outside the caller: the head and its chain length."""

    record_type: Literal["forarbeider_lineage_head_anchor"] = "forarbeider_lineage_head_anchor"
    document_id: NonBlank
    head_sha256: Digest
    lineage_entries: int = Field(ge=1)


class AnchorEvidence(_Evidence):
    document_id: NonBlank
    anchor: HeadAnchor | None

    @model_validator(mode="after")
    def _complete(self) -> Self:
        self._require(self.anchor)
        if self.anchor is not None and self.anchor.document_id != self.document_id:
            raise ValueError("an anchor belongs to its document")
        return self


# --- signers, publisher, approvals ---------------------------------------------------


def verified(kind: EvidenceKind, evidence: _Evidence) -> bool:
    """Whether ``evidence`` is verified: a trusted label *and* its verifier's seal."""
    if not trusted(kind, evidence.status, evidence.source) or kind not in VERIFIERS:
        return False
    return authentic(evidence, kind)


class SignerRegistry(Frozen):
    """The trust root the signatures were checked against, by digest and key counts."""

    trust_root_sha256: Digest
    origin: Origin
    owner_keys: int = Field(ge=0)
    bot_keys: int = Field(ge=0)
    provenance: Provenance | None = None


class PublisherIdentity(Frozen):
    """The publisher as the signer registry resolves it; ``role`` is ``None`` if unlisted."""

    principal: NonBlank
    key_fingerprint: NonBlank | None
    role: Role | None
    namespaces: tuple[NonBlank, ...]
    provenance: Provenance | None = None


class ApprovalEvidence(Frozen):
    """One policy's FC7b approvals as verified; never an acceptance of D11."""

    policy: PolicyReference
    policy_approval: CheckStatus
    automated_publication: CheckStatus
    approver_fingerprints: tuple[NonBlank, ...]
    approver_roles: tuple[Role, ...]
    max_documents_per_batch: int | None = Field(ge=1, le=100)
    legal_reference_stated: str | None
    governance: Governance
    provenance: Provenance | None = None


class ReproductionEvidence(Frozen):
    """The FC8b verifier's result over the offered manifest, sealed by the engine."""

    verification: ReleaseVerification
    provenance: Provenance | None = None


# --- D10 -----------------------------------------------------------------------------


class LegalAssessmentRequirement(Frozen):
    """What D10 requires: an independent assessment of these questions."""

    decision: Literal["D10"] = "D10"
    questions: tuple[NonBlank, ...] = LEGAL_QUESTIONS
    independent: Literal[True] = True


class LegalAssessmentEvidence(_Evidence):
    """An assessment as found; it can never be recorded as accepted here."""

    assessment_sha256: Digest | None
    reference_stated: str | None
    requirement: LegalAssessmentRequirement = LegalAssessmentRequirement()
    independently_accepted: Literal[False] = False

    @model_validator(mode="after")
    def _complete(self) -> Self:
        self._require(self.assessment_sha256)
        return self


# --- limits and budgets --------------------------------------------------------------


class LimitsEvidence(_Evidence):
    """The owner-signed limits as verified: their constraints, policy and signer."""

    constraints: ReleaseConstraints | None
    policy: PolicyReference | None = None
    limits_id: NonBlank | None = None
    limits_sha256: Digest | None = None
    trust_root_sha256: Digest | None = None
    signer_fingerprint: NonBlank | None = None
    signer_role: Role | None = None

    @model_validator(mode="after")
    def _complete(self) -> Self:
        self._require(self.constraints)
        self._require(self.limits_sha256)
        return self


class BudgetUsage(Frozen):
    documents: int = Field(ge=0)
    files: int = Field(ge=0)
    bytes: int = Field(ge=0)
    commits: int = Field(ge=0)


ReleaseState = Literal["none", "reserved", "settled", "cancelled"]


class LedgerEvidence(_Evidence):
    """One day's accounting as the ledger chain shows it; never anchored in AR1b."""

    day: date | None = None
    limits_sha256: Digest | None = None
    committed: BudgetUsage | None = None
    reserved: BudgetUsage | None = None
    release_state: ReleaseState = "none"
    release_reserved: BudgetUsage | None = None
    entries: int = Field(default=0, ge=0)
    head_sha256: Digest | None = None
    anchoring: Literal["unanchored"] = "unanchored"
    persistence_requirements: tuple[NonBlank, ...] = LEDGER_REQUIREMENTS

    @model_validator(mode="after")
    def _complete(self) -> Self:
        self._require(self.committed)
        self._require(self.reserved)
        self._require(self.head_sha256)
        return self


# --- stop and resume -----------------------------------------------------------------


class ControlEvidence(_Evidence):
    """The stop state the owner-signed control chain yields; stopped unless proven otherwise."""

    policy_id: str | None = None
    state: Literal["stopped", "not_stopped"] = "stopped"
    active_stops: tuple[NonBlank, ...] = ()
    records: int = Field(default=0, ge=0)
    head_sha256: Digest | None = None
    trust_root_sha256: Digest | None = None
    anchoring: Literal["unanchored"] = "unanchored"

    @model_validator(mode="after")
    def _complete(self) -> Self:
        self._require(self.head_sha256)
        if self.state == "not_stopped" and (self.status != "verified" or self.active_stops):
            raise ValueError("only a verified chain without an active stop is not stopped")
        return self


# --- the desired release -------------------------------------------------------------


class BatchSize(Frozen):
    documents: int = Field(ge=0)
    files: int = Field(ge=0)
    bytes: int = Field(ge=0)


class ReleaseIntent(Frozen):
    """The desired release: an exact manifest, its documents and publisher. Never approval."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_release_intent"] = "forarbeider_release_intent"
    intent_id: NonBlank
    release_id: Digest
    bindings_sha256: Digest
    document_ids: tuple[NonBlank, ...] = Field(min_length=1)
    publisher_principal: NonBlank
    batch: BatchSize

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        _sorted_once(self.document_ids)
        return self


class ReleaseEvidence(Frozen):
    """Everything the gates read; every part names where it came from."""

    contract_version: Literal["release-contract/2"] = CONTRACT_VERSION
    intent: ReleaseIntent
    manifest: ReleaseManifest
    reproduction: ReproductionEvidence
    inventory: InventoryEvidence
    plan: PlanEvidence = NO_PLAN
    anchors: tuple[AnchorEvidence, ...]
    registry: SignerRegistry
    publisher: PublisherIdentity
    approvals: tuple[ApprovalEvidence, ...]
    legal: LegalAssessmentEvidence
    limits: LimitsEvidence
    ledger: LedgerEvidence
    control: ControlEvidence


# --- the verified authorization ------------------------------------------------------


class GateReason(Frozen):
    code: NonBlank
    document_id: str | None = None


def _reason_key(reason: GateReason) -> tuple[str, str]:
    return (reason.document_id or "", reason.code)


class GateResult(Frozen):
    """One gate: passed with no reason, or failed with every reason, sorted once each."""

    gate: GateId
    status: Literal["passed", "failed"]
    reasons: tuple[GateReason, ...]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if list(self.reasons) != sorted(set(self.reasons), key=_reason_key):
            raise ValueError("reasons are sorted, once each")
        if (self.status == "failed") != bool(self.reasons):
            raise ValueError("a gate fails exactly when it has reasons")
        return self


#: Reason codes that mean the evidence contradicts itself or was forged.
REFUSING_CODES: Final = frozenset(
    {
        "anchor_conflict",
        "anchor_for_unknown_document",
        "anchor_head_mismatch",
        "approval_conflict",
        "approval_for_unknown_policy",
        "approval_signed_by_bot",
        "batch_artifact_binding_mismatch",
        "batch_not_in_plan",
        "batch_selection_mismatch",
        "batch_size_contradicts_intent",
        "batch_size_contradicts_plan",
        "document_outside_inventory",
        "head_ahead_of_anchor",
        "intent_bindings_mismatch",
        "intent_documents_mismatch",
        "intent_release_mismatch",
        "plan_inventory_mismatch",
        "plan_limits_mismatch",
        "publisher_mismatch",
        "publisher_self_approval",
        "release_already_settled",
        "reproduction_invalid",
        "reproduction_of_other_manifest",
        "reservation_contradicts_batch",
        "trust_root_conflict",
        "version_rolled_back",
    }
)


class ReleaseGateVerdict(Frozen):
    """Every gate's result over one body of evidence; never an authorization."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_release_gate_verdict"] = "forarbeider_release_gate_verdict"
    evaluator_version: Literal["release-gates/2"] = "release-gates/2"
    intent_sha256: Digest
    evidence_sha256: Digest
    release_id: Digest
    status: Literal["blocked", "refused"]
    gates: tuple[GateResult, ...]
    failed_gates: tuple[GateId, ...] = Field(min_length=1)
    execution_prerequisites: tuple[NonBlank, ...] = EXECUTION_PREREQUISITES
    authorized: Literal[False] = False
    release_eligible: Literal[False] = False
    published: Literal[False] = False

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if tuple(item.gate for item in self.gates) != GATES:
            raise ValueError("every gate is reported once, in order")
        if self.failed_gates != tuple(g.gate for g in self.gates if g.status == "failed"):
            raise ValueError("failed_gates lists exactly the failed gates")
        refusing = any(r.code in REFUSING_CODES for g in self.gates for r in g.reasons)
        if (self.status == "refused") != refusing:
            raise ValueError("refused exactly when the evidence contradicts itself")
        return self


# --- the executed release ------------------------------------------------------------


class ReleaseExecution(Frozen):
    """What was executed for a verdict: nothing. AR4 adds the executed states."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_release_execution"] = "forarbeider_release_execution"
    verdict_sha256: Digest
    status: Literal["not_executed"] = "not_executed"
    reason: Literal["execution_not_implemented"] = "execution_not_implemented"
    published: Literal[False] = False
