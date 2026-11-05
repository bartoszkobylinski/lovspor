"""Edition evidence: a sidecar beside a v1 metadata record, never inside it (#784).

``ForarbeidRecord`` stays version 1 and byte-identical: release preflight
rebuilds it from its one chapter page, and a fact read from the landing page
could not be rebuilt from there. So the landing page's evidence lives in its
own file with its own schema, naming the v1 record it was checked against.

What the evidence asserts is narrow:

- ``edition`` is what the landing page states about the proposition, read
  from markup observed on a real page. ``unknown`` is the default; ``original``
  is reserved and refused until a page is seen that states it.
- ``chapter_edition`` is always ``unknown``. The chapter page and the landing
  page are separate captures, so nothing proves the chapter bytes belong to
  the stated edition, or which chapters a correction changed.
- ``landing_retrieved_at`` and the bytes behind ``landing_sha256`` are the
  operator's word (``landing_provenance``); no request is made here.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.identity import DocumentType, parse_source_url
from lovspor.forarbeider.models import JSON_LAYOUT, proved_identity
from lovspor.forarbeider.staging import read_private

EditionKind = Literal["original", "corrected", "unknown"]

#: Far above a sidecar's size; a larger file is not one.
MAX_EVIDENCE_BYTES = 1024 * 1024

_SHA256 = r"^[0-9a-f]{64}$"
_ASSET_URL = (
    r"https://www\.regjeringen\.no/contentassets/[0-9a-f]{32}/no/{kind}/[A-Za-z0-9_-]+\.pdf"
)


def _asset(url: str, kind: str) -> bool:
    return re.fullmatch(_ASSET_URL.replace("{kind}", kind), url) is not None


class Edition(BaseModel):
    """The edition one landing page states, as the source states it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: EditionKind
    corrected_on: date | None
    original_edition_url: str | None
    rettebrev_urls: tuple[str, ...]

    @model_validator(mode="after")
    def _stated_by_the_page(self) -> Self:
        if self.kind == "original":
            raise ValueError("no observed landing page states an original edition")
        if self.kind == "unknown":
            stated = (self.corrected_on, self.original_edition_url, self.rettebrev_urls)
            if stated != (None, None, ()):
                raise ValueError("an unknown edition states no edition fact")
            return self
        if self.corrected_on is None or self.original_edition_url is None:
            raise ValueError("a correction is stated with its date and its original edition")
        links = all(_asset(url, "rett") for url in self.rettebrev_urls)
        if not self.rettebrev_urls or not links or not _asset(self.original_edition_url, "opri"):
            raise ValueError("edition links are not the source's own PDFs")
        return self


class ChapterRecordRef(BaseModel):
    """The v1 record the landing page was associated with, by what it states."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_page_url: str
    source_sha256: str = Field(pattern=_SHA256)
    retrieved_at: AwareDatetime


class EditionEvidence(BaseModel):
    """One landing page's edition statement, beside one v1 metadata record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    record_type: Literal["forarbeid-edition-evidence"]
    id: str
    document_type: DocumentType
    session: str = Field(pattern=r"^\d{4}-\d{4}$")
    proposition_number: int = Field(ge=1)
    source_document_id: int = Field(ge=1)
    source_url: str
    landing_page_url: str
    landing_retrieved_at: AwareDatetime
    landing_sha256: str = Field(pattern=_SHA256)
    landing_provenance: Literal["operator_saved_offline"]
    parser_version: Literal[1]
    edition: Edition
    chapter_record: ChapterRecordRef
    chapter_edition: Literal["unknown"]

    @model_validator(mode="after")
    def _one_document(self) -> Self:
        try:
            landing = parse_source_url(self.landing_page_url)
            chapter = parse_source_url(self.chapter_record.source_page_url)
        except ForarbeiderRefusedError as error:
            raise ValueError(str(error)) from error
        stated = (
            self.document_type,
            self.session,
            self.proposition_number,
            self.source_document_id,
            self.source_url,
            self.id,
        )
        if landing.chapter is not None or stated != proved_identity(landing):
            raise ValueError("identity fields do not state one landing page")
        if chapter.chapter is None or chapter.canonical_url != landing.canonical_url:
            raise ValueError("the chapter record is not a chapter of this document")
        return self


def evidence_text(evidence: EditionEvidence) -> str:
    """The evidence as written: sorted keys, two-space indent, UTF-8, one final newline."""
    return json.dumps(evidence.model_dump(mode="json"), **JSON_LAYOUT) + "\n"


def read_edition_evidence(path: Path) -> EditionEvidence:
    """Load and validate private edition evidence.

    Raises:
        ForarbeiderRefusedError: ``observation_unsafe``, ``input_unreadable``,
            ``input_too_large`` or ``schema_invalid``.
    """
    payload = read_private(path, MAX_EVIDENCE_BYTES)
    try:
        return EditionEvidence.model_validate_json(payload)
    except ValidationError as error:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, str(path)) from error
