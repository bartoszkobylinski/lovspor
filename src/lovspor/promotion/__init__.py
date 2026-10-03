"""Promotion of observed local regulations into ``lovverk`` (ADR-0016).

Slice S1 is the identity layer: id minting, title normalisation, the content
hash and the authority block. Slice S2 adds the extractor (captured
HTML/PDF/DOCX bytes to identification block, body and pre-filled fields, or a
typed hold) and the personal-data gate. All of it is pure
— it reads no archive, writes no corpus and is not yet wired to the
observatory, a CLI or MCP; those are later slices. When the writer exists,
this package is the only one that writes ``lovverk/lokale-forskrifter/``
(ADR-0016 4a).
"""

from lovspor.promotion.extract import EXTRACTOR_VERSION, extract_regulation
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
    ExtractedDocument,
    ExtractedRegulation,
    ExtractionHoldReason,
    ExtractionResult,
    HeldExtraction,
    HeldIdentity,
    HoldReason,
    IdentityResult,
    IdScheme,
    MintedIdentity,
    PersonalDataHit,
    PersonalDataKind,
    RegulationFields,
    SourceForm,
)
from lovspor.promotion.personal_data import screen_personal_data

__all__ = [
    "EXTRACTOR_VERSION",
    "Authority",
    "AuthorityType",
    "ExtractedDocument",
    "ExtractedRegulation",
    "ExtractionHoldReason",
    "ExtractionResult",
    "HeldExtraction",
    "HeldIdentity",
    "HoldReason",
    "IdScheme",
    "IdentityResult",
    "MintedIdentity",
    "PersonalDataHit",
    "PersonalDataKind",
    "RegulationFields",
    "SourceForm",
    "content_hash",
    "extract_regulation",
    "find_identification_ids",
    "mint_identity",
    "normalise_text",
    "normalise_title",
    "screen_personal_data",
]
