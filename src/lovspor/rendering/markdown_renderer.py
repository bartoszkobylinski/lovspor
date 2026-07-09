"""Convert Lovdata HTML to Markdown body content.

Rendering is *deterministic*: same XML input → byte-identical Markdown
output. No wall-clock timestamps, no attribute iteration order quirks,
no nondeterministic dict serialization. This invariant is what lets the
change detector rely on MD file content.

The HTML schema is semantic (see docs/legal-and-sources.md and the
reconnaissance notes). Element-by-element mapping:

    <h1>                                    -> # Title
    <section><h2>                           -> ## Heading
    <h3|h4 class='legalArticleHeader'>      -> ### § N. Title
    <h3..h6> (other)                        -> ### Heading
    <article class='legalArticle'>          -> (container, walk children)
    <article class='legalP'>                -> plain paragraph
    <article class='numberedLegalP'>        -> plain paragraph (numbering is in text)
    <article class='listArticle'>           -> plain paragraph (marker is in text)
    <article class='changesToParent'>       -> > blockquote
    <ol>                                    -> 1. 2. 3. numbered list
    <ul>                                    -> -  -  -  bullet list
    <table>                                 -> GFM table (<caption> as a lead-in)

Inline elements (inside any rendered text content):

    <strong>                                -> **text**
    <i> or <em>                             -> *text*
    <a href="url">                          -> [text](url)
    <br>                                    -> hard newline

Not-yet-in-force markup (``futuretitle``, ``futureLegalArticle``,
``futureLegalArticleHeader``) is elided before the walk: the corpus
tracks law as *currently* in force, so an amending act's proposed
provision must not appear as body text, while its operative "``§ N``
skal lyde:" instructions must.

Unknown tags fall through to "walk children, skip this wrapper". That
walk (``_render_children``) emits only child *elements*: an element's
own ``.text`` and each child's ``.tail`` are not rendered. On the
current Lovdata schema those are whitespace-only between block elements.
If real text ever lands there — a ``<p>`` with direct text or any other
unhandled text-bearing element — it guards against silent loss by raising
:class:`~lovspor.errors.RenderError` rather than committing an incomplete
legal document. ``<table>`` is handled explicitly (``_render_table`` /
``_render_mixed_article``), so a table's cell text is never dropped.

Output contract: body only, no YAML frontmatter, no trailing whitespace
except a single trailing newline.
"""

import re
from io import BytesIO

from lxml import etree

from lovspor.errors import ParseError, RenderError
from lovspor.parsing.xml_normalizer import safe_parser

_DROPPED_TEXT_SAMPLE = 60

# Lovdata marks enacted-but-not-yet-in-force text with these classes. The corpus
# tracks law as *currently* in force, so the subtrees are elided before the walk
# rather than rendered as body text. Lovdata's own en-dash elision marks already
# stand where content is omitted.
_NOT_IN_FORCE_CLASSES = frozenset(
    {"futuretitle", "futureLegalArticle", "futureLegalArticleHeader"},
)

_INLINE_STRONG = {"strong"}
_INLINE_EMPHASIS = {"i", "em"}
_LEGAL_ARTICLE_HEADER_CLASS = "legalArticleHeader"
_PARAGRAPH_CLASSES = frozenset(
    {"legalP", "numberedLegalP", "listArticle"},
)
_CHANGE_NOTE_CLASS = "changesToParent"
# Tags _render_element renders as blocks (mirrors its dispatch); everything
# else is inline. Used by _render_mixed_article to keep block children (a
# heading, a nested list, a table) out of the inline paragraph accumulator.
_BLOCK_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "article", "ol", "ul", "table"})

_INLINE_ESCAPE = str.maketrans(
    {
        "\\": "\\\\",
        "`": "\\`",
        "*": "\\*",
    },
)
_ORDERED_LIST_MARKER = re.compile(r"^(\d{1,9})([.)])(?=\s|$)")


def _escape_inline_text(text: str) -> str:
    """Escape inline Markdown specials in a raw text run so law text cannot
    turn into emphasis, a code span, or a stray escape.

    Deliberately conservative: only backslash, backtick, and asterisk are
    escaped. Brackets, underscores, and angle brackets are left verbatim —
    a lone bracket is literal in CommonMark, ``_`` inside a word is not
    emphasis, and escaping them would pepper legal citations and
    identifiers with backslashes for no real ambiguity.
    """
    return text.translate(_INLINE_ESCAPE)


