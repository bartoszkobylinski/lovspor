"""Which stored blobs carry a document, measured offline (issue #332, option c).

The archive files every fetch that returned bytes as an ``ArtifactObservation``
and says nothing about what the bytes hold. On 2026-09-16 the regulation-shaped
HTML blobs were opened for the first time: the median ``<main>`` held 199
characters, 54% held under 300, and 25% contained a ``§`` — most were a
JavaScript shell, not a document. Nothing in the record distinguishes the two.

This module measures that from the blobs already stored, with no change to the
observation schema and no request to anyone. Recording the same measurements
on new captures is a later, separate step (option a on the issue).

The measurements are raw on purpose. ``text_chars`` and ``§`` are proxies for
"carries a document", not for "is a *forskrift*": a budget passes too, and
ADR-0010 defers every legal classification. Nothing here performs one.

**PDFs are counted, never measured.** The engine ships no PDF text extractor,
and the issue's own PDF figures came from an ad-hoc ``pypdf`` run. A count of
PDF blobs per source is honest; a guessed text length is not.
"""

import statistics
from collections.abc import Callable
from typing import Literal

from lxml import etree, html
from pydantic import BaseModel, ConfigDict, Field

from lovspor.observatory.listing import safe_html_parser
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation, ObservationRecord

#: The issue's bar for "has text": 54.4% of core HTML blobs fell under it.
DOCUMENT_TEXT_THRESHOLD = 300
SECTION_SIGN = "§"

#: Elements whose text a reader never sees as the document: code, styling, the
#: "turn on JavaScript" notice a shell carries, and inert templates.
_INVISIBLE = ("script", "style", "noscript", "template")

_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_PDF_TYPES = frozenset({"application/pdf"})

BlobForm = Literal["html", "pdf", "other"]
Region = Literal["main", "body", "none"]


class HtmlText(BaseModel):
    """What one HTML blob's document region holds.

    ``region`` says which element was measured: ``main`` as the issue measured,
    ``body`` when a page has no ``<main>``, ``none`` when nothing parsed or the
    page has neither (a frameset): a ``<head>`` is not a document region.
    """

    model_config = ConfigDict(frozen=True)

    text_chars: int
    has_section_sign: bool
    region: Region


def blob_form(content_type: str) -> BlobForm:
    """HTML, PDF or anything else, by the media type the server recorded."""
    media = content_type.split(";", 1)[0].strip().lower()
    if media in _HTML_TYPES:
        return "html"
    if media in _PDF_TYPES:
        return "pdf"
    return "other"


def _charset(content_type: str) -> str | None:
    for parameter in content_type.split(";")[1:]:
        name, _, value = parameter.partition("=")
        if name.strip().lower() == "charset" and value.strip():
            return value.strip().strip('"')
    return None


def _decoded(payload: bytes, content_type: str) -> str | bytes:
    """The page as text, when its encoding is known; else the bytes as served.

    The header's charset first, as a browser reads it; then UTF-8 if the bytes
    are valid UTF-8. Left to itself, lxml reads an undeclared page as Latin-1,
    which turns every ``ø`` into two characters and inflates the count. Bytes
    that fit neither go to lxml, which still honours a ``<meta charset>``. So
    does a page opening with an XML declaration: lxml refuses one in text, and
    reads the encoding it names from the bytes. Only a declaration at the very
    first character: after leading whitespace lxml accepts the text, and from
    the bytes it would ignore the declaration and fall back to Latin-1.
    """
    for encoding in (_charset(content_type), "utf-8"):
        if encoding is None:
            continue
        try:
            text = payload.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
        return payload if text.startswith("<?xml") else text
    return payload


def _region(page: str | bytes) -> tuple[html.HtmlElement | None, Region]:
    try:
        document = html.document_fromstring(page, parser=safe_html_parser())
    except etree.ParserError:
        return None, "none"
    for tag in ("main", "body"):
        found = document.find(f".//{tag}")
        if isinstance(found, html.HtmlElement):
            return found, "main" if tag == "main" else "body"
    return None, "none"


