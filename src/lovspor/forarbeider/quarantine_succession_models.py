"""AR5d-4: one anchored successor of the AR3 quarantine ledger and its proof (#738; §9.9 AR5).

A :class:`QuarantineSuccession` is one entry of an append-only chain, anchored
as the ``quarantine_succession`` family in the scope of one ledger id. It
carries everything a reader needs to re-prove the transition on its own:

- the predecessor's digest (and, for the first entry that continues a
  document anchored under the AR5a ``quarantine_ledger`` family, the
  predecessor document itself);
- the successor document, whose digest it also names;
- the exact signed owner exceptions and revocations the successor relies on,
  as offered (record bytes and detached signature), and the digest of the
  policy they were judged under;
- the time the transition was judged at.

Nothing in an entry is trusted: no "cleared" flag, no verdict, no assessment.
:mod:`~lovspor.forarbeider.quarantine_succession` re-verifies every
signature against the trust root and replays the AR3 engine.
"""

from hashlib import sha256
from typing import Literal, Self

from pydantic import Field, model_validator

from lovspor.forarbeider.policy_authorization import SignedRecord
from lovspor.forarbeider.policy_authorization_models import Instant
from lovspor.forarbeider.release_quarantine_models import QuarantineLedger
from lovspor.forarbeider.version_models import Digest, Frozen, NonBlank, canonical_text

#: How a chain's first entry knows its predecessor.
Basis = Literal["empty", "anchored_document", "succession"]


def document_sha256(ledger: QuarantineLedger) -> str:
    """The digest of the ledger's canonical bytes (AR3 ``ledger_sha256``)."""
    return sha256(canonical_text(ledger).encode()).hexdigest()


class SignedMaterial(Frozen):
    """An owner record's exact bytes and its detached signature, as offered."""

    record: NonBlank
    signature: str | None

    def signed(self) -> SignedRecord:
        signature = None if self.signature is None else self.signature.encode()
        return SignedRecord(self.record.encode(), signature)


class QuarantineSuccession(Frozen):
    """One successor of a quarantine ledger, with the evidence that proves it."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_quarantine_succession"] = "forarbeider_quarantine_succession"
    domain: Literal["lovspor.forarbeider.quarantine-succession.v1"] = (
        "lovspor.forarbeider.quarantine-succession.v1"
    )
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    ledger_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
    sequence: int = Field(ge=1)
    previous_succession_sha256: Digest | None
    basis: Basis
    predecessor_sha256: Digest
    predecessor: QuarantineLedger | None
    successor_sha256: Digest
    successor: QuarantineLedger
    policy_sha256: Digest | None
    exceptions: tuple[SignedMaterial, ...] = ()
    revocations: tuple[SignedMaterial, ...] = ()
    recorded_at: Instant

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.sequence == 1) != (self.previous_succession_sha256 is None):
            raise ValueError("only the first entry has no previous entry")
        if (self.sequence == 1) == (self.basis == "succession"):
            raise ValueError("the first entry starts a chain; every later one succeeds it")
        if (self.basis == "anchored_document") != (self.predecessor is not None):
            raise ValueError("only a continued anchored document carries its predecessor")
        if self.predecessor is not None and document_sha256(self.predecessor) != (
            self.predecessor_sha256
        ):
            raise ValueError("predecessor_sha256 is the predecessor's digest")
        if document_sha256(self.successor) != self.successor_sha256:
            raise ValueError("successor_sha256 is the successor's digest")
        if (self.exceptions or self.revocations) and self.policy_sha256 is None:
            raise ValueError("exceptions are judged under a named policy")
        return self


def succession_bytes(entry: QuarantineSuccession) -> bytes:
    return canonical_text(entry).encode()