def _escape_block_leading(text: str) -> str:
    """Escape a leading block-structural token so text starting with
    ``- ``/``# ``/``> ``/``| ``/``1. `` is not reparsed as a list, heading,
    quote, or table row.

    Markdown block parsing is line-oriented, and a ``<br/>`` renders as a
    newline inside a paragraph / blockquote / list item, so the check runs
    on EVERY line — not just the first character of the block. A ``- `` on
    line two would otherwise start a list (and break out of a blockquote).
    Asterisk-led bullets are already handled by ``_escape_inline_text``.
    """
    return "\n".join(_escape_line_leading(line) for line in text.split("\n"))


def _escape_line_leading(line: str) -> str:
    if not line:
        return line
    if line[0] in "#>|":
        return "\\" + line
    if line[0] in "-+" and (len(line) == 1 or line[1].isspace()):
        return "\\" + line
    marker = _ORDERED_LIST_MARKER.match(line)
    if marker:
        return f"{marker.group(1)}\\{marker.group(2)}{line[marker.end() :]}"
    return line


def _escape_href(href: str) -> str:
    """Percent-encode the characters that break a Markdown link
    destination: parentheses (which close the ``(...)``) and spaces (which
    would start a link title). Everything else is left verbatim so an
    already-encoded URL is not double-encoded.
    """
    return href.replace("(", "%28").replace(")", "%29").replace(" ", "%20")


def render_markdown(xml_bytes: bytes) -> str:
    """Convert a Lovdata HTML document body to Markdown."""
    try:
        tree = etree.parse(BytesIO(xml_bytes), parser=safe_parser(remove_blank_text=False))
    except etree.XMLSyntaxError as exc:
        raise ParseError(f"malformed XML: {exc}") from exc
    main = tree.find(".//main")
    if main is None:
        raise ParseError("no <main> in document")
    _elide_not_in_force(main)
    return _render_children(main).rstrip() + "\n"


def _elide_not_in_force(root: etree._Element) -> None:
    """Drop the enacted-but-not-yet-in-force subtrees before rendering.

    An amending act carries the provision it *will* install, tagged
    ``futuretitle`` / ``futureLegalArticle``. Publishing that as body text would
    put proposed law into a corpus of current law; refusing the whole document
    would instead lose its operative "``§ N`` skal lyde:" instructions. Elide the
    proposed subtree and render everything around it.

    The matches are collected before removal: mutating a tree mid-iteration
    would skip siblings.
    """
    for elem in [child for child in root.iter() if _is_not_in_force(child)]:
        _remove_preserving_tail(elem)


def _is_not_in_force(elem: etree._Element) -> bool:
    classes = (elem.get("class") or "").split()
    return bool(_NOT_IN_FORCE_CLASSES.intersection(classes))


def _remove_preserving_tail(elem: etree._Element) -> None:
    """Detach ``elem``, keeping its tail — that text belongs to the parent.

    lxml stores text after a close tag on the element itself, so a plain
    ``parent.remove(child)`` would silently take the following run of law text
    with it.
    """
    parent = elem.getparent()
    if parent is None:
        return
    tail = elem.tail or ""
    if tail:
        previous = elem.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(elem)


def _render_element(elem: etree._Element) -> str:
    tag = elem.tag
    classes = (elem.get("class") or "").split()
    if tag in {"h1", "h2"}:
        marker = "#" if tag == "h1" else "##"
        return f"{marker} {_inline(elem)}\n\n"
    if tag in {"h3", "h4", "h5", "h6"}:
        return _render_sub_heading(elem, classes)
    if tag == "article":
        return _render_article(elem, classes)
    if tag in {"ol", "ul"}:
        return _render_list(elem, ordered=(tag == "ol"))
    if tag == "table":
        return _render_table(elem)
    return _render_children(elem)


def _render_sub_heading(
    elem: etree._Element,
    classes: list[str],
) -> str:
    if _LEGAL_ARTICLE_HEADER_CLASS in classes:
        return _render_legal_article_header(elem)
    return f"### {_inline(elem)}\n\n"


def _render_children(elem: etree._Element) -> str:
    _assert_no_dropped_text(elem)
    return "".join(_render_element(child) for child in elem)


