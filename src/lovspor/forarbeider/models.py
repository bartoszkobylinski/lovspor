"""The forarbeider metadata schema, version 1 (#738, contract in PR #739).

Separate from ``ManifestRecord`` and ``LocalRecord``: forarbeider are neither
the Lovdata dataset nor enacted local regulations. ``source_license`` is
``None`` by type because a statutory exclusion is not a licence, and every
field is required, so an unknown value is an explicit ``null``, never an
omission. ``extra="forbid"``, unlike the Lovdata manifest: this schema has no
older readers to keep working yet, and a stray key could carry a licence claim.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from lovspor.atomic_io import atomic_write_text
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.identity import DocumentType, SourceLocator, parse_source_url

_JSON_LAYOUT: dict[str, Any] = {"sort_keys": True, "indent": 2, "ensure_ascii": False}

_Text = Annotated[str, Field(min_length=1)]


class Chapter(BaseModel):
    """One entry of the source's own chapter navigation, in source order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter_number: int = Field(ge=1)
    title: _Text
    url: _Text


class ForarbeidRecord(BaseModel):
    """Metadata of one Prop. L / LS: staging output, not a published record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    id: _Text
    source_document_type: Literal["forarbeid"]
    normative: Literal[False]
    document_type: DocumentType
    session: str = Field(pattern=r"^\d{4}-\d{4}$")
    proposition_number: int = Field(ge=1)
    title: _Text
    ministry: _Text | None
    published_at: date | None
    source_document_id: int = Field(ge=1)
    source_url: _Text
    source_page_url: _Text
    retrieved_at: AwareDatetime
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_version: Literal[1]
    source_provider: Literal["regjeringen.no"]
    rights_basis: Literal["åndsverkloven § 14"]
    source_license: None
    chapters: tuple[Chapter, ...] = Field(min_length=1)

    @field_validator("retrieved_at")
    @classmethod
    def _in_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _one_identity(self) -> Self:
        """Every identifying field must state the same document."""
        try:
            page = parse_source_url(self.source_page_url)
        except ForarbeiderRefusedError as error:
            raise ValueError(str(error)) from error
        if self._stated_identity() != _proved_identity(page) or page.chapter is None:
            raise ValueError("identity fields do not state one document")
        return self._with_chapters_of(page.canonical_url)

    def _stated_identity(self) -> tuple[object, ...]:
        return (
            self.document_type,
            self.session,
            self.proposition_number,
            self.source_document_id,
            self.source_url,
            self.id,
        )

    def _with_chapters_of(self, canonical_url: str) -> Self:
        for position, chapter in enumerate(self.chapters, start=1):
            expected = f"{canonical_url}?ch={position}"
            if chapter.chapter_number != position or chapter.url != expected:
                raise ValueError("chapters are not this document's, in source order")
        return self


def _proved_identity(page: SourceLocator) -> tuple[object, ...]:
    return (
        page.reference.document_type,
        page.reference.session,
        page.reference.proposition_number,
        page.document_id,
        page.canonical_url,
        f"regjeringen-{page.document_id}",
    )


def metadata_text(record: ForarbeidRecord) -> str:
    """The record as written: sorted keys, two-space indent, UTF-8, one final newline."""
    payload = record.model_dump(mode="json")
    return json.dumps(payload, **_JSON_LAYOUT) + "\n"


def write_metadata(record: ForarbeidRecord, path: Path) -> None:
    """Write ``record`` atomically; ``path`` must already have passed the staging check."""
    atomic_write_text(path, metadata_text(record))


def read_metadata(path: Path) -> ForarbeidRecord:
    """Load and validate a record.

    Raises:
        ForarbeiderRefusedError: the file is not version 1 of this schema.
    """
    try:
        return ForarbeidRecord.model_validate_json(path.read_bytes())
    except ValidationError as error:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, str(path)) from error
