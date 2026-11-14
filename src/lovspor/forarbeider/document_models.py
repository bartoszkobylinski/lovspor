"""A whole proposition as ordered spans and explicit gaps (#738, FC3; architecture §2.1, §4.3).

Private ingestion only. A ``DocumentExtraction`` is what the source pages say,
shaped into parts (one per chapter page) of entries in source order: a
``Span`` is a heading, paragraph or list item the extractor reproduced, and a
``Gap`` is anything it could not shape, recorded where it sits with the text it
held, so nothing is dropped silently. It states no rights, no privacy and no
completeness: :mod:`lovspor.forarbeider.document_classify` labels spans apart
from it, and the version manifest of FC6 computes completeness from it.

Nothing here touches the held ``ChapterResult`` of ADR-0018 PR2, which every
review, plan and release step keeps reading unchanged.
"""

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from lovspor.forarbeider.chapter_models import normalized
from lovspor.forarbeider.identity import DocumentType
from lovspor.forarbeider.models import JSON_LAYOUT, Chapter

EXTRACTOR_VERSION = "regjeringen-span/1"

_Text = Annotated[str, Field(pattern=r"\S")]
_Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_DEEPEST_HEADING = 6

SpanKind = Literal["heading", "paragraph", "list_item"]
GapScope = Literal["document", "part", "span"]


class GapCode(StrEnum):
    """Why something expected is not a span. Extraction codes only, never a release code."""

    NOT_FETCHED = "not_fetched"
    PAGE_REFUSED = "page_refused"
    CONFLICTING_PAGES = "conflicting_pages"
    NAVIGATION_DISAGREES = "navigation_disagrees"
    MISSING_REGION = "missing_region"
    AMBIGUOUS_REGION = "ambiguous_region"
    MALFORMED_MARKUP = "malformed_markup"
    BOUNDARY_UNCONFIRMED = "boundary_unconfirmed"
    DUPLICATE_SECTION_ID = "duplicate_section_id"
    COVERAGE_MISMATCH = "coverage_mismatch"
    TABLE = "table"
    FOOTNOTE = "footnote"
    IMAGE = "image"
    FIGURE = "figure"
    ATTACHMENT = "attachment"
    HIDDEN_CONTENT = "hidden_content"
    BARE_TEXT = "bare_text"
    UNSUPPORTED_INLINE = "unsupported_inline"
    UNSUPPORTED_STRUCTURE = "unsupported_structure"


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Span(BaseModel):
    """One heading, paragraph or list item, as the source orders it.

    ``level`` is the heading level or the list depth, 0 for a paragraph.
    ``quote_group`` numbers the outermost ``blockquote`` the span sits in, from
    1 within its part, so two adjacent quotations never merge; ``quote_depth``
    counts the quotations around it. ``list_group`` does the same for lists,
    and ``list_item`` numbers the ``li`` within its group, so two paragraphs of
    one item are told from two items. ``segment`` numbers the spans of one
    element cut apart by an inline gap (hidden or unsupported markup, which is
    never inside a span); an element not cut has none.
    ``locator`` is the element's path below the chapter region, ``source_line``
    the line of the served page it starts on.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry: Literal["span"] = "span"
    id: _Text
    kind: SpanKind
    level: int = Field(ge=0)
    text: _Text
    text_sha256: _Digest
    section_id: _Text
    locator: _Text
    source_line: int | None
    quote_group: int | None = Field(default=None, ge=1)
    quote_depth: int = Field(default=0, ge=0)
    list_group: int | None = Field(default=None, ge=1)
    list_item: int | None = Field(default=None, ge=1)
    segment: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.text != normalized(self.text):
            raise ValueError("span text is not normalized")
        if self.text_sha256 != text_digest(self.text):
            raise ValueError("text_sha256 does not cover the span text")
        if (self.quote_group is None) != (self.quote_depth == 0):
            raise ValueError("a quote group and a quote depth come together")
        if (self.list_group is None) != (self.list_item is None):
            raise ValueError("a list group and a list item come together")
        if not _level_allowed(self.kind, self.level):
            raise ValueError("level does not fit the kind")
        return self


def _level_allowed(kind: SpanKind, level: int) -> bool:
    if kind == "heading":
        return 1 <= level <= _DEEPEST_HEADING
    if kind == "list_item":
        return level >= 1
    return level == 0


class Gap(BaseModel):
    """Something expected that is not a span, with the text it held when it had any.

    ``text`` is that region's text as served, whitespace collapsed, never shaped
    into structure. ``detail`` names the element, chapter or reason.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry: Literal["gap"] = "gap"
    id: _Text
    code: GapCode
    scope: GapScope
    locator: str | None = None
    source_line: int | None = None
    text: _Text | None = None
    detail: str | None = None


Entry = Annotated[Span | Gap, Field(discriminator="entry")]


class Coverage(BaseModel):
    """Non-space characters of the chapter region, of its spans and of its gaps.

    ``exact`` holds when the spans and gap texts, in entry order, are the region's
    text with whitespace removed: nothing lost, duplicated or reordered.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_characters: int = Field(ge=0)
    span_characters: int = Field(ge=0)
    gap_characters: int = Field(ge=0)
    exact: bool


class Part(BaseModel):
    """One chapter page: its raw digest, and every entry of its region in source order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: _Text
    chapter_number: int = Field(ge=1)
    title: _Text
    annex: bool
    source_page_url: _Text
    source_sha256: _Digest
    retrieved_at: AwareDatetime
    entries: tuple[Entry, ...]
    coverage: Coverage


class DocumentExtraction(BaseModel):
    """Every chapter page given for one proposition, extracted; never a published record.

    ``chapters_listed`` is the source's own navigation. ``gaps`` holds what is
    missing at document scope: a listed chapter without a page, a refused page,
    two pages that disagree.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeid_document_extraction"] = "forarbeid_document_extraction"
    extractor_version: Literal["regjeringen-span/1"] = "regjeringen-span/1"
    release_state: Literal["private_ingestion"] = "private_ingestion"
    document_id: _Text
    document_type: DocumentType
    session: str = Field(pattern=r"^\d{4}-\d{4}$")
    proposition_number: int = Field(ge=1)
    source_document_id: int = Field(ge=1)
    source_url: _Text
    title: _Text
    ministry: _Text | None
    chapters_listed: tuple[Chapter, ...]
    parts: tuple[Part, ...]
    gaps: tuple[Gap, ...]

    @model_validator(mode="after")
    def _ordered_and_unique(self) -> Self:
        numbers = [item.chapter_number for item in self.parts]
        if numbers != sorted(set(numbers)):
            raise ValueError("parts are not in chapter order, once each")
        ids = [entry.id for item in self.parts for entry in item.entries]
        ids += [gap.id for gap in self.gaps]
        if len(ids) != len(set(ids)):
            raise ValueError("entry ids are not unique")
        return self


def extraction_text(extraction: DocumentExtraction) -> str:
    """The extraction as written: sorted keys, two-space indent, UTF-8, one final newline."""
    return json.dumps(extraction.model_dump(mode="json"), **JSON_LAYOUT) + "\n"
