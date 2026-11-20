"""AR2: fail-closed verification of the owner-signed document inventory (#738; §4.1, §9.9).

:func:`verify_inventory` takes the exact bytes of the policy, the inventory
records, revocations and catalogue pages a source offers, a check time and the
trust root. It trusts none of them. A record is verified only when it is
canonical, signed in the inventory namespace by an owner-role key the root
lists, unrevoked, inside the key's window at both the signing and the check
time (:func:`verify_owner_record`), names the key's principal, binds exactly
this policy's id, version, digest, source and document types (FC7b's scope
check), is inside its own validity window and is not withdrawn by a verified
revocation.

The evidence is:

- ``missing`` when there is no policy or no signed record. Catalogue pages
  alone stay ``missing`` (``catalogue_verifier_not_implemented``): FC11
  discovery has no verifier of a catalogue's authenticity and D7 is open, so
  no inventory is ever built from them here;
- ``invalid`` when any record or revocation fails, or when more than one
  record verifies (``inventory_conflict``: the engine never picks one). The
  owner replaces an inventory by revoking it: a record withdrawn by a verified
  revocation gives way to the one left, and is ``inventory_revoked`` only when
  nothing else is offered;
- ``verified`` with exactly one record, sealed as ``document_inventory``.

No caller's inventory, completeness or status is accepted: the expected
documents are exactly the entries of the one verified record.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import NamedTuple

from lovspor.forarbeider.policy_authorization import SignedRecord, _scope_reasons
from lovspor.forarbeider.policy_authorization_models import ApprovalRevocation
from lovspor.forarbeider.policy_trust import INVENTORY_NAMESPACE, POLICY_NAMESPACE, TrustRoot
from lovspor.forarbeider.publication_policy import PublicationPolicy, parse_policy, policy_digest
from lovspor.forarbeider.release_contract_models import (
    DocumentInventory,
    InventoryEvidence,
    InventoryScope,
)
from lovspor.forarbeider.release_inventory_models import SignedInventory
from lovspor.forarbeider.release_limits import utc
from lovspor.forarbeider.release_provenance import issue_provenance
from lovspor.forarbeider.release_signed import OwnerCheck, RecordSpec, verify_owner_record
from lovspor.forarbeider.version_release_models import PolicyReference

_INVENTORY = RecordSpec(SignedInventory, INVENTORY_NAMESPACE)
_REVOCATION = RecordSpec(ApprovalRevocation, POLICY_NAMESPACE)


class InventoryMaterial(NamedTuple):
    """What an inventory source offers: signed records, revocations, catalogue pages."""

    records: tuple[SignedRecord, ...] = ()
    revocations: tuple[SignedRecord, ...] = ()
    catalogue: tuple[bytes, ...] = ()


class InventoryRequest(NamedTuple):
    policy: bytes | None
    material: InventoryMaterial
    checked_at: datetime


class _Context(NamedTuple):
    root: TrustRoot
    policy: PublicationPolicy
    at: datetime
    revoked: frozenset[str]


def verify_inventory(request: InventoryRequest, root: TrustRoot) -> InventoryEvidence:
    """The inventory evidence for ``request``; never trusts a list or status it is given.

    Raises:
        ForarbeiderRefusedError: ``invalid_timestamp`` when the check time is not
            UTC; ``schema_invalid`` when the policy bytes are not a policy.
    """
    at = utc(request.checked_at)
    material = request.material
    if request.policy is None:
        return _missing(("inventory_policy_unavailable",))
    if not material.records:
        return _missing(_absent(material))
    revocations = [verify_owner_record(root, r, _REVOCATION, at) for r in material.revocations]
    ctx = _Context(root, parse_policy(request.policy), at, _revoked_ids(revocations))
    checks = [_inventory(ctx, offered) for offered in material.records]
    unverified = (
        ["revocation_not_verified"] if any(c.status != "verified" for c in revocations) else []
    )
    return _verdict(ctx, checks, unverified)


def _absent(material: InventoryMaterial) -> tuple[str, ...]:
    if material.catalogue:
        return ("catalogue_verifier_not_implemented",)
    return ("signed_inventory_missing",)


def _missing(reasons: tuple[str, ...]) -> InventoryEvidence:
    return InventoryEvidence(
        status="missing", source=None, inventory=None, inventory_sha256=None, reasons=reasons
    )


def _invalid(reasons: Iterable[str]) -> InventoryEvidence:
    return InventoryEvidence(
        status="invalid",
        source=None,
        inventory=None,
        inventory_sha256=None,
        reasons=tuple(sorted(set(reasons))),
    )


def _revoked_ids(revocations: Iterable[OwnerCheck]) -> frozenset[str]:
    ids: set[str] = set()
    for check in revocations:
        if isinstance(check.record, ApprovalRevocation):
            ids.update(check.record.revokes)
    return frozenset(ids)


def _inventory(ctx: _Context, offered: SignedRecord) -> OwnerCheck:
    check = verify_owner_record(ctx.root, offered, _INVENTORY, ctx.at)
    record = check.record
    if not isinstance(record, SignedInventory):
        return check
    reasons = _scope_reasons(record.scope, ctx.policy)
    if ctx.at < record.valid_from:
        reasons.append("inventory_not_yet_valid")
    if ctx.at >= record.valid_until:
        reasons.append("inventory_expired")
    if record.inventory_id in ctx.revoked:
        reasons.append("inventory_revoked")
    return (
        check._replace(status="invalid", reasons=tuple(reasons), record=None) if reasons else check
    )


def _verdict(ctx: _Context, checks: list[OwnerCheck], extra: list[str]) -> InventoryEvidence:
    """One verified record wins; a verifiably revoked one gives way, unless it is the only one."""
    live = [c for c in checks if c.reasons != ("inventory_revoked",)]
    if live:
        checks = live
    good = [c for c in checks if c.status == "verified"]
    reasons = set(extra) | {r for c in checks for r in c.reasons}
    if len(good) > 1:
        reasons.add("inventory_conflict")
    if extra or len(good) > 1 or any(c.status == "invalid" for c in checks):
        return _invalid(reasons)
    if not good:
        return _missing(tuple(sorted(reasons)))
    return _sealed(ctx, good[0])


def _sealed(ctx: _Context, check: OwnerCheck) -> InventoryEvidence:
    record, entry = check.record, check.entry
    if not isinstance(record, SignedInventory) or entry is None:
        return _missing(("inventory_unverifiable",))
    evidence = InventoryEvidence(
        status="verified",
        source="owner_signed_inventory",
        reasons=(),
        inventory=_expected(record),
        inventory_sha256=check.record_sha256,
        inventory_version=record.inventory_version,
        policy=_reference(ctx.policy),
        discovery_sha256s=record.discovery_sha256s,
        trust_root_sha256=ctx.root.sha256,
        signer_fingerprint=entry.fingerprint,
        signer_role=entry.role,
    )
    return issue_provenance(evidence, "document_inventory")


def _expected(record: SignedInventory) -> DocumentInventory:
    return DocumentInventory(
        inventory_id=record.inventory_id,
        scope=InventoryScope(document_types=record.scope.document_types),
        document_ids=record.document_ids,
    )


def _reference(policy: PublicationPolicy) -> PolicyReference:
    return PolicyReference(
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_sha256=policy_digest(policy),
    )