def _assert_no_dropped_text(elem: etree._Element) -> None:
    """Raise if this container carries non-whitespace text the child-only
    walk would silently drop.

    ``_render_children`` emits only child *elements*; the element's own
    ``.text`` (before the first child) and each child's ``.tail`` (after
    its close tag) are never rendered here. On the current Lovdata schema
    those nodes are whitespace-only between block elements. Real text means
    an unhandled text-bearing element reached the block walk — a ``<p>``
    with direct text, a ``<table>``, or a future schema addition. Dropping
    it would publish an incomplete legal document, so fail loudly. Text
    handled elsewhere (paragraph/heading/list/anchor rendering) never
    reaches this walk, so intentional drops are not affected.
    """
    skipped = (elem.text, *(child.tail for child in elem))
    offending = next((text.strip() for text in skipped if text and text.strip()), "")
    if offending:
        raise RenderError(
            f"renderer would drop block-level text ({offending[:_DROPPED_TEXT_SAMPLE]!r}) "
            f"in <{elem.tag}>; an unhandled text-bearing element reached the "
            f"child-only walk",
        )


def _render_article(
    elem: etree._Element,
    classes: list[str],
) -> str:
    if elem.find("table") is not None:
        return _render_mixed_article(elem)
    if _CHANGE_NOTE_CLASS in classes:
        text = _escape_block_leading(_inline(elem))
        return f"> {text}\n\n" if text else ""
    if any(c in _PARAGRAPH_CLASSES for c in classes):
        text = _escape_block_leading(_inline(elem))
        return f"{text}\n\n" if text else ""
    # Any other article (footnote, defaultP, centeredP, the legalArticle
    # container) may carry inline text next to element children — a footnote's
    # "Jf. <a/>" reference is the common case. Render inline runs as paragraphs
    # and block children as blocks, rather than dropping the text via the
    # child-only walk.
    return _render_mixed_article(elem)


def _render_legal_article_header(elem: etree._Element) -> str:
    value_span = elem.find(".//span[@class='legalArticleValue']")
    title_span = elem.find(".//span[@class='legalArticleTitle']")
    value = _span_text(value_span)
    title = _span_text(title_span)
    if value and title:
        return f"### {value}. {title}\n\n"
    if value:
        return f"### {value}\n\n"
    return f"### {_inline(elem)}\n\n"


def _render_mixed_article(elem: etree._Element) -> str:
    """Render an ``<article>`` as a run of inline paragraphs and block children.

    The general renderer for every article that is not a plain paragraph or a
    blockquote: footnotes (``footnote``: label span + "Jf. <a/>" reference
    text), ``defaultP`` / ``centeredP`` notes, and the ``legalArticle``
    container (a header plus nested paragraph articles). Block children (a
    ``legalArticleHeader``, a nested list, a table) go through
    ``_render_element`` so a header stays a ``###`` heading and a table a GFM
    block; genuinely-inline children (spans, links, ``<br/>``) accumulate into
    the paragraphs between them, so no reference text is dropped.
    """
    blocks: list[str] = []
    inline: list[str] = [_escape_inline_text(elem.text or "")]
    for child in elem:
        if child.tag in _BLOCK_TAGS:
            blocks.append(_flush_inline(inline))
            blocks.append(_render_element(child))
            inline = [_escape_inline_text(child.tail or "")]
        else:
            inline.append(_inline_for_child(child))
            inline.append(_escape_inline_text(child.tail or ""))
    blocks.append(_flush_inline(inline))
    return "".join(block for block in blocks if block)


def _flush_inline(parts: list[str]) -> str:
    text = "".join(parts).strip()
    return f"{_escape_block_leading(text)}\n\n" if text else ""


def _render_table(elem: etree._Element) -> str:
    """Render an HTML ``<table>`` as a GitHub-Flavored-Markdown table.

    Every row is padded to the widest row's column count, expanding
    ``colspan`` into empty pad cells so the grid stays rectangular (GFM has
    no cell spanning). A ``<thead>`` row is the header; a headerless table
    gets a blank header row so no data row is promoted into header position.
    """
    rows = list(elem.iter("tr"))
    width = max((_row_width(row) for row in rows), default=0)
    if width == 0:
        return ""
    header, body = _table_header_and_body(elem, rows, width)
    lines = [_table_row(header), _table_separator(width)]
    lines.extend(_table_row(_expand_row(row, width)) for row in body)
    return _table_caption(elem) + "\n".join(lines) + "\n\n"


def _table_caption(elem: etree._Element) -> str:
    caption = elem.find("caption")
    if caption is None:
        return ""
    text = _escape_block_leading(_inline(caption))
    return f"{text}\n\n" if text else ""


