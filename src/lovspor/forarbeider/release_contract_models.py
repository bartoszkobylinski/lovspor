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
vouch for it, and ``VERIFIED_SOURCES`` the sources whose verifier exists in
the engine today. Evidence counts as verified only when its status says so
*and* its source is both accepted for its kind and verified. ``api_request``
is never accepted: a head, an inventory or an approval is not trusted because
a caller sent it. Today only the engine trust root and owner-signed FC7b
records have verifiers, so anchors, inventories, legal assessments, signed
limits and the budget ledger are untrusted whatever they claim.

**Owner decisions.** D10 cannot be recorded as accepted
(``independently_accepted`` is ``Literal[False]``) and D11 governance is the
FC7b ``Governance`` record, which knows only ``proposed_not_accepted``.
Nothing here states a legal conclusion.
"""

from typing import Final, Literal, Self

from pydantic import Field, model_validator

from lovspor.forarbeider.identity import DocumentType
from lovspor.forarbeider.policy_authorization_models import CheckStatus, Governance
from lovspor.forarbeider.policy_trust import Origin, Role
from lovspor.forarbeider.version_models import Digest, Frozen, NonBlank
from lovspor.forarbeider.version_release_models import (
    PolicyReference,
    ReleaseManifest,
    ReleaseVerification,
)

CONTRACT_VERSION: Final = "release-contract/1"
#: The hard batch bounds of §9.3 step 2 (recommended answer, pending).
MAX_BATCH_FILES: Final = 100
MAX_BATCH_BYTES: Final = 25_000_000

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
    "runner_budget_ledger",
]
EvidenceKind = Literal[
    "lineage_head_anchor",
    "document_inventory",
    "signer_registry",
    "policy_approval",
    "legal_assessment",
    "publication_limits",
    "budget_ledger",
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
        accepted_sources=("owner_signed_inventory", "official_catalogue"),
        verifier="not_implemented",
        implemented_by="AR2 with FC11 discovery (D5)",
    ),
    TrustBoundary(
        evidence="signer_registry",
        accepted_sources=("engine_trust_root",),
        verifier="lovspor.forarbeider.policy_trust.load_trust_root",
        implemented_by="FC7b; keys by owner-merged change (D6)",
    ),
    TrustBoundary(
        evidence="policy_approval",
        accepted_sources=("owner_signed_record",),
        verifier="lovspor.forarbeider.policy_authorization.verify_authorization",
        implemented_by="FC7b",
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
        verifier="not_implemented",
        implemented_by="a signed policy v2 limits record (not built yet), AR5",
    ),
    TrustBoundary(
        evidence="budget_ledger",
        accepted_sources=("runner_budget_ledger",),
        verifier="not_implemented",
        implemented_by="AR5",
    ),
)
#: Sources with a verifier in the engine today. Adding one needs its verifier.
VERIFIED_SOURCES: Final[frozenset[SourceKind]] = frozenset(
    {"engine_trust_root", "owner_signed_record"}
)


def trusted(kind: EvidenceKind, status: EvidenceStatus, source: SourceKind | None) -> bool:
    """Whether evidence of ``kind`` counts as verified: status, accepted source, verifier."""
    boundary = next(item for item in TRUST_BOUNDARIES if item.evidence == kind)
    return (
        status == "verified" and source in boundary.accepted_sources and source in VERIFIED_SOURCES
    )


class _Evidence(Frozen):
    """Evidence from one source; ``verified`` needs a named source."""

    status: EvidenceStatus
    source: SourceKind | None
    reasons: tuple[NonBlank, ...]

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
    inventory: DocumentInventory | None
    inventory_sha256: Digest | None

    @model_validator(mode="after")
    def _complete(self) -> Self:
        self._require(self.inventory)
        return self


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


class SignerRegistry(Frozen):
    """The trust root the signatures were checked against, by digest and key counts."""

    trust_root_sha256: Digest
    origin: Origin
    owner_keys: int = Field(ge=0)
    bot_keys: int = Field(ge=0)


class PublisherIdentity(Frozen):
    """The publisher as the signer registry resolves it; ``role`` is ``None`` if unlisted."""

    principal: NonBlank
    key_fingerprint: NonBlank | None
    role: Role | None
    namespaces: tuple[NonBlank, ...]


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


class ReleaseConstraints(Frozen):
    """Batch bounds and daily budgets; the batch bounds never exceed §9.3's."""

    max_files_per_batch: int = Field(ge=1, le=MAX_BATCH_FILES)
    max_bytes_per_batch: int = Field(ge=1, le=MAX_BATCH_BYTES)
    max_documents_per_day: int = Field(ge=0)
    max_files_per_day: int = Field(ge=0)
    max_bytes_per_day: int = Field(ge=0)
    max_commits_per_day: int = Field(ge=0)


class LimitsEvidence(_Evidence):
    constraints: ReleaseConstraints | None

    @model_validator(mode="after")
    def _complete(self) -> Self:
        self._require(self.constraints)
        return self


class BudgetUsage(Frozen):
    documents: int = Field(ge=0)
    files: int = Field(ge=0)
    bytes: int = Field(ge=0)
    commits: int = Field(ge=0)


class LedgerEvidence(_Evidence):
    consumed: BudgetUsage | None

    @model_validator(mode="after")
    def _complete(self) -> Self:
        self._require(self.consumed)
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

    contract_version: Literal["release-contract/1"] = CONTRACT_VERSION
    intent: ReleaseIntent
    manifest: ReleaseManifest
    reproduction: ReleaseVerification
    inventory: InventoryEvidence
    anchors: tuple[AnchorEvidence, ...]
    registry: SignerRegistry
    publisher: PublisherIdentity
    approvals: tuple[ApprovalEvidence, ...]
    legal: LegalAssessmentEvidence
    limits: LimitsEvidence
    ledger: LedgerEvidence


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
        "batch_size_contradicts_intent",
        "document_outside_inventory",
        "head_ahead_of_anchor",
        "intent_bindings_mismatch",
        "intent_documents_mismatch",
        "intent_release_mismatch",
        "publisher_mismatch",
        "publisher_self_approval",
        "reproduction_invalid",
        "reproduction_of_other_manifest",
        "version_rolled_back",
    }
)


class ReleaseGateVerdict(Frozen):
    """Every gate's result over one body of evidence; never an authorization."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_release_gate_verdict"] = "forarbeider_release_gate_verdict"
    evaluator_version: Literal["release-gates/1"] = "release-gates/1"
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
