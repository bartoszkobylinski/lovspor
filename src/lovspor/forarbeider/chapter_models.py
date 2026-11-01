"""The staging result of one extracted chapter, version 1 (#738, ADR-0018 PR2).

Nothing here can state a clearance. ``release_state`` has one value, and every
result carries ``review_required``, so approving a chapter takes a later schema
and PR3's supported review command. A body exists only when every element of
the chapter was reproduced: :data:`WITHHOLDING` reasons exclude it by type, so a
chapter with a table has no text rather than text with a table missing.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.identity import DocumentType, parse_source_url
from lovspor.forarbeider.models import proved_identity

BlockKind = Literal["heading", "paragraph", "list_item", "quote"]

_Text = Annotated[str, Field(pattern=r"\S")]
_SOURCE_SPACE = re.compile(r"[ \t\r\n\f]+")
_DEEPEST_HEADING = 6


class HoldReason(StrEnum):
    """Why a result is held. The order is the order a result lists them in."""

    REVIEW_REQUIRED = "review_required"
    ANNEX = "annex"
    IMAGE = "image"
    TABLE = "table"
    FIGURE = "figure"
    FOOTNOTES = "footnotes"
    AMBIGUOUS_BOUNDARY = "ambiguous_boundary"
    DAMAGED_DOM = "damaged_dom"
    INCOMPLETE_EXTRACTION = "incomplete_extraction"
    QUOTATION = "quotation"
    THIRD_PARTY_WORK = "third_party_work"
    HEARING_PARTICIPANTS = "hearing_participants"
    PERSONAL_DATA = "personal_data"
    SENSITIVE_DATA = "sensitive_data"


#: Any of these and the body is not written: what remains would not be the chapter.
WITHHOLDING = frozenset(
    {
        HoldReason.ANNEX,
        HoldReason.IMAGE,
        HoldReason.TABLE,
        HoldReason.FIGURE,
        HoldReason.FOOTNOTES,
        HoldReason.AMBIGUOUS_BOUNDARY,
        HoldReason.DAMAGED_DOM,
        HoldReason.INCOMPLETE_EXTRACTION,
    }
)


def normalized(text: str) -> str:
    """HTML source whitespace collapsed; every other character stays, no-break spaces included."""
    return _SOURCE_SPACE.sub(" ", text).strip(" ")


class TextBlock(BaseModel):
    """One heading, paragraph, list item or quotation, in source order.

    ``level`` is the heading level or the list depth, and 0 for the other kinds.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: BlockKind
    level: int = 0
    text: _Text

    @model_validator(mode="after")
    def _normal(self) -> Self:
        if self.text != normalized(self.text):
            raise ValueError("block text is not normalized")
        if not _level_allowed(self.kind, self.level):
            raise ValueError("level does not fit the kind")
        return self


def _level_allowed(kind: BlockKind, level: int) -> bool:
    if kind == "heading":
        return 1 <= level <= _DEEPEST_HEADING
    if kind == "list_item":
        return level >= 1
    return level == 0


def chapter_text(blocks: tuple[TextBlock, ...]) -> str:
    """The normalized chapter text ``text_sha256`` covers: one line per block.

    Kind and level are part of it, so a paragraph turned into a heading is a
    new text, and wrapper markup is not. The text of a block has no newline.
    """
    return "".join(f"{block.kind} {block.level}\t{block.text}\n" for block in blocks)


def text_hash(blocks: tuple[TextBlock, ...]) -> str:
    return hashlib.sha256(chapter_text(blocks).encode()).hexdigest()


class ChapterResult(BaseModel):
    """One chapter of one saved page: private staging output, never a published record.

    ``source_page_url`` is the page ``source_sha256`` covers, and its chapter
    is proved by that page's own paging links. ``retrieved_at`` and the bytes
    stay the operator's word. No hold reason beyond ``review_required`` does
    not mean a chapter is free of personal data or third-party work: the
    candidates are prompts for a reviewer, and false negatives are expected.
    """

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
    source_document_id: int = Field(ge=1)
    source_url: _Text
    source_page_url: _Text
    chapter_number: int = Field(ge=1)
    chapter_title: _Text
    retrieved_at: AwareDatetime
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text_sha256: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    extractor_version: Literal[1]
    normalizer_version: Literal[1]
    source_provider: Literal["regjeringen.no"]
    rights_basis: Literal["åndsverkloven § 14"]
    source_license: None
    release_state: Literal["held"]
    hold_reasons: tuple[HoldReason, ...]
    blocks: tuple[TextBlock, ...] | None = Field(min_length=1)

    @field_validator("retrieved_at")
    @classmethod
    def _in_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("hold_reasons")
    @classmethod
    def _review_first_and_ordered(cls, value: tuple[HoldReason, ...]) -> tuple[HoldReason, ...]:
        if not value or value != ordered_reasons(value) or len(set(value)) != len(value):
            raise ValueError("hold reasons must start with review_required, once each, in order")
        if value[0] is not HoldReason.REVIEW_REQUIRED:
            raise ValueError("hold reasons must start with review_required, once each, in order")
        return value

    @model_validator(mode="after")
    def _one_identity(self) -> Self:
        """Every identifying field must state the same document and chapter."""
        try:
            page = parse_source_url(self.source_page_url)
        except ForarbeiderRefusedError as error:
            raise ValueError(str(error)) from error
        stated = (
            self.document_type,
            self.session,
            self.proposition_number,
            self.source_document_id,
            self.source_url,
            self.id.removesuffix(f"-ch{self.chapter_number}"),
        )
        if stated != proved_identity(page) or page.chapter != self.chapter_number:
            raise ValueError("identity fields do not state one chapter")
        if not self.id.endswith(f"-ch{self.chapter_number}"):
            raise ValueError("identity fields do not state one chapter")
        return self._with_a_true_body()

    def _with_a_true_body(self) -> Self:
        withheld = bool(WITHHOLDING.intersection(self.hold_reasons))
        if withheld != (self.blocks is None):
            raise ValueError("a body exists exactly when nothing withholds it")
        expected = None if self.blocks is None else text_hash(self.blocks)
        if self.text_sha256 != expected:
            raise ValueError("text_sha256 does not cover the body")
        return self


def ordered_reasons(reasons: tuple[HoldReason, ...]) -> tuple[HoldReason, ...]:
    order = list(HoldReason)
    return tuple(sorted(reasons, key=order.index))


def result_text(result: ChapterResult) -> str:
    """The result as written: sorted keys, two-space indent, UTF-8, one final newline."""
    payload = result.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def read_chapter(path: Path) -> ChapterResult:
    """Load and validate a result.

    Raises:
        ForarbeiderRefusedError: the file is unreadable or not version 1 of this schema.
    """
    try:
        return ChapterResult.model_validate_json(path.read_bytes())
    except OSError as error:
        raise ForarbeiderRefusedError(RefusalReason.INPUT_UNREADABLE, str(path)) from error
    except ValidationError as error:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, str(path)) from error
