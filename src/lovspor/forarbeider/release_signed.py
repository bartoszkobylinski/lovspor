"""AR1b: one owner-signed record, verified against the trust root (#738; D6, §9.1).

The signed limits and the stop/resume records reuse FC7b's mechanism: exact
canonical bytes, a detached OpenSSH signature, ``ssh-keygen -Y verify``
against the trust root and its revocation list (:func:`check_signature`).
On top of FC7b's checks, a record verified here must also:

- be signed in its own namespace by a key holding the owner role;
- name, in ``signer_principal``, the principal of the key that signed it
  (records without that field, such as FC7b revocations, skip this check);
- be signed no later than the check time;
- come from a key still inside its validity window **at the check time**, not
  only when it was signed, so an expired key cannot keep a record alive.

Nothing here trusts a status a caller sends; every outcome is recomputed.
"""

from datetime import datetime
from hashlib import sha256
from typing import NamedTuple, Protocol

from pydantic import BaseModel, ValidationError

from lovspor.forarbeider.corpus_models import _loaded
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_authorization import SignedRecord
from lovspor.forarbeider.policy_authorization_models import CheckStatus
from lovspor.forarbeider.policy_trust import (
    SignatureCheck,
    SignedBytes,
    SignerEntry,
    TrustRoot,
    check_signature,
)
from lovspor.forarbeider.version_models import canonical_text


class _Stamped(Protocol):
    @property
    def signed_at(self) -> datetime: ...


class RecordSpec(NamedTuple):
    """What a record must be: its model and the namespace it is signed in."""

    model: type[BaseModel]
    namespace: str


class OwnerCheck(NamedTuple):
    """One record's outcome: its status, every reason, and on success the record."""

    status: CheckStatus
    reasons: tuple[str, ...]
    record: BaseModel | None = None
    entry: SignerEntry | None = None
    record_sha256: str | None = None


def verify_owner_record(
    root: TrustRoot, offered: SignedRecord, spec: RecordSpec, at: datetime
) -> OwnerCheck:
    """Whether ``offered`` is a canonical ``spec`` record the owner signed, as of ``at``."""
    record, refusal = parsed(spec.model, offered.record)
    if record is None:
        return OwnerCheck("invalid", (refusal,))
    if offered.signature is None:
        return OwnerCheck("missing", ("signature_missing",))
    stamped: _Stamped = record  # type: ignore[assignment]
    found = check_signature(
        root, SignedBytes(offered.record, offered.signature), spec.namespace, stamped.signed_at
    )
    reasons = _refusals(found, record, at)
    if reasons:
        return OwnerCheck("invalid", reasons, entry=found.entry)
    return OwnerCheck("verified", (), record, found.entry, sha256(offered.record).hexdigest())


def parsed(model: type[BaseModel], raw: bytes) -> tuple[BaseModel | None, str]:
    """The record in ``raw`` if it is exactly its canonical bytes, else a reason."""
    try:
        record = model.model_validate(_loaded(raw))
    except (ValidationError, ForarbeiderRefusedError):
        return None, "record_malformed"
    if canonical_text(record).encode() != raw:
        return None, "record_noncanonical"
    return record, ""


def _refusals(found: SignatureCheck, record: BaseModel, at: datetime) -> tuple[str, ...]:
    entry = found.entry
    if not found.verified or entry is None:
        return (found.reason,)
    reasons = []
    if entry.role != "owner_policy":
        reasons.append("signer_not_owner")
    principal = getattr(record, "signer_principal", None)
    if principal is not None and principal != entry.principal:
        reasons.append("signer_principal_mismatch")
    stamped: _Stamped = record  # type: ignore[assignment]
    if stamped.signed_at > at:
        reasons.append("signed_after_check_time")
    if not entry.valid_after <= at < entry.valid_before:
        reasons.append("signer_key_expired")
    return tuple(reasons)
