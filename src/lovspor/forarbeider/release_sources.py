"""AR1: the trust-boundary interfaces and the collection of release evidence (#738; §9).

Each kind of evidence the gates need comes from an independent source,
declared here as a protocol that a later slice implements:

- :class:`AnchorSource`: lineage-head anchors (AR6 attestation reader, AR8);
- :class:`InventorySource`: the owner-signed document inventory and its
  revocations (AR2); FC11 catalogue pages have no verifier yet (D7);
- :class:`LegalAssessmentSource`: D10 assessment evidence (after D10);
- :class:`LimitsSource`: owner-signed limits and their revocations (AR1b);
- :class:`ControlSource`: the owner-signed stop/resume chain (AR1b);
- :class:`BudgetLedgerSource`: the runner's budget ledger entries (AR1b, AR5).

:class:`Unavailable` implements all of them and offers nothing: every piece is
``missing`` and the gates block.

**Provenance boundary.** The AR1b and AR2 sources (limits, control, ledger,
inventory) hand over raw bytes and signatures, never evidence;
:func:`collect_evidence` verifies them and only its verifiers seal the result
(:mod:`lovspor.forarbeider.release_provenance`). The anchor and
legal-assessment sources still return evidence objects, which the engine
cannot seal, so the gates never trust them. Before either can count, its
slice must turn it into a raw-material source with an engine verifier that
proves the source's authenticity: AR6/AR8 (lineage-head anchors from
bot-signed publication attestations) and D10 (a verifiable binding of the
assessment).

**Scope and batch (AR2).** ``offers`` covers the whole planning scope: every
document the caller offers for the inventory. The manifest is one batch of
it. The FC8b reproduction is checked over the manifest's own documents; the
planner (:func:`~lovspor.forarbeider.release_plan.plan_release`) rebuilds the
plan over all of them, so the gates can tell whether the manifest is exactly
one planned batch and count its size from the artifacts.

**One trust root.** :func:`resolve_trust_root` resolves the root once: the
request's, or the engine's pinned root when the request names none. That one
object reaches the signer registry, the publisher, every FC7b
re-verification, the FC8b reproduction and the limits and control verifiers.
A stored FC7b verdict made under another root refuses the whole request
(``trust root conflict``); nothing falls back silently. The inventory verifier
and the planner get the same root.

:func:`collect_evidence` is the impure step: it verifies signatures and
rebuilds the manifest. The pure gates of
:mod:`lovspor.forarbeider.release_gates` then read only the collected records.
Nothing here writes, signs or publishes.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import NamedTuple, Protocol

from lovspor.forarbeider.corpus_models import _loaded
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_authorization import (
    AuthorizationRequest,
    SignedRecord,
    parse_authorization,
    verify_authorization,
)
from lovspor.forarbeider.policy_authorization_models import (
    AutomatedPublicationApproval,
    PolicyAuthorization,
    RecordCheck,
)
from lovspor.forarbeider.policy_trust import TrustRoot, load_trust_root
from lovspor.forarbeider.publication_policy import parse_policy, policy_digest
from lovspor.forarbeider.release_contract_models import (
    AnchorEvidence,
    ApprovalEvidence,
    ControlEvidence,
    InventoryEvidence,
    LedgerEvidence,
    LegalAssessmentEvidence,
    LimitsEvidence,
    PublisherIdentity,
    ReleaseEvidence,
    ReleaseIntent,
    ReproductionEvidence,
    SignerRegistry,
)
from lovspor.forarbeider.release_control import ControlRequest, verify_control
from lovspor.forarbeider.release_inventory import (
    InventoryMaterial,
    InventoryRequest,
    verify_inventory,
)
from lovspor.forarbeider.release_ledger import LedgerRequest, verify_ledger
from lovspor.forarbeider.release_limits import LimitsMaterial, LimitsRequest, verify_limits
from lovspor.forarbeider.release_plan import PlanRequest, plan_release
from lovspor.forarbeider.release_provenance import issue_provenance
from lovspor.forarbeider.version_release import DocumentOffer, parse_release, verify_release
from lovspor.forarbeider.version_release_models import PolicyReference, ReleaseManifest


class AnchorSource(Protocol):
    def anchor(self, document_id: str) -> AnchorEvidence: ...


class InventorySource(Protocol):
    def inventory(self) -> InventoryMaterial: ...


class LegalAssessmentSource(Protocol):
    def assessment(self) -> LegalAssessmentEvidence: ...


class LimitsSource(Protocol):
    def limits(self) -> LimitsMaterial: ...


class ControlSource(Protocol):
    def history(self) -> tuple[SignedRecord, ...]: ...


class BudgetLedgerSource(Protocol):
    def entries(self) -> tuple[bytes, ...]: ...


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

    def inventory(self) -> InventoryMaterial:
        return InventoryMaterial()

    def assessment(self) -> LegalAssessmentEvidence:
        return LegalAssessmentEvidence(
            status="missing",
            source=None,
            assessment_sha256=None,
            reference_stated=None,
            reasons=("legal_assessment_source_not_implemented",),
        )

    def limits(self) -> LimitsMaterial:
        return LimitsMaterial()

    def history(self) -> tuple[SignedRecord, ...]:
        return ()

    def entries(self) -> tuple[bytes, ...]:
        return ()


_UNAVAILABLE = Unavailable()


class ReleaseSources(NamedTuple):
    """The independent sources; each unavailable until its slice lands."""

    anchors: AnchorSource = _UNAVAILABLE
    inventory: InventorySource = _UNAVAILABLE
    legal: LegalAssessmentSource = _UNAVAILABLE
    limits: LimitsSource = _UNAVAILABLE
    control: ControlSource = _UNAVAILABLE
    ledger: BudgetLedgerSource = _UNAVAILABLE


class ReleaseRequest(NamedTuple):
    """What a caller asks to release; none of it is trusted until verified.

    ``root`` ``None`` means the engine's pinned root, resolved once
    (:func:`resolve_trust_root`); ``checked_at`` is the UTC time every signed
    record is judged at.
    """

    intent: ReleaseIntent
    manifest: bytes
    offers: tuple[DocumentOffer, ...]
    artifacts: Mapping[str, bytes]
    root: TrustRoot | None
    checked_at: datetime


def resolve_trust_root(root: TrustRoot | None) -> TrustRoot:
    """The one trust root a request is judged under: its own, else the engine's pinned root."""
    return load_trust_root() if root is None else root