def _table_header_and_body(
    elem: etree._Element,
    rows: list[etree._Element],
    width: int,
) -> tuple[list[str], list[etree._Element]]:
    thead = elem.find(".//thead")
    header_tr = thead.find(".//tr") if thead is not None else None
    if header_tr is None:
        return [""] * width, rows
    body = [row for row in rows if row is not header_tr]
    return _expand_row(header_tr, width), body


def _row_cells(row: etree._Element) -> list[etree._Element]:
    return [cell for cell in row if cell.tag in {"th", "td"}]


def _colspan(cell: etree._Element) -> int:
    raw = cell.get("colspan")
    if raw is None:
        return 1
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _row_width(row: etree._Element) -> int:
    return sum(_colspan(cell) for cell in _row_cells(row))


def _expand_row(row: etree._Element, width: int) -> list[str]:
    cells: list[str] = []
    for cell in _row_cells(row):
        cells.append(_render_table_cell(cell))
        cells.extend([""] * (_colspan(cell) - 1))
    cells.extend([""] * (width - len(cells)))
    return cells[:width]


def _render_table_cell(cell: etree._Element) -> str:
    # Reuse inline rendering, then adapt for a GFM cell: a <br/> became a
    # newline (which would break the row) -> an HTML line break; and escape
    # the pipe that would otherwise start a new cell.
    return _inline(cell).replace("|", "\\|").replace("\n", "<br>")


def _table_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _table_separator(width: int) -> str:
    return "| " + " | ".join(["---"] * width) + " |"


def _span_text(span: etree._Element | None) -> str:
    if span is None:
        return ""
    # itertext() yields text in the span's subtree but NOT the span's own tail,
    # which is what we want. tostring(method="text") incorrectly includes the tail.
    # lxml-stubs types itertext() as Iterator[str | bytes] but it only yields str
    # for text parsed from an XML document.
    return _escape_inline_text("".join(span.itertext()).strip())  # type: ignore[arg-type]


_LIST_TAGS = frozenset({"ul", "ol"})


def _render_list(elem: etree._Element, *, ordered: bool) -> str:
    lines = _render_list_lines(elem, ordered=ordered, depth=0)
    if not lines:
        return ""
    return "\n".join(lines) + "\n\n"


def _render_list_lines(
    elem: etree._Element,
    *,
    ordered: bool,
    depth: int,
) -> list[str]:
    """Render a list (and any nested sub-lists) as indented Markdown lines.

    Returns raw lines without a trailing blank; the top-level caller
    (_render_list) adds the trailing blank. Recurses into <ul>/<ol>
    children of each <li> with deeper indent.
    """
    items = elem.findall("./li")
    lines: list[str] = []
    indent = "  " * depth
    for index, li in enumerate(items, start=1):
        prefix = f"{index}. " if ordered else "- "
        lines.append(f"{indent}{prefix}{_escape_block_leading(_inline_of_li(li))}")
        for child in li:
            if child.tag in _LIST_TAGS:
                lines.extend(
                    _render_list_lines(
                        child,
                        ordered=(child.tag == "ol"),
                        depth=depth + 1,
                    ),
                )
    return lines


def _inline_of_li(li: etree._Element) -> str:
    """Inline text of a <li> excluding any nested <ul>/<ol> children.

    Nested lists are rendered separately by _render_list_lines with
    their own indentation; their tails (text after the closing tag) are
    preserved as inline content of the parent li.
    """
    parts = [_escape_inline_text(li.text or "")]
    for child in li:
        if child.tag in _LIST_TAGS:
            parts.append(_escape_inline_text(child.tail or ""))
            continue
        parts.append(_inline_for_child(child))
        parts.append(_escape_inline_text(child.tail or ""))
    return "".join(parts).strip()


def _inline(elem: etree._Element) -> str:
    parts = [_escape_inline_text(elem.text or "")]
    for child in elem:
        parts.append(_inline_for_child(child))
        parts.append(_escape_inline_text(child.tail or ""))
    return "".join(parts).strip()


def _inline_for_child(child: etree._Element) -> str:
    tag = child.tag
    if tag in _INLINE_STRONG:
        return f"**{_inline(child)}**"
    if tag in _INLINE_EMPHASIS:
        return f"*{_inline(child)}*"
    if tag == "a":
        return _render_anchor(child)
    if tag == "br":
        return "\n"
    return _inline(child)


def _render_anchor(elem: etree._Element) -> str:
    text = _inline(elem)
    href = elem.get("href") or ""
    if href:
        return f"[{text}]({_escape_href(href)})"
    return text
