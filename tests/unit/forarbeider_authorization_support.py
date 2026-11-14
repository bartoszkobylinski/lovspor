"""Throwaway SSH keys, trust roots and signed records for the FC7b tests (#738).

Every key is generated in pytest's temporary directory and dies with it. None
stands for the owner's or the bot's key, and none is ever written into the
engine's shipped trust root (``src/lovspor/forarbeider/trust``).
"""

import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from lovspor.forarbeider.policy_authorization import AuthorizationRequest, SignedRecord
from lovspor.forarbeider.policy_authorization_models import (
    ApprovalRevocation,
    ApprovalScope,
    AutomatedPublicationApproval,
    PolicyApproval,
)
from lovspor.forarbeider.policy_trust import (
    POLICY_NAMESPACE,
    PUBLICATION_NAMESPACE,
    RELEASE_NAMESPACE,
    REVOKED_FILE,
    SIGNERS_FILE,
    TrustRoot,
    load_trust_root,
)
from lovspor.forarbeider.publication_policy import (
    PolicyProvenance,
    PublicationPolicy,
    RightsRule,
    policy_digest,
    policy_text,
)
from lovspor.forarbeider.span_assessment_models import Evidence, RightsClass
from lovspor.forarbeider.version_models import canonical_text

SIGNED_AT = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
CHECKED_AT = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
VALID_FROM = datetime(2026, 11, 14, 0, 0, tzinfo=UTC)
VALID_UNTIL = datetime(2027, 11, 14, 0, 0, tzinfo=UTC)
KEY_AFTER = "20260101000000Z"
KEY_BEFORE = "20300101000000Z"
OWNER_NAMESPACES = POLICY_NAMESPACE
BOT_NAMESPACES = f"{RELEASE_NAMESPACE},{PUBLICATION_NAMESPACE}"


@dataclass(frozen=True)
class Key:
    path: Path
    public: str


def new_key(directory: Path, name: str) -> Key:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(path)],
        capture_output=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    public = path.with_name(f"{name}.pub").read_text(encoding="utf-8").split()
    return Key(path, f"{public[0]} {public[1]}")


def signer_line(
    principal: str, key: Key, namespaces: str, window: tuple[str, str] | None = None
) -> str:
    after, before = window or (KEY_AFTER, KEY_BEFORE)
    options = f'namespaces="{namespaces}",valid-after="{after}",valid-before="{before}"'
    return f"{principal} {options} {key.public}"


def root_dir(directory: Path, lines: list[str], revoked: tuple[Key, ...] = ()) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / SIGNERS_FILE).write_text("".join(f"{line}\n" for line in lines), "ascii")
    (directory / REVOKED_FILE).write_text("".join(f"{k.public}\n" for k in revoked), "ascii")
    return directory


def trust_root(directory: Path, lines: list[str], revoked: tuple[Key, ...] = ()) -> TrustRoot:
    return load_trust_root(root_dir(directory, lines, revoked))


def sign(key: Key, data: bytes, namespace: str = POLICY_NAMESPACE) -> bytes:
    done = subprocess.run(
        ["ssh-keygen", "-Y", "sign", "-q", "-f", str(key.path), "-n", namespace],
        input=data,
        capture_output=True,
        check=True,
    )
    return done.stdout


def policy(**changes: Any) -> PublicationPolicy:
    fields: dict[str, Any] = {
        "policy_id": "synthetic-policy",
        "policy_version": 1,
        "document_types": ("prop-l",),
        "rights_rules": (
            RightsRule(
                rights_class=RightsClass.OFFICIAL_TEXT, accepted_evidence=(Evidence.STRUCTURE,)
            ),
            RightsRule(
                rights_class=RightsClass.STATUTE_TEXT, accepted_evidence=(Evidence.CHAPTER_CONTEXT,)
            ),
        ),
        "attachment_rules": (),
        "provenance": PolicyProvenance(
            drafted_by="synthetic test", drafted_on=date(2100, 1, 1), basis=("synthetic",)
        ),
    }
    return PublicationPolicy.model_validate({**fields, **changes})


def raw(record: BaseModel) -> bytes:
    return canonical_text(record).encode()


def scope(chosen: PublicationPolicy, **changes: Any) -> ApprovalScope:
    fields = {
        "policy_id": chosen.policy_id,
        "policy_version": chosen.policy_version,
        "policy_sha256": policy_digest(chosen),
        "document_types": chosen.document_types,
    }
    return ApprovalScope.model_validate({**fields, **changes})


def approval(chosen: PublicationPolicy, **changes: Any) -> PolicyApproval:
    fields = {
        "approval_id": "synthetic-approval-1",
        "scope": scope(chosen),
        "signed_at": SIGNED_AT,
        "valid_from": VALID_FROM,
        "valid_until": VALID_UNTIL,
    }
    return PolicyApproval.model_validate({**fields, **changes})


def automated(
    chosen: PublicationPolicy, approval_raw: bytes, **changes: Any
) -> AutomatedPublicationApproval:
    fields = {
        "approval_id": "synthetic-automation-1",
        "scope": scope(chosen),
        "policy_approval_sha256": sha256(approval_raw).hexdigest(),
        "max_documents_per_batch": 10,
        "legal_assessment_reference": "synthetic-reference-not-an-assessment",
        "signed_at": SIGNED_AT,
        "valid_from": VALID_FROM,
        "valid_until": VALID_UNTIL,
    }
    return AutomatedPublicationApproval.model_validate({**fields, **changes})


def revocation(*ids: str, **changes: Any) -> ApprovalRevocation:
    fields = {"revocation_id": "synthetic-revocation-1", "revokes": ids, "signed_at": SIGNED_AT}
    return ApprovalRevocation.model_validate({**fields, **changes})


def signed(record: BaseModel | bytes, key: Key, namespace: str = POLICY_NAMESPACE) -> SignedRecord:
    data = record if isinstance(record, bytes) else raw(record)
    return SignedRecord(data, sign(key, data, namespace))


def full_request(chosen: PublicationPolicy, owner: Key, **changes: Any) -> AuthorizationRequest:
    """A request with an owner-signed approval and automated-publication approval."""
    approved = signed(approval(chosen), owner)
    fields: dict[str, Any] = {
        "policy": policy_text(chosen).encode(),
        "checked_at": CHECKED_AT,
        "policy_approval": approved,
        "automated_publication": signed(automated(chosen, approved.record), owner),
    }
    return AuthorizationRequest(**{**fields, **changes})
