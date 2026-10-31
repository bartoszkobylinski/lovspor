"""One saved regjeringen.no chapter page, read into typed metadata.

A chapter page (``?ch=N``) is the only input: the landing page carries the
same identity but no chapter navigation (read on Prop. 54 LS and Prop. 112 L,
2026-10-31). Each identity fact must be stated by the markup this module knows
and must agree with every other statement of it. There are no fallbacks: an
unfamiliar page is refused, not approximated.

``published_at`` stays ``None``. ``DC.Date`` equals the date of the tilråding
in the samples, the page states no publication date, and a date is not
inferred from one that happens to be close.
"""

import hashlib
from collections.abc import Iterable
from datetime import datetime

from lxml import etree, html
from pydantic import BaseModel, ConfigDict, ValidationError

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.identity import (
    SourceLocator,
    chapter_of,
    parse_reference,
    parse_source_url,
)
from lovspor.forarbeider.models import Chapter, ForarbeidRecord

#: What every record states whatever the page says: never read from a source.
_FIXED_FIELDS: dict[str, object] = {
    "schema_version": 1,
    "source_document_type": "forarbeid",
    "normative": False,
    "published_at": None,
    "parser_version": 1,
    "source_provider": "regjeringen.no",
    "rights_basis": "åndsverkloven § 14",
    "source_license": None,
}

_PROPOSITION_TYPE = "Proposisjon"
_HEADER = "//main//div[contains(concat(' ', normalize-space(@class), ' '), ' content-header ')]"
_SUBHEADER = "./h2[contains(concat(' ', normalize-space(@class), ' '), ' subheader ')]"
_OWNERS = "//div[@data-nav='content-owner-link']//a"
_NAVIGATION = "//ul[@data-longdoc-table-of-content]"


