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
regulation's text, and so cannot mint a version. A region holding several
``<article>``s is narrowed to the innermost one whose own lines start the
regulation exactly where the whole region does — the same title line and
the same first section (``anchors.py``) — when exactly one such article
exists; otherwise the whole region is read. A regulation that is one FAQ
item among sibling items and teasers (issue #576) is then read without what
follows its last section, which would otherwise change its hash with every
news item the page lists.

**PDF.** ``pypdf``, pinned exactly: the page text stream, never the document
metadata (Author, Creator), which names people and is not the regulation.
``fontTools`` is pinned with it: pypdf reads an embedded CFF font's own
encoding through it, and a subset font with no ToUnicode map is otherwise
read as control characters (#573).
The page's hard line wraps are rejoined, so a wrapped title or paragraph is
one line again; a section heading, a list item, an enactment line and a new
sentence after a full line stay lines of their own. A page pasted from Word
into a CMS keeps the same hard wraps as ``<br>``; those are rejoined by the
same rules, but only inside one block element, never across two. DOCX
carries its author's paragraphs and is never rejoined.

**DOCX.** ``word/document.xml`` read in memory — nothing is extracted to
disk — under a size cap, through the pipeline's hardened XML parser. A
paragraph is one line; a soft line break (``w:br``, ``w:cr``) inside it starts
another, and a tab is a space — except right after a section heading, where
Word's "heading, tabs, text" layout in one paragraph is a heading and its
text on two lines (#652).

Word's Symbol-font bullet reaches the text as a Private Use Area character
(U+F0B7; Wingdings U+F0A7, U+F076, U+F0D8) that renders as nothing or as
tofu. At the start of a line it is the list marker and is read as one
(#654); anywhere else it is kept as it is.

Lines that are page furniture rather than law — update stamps ("Sist
endra"), "Skriv ut", "Del på", "Til toppen", a CMS feedback widget ("Fant du
det du trengte?"), page numbers — are dropped in every form: a stamp that
changes daily would otherwise change the content hash daily. A byline is kept,
because the personal-data gate must see it.
"""

from __future__ import annotations

import copy
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
from lovspor.promotion.anchors import anchor_lines
from lovspor.promotion.models import SourceForm

PDF_LIBRARY_VERSION = "6.21.1"
FONT_LIBRARY_VERSION = "4.67.0"
MAX_DOCX_PART_BYTES = 20 * 1024 * 1024

_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_DOCX_PART = "word/document.xml"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_TAB = "\t"
_BREAK = "\n"
_INLINE_TEXT = {f"{_W}tab": _TAB, f"{_W}br": _BREAK, f"{_W}cr": _BREAK}
_PUA_BULLET = re.compile(r"^\s*[\uf0b7\uf0a7\uf076\uf0d8]\s*")

_BLOCK_TAGS = frozenset(
    {"p", "div", "section", "article", "header", "main", "li", "ul", "ol", "dl", "dt", "dd"}
    | {"h1", "h2", "h3", "h4", "h5", "h6", "table", "tr", "td", "th", "pre", "blockquote"}
)
_CHROME_TAGS = ("nav", "aside", "footer", "form", "button", "select", "iframe", "svg")
_INVISIBLE_TAGS = ("script", "style", "noscript", "template")
_CHROME_ROLES = frozenset({"navigation", "complementary", "banner", "contentinfo", "search"})
_CHROME_CLASS_WORDS = frozenset({"sidebar", "breadcrumb", "breadcrumbs", "cookie", "share"})
_CLASS_WORD = re.compile(r"[a-z]+")
_WHITESPACE = re.compile(r"\s+")
# Whitespace in source text is collapsed to one space first, so this
# separator can only come from a <br>.
_BR = "\u2028"

# Whole lines only. Each is page furniture a reader never takes for law, in
# bokmål and nynorsk. A byline ("Publisert av ...") is not furniture: it may
# name a person, and the personal-data gate must see it.
_FURNITURE_LINE = re.compile(
    r"(?:(?:sist\s+)?(?:oppdatert|endret|endra|publisert|revidert)\b\s*:?\s*\d.*"
    r"|(?:skriv\s+ut|del|tips\s+(?:en|ein)\s+venn)(?:\s+(?:denne|dette)?\s*(?:siden|sida))?"
    r"|del\s+på(?:\s+(?:facebook|twitter|x|linkedin|e-post|epost|messenger))?"
    r"|(?:(?:tilbake|gå)\s+)?til\s+toppen"
    r"|fan[tn]\s+du\s+det\s+du\s+\w+(?:\s+etter)?\s*\?"
    r"|var\s+(?:denne\s+(?:siden|sida)|informasjonen|innhaldet|innholdet)\s+nyttig\s*\?"
    r"|side\s+\d+(?:\s+av\s+\d+)?|\d{1,3})",
    re.IGNORECASE,
)

# A PDF breaks lines where the page ends, not where the text does. A line
# continues the one before it unless it starts a unit of its own or follows a
# section heading: lowercase, a digit or "(" after a word, anything after a
# comma. Uppercase after a full line is a new paragraph, so a title is joined
# only when its continuation reads as one.
_SECTION_START = re.compile(r"§|kap(?:ittel|\.)\s*(?:\d|[IVXLC]+\b)", re.IGNORECASE)
_MONTH = r"(?:jan|feb|mar|apr|mai|jun|jul|aug|sep|okt|nov|des)"
_BULLET = r"[-\u2013\u2014\u2022\u25aa*]\s"
_LIST_ITEM = re.compile(
    rf"{_BULLET}|\(?[a-zæøå]{{1,2}}\)\s|\(?\d+[.)]\s(?!{_MONTH})", re.IGNORECASE
)
# "8.4 Avkorting": a numbered subsection, never a date ("1.1.2010.") or an amount.
_NUMBERED_HEADING = re.compile(r"\d+(?:\.\d+)+\s+[A-ZÆØÅ]")
_ENACTMENT_START = re.compile(r"(?:vedtatt|vedteke|vedteken|fastsatt|fastsett)\b", re.IGNORECASE)
_MAX_HEADING_CHARS = 100
# "Sør-" / "Trøndelag" is one word the line end split at its own hyphen, and
# rejoins with no space (#638). A suspended hyphen ("vann- og avløp") is
# followed by its conjunction and keeps the space.
_HYPHEN_WRAP = re.compile(r"\w-$")
_FIRST_WORD = re.compile(r"[^\s/,.]+")
_SUSPENDED_BEFORE = frozenset({"og", "eller", "samt"})


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
    return _narrowed_lines(region)


def pdf_lines(payload: bytes) -> tuple[str, ...]:
    """The text of every page, in page order; never the document metadata."""
    try:
        reader = PdfReader(io.BytesIO(payload), strict=False)
        pages = [page.extract_text() for page in reader.pages]
    except (PyPdfError, ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
        # pypdf reports a malformed file through its own errors and, deeper in
        # the object graph, through these builtins; each is "cannot read".
        raise UnreadableSourceError(f"PDF did not parse: {error}") from error
    return _rejoined(_clean_lines("\n".join(pages).split("\n")))


def docx_lines(payload: bytes) -> tuple[str, ...]:
    """One line per paragraph of ``word/document.xml``, its runs joined."""
    try:
        root = etree.fromstring(_docx_part(payload), parser=safe_parser(remove_blank_text=False))
    except etree.XMLSyntaxError as error:
        raise UnreadableSourceError(f"DOCX {_DOCX_PART} did not parse: {error}") from error
    paragraphs = (_paragraph_text(paragraph) for paragraph in root.iter(f"{_W}p"))
    return _clean_lines(line for text in paragraphs for line in _paragraph_lines(text))


def _paragraph_text(paragraph: etree._Element) -> str:
    """The paragraph's text runs, minus a ``PAGEREF`` field's result (#638).

    A contents entry ends on its page number, a field Word computes; joined to
    the title it reads ``Sør-Trøndelag1``. Fields nest (the whole contents is a
    ``TOC`` field), so the innermost open field decides. A tab and a soft line
    break are text too (#652), as ``_TAB`` and ``_BREAK``.
    """
    fields: list[str] = []
    text: list[str] = []
    for node in paragraph.iter(f"{_W}fldChar", f"{_W}instrText", f"{_W}t", *_INLINE_TEXT):
        if node.tag == f"{_W}fldChar":
            _step_field(fields, node.get(f"{_W}fldCharType"))
        elif node.tag == f"{_W}instrText":
            fields[-1:] = [f"{field}{node.text or ''}" for field in fields[-1:]]
        elif not (fields and fields[-1].split()[:1] == ["PAGEREF"]):
            text.append(_INLINE_TEXT.get(node.tag, node.text or ""))
    return "".join(text)


def _paragraph_lines(text: str) -> Iterable[str]:
    """One line per soft break; a section heading's text after its tabs is a line of its own."""
    for piece in text.split(_BREAK):
        heading, tab, rest = piece.partition(_TAB)
        heading = " ".join(heading.split())
        if tab and _is_section_heading(heading):
            yield heading
            yield rest.replace(_TAB, " ")
        else:
            yield piece.replace(_TAB, " ")


def _step_field(fields: list[str], kind: str | None) -> None:
    if kind == "begin":
        fields.append("")
    elif kind == "end" and fields:
        fields.pop()


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


def _narrowed_lines(region: html.HtmlElement) -> tuple[str, ...]:
    articles = [node for node in region.iter("article") if isinstance(node, html.HtmlElement)]
    if len(articles) == 1:
        return _lines_of(articles[0])
    lines = _lines_of(region)
    holder = _holding_article(articles, anchor_lines(lines))
    return lines if holder is None else _lines_of(holder)


def _holding_article(
    articles: list[html.HtmlElement], anchors: tuple[str, str] | None
) -> html.HtmlElement | None:
    """The one innermost article starting the regulation where the region does, else ``None``."""
    if anchors is None:
        return None
    holding = [article for article in articles if anchor_lines(_lines_of(article)) == anchors]
    innermost = [
        article
        for article in holding
        if not any(other in article.iterdescendants() for other in holding)
    ]
    return innermost[0] if len(innermost) == 1 else None


def _lines_of(element: html.HtmlElement) -> tuple[str, ...]:
    # _block_text rewrites the tree's text in place; a copy leaves the region
    # readable again for the next candidate article.
    blocks = _block_text(copy.deepcopy(element)).split("\n")
    return tuple(line for block in blocks for line in _rejoined(_clean_lines(block.split(_BR))))


def _block_text(region: html.HtmlElement) -> str:
    # Source whitespace, newlines included, is layout; only block elements break lines.
    for node in region.iter():
        node.text = _spaced(node.text)
        node.tail = _spaced(node.tail)
        if isinstance(node.tag, str) and node.tag in _BLOCK_TAGS:
            node.text = "\n" + node.text
            node.tail = "\n" + node.tail
        elif node.tag == "br":
            node.tail = _BR + node.tail
    return str(region.text_content())


def _spaced(text: str | None) -> str:
    return _WHITESPACE.sub(" ", text or "")


def _clean_lines(lines: Iterable[str]) -> tuple[str, ...]:
    collapsed = (" ".join(_PUA_BULLET.sub("- ", line).split()) for line in lines)
    return tuple(line for line in collapsed if line and not _FURNITURE_LINE.fullmatch(line))


def _rejoined(lines: tuple[str, ...]) -> tuple[str, ...]:
    joined: list[str] = []
    for line in lines:
        if joined and _wrapped_at_hyphen(joined[-1], line):
            joined[-1] = f"{joined[-1]}{line}"
        elif joined and _continues(joined[-1], line):
            joined[-1] = f"{joined[-1]} {line}"
        else:
            joined.append(line)
    return tuple(joined)


def _wrapped_at_hyphen(previous: str, line: str) -> bool:
    if not _HYPHEN_WRAP.search(previous) or _SECTION_START.match(line):
        return False
    first = _FIRST_WORD.match(line)
    return first is not None and first.group().casefold() not in _SUSPENDED_BEFORE


def _continues(previous: str, line: str) -> bool:
    if _starts_a_unit(line) or _is_section_heading(previous):
        return False
    if line[0].islower() or previous.endswith(","):
        return True
    return (line[0].isdigit() or line[0] == "(") and previous[-1].isalnum()


def _starts_a_unit(line: str) -> bool:
    return any(
        pattern.match(line)
        for pattern in (_SECTION_START, _LIST_ITEM, _NUMBERED_HEADING, _ENACTMENT_START)
    )


def _is_section_heading(line: str) -> bool:
    short = len(line) <= _MAX_HEADING_CHARS and not line.endswith((".", ","))
    return short and _SECTION_START.match(line) is not None
