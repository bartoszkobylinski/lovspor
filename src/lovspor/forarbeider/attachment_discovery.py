"""Which attachments a proposition publishes, read from its saved pages alone (#738, FC6b).

The reader takes nothing from a caller but raw page bytes: every listed
chapter page, already accepted by FC3 extraction, and the document's landing
page. Every reference on every page (a link, an embedded source, a form
action, a stylesheet) is classified by explicit rules; nothing is skipped
because a selector found no match.

- **attachment**: a file-like link (``/contentassets/`` or a document or media
  extension) inside a chapter's text region;
- **embedded**: an image, frame, object or media source inside that region;
- **edition**: the original edition and rettebrev PDFs of the landing page's
  edition block, as :mod:`lovspor.forarbeider.edition` reads them;
- **rendition**: the whole document as PDF, DOCX or EPUB in the page's own
  download list. Kept apart: it is the same document in another format, not
  an attachment of it;
- site chrome (page header and footer, site menus, the chapter navigation,
  paging, the template's own scripts and icons) is read and set aside.

Anything else is a discovery gap: a file-like reference outside a known
container, any unrecognised reference in the page's main area, a container
whose class names attachments or downloads, renditions that differ between
pages, an absent or refused landing page, a listed chapter without its page,
or chapter navigation the pages disagree on. Discovery is exhaustive only
when there is no gap, and only then may an inventory, even an empty one, be
counted. A link found is never a file captured.
"""

import hashlib
import re
from collections.abc import Iterable, Iterator, Mapping
from typing import Annotated, Literal, NamedTuple, Self
from urllib.parse import urljoin, urlsplit, urlunsplit

from lxml import html
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lovspor.forarbeider.document_extract import SourcePage
from lovspor.forarbeider.document_models import DocumentExtraction, GapCode
from lovspor.forarbeider.edition import EditionFacts, read_landing
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.page import page_document

DISCOVERY_READER = "regjeringen-attachments/1"

_Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_Text = Annotated[str, Field(pattern=r"\S")]

_REFERENCES = ("href", "src", "data", "srcset", "data-src", "data-href", "poster", "action")
_EMBEDS = frozenset({"img", "iframe", "object", "embed", "video", "audio", "source", "track"})
_FILE = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|odt|ods|odp|rtf|epub|zip|csv|txt|xml|json"
    r"|png|jpe?g|gif|svg|tiff?|mp4|mp3|wav)$",
    re.IGNORECASE,
)
_RENDITION = re.compile(r"/contentassets/[0-9a-f]{32}/[a-z]{2}/(?P<format>pdfs|docx|epub)/[^/]+")
_SITE_ASSETS = "/dist-web/"
_IGNORED_SCHEMES = ("mailto:", "tel:", "javascript:")
#: Template containers in ``main`` whose links are navigation, read off the sampled pages.
_TEMPLATE = frozenset(
    {
        "longdoc-paging",
        "content-owner-dep",
        "longdoc-linktool-is-active",
        "to-top-of-page",
        "longdoc-nav-area",
        "content-header",
        "longdoc-nav",
    }
)
#: The download list and its wrappers, read off the sampled chapter pages.
_DOWNLOADS = frozenset(
    {
        "download-container-wrapper",
        "longdoc-tools-area",
        "longdoc-download",
        "longdoc-download-area",
        "longdoc-download-list",
    }
)
_SUSPECT = re.compile(r"vedlegg|attachment|download|nedlast|filliste|dokumentliste", re.IGNORECASE)
_REGION = "data-longdoc-content"

