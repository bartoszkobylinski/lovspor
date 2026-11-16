"""AR1: the trust-boundary interfaces and the collection of release evidence (#738; §9).

Each kind of evidence the gates need comes from an independent source,
declared here as a protocol that a later slice implements:

- :class:`AnchorSource`: lineage-head anchors (AR6 attestation reader, AR8);
- :class:`InventorySource`: the expected document inventory (AR2, FC11/D5);
- :class:`LegalAssessmentSource`: D10 assessment evidence (after D10);
- :class:`LimitsSource`: owner-signed limits and budgets (policy v2, AR5);
- :class:`BudgetLedgerSource`: the runner's budget ledger (AR5).

:class:`Unavailable` implements all of them and reports every piece as
``missing``: nothing is trusted until its verifier exists, and the gates then
block. The signer registry, the FC7b approvals and the FC8b reproduction are
collected here from what the engine already verifies.

:func:`collect_evidence` is the impure step: it verifies signatures (through
FC7b) and rebuilds the manifest (through FC8b). The pure gates of
:mod:`lovspor.forarbeider.release_gates` then read only the collected records.
Nothing here writes, signs or publishes.
"""

from collections.abc import Mapping
from typing import NamedTuple, Protocol

from lovspor.forarbeider.corpus_models import _loaded
from lovspor.forarbeider.policy_authorization import AuthorizationRequest, verify_authorization
from lovspor.forarbeider.policy_authorization_models import (
    AutomatedPublicationApproval,
    PolicyAuthorization,
    RecordCheck,
)
from lovspor.forarbeider.policy_trust import TrustRoot, load_trust_root
from lovspor.forarbeider.release_contract_models import (
    AnchorEvidence,
    ApprovalEvidence,
    InventoryEvidence,
    LedgerEvidence,
    LegalAssessmentEvidence,
    LimitsEvidence,
    PublisherIdentity,
    ReleaseEvidence,
    ReleaseIntent,
    SignerRegistry,
)
from lovspor.forarbeider.version_release import DocumentOffer, parse_release, verify_release
from lovspor.forarbeider.version_release_models import PolicyReference


class AnchorSource(Protocol):
    def anchor(self, document_id: str) -> AnchorEvidence: ...


class InventorySource(Protocol):
    def inventory(self) -> InventoryEvidence: ...


class LegalAssessmentSource(Protocol):
    def assessment(self) -> LegalAssessmentEvidence: ...


class LimitsSource(Protocol):
    def limits(self) -> LimitsEvidence: ...


class BudgetLedgerSource(Protocol):
    def consumed(self) -> LedgerEvidence: ...


class Unavailable:
    """Every source AR1 has no verifier for: each piece of evidence is missing."""

    def anchor(self, document_id: str) -> AnchorEvidence:
        return AnchorEvidence(
            document_id=document_id,
            status="missing",
            source=None,
            anchor=None,
            reasons=("anchor_source_not_implemented",),
        )

    def inventory(self) -> InventoryEvidence:
        return InventoryEvidence(
            status="missing",
            source=None,
            inventory=None,
            inventory_sha256=None,
            reasons=("inventory_source_not_implemented",),
        )

    def assessment(self) -> LegalAssessmentEvidence:
        return LegalAssessmentEvidence(
            status="missing",
            source=None,
            assessment_sha256=None,
            reference_stated=None,
            reasons=("legal_assessment_source_not_implemented",),
        )

    def limits(self) -> LimitsEvidence:
        return LimitsEvidence(
            status="missing",
            source=None,
            constraints=None,
            reasons=("signed_limits_not_implemented",),
        )

    def consumed(self) -> LedgerEvidence:
        return LedgerEvidence(
            status="missing",
            source=None,
            consumed=None,
            reasons=("budget_ledger_not_implemented",),
        )


_UNAVAILABLE = Unavailable()


class ReleaseSources(NamedTuple):
    """The independent sources; each unavailable until its slice lands."""

    anchors: AnchorSource = _UNAVAILABLE
    inventory: InventorySource = _UNAVAILABLE
    legal: LegalAssessmentSource = _UNAVAILABLE
    limits: LimitsSource = _UNAVAILABLE
    ledger: BudgetLedgerSource = _UNAVAILABLE


