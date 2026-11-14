"""Saved chapter pages of one proposition, extracted into a ``DocumentExtraction`` (#738, FC3).

A pure function of the pages given: the same bytes, URLs and retrieval times
give the same extraction, in any input order. Each page must pass
:func:`lovspor.forarbeider.page.read_proposition`, the check every chapter
result already passes; a page that fails it is a document gap, never a part.
Raw bytes are only read: they are named by their raw SHA-256, and no
normalized digest stands in for it.

Nothing is fetched here, and nothing is written: the caller decides where a
private extraction lives.
"""

import hashlib
from collections.abc import Iterable
from datetime import datetime
from typing import NamedTuple

from lxml import html

from lovspor.forarbeider.chapter_markup import tree_states_source
from lovspor.forarbeider.chapter_models import normalized
from lovspor.forarbeider.document_models import (
    Coverage,
    DocumentExtraction,
    Gap,
    GapCode,
    Part,
    Span,
)
from lovspor.forarbeider.document_spans import EntryWalker, is_hidden
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.models import Chapter
from lovspor.forarbeider.page import (
    PropositionFacts,
    identity_fields,
    page_document,
    read_proposition,
)

_REGION = "//main//div[@data-longdoc-content]"
_ANNEX_TITLES = (
    "//ul[@data-longdoc-table-of-content]/li[not(a)][starts-with(normalize-space(), 'Vedlegg')]"
    "/following-sibling::li/a"
)


class SourcePage(NamedTuple):
    """One saved chapter page: its bytes as served, the URL asked for, when it was read."""

    payload: bytes
    url: str
    retrieved_at: datetime


class _Read(NamedTuple):
    facts: PropositionFacts
    page: SourcePage
    sha256: str

    @property
    def chapter(self) -> int:
        return _chapter_number(self.facts)


def _chapter_number(facts: PropositionFacts) -> int:
    return next(
        item for item in facts.chapters if item.url == facts.locator.page_url
    ).chapter_number


def extract_document(pages: Iterable[SourcePage]) -> DocumentExtraction:
    """Every chapter page of one proposition, as ordered parts and explicit gaps.

    Raises:
        ForarbeiderRefusedError: no page proves the proposition
            (``source_identity_missing``), or the pages prove two
            (``conflicting_identity``).
    """
    read, refused = _read_all(pages)
    facts = _proved(read)
    identity = identity_fields(facts.locator)
    document_id = str(identity["id"])
    parts, conflicts = _parts(document_id, read)
    gaps = _document_gaps(read, parts, conflicts, refused)
    return DocumentExtraction.model_validate(
        {
            **{key: identity[key] for key in _IDENTITY},
            "document_id": document_id,
            "title": facts.title,
            "ministry": facts.ministry,
            "chapters_listed": facts.chapters,
            "parts": parts,
            "gaps": [
                gap.model_copy(update={"id": f"{document_id}/document/{index}"})
                for index, gap in enumerate(gaps, 1)
            ],
        }
    )


def _proved(read: list[_Read]) -> PropositionFacts:
    """The facts of the one proposition the accepted pages prove."""
    if not read:
        raise ForarbeiderRefusedError(RefusalReason.SOURCE_IDENTITY_MISSING, "no page proved")
    if len({item.facts.locator.document_id for item in read}) != 1:
        raise ForarbeiderRefusedError(RefusalReason.CONFLICTING_IDENTITY, "pages of two documents")
    return read[0].facts


_IDENTITY = ("document_type", "session", "proposition_number", "source_document_id", "source_url")


def _read_all(pages: Iterable[SourcePage]) -> tuple[list[_Read], list[tuple[SourcePage, str]]]:
    """Pages that pass the page checks, ordered by chapter and digest; the rest with a reason."""
    read: list[_Read] = []
    refused: list[tuple[SourcePage, str]] = []
    for page in pages:
        try:
            facts = read_proposition(page.payload, page.url)
        except ForarbeiderRefusedError as error:
            refused.append((page, error.reason.value))
            continue
        read.append(_Read(facts, page, hashlib.sha256(page.payload).hexdigest()))
    read.sort(key=lambda item: (item.chapter, item.sha256, item.page.retrieved_at))
    refused.sort(key=lambda item: (item[0].url, hashlib.sha256(item[0].payload).hexdigest()))
    return read, refused


def _parts(document_id: str, read: list[_Read]) -> tuple[list[Part], set[int]]:
    """One part per chapter; a chapter given as two different pages is a conflict, no part."""
    by_chapter: dict[int, list[_Read]] = {}
    for item in read:
        by_chapter.setdefault(item.chapter, []).append(item)
    conflicts = {
        number for number, items in by_chapter.items() if len({i.sha256 for i in items}) > 1
    }
    parts = [
        _part(document_id, items[0])
        for number, items in sorted(by_chapter.items())
        if number not in conflicts
    ]
    return parts, conflicts


