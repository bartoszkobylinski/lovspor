"""Promotion of observed local regulations into ``lovverk`` (ADR-0016).

Slice S1 is the identity layer alone: id minting, title normalisation, the
content hash and the authority block. It is pure — it reads no archive,
writes no corpus and is not yet wired to the observatory, the renderer or
MCP; those are later slices. When the writer exists, this package is the
only one that writes ``lovverk/lokale-forskrifter/`` (ADR-0016 4a).
"""

from lovspor.promotion.identity import (
    content_hash,
    find_identification_ids,
    mint_identity,
    normalise_text,
    normalise_title,
)
from lovspor.promotion.models import (
    Authority,
    AuthorityType,
    ExtractedRegulation,
    HeldIdentity,
    HoldReason,
    IdentityResult,
    IdScheme,
    MintedIdentity,
)

__all__ = [
    "Authority",
    "AuthorityType",
    "ExtractedRegulation",
    "HeldIdentity",
    "HoldReason",
    "IdScheme",
    "IdentityResult",
    "MintedIdentity",
    "content_hash",
    "find_identification_ids",
    "mint_identity",
    "normalise_text",
    "normalise_title",
]