class ReleaseRequest(NamedTuple):
    """What a caller asks to release; none of it is trusted until verified."""

    intent: ReleaseIntent
    manifest: bytes
    offers: tuple[DocumentOffer, ...]
    artifacts: Mapping[str, bytes]
    root: TrustRoot | None = None


def collect_evidence(request: ReleaseRequest, sources: ReleaseSources) -> ReleaseEvidence:
    """The evidence for ``request``, each piece from its own source.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid`` when the offered manifest does
            not parse as canonical FC8b bytes; ``lineage_conflict`` for offers
            FC8b refuses.
    """
    manifest = parse_release(request.manifest)
    root = request.root or load_trust_root()
    ids = [item.document_id for item in manifest.content.documents]
    return ReleaseEvidence(
        intent=request.intent,
        manifest=manifest,
        reproduction=verify_release(
            request.manifest, request.offers, request.artifacts, request.root
        ),
        inventory=sources.inventory.inventory(),
        anchors=tuple(sources.anchors.anchor(document) for document in ids),
        registry=registry_of(root),
        publisher=publisher_of(root, request.intent.publisher_principal),
        approvals=approvals_of(request.offers, request.root),
        legal=sources.legal.assessment(),
        limits=sources.limits.limits(),
        ledger=sources.ledger.consumed(),
    )


def registry_of(root: TrustRoot) -> SignerRegistry:
    """The trust root by digest, origin and key counts per role."""
    roles = [entry.role for entry in root.entries]
    return SignerRegistry(
        trust_root_sha256=root.sha256,
        origin=root.origin,
        owner_keys=roles.count("owner_policy"),
        bot_keys=roles.count("bot_execution"),
    )


def publisher_of(root: TrustRoot, principal: str) -> PublisherIdentity:
    """The publisher as the trust root lists it; an unlisted principal has no role."""
    entry = next((item for item in root.entries if item.principal == principal), None)
    if entry is None:
        return PublisherIdentity(
            principal=principal, key_fingerprint=None, role=None, namespaces=()
        )
    return PublisherIdentity(
        principal=principal,
        key_fingerprint=entry.fingerprint,
        role=entry.role,
        namespaces=tuple(sorted(entry.namespaces)),
    )


def approvals_of(
    offers: tuple[DocumentOffer, ...], root: TrustRoot | None
) -> tuple[ApprovalEvidence, ...]:
    """Each offered policy's FC7b approvals, verified against ``root``; none without one."""
    if root is None:
        return ()
    found = {
        _approval(offer.authorization, verify_authorization(offer.authorization, root))
        for offer in offers
        if offer.authorization is not None
    }
    return tuple(
        sorted(found, key=lambda item: (item.policy.policy_sha256, item.model_dump_json()))
    )


def _approval(request: AuthorizationRequest, verdict: PolicyAuthorization) -> ApprovalEvidence:
    checks = (verdict.policy_approval, verdict.automated_publication)
    signatures = [check.signature for check in checks if check.signature is not None]
    return ApprovalEvidence(
        policy=PolicyReference(
            policy_id=verdict.policy_id,
            policy_version=verdict.policy_version,
            policy_sha256=verdict.policy_sha256,
        ),
        policy_approval=verdict.policy_approval.status,
        automated_publication=verdict.automated_publication.status,
        approver_fingerprints=tuple(
            sorted({s.key_fingerprint for s in signatures if s.key_fingerprint})
        ),
        approver_roles=tuple(sorted({s.role for s in signatures if s.role is not None})),
        max_documents_per_batch=_limit(request, verdict.automated_publication),
        legal_reference_stated=verdict.legal_assessment_reference_stated,
        governance=verdict.governance,
    )


def _limit(request: AuthorizationRequest, check: RecordCheck) -> int | None:
    """The approved batch limit, read only from a verified automated approval."""
    if check.status != "verified" or request.automated_publication is None:
        return None
    record = AutomatedPublicationApproval.model_validate(
        _loaded(request.automated_publication.record)
    )
    return record.max_documents_per_batch