def collect_evidence(request: ReleaseRequest, sources: ReleaseSources) -> ReleaseEvidence:
    """The evidence for ``request``, each piece from its own source, under one trust root.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid`` when the offered manifest does
            not parse as canonical FC8b bytes; ``lineage_conflict`` for offers
            FC8b refuses; ``release_invalid`` when a stored FC7b verdict was made
            under another trust root; ``invalid_timestamp`` for a non-UTC time.
    """
    manifest = parse_release(request.manifest)
    root = resolve_trust_root(request.root)
    _one_root(manifest, request.artifacts, root)
    ids = [item.document_id for item in manifest.content.documents]
    controls = _controls(request, sources, manifest, root)
    inventory = _inventory(request, sources, manifest, root)
    scope = PlanRequest(inventory, controls.limits, request.offers, request.artifacts)
    return ReleaseEvidence(
        intent=request.intent,
        manifest=manifest,
        reproduction=reproduction_of(_batch_only(request, ids), root),
        inventory=inventory,
        plan=plan_release(scope, root),
        anchors=tuple(sources.anchors.anchor(document) for document in ids),
        registry=registry_of(root),
        publisher=publisher_of(root, request.intent.publisher_principal),
        approvals=approvals_of(request.offers, root),
        legal=sources.legal.assessment(),
        **controls._asdict(),
    )


def _batch_only(request: ReleaseRequest, ids: list[str]) -> ReleaseRequest:
    """The request with only the offers of the manifest's own documents."""
    wanted = set(ids)
    return request._replace(offers=tuple(o for o in request.offers if o.document_id in wanted))


