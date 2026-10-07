"""Promotion of observed local regulations into ``lovverk`` (ADR-0016).

Slice S1 is the identity layer: id minting, title normalisation, the content
hash and the authority block. Slice S2 adds the extractor (captured
HTML/PDF/DOCX bytes to identification block, body and pre-filled fields, or a
typed hold), the personal-data gate and the local renderer, all pure. Slice
S3 adds the writer, ``lovspor promote`` (``commands.py``): one artifact read
from the observatory archive (``archive.py``), a human decision in the
append-only decision log beside it (``decisions.py``), the version placed and
rendered (``plan.py``) and written into a ``lovverk`` checkout (``corpus.py``,
``writer.py``), and its history derived from the checkout's log
(``local_history.py``). This package is the only writer of
``lovverk/lokale-forskrifter/`` (ADR-0016 4a); MCP serving is a later slice.
Slice S6 reads every version of a document from its primary URL's
observations (``versions.py``) and re-reads the promoted versions'
observation intervals from the log (``intervals.py``); ``backfill.py`` writes
them one approved version per run, in order, and ``backfill_commands.py``
adds ``promote backfill``, ``backfill-preview`` and the ``observe`` refresh.
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
    LocalDocument,
    MintedIdentity,
    ObservedSource,
    PersonalDataHit,
    PersonalDataKind,
    RegulationFields,
    SourceForm,
)
from lovspor.promotion.personal_data import screen_personal_data
from lovspor.promotion.render import LOCAL_RENDERER_VERSION, render_local_regulation

__all__ = [
    "EXTRACTOR_VERSION",
    "LOCAL_RENDERER_VERSION",
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
    "LocalDocument",
    "MintedIdentity",
    "ObservedSource",
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
    "render_local_regulation",
    "screen_personal_data",
]
