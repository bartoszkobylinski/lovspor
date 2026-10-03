"""Captured bytes to lines of text: the extractor's readers (ADR-0016 S2).

One reader per source form, each deterministic for a fixed library version
and each returning the document's text as non-empty lines, whitespace
collapsed within a line. A reader that cannot read raises
``UnreadableSourceError``; the extractor turns that into a counted hold.

**HTML.** The document region is the observatory's (``<main>``, else
``<body>``), parsed with the observatory's hardened parser. Inside it, page
chrome is dropped before any text is read — navigation, sidebars, headers
without the page title, footers, forms — and a region holding exactly one
``<article>`` is narrowed to it. A CMS that embeds another page's teaser in
every sidebar (the classification study's §6.1) therefore cannot move the
regulation's text, and so cannot mint a version.

**PDF.** ``pypdf``, pinned exactly: the page text stream, never the document
metadata (Author, Creator), which names people and is not the regulation.

**DOCX.** ``word/document.xml`` read in memory — nothing is extracted to
disk — under a size cap, through the pipeline's hardened XML parser.

Lines that are page furniture rather than law — update stamps, "Skriv ut",
page numbers — are dropped in every form: a stamp that changes daily would
otherwise change the content hash daily.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterable

from lxml import etree, html
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from lovspor.errors import UnreadableSourceError
from lovspor.observatory.document_report import _decoded, _region, blob_form
from lovspor.parsing.xml_normalizer import safe_parser
from lovspor.promotion.models import SourceForm

PDF_LIBRARY_VERSION = "6.19.0"
MAX_DOCX_PART_BYTES = 20 * 1024 * 1024

_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_DOCX_PART = "word/document.xml"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_BLOCK_TAGS = frozenset(
    {"p", "div", "section", "article", "header", "main", "li", "ul", "ol", "dl", "dt", "dd"}
    | {"h1", "h2", "h3", "h4", "h5", "h6", "table", "tr", "td", "th", "pre", "blockquote", "br"}
)
_CHROME_TAGS = ("nav", "aside", "footer", "form", "button", "select", "iframe", "svg")
_INVISIBLE_TAGS = ("script", "style", "noscript", "template")
_CHROME_ROLES = frozenset({"navigation", "complementary", "banner", "contentinfo", "search"})
_CHROME_CLASS_WORDS = frozenset({"sidebar", "breadcrumb", "breadcrumbs", "cookie", "share"})
_CLASS_WORD = re.compile(r"[a-z]+")
_WHITESPACE = re.compile(r"\s+")

# Whole lines only. Each is page furniture a reader never takes for law.
_FURNITURE_LINE = re.compile(
    r"(?:(?:sist\s+)?(?:oppdatert|endret|publisert|revidert)\b\s*:?\s*\d.*"
    r"|skriv\s+ut|del\s+denne\s+siden|del\s+siden|til\s+toppen"
    r"|side\s+\d+(?:\s+av\s+\d+)?|\d{1,3})",
    re.IGNORECASE,
)


def source_form(content_type: str) -> SourceForm | None:
    """HTML, PDF or DOCX by the media type the server recorded; else ``None``."""
    form = blob_form(content_type)
    if form != "other":
        return SourceForm(form)
    media = content_type.split(";", 1)[0].strip().lower()
    return SourceForm.DOCX if media == _DOCX_MEDIA else None


def html_lines(payload: bytes, content_type: str) -> tuple[str, ...]:
    """The lines of the page's document region, page chrome removed."""
    try:
        region, _ = _region(_decoded(payload, content_type))
    except ValueError as error:
        raise UnreadableSourceError(f"HTML did not parse: {error}") from error
    if region is None:
        raise UnreadableSourceError("HTML has no document region (<main> or <body>)")
    _drop_chrome(region)
    return _clean_lines(_block_text(_narrowed(region)).split("\n"))


def pdf_lines(payload: bytes) -> tuple[str, ...]:
    """The text of every page, in page order; never the document metadata."""
    try:
        reader = PdfReader(io.BytesIO(payload), strict=False)
        pages = [page.extract_text() for page in reader.pages]
    except (PyPdfError, ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
        # pypdf reports a malformed file through its own errors and, deeper in
        # the object graph, through these builtins; each is "cannot read".
        raise UnreadableSourceError(f"PDF did not parse: {error}") from error
    return _clean_lines("\n".join(pages).split("\n"))


def docx_lines(payload: bytes) -> tuple[str, ...]:
    """One line per paragraph of ``word/document.xml``, its runs joined."""
    try:
        root = etree.fromstring(_docx_part(payload), parser=safe_parser(remove_blank_text=False))
    except etree.XMLSyntaxError as error:
        raise UnreadableSourceError(f"DOCX {_DOCX_PART} did not parse: {error}") from error
    paragraphs = (
        "".join(node.text or "" for node in paragraph.iter(f"{_W}t"))
        for paragraph in root.iter(f"{_W}p")
    )
    return _clean_lines(paragraphs)


def _docx_part(payload: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            info = archive.getinfo(_DOCX_PART)
            if info.file_size > MAX_DOCX_PART_BYTES:
                msg = f"{_DOCX_PART} is larger than {MAX_DOCX_PART_BYTES} bytes"
                raise UnreadableSourceError(msg)
            return archive.read(info)
    except KeyError as error:
        raise UnreadableSourceError(f"DOCX has no {_DOCX_PART}") from error
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise UnreadableSourceError(f"DOCX is not a readable zip: {error}") from error


def _drop_chrome(region: html.HtmlElement) -> None:
    for node in [node for node in region.iterdescendants() if _is_chrome(node)]:
        node.drop_tree()


def _is_chrome(node: html.HtmlElement) -> bool:
    if node.tag in _CHROME_TAGS or node.tag in _INVISIBLE_TAGS:
        return True
    if node.tag == "header" and node.find(".//h1") is None:
        return True
    if (node.get("role") or "").strip().lower() in _CHROME_ROLES:
        return True
    words = _CLASS_WORD.findall(f"{node.get('class') or ''} {node.get('id') or ''}".lower())
    return not _CHROME_CLASS_WORDS.isdisjoint(words)


def _narrowed(region: html.HtmlElement) -> html.HtmlElement:
    articles = [node for node in region.iter("article") if isinstance(node, html.HtmlElement)]
    return articles[0] if len(articles) == 1 else region


def _block_text(region: html.HtmlElement) -> str:
    # Source whitespace, newlines included, is layout; only block elements break lines.
    for node in region.iter():
        node.text = _spaced(node.text)
        node.tail = _spaced(node.tail)
        if isinstance(node.tag, str) and node.tag in _BLOCK_TAGS:
            node.text = "\n" + node.text
            node.tail = "\n" + node.tail
    return str(region.text_content())


def _spaced(text: str | None) -> str:
    return _WHITESPACE.sub(" ", text or "")


def _clean_lines(lines: Iterable[str]) -> tuple[str, ...]:
    collapsed = (" ".join(line.split()) for line in lines)
    return tuple(line for line in collapsed if line and not _FURNITURE_LINE.fullmatch(line))
