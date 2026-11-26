"""AR2 records: the owner-signed inventory of documents a release scope must contain (#738).

``SignedInventory`` is the owner's signed statement of **which documents one
policy's scope contains**: every document of its families on its source, each
by its stable upstream identity, with the URL it was discovered at. It binds:

- the exact policy by id, version and SHA-256 digest, its source provider and
  document types (the FC7b :class:`ApprovalScope`);
- an inventory id, a version and the time it states it was signed, with a
  validity window;
- the digests of the discovery evidence the owner reviewed (catalogue
  fetch-run records, §4.1), so the inventory names what it rests on;
- ``completeness: "complete"``: the owner attests that the list is the whole
  scope. There is no partial inventory; an incomplete one is not signed;
- the principal of the owner key that signs it.

It is signed in its own namespace, ``lovspor-forarbeider-inventory``, which the
trust root lets only an owner-role key hold: a bot key cannot sign an
inventory. Entries are sorted by document id, once each; every entry's type is
one of the scope's types and its URL names the same upstream id as its
document id. Extra fields are refused, so a record cannot carry a ``verified``
flag. The ``domain`` names the record kind, so its bytes never verify as
another kind.

**Why owner-signed, not discovered.** FC11 discovery from official catalogues
needs owner decision D7 and a verifier proving the catalogue's authenticity;
neither exists, so a catalogue never becomes an inventory on its own
(:mod:`lovspor.forarbeider.release_inventory`). Until then the owner signs the
inventory after reviewing the discovery evidence it names.
"""

import re
from typing import Literal, Self

from pydantic import Field, model_validator

from lovspor.forarbeider.identity import DocumentType
from lovspor.forarbeider.policy_authorization_models import ApprovalScope, Instant
from lovspor.forarbeider.version_models import Digest, Frozen

_Principal = Field(pattern=r"^[a-z0-9][a-z0-9._@-]{0,63}$")
_Id = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
_URL = re.compile(
    r"^https://www\.regjeringen\.no/no/dokumenter/[a-z0-9.-]+/id(?P<id>[1-9]\d{0,11})/$"
)


class InventoryEntry(Frozen):
    """One expected document: its stable id, its type and the URL it was discovered at."""

    document_id: str = Field(pattern=r"^regjeringen-[1-9]\d{0,11}$")
    document_type: DocumentType
    source_url: str

    @model_validator(mode="after")
    def _same_identity(self) -> Self:
        found = _URL.fullmatch(self.source_url)
        if found is None or f"regjeringen-{found['id']}" != self.document_id:
            raise ValueError("the source URL names the document's own upstream id")
        return self


class SignedInventory(Frozen):
    """The owner's signed inventory for one exact policy, in its own namespace."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_signed_inventory"] = "forarbeider_signed_inventory"
    domain: Literal["lovspor.forarbeider.document-inventory.v1"] = (
        "lovspor.forarbeider.document-inventory.v1"
    )
    inventory_id: str = _Id
    inventory_version: int = Field(ge=1)
    scope: ApprovalScope
    completeness: Literal["complete"]
    discovery_sha256s: tuple[Digest, ...] = Field(min_length=1)
    entries: tuple[InventoryEntry, ...] = Field(min_length=1)
    signer_principal: str = _Principal
    signed_at: Instant
    valid_from: Instant
    valid_until: Instant

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        ids = [item.document_id for item in self.entries]
        if ids != sorted(set(ids)):
            raise ValueError("entries are sorted by document id, once each")
        if list(self.discovery_sha256s) != sorted(set(self.discovery_sha256s)):
            raise ValueError("discovery digests are sorted, once each")
        if any(item.document_type not in self.scope.document_types for item in self.entries):
            raise ValueError("every entry is of a type the scope covers")
        if not self.valid_from < self.valid_until:
            raise ValueError("valid_from must precede valid_until")
        if self.signed_at >= self.valid_until:
            raise ValueError("an inventory cannot be signed after it expires")
        return self

    @property
    def document_ids(self) -> tuple[str, ...]:
        return tuple(item.document_id for item in self.entries)