def _inventory(
    request: ReleaseRequest, sources: ReleaseSources, manifest: ReleaseManifest, root: TrustRoot
) -> InventoryEvidence:
    policy = _policy_bytes(_single_policy(manifest), request.offers)
    material = InventoryRequest(policy, sources.inventory.inventory(), request.checked_at)
    return verify_inventory(material, root)


def _one_root(manifest: ReleaseManifest, artifacts: Mapping[str, bytes], root: TrustRoot) -> None:
    """Refuse when a stored FC7b verdict the manifest binds names another trust root."""
    for binding in manifest.bindings:
        stored = artifacts.get(binding.authorization_sha256 or "")
        if stored is None:
            continue
        try:
            verdict = parse_authorization(stored)
        except ForarbeiderRefusedError:
            continue
        if verdict.trust_root_sha256 != root.sha256:
            raise ForarbeiderRefusedError(
                RefusalReason.RELEASE_INVALID,
                f"trust root conflict for {binding.document_id}: the stored authorization "
                "was verified under another root",
            )


class _Controls(NamedTuple):
    limits: LimitsEvidence
    control: ControlEvidence
    ledger: LedgerEvidence


def _controls(
    request: ReleaseRequest, sources: ReleaseSources, manifest: ReleaseManifest, root: TrustRoot
) -> _Controls:
    at = request.checked_at
    policy = _single_policy(manifest)
    material = LimitsRequest(_policy_bytes(policy, request.offers), sources.limits.limits(), at)
    limits = verify_limits(material, root)
    history = ControlRequest(sources.control.history(), _policy_id(policy), at)
    ledger = LedgerRequest(sources.ledger.entries(), limits, at.date(), request.intent.release_id)
    return _Controls(limits, verify_control(history, root), verify_ledger(ledger))


def _single_policy(manifest: ReleaseManifest) -> PolicyReference | None:
    found = {d.policy for d in manifest.content.documents if d.policy is not None}
    return next(iter(found)) if len(found) == 1 else None


def _policy_id(policy: PolicyReference | None) -> str | None:
    return None if policy is None else policy.policy_id


def _policy_bytes(
    policy: PolicyReference | None, offers: tuple[DocumentOffer, ...]
) -> bytes | None:
    """The exact policy bytes an offer carries for the manifest's one policy, if any."""
    if policy is None:
        return None
    for offer in offers:
        offered = offer.authorization
        if (
            offered is not None
            and policy_digest(parse_policy(offered.policy)) == policy.policy_sha256
        ):
            return offered.policy
    return None


def reproduction_of(request: ReleaseRequest, root: TrustRoot) -> ReproductionEvidence:
    """The FC8b reproduction of the offered manifest under ``root``, sealed."""
    verification = verify_release(request.manifest, request.offers, request.artifacts, root)
    return issue_provenance(
        ReproductionEvidence(verification=verification), "manifest_reproduction"
    )


def registry_of(root: TrustRoot) -> SignerRegistry:
    """The trust root by digest, origin and key counts per role, sealed."""
    roles = [entry.role for entry in root.entries]
    registry = SignerRegistry(
        trust_root_sha256=root.sha256,
        origin=root.origin,
        owner_keys=roles.count("owner_policy"),
        bot_keys=roles.count("bot_execution"),
    )
    return issue_provenance(registry, "signer_registry")


def publisher_of(root: TrustRoot, principal: str) -> PublisherIdentity:
    """The publisher as the trust root lists it, sealed; an unlisted principal has no role."""
    entry = next((item for item in root.entries if item.principal == principal), None)
    identity = PublisherIdentity(
        principal=principal,
        key_fingerprint=None if entry is None else entry.fingerprint,
        role=None if entry is None else entry.role,
        namespaces=() if entry is None else tuple(sorted(entry.namespaces)),
    )
    return issue_provenance(identity, "publisher_identity")


def approvals_of(
    offers: tuple[DocumentOffer, ...], root: TrustRoot
) -> tuple[ApprovalEvidence, ...]:
    """Each offered policy's FC7b approvals, verified against ``root`` and sealed."""
    found = {
        issue_provenance(
            _approval(offer.authorization, verify_authorization(offer.authorization, root)),
            "policy_approval",
        )
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
