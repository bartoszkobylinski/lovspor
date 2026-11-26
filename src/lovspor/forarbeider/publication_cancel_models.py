"""AR5d-3: the owner's instruction to cancel one abandoned reservation (#738; §9.9 AR5).

A ``ReservationCancellation`` is an owner-signed, single-use instruction, in the
namespace ``lovspor-forarbeider-control`` that only an owner-role key may hold
(a bot key can sign neither it nor a STOP or RESUME). It names exactly one
reservation and the evidence the owner looked at:

- the repository, the ledger, the transaction, its batch, its path and base;
- the SHA-256 of the reservation entry's exact bytes and of the ledger head;
- the SHA-256 of the transaction's newest attempt record (``None``: none);
- the workspace identity scope it is carried out from;
- a reason, the signer and a validity window of at most
  :data:`MAX_CANCELLATION_VALIDITY`.

There is no wildcard: every binding is required. Its own record type and
domain keep its signature from verifying as any other owner record, and any
other owner record from verifying as it. It authorizes nothing by itself:
:mod:`~lovspor.forarbeider.publication_cancel` cancels only when the evidence
it re-reads now proves that the publication did not happen.
"""

from datetime import timedelta
from typing import Final, Literal, Self

from pydantic import Field, model_validator

from lovspor.forarbeider.policy_authorization_models import Instant
from lovspor.forarbeider.publication_models import CommitId
from lovspor.forarbeider.version_models import Digest, Frozen

#: The longest an instruction may stay usable after it is signed.
MAX_CANCELLATION_VALIDITY: Final = timedelta(hours=24)
#: The reason an attempt record carries when it records an owner cancellation.
CANCELLED: Final = "reservation_cancelled_by_owner"
_Id = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
_Principal = Field(pattern=r"^[a-z0-9][a-z0-9._@-]{0,63}$")


class ReservationCancellation(Frozen):
    """One owner-signed cancellation of one open reservation (namespace ``...-control``)."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_reservation_cancellation"] = (
        "forarbeider_reservation_cancellation"
    )
    domain: Literal["lovspor.forarbeider.reservation-cancellation.v1"] = (
        "lovspor.forarbeider.reservation-cancellation.v1"
    )
    operation: Literal["cancel_abandoned_reservation"] = "cancel_abandoned_reservation"
    instruction_id: str = _Id
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    ledger_id: str = _Id
    transaction_id: Digest
    batch_id: str = Field(pattern=r"^batch-[0-9a-f]{56}$")
    path: Literal["A", "B"]
    base_commit: CommitId
    reservation_sha256: Digest
    ledger_head_sha256: Digest
    attempts_head_sha256: Digest | None
    workspace_scope: str = Field(pattern=r"^workspace:[0-9a-f]{64}$")
    reason: str = Field(pattern=r"\S", max_length=500)
    signer_principal: str = _Principal
    signed_at: Instant
    valid_until: Instant

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        if self.valid_until <= self.signed_at:
            raise ValueError("an instruction expires after it is signed")
        if self.valid_until - self.signed_at > MAX_CANCELLATION_VALIDITY:
            raise ValueError("an instruction holds for at most a day")
        return self