DiscoveryCode = Literal[
    "landing_page_missing",
    "landing_page_refused",
    "landing_identity_mismatch",
    "chapter_page_missing",
    "navigation_unproved",
    "unrecognized_reference",
    "file_outside_known_container",
    "unknown_container",
    "renditions_disagree",
    "conflicting_reference",
]
PageRole = Literal["landing", "chapter"]
AttachmentKind = Literal["attachment", "embedded", "edition"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FoundAt(_Frozen):
    """One place a reference sits: the page by raw digest, the element by path."""

    page_sha256: _Digest
    locator: _Text


class DiscoveredAttachment(_Frozen):
    """One attachment by canonical URL, with every place it is referenced."""

    url: _Text
    kind: AttachmentKind
    found_at: tuple[FoundAt, ...] = Field(min_length=1)


class Rendition(_Frozen):
    """The whole document in another format, from a page's own download list."""

    url: _Text
    format: Literal["pdfs", "docx", "epub"]
    found_at: tuple[FoundAt, ...] = Field(min_length=1)


class DiscoveryGap(_Frozen):
    """Why discovery is not proved exhaustive, and where."""

    code: DiscoveryCode
    page_sha256: _Digest | None = None
    locator: str | None = None
    detail: str | None = None


class RequiredPage(_Frozen):
    role: PageRole
    chapter_number: int | None = Field(default=None, ge=1)


class ScannedPage(_Frozen):
    role: PageRole
    chapter_number: int | None = Field(default=None, ge=1)
    raw_sha256: _Digest


class AttachmentDiscovery(_Frozen):
    """What the reader found on the pages it scanned, and what keeps it from exhaustive."""

    reader: Literal["regjeringen-attachments/1"] = "regjeringen-attachments/1"
    pages_required: tuple[RequiredPage, ...] = Field(min_length=1)
    pages_scanned: tuple[ScannedPage, ...]
    attachments: tuple[DiscoveredAttachment, ...]
    renditions: tuple[Rendition, ...]
    gaps: tuple[DiscoveryGap, ...]

    @model_validator(mode="after")
    def _scanned_or_gap(self) -> Self:
        scanned = {(page.role, page.chapter_number) for page in self.pages_scanned}
        required = {(page.role, page.chapter_number) for page in self.pages_required}
        if not scanned <= required:
            raise ValueError("a scanned page is not a required page")
        if required - scanned and not self.gaps:
            raise ValueError("a required page was not scanned and no gap says so")
        return self

    @property
    def exhaustive(self) -> bool:
        """Every required page scanned and every reference recognised."""
        return not self.gaps


class _Reference(NamedTuple):
    """One classified reference: what it is and where it sits."""

    kind: str
    url: str
    found: FoundAt


class _Context(NamedTuple):
    """What a page's references are judged against: its document and its edition links."""

    document_url: str
    editions: frozenset[str]


class _Page(NamedTuple):
    page: SourcePage
    sha256: str
    role: PageRole
    chapter_number: int | None


def canonical_url(base: str, value: str) -> str:
    """``value`` resolved against ``base``: scheme and host lower-cased, fragment dropped."""
    parts = urlsplit(urljoin(base, value.strip()))
    host = parts.netloc.lower().removesuffix(":443").removesuffix(":80")
    return urlunsplit((parts.scheme.lower(), host, parts.path, parts.query, ""))


def discover_attachments(
    extraction: DocumentExtraction, pages: Mapping[str, SourcePage], landing: SourcePage | None
) -> AttachmentDiscovery:
    """The attachments the given pages show, with every gap that keeps the list unproved.

    ``pages`` maps raw SHA-256 to the chapter pages FC3 read; each part's page is
    looked up by its own digest, so input order never matters.
    """
    chapters = [
        _Page(pages[part.source_sha256], part.source_sha256, "chapter", part.chapter_number)
        for part in extraction.parts
    ]
    gaps = list(_coverage_gaps(extraction))
    landing_page, landing_facts, landing_gaps = _landing(extraction, landing)
    gaps += landing_gaps
    scanned = [*chapters, *([landing_page] if landing_page else [])]
    references = [ref for item in scanned for ref in _references(item, landing_facts, gaps)]
    attachments, renditions = _collect(references, chapters, gaps)
    return AttachmentDiscovery(
        pages_required=_required(extraction),
        pages_scanned=tuple(
            ScannedPage(role=item.role, chapter_number=item.chapter_number, raw_sha256=item.sha256)
            for item in scanned
        ),
        attachments=attachments,
        renditions=renditions,
        gaps=tuple(sorted(gaps, key=_gap_key)),
    )


def _required(extraction: DocumentExtraction) -> tuple[RequiredPage, ...]:
    chapters = (
        RequiredPage(role="chapter", chapter_number=chapter.chapter_number)
        for chapter in extraction.chapters_listed
    )
    return (*chapters, RequiredPage(role="landing"))


def _gap_key(gap: DiscoveryGap) -> tuple[str, str, str, str]:
    return (gap.code, gap.page_sha256 or "", gap.locator or "", gap.detail or "")


def _coverage_gaps(extraction: DocumentExtraction) -> Iterator[DiscoveryGap]:
    """Every listed chapter has its page, and the pages agree on the navigation."""
    present = {part.chapter_number for part in extraction.parts}
    for chapter in extraction.chapters_listed:
        if chapter.chapter_number not in present:
            detail = f"ch{chapter.chapter_number}"
            yield DiscoveryGap(code="chapter_page_missing", detail=detail)
    if any(gap.code is GapCode.NAVIGATION_DISAGREES for gap in extraction.gaps):
        yield DiscoveryGap(code="navigation_unproved")


def _landing(
    extraction: DocumentExtraction, landing: SourcePage | None
) -> tuple[_Page | None, EditionFacts | None, list[DiscoveryGap]]:
    """The landing page, when it is this document's, read by the edition reader."""
    if landing is None:
        return None, None, [DiscoveryGap(code="landing_page_missing")]
    sha = _sha(landing.payload)
    try:
        locator, facts = read_landing(landing.payload, landing.url)
    except ForarbeiderRefusedError as error:
        gap = DiscoveryGap(code="landing_page_refused", page_sha256=sha, detail=error.reason.value)
        return None, None, [gap]
    if locator.document_id != extraction.source_document_id:
        return None, None, [DiscoveryGap(code="landing_identity_mismatch", page_sha256=sha)]
    return _Page(landing, sha, "landing", None), facts, []


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _references(
    item: _Page, facts: EditionFacts | None, gaps: list[DiscoveryGap]
) -> Iterator[_Reference]:
    """Every reference of one page, classified; unrecognised ones become gaps."""
    root = page_document(item.page.payload)
    tree = root.getroottree()
    editions = _edition_urls(facts) if item.role == "landing" else frozenset()
    context = _Context(_document_url(item.page.url), editions)
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        found = FoundAt(page_sha256=item.sha256, locator=tree.getpath(element))
        if _suspect_container(element):
            gaps.append(DiscoveryGap(code="unknown_container", **found.model_dump()))
        for value in _values(element):
            url = canonical_url(item.page.url, value)
            kind = _kind(element, value, url, context)
            if kind in _GAP_KINDS:
                gaps.append(
                    DiscoveryGap.model_validate({"code": kind, "detail": url, **found.model_dump()})
                )
            elif kind != "ignored":
                yield _Reference(kind, url, found)


_GAP_KINDS = frozenset({"unrecognized_reference", "file_outside_known_container"})


def _edition_urls(facts: EditionFacts | None) -> frozenset[str]:
    if facts is None or facts.original_edition_url is None:
        return frozenset()
    return frozenset({facts.original_edition_url, *facts.rettebrev_urls})


def _values(element: html.HtmlElement) -> Iterator[str]:
    """Each referenced address on ``element``; a ``srcset`` names several."""
    for name in _REFERENCES:
        value = element.get(name)
        if value is None:
            continue
        if name == "srcset":
            yield from (part.split()[0] for part in value.split(",") if part.split())
        else:
            yield value


def _document_url(page_url: str) -> str:
    """The document's own address: the page URL without its query."""
    parts = urlsplit(canonical_url(page_url, page_url))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _kind(element: html.HtmlElement, value: str, url: str, context: _Context) -> str:
    """What one reference is, by the rules in the module docstring."""
    if _harmless(value):
        return "ignored"
    if url in context.editions:
        return "edition"
    if _in(element, _REGION):
        return _region_kind(element, url)
    return _outside_region_kind(element, url, context)


def _outside_region_kind(element: html.HtmlElement, url: str, context: _Context) -> str:
    """Outside the text: a rendition list, site assets and chrome, or a gap."""
    file_like = _file_like(url)
    if file_like and _within_classes(element, _DOWNLOADS) and _RENDITION.search(url):
        return "rendition"
    if urlsplit(url).path.startswith(_SITE_ASSETS):
        return "ignored"
    if file_like:
        return "file_outside_known_container"
    own = urlsplit(url)._replace(query="").geturl() == context.document_url
    if _within_main(element) and not own and not _within_classes(element, _TEMPLATE):
        return "unrecognized_reference"
    return "ignored"


def _harmless(value: str) -> bool:
    stripped = value.strip()
    return not stripped or stripped.startswith("#") or stripped.lower().startswith(_IGNORED_SCHEMES)


def _region_kind(element: html.HtmlElement, url: str) -> str:
    if element.tag in _EMBEDS or element.get("href") is None:
        return "embedded"
    if _file_like(url):
        return "attachment"
    return "ignored" if urlsplit(url).scheme in {"http", "https"} else "unrecognized_reference"


def _file_like(url: str) -> bool:
    path = urlsplit(url).path
    return "/contentassets/" in path or _FILE.search(path) is not None


def _in(element: html.HtmlElement, attribute: str) -> bool:
    return any(node.get(attribute) is not None for node in element.iterancestors())


def _within_main(element: html.HtmlElement) -> bool:
    return any(node.tag == "main" for node in element.iterancestors())


def _within_classes(element: html.HtmlElement, names: frozenset[str]) -> bool:
    nodes = [element, *element.iterancestors()]
    return any(set((node.get("class") or "").split()) & names for node in nodes)


def _suspect_container(element: html.HtmlElement) -> bool:
    """A container in ``main``, outside the text and the known lists, named like attachments."""
    classes = set((element.get("class") or "").split())
    if not classes or classes & _DOWNLOADS or not _within_main(element) or _in(element, _REGION):
        return False
    return any(_SUSPECT.search(name) for name in classes)


def _collect(
    references: Iterable[_Reference], chapters: list[_Page], gaps: list[DiscoveryGap]
) -> tuple[tuple[DiscoveredAttachment, ...], tuple[Rendition, ...]]:
    """Attachments and renditions by URL, each with every place it sits, in URL order."""
    by_url: dict[str, list[_Reference]] = {}
    for reference in references:
        by_url.setdefault(reference.url, []).append(reference)
    attachments: list[DiscoveredAttachment] = []
    renditions: list[Rendition] = []
    for url, found in sorted(by_url.items()):
        kinds = sorted({item.kind for item in found})
        places = tuple(sorted({item.found for item in found}, key=_place_key))
        if len(kinds) > 1:
            gaps.append(DiscoveryGap(code="conflicting_reference", detail=url))
        elif kinds == ["rendition"]:
            renditions.append(_rendition(url, places))
        else:
            attachments.append(
                DiscoveredAttachment.model_validate(
                    {"url": url, "kind": kinds[0], "found_at": places}
                )
            )
    gaps += _rendition_gaps(renditions, chapters)
    return tuple(attachments), tuple(renditions)


def _place_key(found: FoundAt) -> tuple[str, str]:
    return (found.page_sha256, found.locator)


def _rendition(url: str, places: tuple[FoundAt, ...]) -> Rendition:
    found = _RENDITION.search(urlsplit(url).path)
    return Rendition.model_validate(
        {"url": url, "format": found["format"] if found else "", "found_at": places}
    )


def _rendition_gaps(renditions: list[Rendition], chapters: list[_Page]) -> list[DiscoveryGap]:
    """Every chapter page lists the same renditions; otherwise which ones is unproved."""
    for rendition in renditions:
        pages = {place.page_sha256 for place in rendition.found_at}
        if any(item.sha256 not in pages for item in chapters):
            return [DiscoveryGap(code="renditions_disagree", detail=rendition.url)]
    return []
