"""AR4b-2: recovery receipts, integrity and authorization verdicts (#738; §9.3a).

A receipt records what recovery established about one remote commit, from the
remote itself, as two separate verdicts:

- **integrity** (§9.3a 2a): is this commit, byte for byte, the transaction's
  publication, on the configured path, signed in the class that path requires?
  ``integrity_verified`` or ``integrity_failed``;
- **authorization** (§9.3a 2b): did the policy hold at the publication event?
  ``authorized``, ``unauthorized`` or ``authorization_unknown``. A valid commit
  never proves it was authorized, and missing evidence is never approval.

Since AR6b (``publication-receipt/2``) a receipt also names the digest of the
manifest signature **published with the commit** and the fingerprint of the
bot key it verified for; a verified publication always names both. Version 1
receipts were written only by simulations before AR6b and are refused on read
(no release has happened, so nothing real migrates).

The receipt is canonical and holds no time of its own making: every field
follows from verified facts (the remote, the trust root, the signed records,
the attempt records), so recovering twice writes nothing the second time.
"""

from typing import Final, Literal, Self

from pydantic import Field, model_validator

from lovspor.forarbeider.policy_authorization_models import Instant
from lovspor.forarbeider.publication_execution_models import CheckStatus
from lovspor.forarbeider.publication_models import CommitId
from lovspor.forarbeider.version_models import Digest, Frozen, NonBlank

RECEIPT_VERSION: Final = "publication-receipt/2"
#: How far apart two clocks may be before an ordering is called uncertain.
CLOCK_UNCERTAINTY_SECONDS: Final = 300

IntegrityName = Literal[
    "ancestry",
    "signature_class",
    "message",
    "files",
    "manifest",
    "ar6_release_manifest_signature",
]
#: Integrity checks a simulation may waive: none since AR6a verifies the manifest signature.
WAIVABLE_INTEGRITY: Final[frozenset[str]] = frozenset()
Integrity = Literal["integrity_verified", "integrity_failed"]
Authorization = Literal["authorized", "unauthorized", "authorization_unknown"]
Classification = Literal[
    "published_authorized",
    "published_authorization_unknown",
    "published_unauthorized",
    "halted_integrity_failed",
    "halted_foreign_commit",
]
_PUBLISHED: Final[dict[Authorization, Classification]] = {
    "authorized": "published_authorized",
    "authorization_unknown": "published_authorization_unknown",
    "unauthorized": "published_unauthorized",
}


class IntegrityCheck(Frozen):
    name: IntegrityName
    status: CheckStatus
    reasons: tuple[NonBlank, ...] = ()

    @model_validator(mode="after")
    def _explained(self) -> Self:
        if (self.status == "passed") == bool(self.reasons):
            raise ValueError("a check has reasons exactly when it did not pass")
        return self


def integrity_of(checks: tuple[IntegrityCheck, ...]) -> tuple[Integrity, tuple[str, ...]]:
    """The integrity verdict and the checks it waived; unavailable is never passed."""
    failed = any(c.status == "failed" for c in checks)
    unavailable = {c.name for c in checks if c.status == "unavailable"}
    if failed or unavailable - WAIVABLE_INTEGRITY:
        return "integrity_failed", ()
    return "integrity_verified", tuple(sorted(unavailable))


def classification_of(
    integrity: Integrity, authorization: Authorization, ours: bool
) -> Classification:
    if integrity == "integrity_verified":
        return _PUBLISHED[authorization]
    return "halted_integrity_failed" if ours else "halted_foreign_commit"


class RecoveryReceipt(Frozen):
    """One remote commit, verified and classified; append-only and hash-linked."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_publication_receipt"] = "forarbeider_publication_receipt"
    receipt_version: Literal["publication-receipt/2"] = RECEIPT_VERSION
    mode: Literal["simulation"] = "simulation"
    sequence: int = Field(ge=1)
    previous_sha256: Digest | None
    transaction_id: Digest
    attempt_id: Digest | None
    release_manifest_sha256: Digest
    path: Literal["A", "B"]
    base_commit: CommitId
    commit: CommitId
    parent: CommitId | None
    integrity: Integrity
    integrity_checks: tuple[IntegrityCheck, ...] = Field(min_length=1)
    waived: tuple[NonBlank, ...]
    authorization: Authorization
    authorization_reasons: tuple[NonBlank, ...]
    authorization_waived: tuple[NonBlank, ...]
    attempt_at: Instant | None
    remote_event_at: Instant | None
    ours: bool
    classification: Classification
    recovered_from_remote: bool
    #: AR6b: the digest of the manifest signature published with the commit, when one verified.
    manifest_signature_sha256: Digest | None
    #: AR6b: the fingerprint of the bot key that signature verified for.
    bot_fingerprint: NonBlank | None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.manifest_signature_sha256 is None) != (self.bot_fingerprint is None):
            raise ValueError("a verified manifest signature names its digest and its key")
        if self.published and self.bot_fingerprint is None:
            raise ValueError("a verified publication names the bot key that signed its manifest")
        if (self.sequence == 1) != (self.previous_sha256 is None):
            raise ValueError("only the first receipt has no previous receipt")
        if integrity_of(self.integrity_checks) != (self.integrity, self.waived):
            raise ValueError("the integrity verdict follows from its checks")
        if (self.authorization == "authorized") == bool(self.authorization_reasons):
            raise ValueError("an authorization has reasons exactly when it is not authorized")
        expected = classification_of(self.integrity, self.authorization, self.ours)
        if self.classification != expected:
            raise ValueError("the classification follows from the two verdicts")
        return self

    @property
    def published(self) -> bool:
        return self.integrity == "integrity_verified"