class PropositionFacts(BaseModel):
    """What a chapter page proves, before the operator's inputs are added."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    locator: SourceLocator
    title: str
    ministry: str | None
    chapters: tuple[Chapter, ...]


def build_record(payload: bytes, source_url: str, retrieved_at: datetime) -> ForarbeidRecord:
    """The metadata record of ``payload``, depending on these inputs alone.

    Raises:
        ForarbeiderRefusedError: the input fails any check of this package.
    """
    facts = read_proposition(payload, source_url)
    fields = {
        **_FIXED_FIELDS,
        **_identity_fields(facts.locator),
        "title": facts.title,
        "ministry": facts.ministry,
        "retrieved_at": retrieved_at,
        "source_sha256": hashlib.sha256(payload).hexdigest(),
        "chapters": facts.chapters,
    }
    try:
        return ForarbeidRecord.model_validate(fields)
    except ValidationError as error:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, source_url) from error


def _identity_fields(locator: SourceLocator) -> dict[str, object]:
    return {
        "id": f"regjeringen-{locator.document_id}",
        "document_type": locator.reference.document_type,
        "session": locator.reference.session,
        "proposition_number": locator.reference.proposition_number,
        "source_document_id": locator.document_id,
        "source_url": locator.canonical_url,
        "source_page_url": locator.page_url,
    }


def read_proposition(payload: bytes, source_url: str) -> PropositionFacts:
    """Every fact the page at ``source_url`` proves about its proposition.

    Raises:
        ForarbeiderRefusedError: the page is refused, with a typed reason.
    """
    locator = parse_source_url(source_url)
    if locator.chapter is None:
        raise ForarbeiderRefusedError(RefusalReason.CHAPTER_PAGE_REQUIRED, source_url)
    root = _document(payload)
    _require_proposition(root)
    _require_identity(root, locator)
    return PropositionFacts(
        locator=locator,
        title=_title(root, locator),
        ministry=_ministry(root),
        chapters=_chapters(root, locator),
    )


def _document(payload: bytes) -> html.HtmlElement:
    try:
        return html.document_fromstring(
            payload.decode("utf-8"), parser=html.HTMLParser(no_network=True)
        )
    except (UnicodeDecodeError, ValueError, etree.ParserError) as error:
        raise ForarbeiderRefusedError(RefusalReason.UNREADABLE_PAGE, "saved page") from error


def _clean(text: str) -> str:
    return " ".join(text.split())


def _one(values: Iterable[str], name: str) -> str | None:
    """The single value ``values`` states, ``None`` for none, refused when they differ."""
    distinct = {_clean(value) for value in values}
    if len(distinct) > 1:
        raise ForarbeiderRefusedError(RefusalReason.CONFLICTING_IDENTITY, name)
    return distinct.pop() if distinct else None


def _meta(root: html.HtmlElement, attribute: str, name: str) -> str | None:
    values = root.xpath(f"//meta[@{attribute}=$name]/@content", name=name)
    return _one(values, name)


def _required_meta(root: html.HtmlElement, tag: tuple[str, str], missing: RefusalReason) -> str:
    value = _meta(root, *tag)
    if not value:
        raise ForarbeiderRefusedError(missing, tag[1])
    return value


def _require_proposition(root: html.HtmlElement) -> None:
    stated = _required_meta(root, ("name", "DC.Type"), RefusalReason.DOM_CHANGED)
    if stated != _PROPOSITION_TYPE:
        raise ForarbeiderRefusedError(RefusalReason.UNSUPPORTED_DOCUMENT_TYPE, "DC.Type")


def _require_identity(root: html.HtmlElement, locator: SourceLocator) -> None:
    missing = RefusalReason.SOURCE_IDENTITY_MISSING
    if _required_meta(root, ("property", "og:url"), missing) != locator.canonical_url:
        raise ForarbeiderRefusedError(RefusalReason.CONFLICTING_IDENTITY, "og:url")
    canonical_path = locator.canonical_url.removeprefix("https://www.regjeringen.no")
    if _required_meta(root, ("name", "DC.Identifier.URL"), missing) != canonical_path:
        raise ForarbeiderRefusedError(RefusalReason.CONFLICTING_IDENTITY, "DC.Identifier.URL")


def _header(root: html.HtmlElement) -> html.HtmlElement:
    headers = root.xpath(_HEADER)
    if len(headers) != 1:
        raise ForarbeiderRefusedError(RefusalReason.DOM_CHANGED, "content-header")
    return headers[0]


def _require_same_reference(text: str, locator: SourceLocator, name: str) -> None:
    if parse_reference(text) != locator.reference:
        raise ForarbeiderRefusedError(RefusalReason.CONFLICTING_IDENTITY, name)


def _title(root: html.HtmlElement, locator: SourceLocator) -> str:
    header = _header(root)
    headings = header.xpath("./h1")
    if len(headings) != 1:
        raise ForarbeiderRefusedError(RefusalReason.DOM_CHANGED, "h1")
    _require_same_reference(_clean(headings[0].text_content()), locator, "h1")
    og_title = _required_meta(root, ("property", "og:title"), RefusalReason.DOM_CHANGED)
    _require_same_reference(og_title, locator, "og:title")
    stated = _required_meta(root, ("name", "DC.Title"), RefusalReason.TITLE_MISSING)
    reference, _, title = stated.partition(" - ")
    if not title:
        raise ForarbeiderRefusedError(RefusalReason.TITLE_MISSING, "DC.Title")
    _require_same_reference(reference, locator, "DC.Title")
    _require_same_subheader(header, title)
    return title


def _require_same_subheader(header: html.HtmlElement, title: str) -> None:
    for subheader in header.xpath(_SUBHEADER):
        if _clean(subheader.text_content()) != title:
            raise ForarbeiderRefusedError(RefusalReason.CONFLICTING_IDENTITY, "subheader")


def _ministry(root: html.HtmlElement) -> str | None:
    owner = _one((link.text_content() for link in root.xpath(_OWNERS)), "content-owner")
    creator = _meta(root, "name", "DC.Creator")
    if owner and creator and owner != creator:
        raise ForarbeiderRefusedError(RefusalReason.CONFLICTING_IDENTITY, "ministry")
    return owner or creator or None


def _chapters(root: html.HtmlElement, parent: SourceLocator) -> tuple[Chapter, ...]:
    lists = root.xpath(_NAVIGATION)
    if len(lists) > 1:
        raise ForarbeiderRefusedError(RefusalReason.DOM_CHANGED, "chapter list")
    links = lists[0].xpath("./li/a") if lists else []
    if not links:
        raise ForarbeiderRefusedError(RefusalReason.CHAPTER_NAVIGATION_MISSING, "chapter list")
    chapters = tuple(_chapter(link, parent) for link in links)
    _require_sequence(chapters)
    return chapters


def _chapter(link: html.HtmlElement, parent: SourceLocator) -> Chapter:
    href = link.get("href")
    if href is None:
        raise ForarbeiderRefusedError(RefusalReason.CHAPTER_NAVIGATION_MISSING, "chapter link")
    title = _clean(link.text_content())
    if not title:
        raise ForarbeiderRefusedError(RefusalReason.CHAPTER_NAVIGATION_MISSING, "chapter title")
    number, url = chapter_of(href, parent)
    return Chapter(chapter_number=number, title=title, url=url)


def _require_sequence(chapters: tuple[Chapter, ...]) -> None:
    seen: set[int] = set()
    for chapter in chapters:
        if chapter.chapter_number in seen:
            detail = str(chapter.chapter_number)
            raise ForarbeiderRefusedError(RefusalReason.CHAPTER_DUPLICATE, detail)
        seen.add(chapter.chapter_number)
    for position, chapter in enumerate(chapters, start=1):
        if chapter.chapter_number != position:
            detail = str(chapter.chapter_number)
            raise ForarbeiderRefusedError(RefusalReason.CHAPTER_SEQUENCE, detail)