def _visible_text(region: html.HtmlElement) -> str:
    for invisible in list(region.iter(*_INVISIBLE)):
        if isinstance(invisible, html.HtmlElement):
            invisible.drop_tree()
    return " ".join(region.text_content().split())


def measure_html(payload: bytes, content_type: str = "text/html") -> HtmlText:
    """The visible text of ``<main>`` (else ``<body>``), whitespace collapsed.

    A run of whitespace counts as one character, so indentation in the served
    markup does not read as text. The text of a dropped element's tail stays:
    it belongs to the parent, not to the script before it.
    """
    region, name = _region(_decoded(payload, content_type))
    if region is None:
        return HtmlText(text_chars=0, has_section_sign=False, region=name)
    text = _visible_text(region)
    return HtmlText(text_chars=len(text), has_section_sign=SECTION_SIGN in text, region=name)


class SourceDocuments(BaseModel):
    """One source's distinct blobs and what their bytes hold."""

    html_chars: list[int] = Field(default_factory=list)
    html_without_main: int = 0
    html_with_section_sign: int = 0
    html_documents: int = 0
    pdf_blobs: int = 0
    other_blobs: int = 0
    unreadable_blobs: int = 0

    @property
    def html_blobs(self) -> int:
        return len(self.html_chars)

    @property
    def median_html_chars(self) -> float | None:
        return statistics.median(self.html_chars) if self.html_chars else None

    @property
    def html_under_threshold(self) -> int:
        return sum(1 for chars in self.html_chars if chars < DOCUMENT_TEXT_THRESHOLD)

    def add_html(self, measured: HtmlText) -> None:
        self.html_chars.append(measured.text_chars)
        self.html_without_main += measured.region != "main"
        self.html_with_section_sign += measured.has_section_sign
        self.html_documents += (
            measured.has_section_sign and measured.text_chars >= DOCUMENT_TEXT_THRESHOLD
        )


class DocumentReport(BaseModel):
    """Every source's measurements, and whether the log read to its end."""

    sources: dict[str, SourceDocuments] = Field(default_factory=dict)
    complete: bool = True

    def total(self) -> SourceDocuments:
        """All sources as one, for the totals line."""
        merged = SourceDocuments()
        for source in self.sources.values():
            for name, value in source:
                setattr(merged, name, getattr(merged, name) + value)
        return merged


def _measure_into(source: SourceDocuments, payload: bytes, content_type: str) -> None:
    form = blob_form(content_type)
    if form == "html":
        source.add_html(measure_html(payload, content_type))
    elif form == "pdf":
        source.pdf_blobs += 1
    else:
        source.other_blobs += 1


def collect_documents(
    log: ObservationLog, report: DocumentReport
) -> Callable[[ObservationRecord], None]:
    """A scan callback measuring each distinct blob once per source.

    Keyed on source and hash: the same bytes observed on every sweep are one
    blob, but the same bytes served by two sources count for each of them. A
    blob absent from disk — tombstoned or lost — is counted, not guessed at.
    """
    seen: set[tuple[str, str]] = set()

    def collect(record: ObservationRecord) -> None:
        if not isinstance(record, ArtifactObservation):
            return
        key = (record.authority_id, record.sha256)
        if key in seen:
            return
        seen.add(key)
        source = report.sources.setdefault(record.authority_id, SourceDocuments())
        try:
            payload = log.read_blob(record.sha256)
        except FileNotFoundError:
            source.unreadable_blobs += 1
            return
        _measure_into(source, payload, record.content_type)

    return collect


def build_report(log: ObservationLog) -> DocumentReport:
    """One streamed pass over the corrected log, reading each blob it names.

    Corrected, so a record re-attributed under ADR-0015 counts for the source
    it was re-filed to. A log that does not read to its end yields a report
    marked incomplete; the caller refuses it rather than printing part of the
    archive as the whole.
    """
    report = DocumentReport()
    scan = log.scan_corrected_into(collect_documents(log, report))
    report.complete = scan.complete
    return report
