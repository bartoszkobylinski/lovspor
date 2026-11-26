"""AR1b records: owner-signed batch limits and daily budgets (#738; §9.3, §9.9 AR1).

``ReleaseLimits`` is the owner's signed statement of how much one policy may
release automatically: the batch caps (documents, files, bytes) and the daily
budgets (documents, files, bytes, commits). It binds:

- the exact policy by id, version and SHA-256 digest, its source provider and
  document types (the FC7b :class:`ApprovalScope`);
- a record id, the time it states it was signed and a validity window;
- the principal of the owner key that signs it.

It is signed in its own namespace, ``lovspor-forarbeider-limits``, which the
trust root lets only an owner-role key hold: a bot key cannot sign limits, so
it cannot raise its own. The ``domain`` names the record kind, so its bytes
never verify as another kind. Extra fields are refused, so a record cannot
carry a ``verified`` or ``approved`` flag. The batch caps can never exceed
§9.3's hard bounds.
"""

from typing import Final, Literal, Self

from pydantic import Field, model_validator

from lovspor.forarbeider.policy_authorization_models import ApprovalScope, Instant
from lovspor.forarbeider.version_models import Frozen

#: The hard batch bounds of §9.3 step 2 (recommended answer, pending).
MAX_BATCH_DOCUMENTS: Final = 100
MAX_BATCH_FILES: Final = 100
MAX_BATCH_BYTES: Final = 25_000_000
_Principal = Field(pattern=r"^[a-z0-9][a-z0-9._@-]{0,63}$")
_Id = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")


class ReleaseConstraints(Frozen):
    """Batch caps and daily budgets; the batch caps never exceed §9.3's."""

    max_documents_per_batch: int = Field(ge=1, le=MAX_BATCH_DOCUMENTS)
    max_files_per_batch: int = Field(ge=1, le=MAX_BATCH_FILES)
    max_bytes_per_batch: int = Field(ge=1, le=MAX_BATCH_BYTES)
    max_documents_per_day: int = Field(ge=0)
    max_files_per_day: int = Field(ge=0)
    max_bytes_per_day: int = Field(ge=0)
    max_commits_per_day: int = Field(ge=0)


class ReleaseLimits(Frozen):
    """The owner's signed limits for one exact policy (namespace ``lovspor-forarbeider-limits``)."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_release_limits"] = "forarbeider_release_limits"
    domain: Literal["lovspor.forarbeider.release-limits.v1"] = (
        "lovspor.forarbeider.release-limits.v1"
    )
    limits_id: str = _Id
    scope: ApprovalScope
    constraints: ReleaseConstraints
    signer_principal: str = _Principal
    signed_at: Instant
    valid_from: Instant
    valid_until: Instant

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not self.valid_from < self.valid_until:
            raise ValueError("valid_from must precede valid_until")
        if self.signed_at >= self.valid_until:
            raise ValueError("limits cannot be signed after they expire")
        return self
