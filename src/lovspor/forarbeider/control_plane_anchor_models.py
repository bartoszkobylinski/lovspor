"""AR5a: the canonical anchor of one control-plane record family (#738; §9.3a, §9.9 AR5).

An anchor is a statement, signed by an **anchor witness** outside the runner,
that one record family in one scope had a given canonical head:

- the family and the schema of its records; the repository and the scope (a
  transaction, a policy, a ledger or the whole workspace);
- the digest of the chain head, the number of records and the last sequence;
- the anchor's own place in the witness's chain: its sequence and the digest
  of the anchor before it;
- the verification status the runner established before asking for it.

The records stay the source of truth; an anchor holds no record content, only
the digest a verifier compares them with. The witness signs the canonical
bytes of the anchor in the ``lovspor-forarbeider-anchor`` namespace with a key
whose only role is ``anchor_witness``; a hash alone authenticates nothing.
"""

from hashlib import sha256
from typing import Final, Literal, Self

from pydantic import Field, model_validator

from lovspor.forarbeider.version_models import Digest, Frozen, NonBlank, canonical_text

ANCHOR_VERSION: Final = "control-plane-anchor/1"

Family = Literal[
    "publication_attempts",
    "recovery_receipts",
    "stop_latch",
    "budget_ledger",
    "control_history",
    "quarantine_ledger",
]
ScopeKind = Literal["transaction", "workspace", "ledger", "policy"]

#: Each family, the schema of its records and the kind of scope it is anchored in.
FAMILIES: Final[dict[Family, tuple[str, ScopeKind]]] = {
    "publication_attempts": ("forarbeider_publication_attempt/1", "transaction"),
    "recovery_receipts": ("publication-receipt/1", "workspace"),
    "stop_latch": ("forarbeider_publication_stop_latch/1", "workspace"),
    "budget_ledger": ("forarbeider_budget_ledger_entry/1", "ledger"),
    "control_history": ("forarbeider_release_control/1", "policy"),
    "quarantine_ledger": ("quarantine/1", "ledger"),
}

Status = Literal[
    "verified",
    "unanchored",
    "pending_anchor",
    "unanchored_tail",
    "local_rolled_back",
    "diverged",
    "local_invalid",
    "anchor_invalid",
    "anchor_rolled_back",
    "store_unavailable",
    "store_not_independent",
]


class ControlPlaneAnchor(Frozen):
    """One family's canonical head in one scope, as the runner verified it."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_control_plane_anchor"] = "forarbeider_control_plane_anchor"
    anchor_version: Literal["control-plane-anchor/1"] = ANCHOR_VERSION
    family: Family
    family_schema: NonBlank
    repository: NonBlank
    scope_kind: ScopeKind
    scope: NonBlank
    head_sha256: Digest | None
    record_count: int = Field(ge=0)
    last_sequence: int = Field(ge=0)
    anchor_sequence: int = Field(ge=1)
    previous_anchor_sha256: Digest | None
    verification: Literal["verified"] = "verified"

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        schema, kind = FAMILIES[self.family]
        if (self.family_schema, self.scope_kind) != (schema, kind):
            raise ValueError("family_schema and scope_kind are the family's")
        if (self.record_count == 0) != (self.head_sha256 is None):
            raise ValueError("only an empty family has no head")
        if (self.anchor_sequence == 1) != (self.previous_anchor_sha256 is None):
            raise ValueError("only the first anchor has no previous anchor")
        return self


class WitnessedAnchor(Frozen):
    """An anchor and the witness's detached SSH signature over its canonical bytes."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_witnessed_anchor"] = "forarbeider_witnessed_anchor"
    anchor: ControlPlaneAnchor
    signature: NonBlank


def anchor_bytes(anchor: ControlPlaneAnchor) -> bytes:
    """The exact bytes the witness signs and the next anchor's digest covers."""
    return canonical_text(anchor).encode()


def anchor_digest(anchor: ControlPlaneAnchor) -> str:
    return sha256(anchor_bytes(anchor)).hexdigest()