def _document_gaps(
    read: list[_Read], parts: list[Part], conflicts: set[int], refused: list[tuple[SourcePage, str]]
) -> list[Gap]:
    listed = read[0].facts.chapters
    gaps: list[Gap] = []
    if any(item.facts.chapters != listed for item in read):
        gaps.append(_document_gap(GapCode.NAVIGATION_DISAGREES, None, None))
    present = {item.chapter_number for item in parts}
    for chapter in listed:
        if chapter.chapter_number in conflicts:
            gaps.append(_document_gap(GapCode.CONFLICTING_PAGES, chapter.url, chapter))
        elif chapter.chapter_number not in present:
            gaps.append(_document_gap(GapCode.NOT_FETCHED, chapter.url, chapter))
    gaps += [
        _document_gap(GapCode.PAGE_REFUSED, page.url, None, reason) for page, reason in refused
    ]
    return gaps


def _document_gap(
    code: GapCode, locator: str | None, chapter: Chapter | None, reason: str | None = None
) -> Gap:
    detail = f"ch{chapter.chapter_number}" if chapter is not None else reason
    return Gap(id="pending", code=code, scope="document", locator=locator, detail=detail)


def _part(document_id: str, item: _Read) -> Part:
    root = page_document(item.page.payload)
    chapter = next(c for c in item.facts.chapters if c.chapter_number == item.chapter)
    part_id = f"{document_id}-ch{chapter.chapter_number}"
    entries, coverage = _entries(root, part_id, chapter.title, item.page.payload.decode())
    return Part(
        id=part_id,
        chapter_number=chapter.chapter_number,
        title=chapter.title,
        annex=chapter.title in _annex_titles(root),
        source_page_url=item.facts.locator.page_url,
        source_sha256=item.sha256,
        retrieved_at=item.page.retrieved_at,
        entries=tuple(entries),
        coverage=coverage,
    )


def _annex_titles(root: html.HtmlElement) -> set[str]:
    """The source's own divider: every chapter listed after it is an annex."""
    return {normalized(link.text_content()) for link in root.xpath(_ANNEX_TITLES)}


def _entries(
    root: html.HtmlElement, part_id: str, title: str, text: str
) -> tuple[list[Span | Gap], Coverage]:
    regions = root.xpath(_REGION)
    if len(regions) != 1:
        code = GapCode.AMBIGUOUS_REGION if regions else GapCode.MISSING_REGION
        empty = Coverage(source_characters=0, span_characters=0, gap_characters=0, exact=False)
        return [_part_gap(part_id, 1, code)], empty
    region = regions[0]
    entries = EntryWalker(part_id, region).walk()
    codes = _region_codes(region, title, text)
    coverage = _coverage(region, entries)
    if not coverage.exact:
        codes.append(GapCode.COVERAGE_MISMATCH)
    entries += [_part_gap(part_id, index, code) for index, code in enumerate(codes, 1)]
    return entries, coverage


def _region_codes(region: html.HtmlElement, title: str, text: str) -> list[GapCode]:
    """Part-scope gaps: the tree is not what the bytes state, the boundary is unproved."""
    codes: list[GapCode] = []
    if not tree_states_source(region, text):
        codes.append(GapCode.MALFORMED_MARKUP)
    headings = region.xpath(".//h1")
    if len(headings) != 1 or normalized(headings[0].text_content()) != title:
        codes.append(GapCode.BOUNDARY_UNCONFIRMED)
    if any(is_hidden(node) for node in region.iterancestors()):
        codes.append(GapCode.HIDDEN_CONTENT)
    return codes


def _part_gap(part_id: str, index: int, code: GapCode) -> Gap:
    return Gap(id=f"{part_id}/part/{index}", code=code, scope="part")


def _squeezed(text: str) -> str:
    return "".join(text.split())


def _coverage(region: html.HtmlElement, entries: list[Span | Gap]) -> Coverage:
    """Whether spans and gap texts, in order, are the region's text, whitespace aside."""
    source = _squeezed(region.text_content())
    spans = [_squeezed(entry.text) for entry in entries if isinstance(entry, Span)]
    gaps = [_squeezed(entry.text) for entry in entries if isinstance(entry, Gap) and entry.text]
    ordered = "".join(_squeezed(entry.text or "") for entry in entries)
    return Coverage(
        source_characters=len(source),
        span_characters=sum(map(len, spans)),
        gap_characters=sum(map(len, gaps)),
        exact=ordered == source,
    )
