"""AR1b records: owner-signed stop and resume controls (#738; §9.9 AR1, AR5).

A ``ReleaseControl`` is one link in a single, linear, owner-signed chain:

- ``action`` is ``stop`` or ``resume``; ``scope`` names one policy or, with
  ``policy_id`` ``None``, every policy;
- ``sequence`` starts at 1 and rises by one; ``previous_control_sha256`` is
  the SHA-256 of the exact bytes of the record before it (``None`` only for
  the first), so records cannot be reordered, dropped from the middle or
  spliced in from another chain;
- a ``resume`` names the stop it clears by the SHA-256 of that stop's bytes
  (``resumes_control_sha256``), or ``None`` for the initial stop every chain
  begins in. It never clears a stop it does not name. It is a lease: it holds
  only until ``valid_until``, at most :data:`MAX_RESUME_VALIDITY` after it was
  signed, so a resume goes stale and cannot be reused indefinitely;
- ``signer_principal`` names the owner key that signs it, in the namespace
  ``lovspor-forarbeider-control`` that only an owner-role key may hold. The
  bot cannot sign a control record, so it can neither stop nor resume itself.

There is no field for a state: the state is recomputed from the chain, and
anything missing, conflicting, reordered or unverifiable means stopped.
"""

from datetime import timedelta
from typing import Final, Literal, Self

from pydantic import Field, model_validator

from lovspor.forarbeider.policy_authorization_models import Instant
from lovspor.forarbeider.version_models import Digest, Frozen

#: The longest a resume may hold before the owner must sign a fresh one.
MAX_RESUME_VALIDITY: Final = timedelta(days=7)
_Principal = Field(pattern=r"^[a-z0-9][a-z0-9._@-]{0,63}$")
_Id = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")


class ControlScope(Frozen):
    """One policy by id, or every policy when ``policy_id`` is ``None``."""

    policy_id: str | None = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")

    def covers(self, policy_id: str | None) -> bool:
        return self.policy_id is None or self.policy_id == policy_id


class ReleaseControl(Frozen):
    """One owner-signed stop or resume (namespace ``lovspor-forarbeider-control``)."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_release_control"] = "forarbeider_release_control"
    domain: Literal["lovspor.forarbeider.release-control.v1"] = (
        "lovspor.forarbeider.release-control.v1"
    )
    control_id: str = _Id
    action: Literal["stop", "resume"]
    scope: ControlScope
    reason: str = Field(pattern=r"\S", max_length=500)
    sequence: int = Field(ge=1)
    previous_control_sha256: Digest | None
    resumes_control_sha256: Digest | None
    signer_principal: str = _Principal
    signed_at: Instant
    valid_until: Instant | None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.sequence == 1) != (self.previous_control_sha256 is None):
            raise ValueError("only the first record has no previous record")
        if self.action == "stop":
            if self.resumes_control_sha256 is not None or self.valid_until is not None:
                raise ValueError("a stop names no target and has no expiry")
            return self
        if self.valid_until is None or self.valid_until <= self.signed_at:
            raise ValueError("a resume expires after it is signed")
        if self.valid_until - self.signed_at > MAX_RESUME_VALIDITY:
            raise ValueError("a resume holds for at most a week")
        return self
