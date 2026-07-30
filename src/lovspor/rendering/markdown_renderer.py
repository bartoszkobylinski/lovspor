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
    <div role='heading' aria-level='N'>     -> ###### ATX heading (level clamped)
    <article class='legalArticle'>          -> (container, walk children)
    <article class='legalP'>                -> plain paragraph
    <article class='numberedLegalP'>        -> plain paragraph (numbering is in text)
    <article class='listArticle'>           -> plain paragraph (marker is in text)
    <article class='changesToParent'>       -> > blockquote
    <p> (bare, e.g. 'leddfortsettelse')     -> paragraph (EU consolidated regs)
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
If real text ever lands there — an unhandled text-bearing element, e.g. a
future schema addition — it guards against silent loss by raising
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

RENDERER_VERSION = 6
"""Stamp recorded on every rendered document (``ManifestRecord.renderer_version``).

Change detection keys on the *upstream XML* hash, so a renderer fix leaves every
existing document classified ``unchanged`` and never reaches it — the corpus stays
frozen on whatever this code produced when each file was last written. Sync
therefore also re-renders any current document whose stamp is not this value.

BUMP THIS (+1) IN THE SAME COMMIT AS ANY CHANGE TO RENDERED OUTPUT — body or
frontmatter. Forgetting is a silent corpus bug, not a test failure, so
``tests/unit/test_rendering_golden.py`` pins the produced bytes against this
number and goes red when the two drift apart.
"""

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
_FOOTNOTE_REFERENCE_CLASS = "footnotereference"
_CHANGE_NOTE_CLASS = "changesToParent"
_HEADING_ROLE = "heading"
_MAX_HEADING_LEVEL = 6
_DEFAULT_HEADING_LEVEL = 6
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# Tags _render_element renders as blocks. _render_mixed_article uses these (via
# _is_block_element) to keep block children — a heading, a nested list, a table,
# a bare <p> — out of the inline paragraph accumulator.
_BLOCK_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "article", "p", "ol", "ul", "table"})
# Block-level wrappers Lovdata puts around runs of block content. They are block
# by HTML semantics, so they must not reach _inline — it concatenates the whole
# subtree with no separator, fusing every article, row and list item inside into
# one paragraph. They may also carry text of their own beside block children,
# which is why they render through _render_mixed_article rather than the
# child-only walk.
#
# Counted in <main> across the 2026-07-22 corpus (5,917 documents):
#   li 184,344 in 3,478 docs | footer 3,873 in 1,094 | div 3,526 in 139
#   blockquote 28 in 18      | figure/figcaption 11 in 6
#   dl/dt/dd   0 in 0
# The definition list tags are kept although unobserved, and that is a choice,
# not an oversight: an unhandled wrapper is not a loud failure. As a child of an
# article it goes to _inline and flattens SILENTLY, which is the whole defect
# this constant exists to prevent, and Lovdata has added element types before.
# Anything added here on that reasoning belongs in this list with a 0.
_WRAPPER_TAGS = frozenset(
    {"div", "footer", "figure", "figcaption", "blockquote", "li", "dl", "dt", "dd"},
)

_INLINE_ESCAPE = str.maketrans(
    {
        "\\": "\\\\",
        "`": "\\`",
        "*": "\\*",
    },
)
_ORDERED_LIST_MARKER = re.compile(r"^(\d{1,9})([.)])(?=\s|$)")
# The "(" that turns a preceding "]" into an inline link destination.
_LINK_DEST_OPEN = re.compile(r"(?<=\])\(")


def _escape_inline_text(text: str) -> str:
    """Escape inline Markdown specials in a raw text run so law text cannot
    turn into emphasis, a code span, a link, or a stray escape.

    Deliberately conservative: only backslash, backtick, and asterisk are
    escaped, plus the ``(`` of a ``](`` sequence. Brackets, underscores, and
    angle brackets are left verbatim — a lone bracket is literal in CommonMark,
    ``_`` inside a word is not emphasis, and escaping them would pepper legal
    citations and identifiers with backslashes for no real ambiguity.

    ``](`` is the exception, because a lone bracket being literal does not make
    the *pair* literal: it closes a link text and opens a destination. Chemical
    nomenclature writes exactly that shape —
    ``[1-(5-fluoropentyl)-1H-indol-3-yl](2,2,3,3-tetrametylsyklopropyl)metanon``
    — and a CommonMark reader then renders the parenthesised fragment as a URL
    it never displays, so the legal text becomes invisible and unquotable. Only
    the paren is escaped; the brackets stay readable in the raw file.
    """
    return _LINK_DEST_OPEN.sub(r"\\\g<0>", text.translate(_INLINE_ESCAPE))


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
    if tag in _HEADING_TAGS:
        return _render_heading(elem, tag, classes)
    if tag in {"article", "p"}:
        return _render_article(elem, classes)
    if tag in {"ol", "ul"}:
        return _render_list(elem, ordered=(tag == "ol"))
    if tag == "table":
        return _render_table(elem)
    if tag == "div" and elem.get("role") == _HEADING_ROLE:
        return _render_heading_div(elem)
    return _render_mixed_article(elem) if tag in _WRAPPER_TAGS else _render_children(elem)


def _render_heading(elem: etree._Element, tag: str, classes: list[str]) -> str:
    if tag in {"h1", "h2"}:
        marker = "#" if tag == "h1" else "##"
        return _atx_heading(marker, _inline(elem))
    return _render_sub_heading(elem, classes)


def _atx_heading(marker: str, text: str) -> str:
    """Emit ``text`` as an ATX heading, spilling anything past the first line
    into a following paragraph.

    A ``<br/>`` inside a heading is common in EU-regulation titles (2,565
    headings across 152 documents), and an ATX heading cannot span lines: the
    continuation is a separate block whatever the renderer intends. Emitting
    ``_inline()`` raw therefore left the heading reading as truncated
    mid-phrase, with its rest glued underneath and unescaped — two of them
    opened with "18. " / "5. " and reparsed as an ordered list. Making the
    split explicit is the same rule ``_render_heading_div`` already applies to
    ARIA heading blocks, applied to real ``h1``-``h6`` as well.
    """
    head, _, spill = text.partition("\n")
    heading = f"{marker} {head}\n\n" if head else ""
    spill = spill.strip()
    if not spill:
        return heading
    return heading + f"{_escape_block_leading(spill)}\n\n"


def _render_heading_div(elem: etree._Element) -> str:
    """Render a ``<div role="heading">`` ARIA heading block.

    EU-regulation annexes (animal health, food safety, sanctions) lay out
    their section titles as generic heading divs
    (``<div aria-level="7" role="heading">Avsnitt 1<br/>…</div>``) instead of
    the ``legalArticle`` schema, so the block walk had no branch for them and
    the lost-content guard froze ~29 forskrifter out of the corpus. Map the
    div to an ATX heading clamped to Markdown's six levels; a ``<br/>``
    separates a numbered label from a descriptive continuation, and a heading
    cannot span lines, so the first line is the heading and the rest follows
    as a paragraph.
    """
    return _atx_heading("#" * _aria_heading_level(elem), _inline(elem))


def _aria_heading_level(elem: etree._Element) -> int:
    raw = elem.get("aria-level")
    if raw is None:
        return _DEFAULT_HEADING_LEVEL
    try:
        level = int(raw)
    except ValueError:
        return _DEFAULT_HEADING_LEVEL
    return min(max(level, 1), _MAX_HEADING_LEVEL)


def _render_sub_heading(
    elem: etree._Element,
    classes: list[str],
) -> str:
    if _LEGAL_ARTICLE_HEADER_CLASS in classes:
        return _render_legal_article_header(elem)
    return _atx_heading("###", _inline(elem))


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
    if _CHANGE_NOTE_CLASS in classes:
        return _render_change_note(elem)
    # ANY block child, not just a <table>: a paragraph-class article holding a
    # nested <ol>/<ul>/<p>/<article> used to fall through to _inline below,
    # which concatenates descendant text with no separator and fuses the last
    # word before the block into the block's first word
    # ("Grunndata:" + "navn" -> "Grunndata:navn"). Nothing was lost, but the
    # fused words stopped being findable, which breaks search, quotation and
    # the embeddings computed from this output. 74,622 such words stood in
    # 2,690 of 5,916 documents (analysis/llm-infra/10-byte-audit-results.md).
    if any(_is_block_element(child) for child in elem):
        return _render_mixed_article(elem)
    # Everything else — a paragraph-class article whose children are all inline,
    # a footnote, defaultP, centeredP, the legalArticle container — goes the same
    # way. _render_mixed_article with no block child among them IS the inline
    # paragraph render, so the paragraph classes needed no branch of their own:
    # removing the one they had changed 0 of 5,917 rendered documents (SHA-256
    # per document, re-verified against this renderer, not an earlier one).
    return _render_mixed_article(elem)


def _render_change_note(elem: etree._Element) -> str:
    """Render a ``changesToParent`` note as a blockquote.

    Almost every change note is one inline run ("Endret ved lov 9 des 2011
    nr. 52"). One in the corpus is not: ``skatteloven`` quotes the whole text
    of a repealed § back under "Paragrafen lød før oppheving:" as a nested
    ``<ol>``. Routing that one to the plain block path would fix its fusion but
    strip the marking that says the quoted provision is no longer in force —
    trading a boundary defect for a worse one. So the blockquote is applied to
    the structured render instead, which keeps both.

    Checked corpus-wide before this ordering was chosen: 42,299 change notes
    exist, exactly 1 carries a block child, and none carries a table.
    """
    if not any(_is_block_element(child) for child in elem):
        text = _escape_block_leading(_inline(elem))
        return f"> {text}\n\n" if text else ""
    body = _render_mixed_article(elem).rstrip("\n")
    if not body:
        return ""
    quoted = "\n".join(f"> {line}" if line else ">" for line in body.split("\n"))
    return f"{quoted}\n\n"


def _render_legal_article_header(elem: etree._Element) -> str:
    """Render a ``legalArticleHeader`` as "### {value}. {title}".

    Built from the two spans alone. Anything else the header carries is NOT
    emitted — in this corpus that is always a footnote-reference marker, 424 of
    them in 83 documents. A DOCUMENTED, DELIBERATE DROP, not an oversight:

    * 206 of those markers sit on the § number rather than after the title
      (``<span>§ 5</span>.<sup>1</sup> <span>(skade som ikkje…)</span>``), so
      emitting them verbatim yields "### § 5.1 (skade som ikkje…)" and
      "### § 46.1" — a form that reads as a citation of subsection 5.1. The
      corpus is consumed by ``validate_citation`` and ``verify_quote``; a
      heading that impersonates a different provision is worse than a missing
      marker.
    * Nothing legal is lost with it. The footnote's own text still renders,
      carrying its own label, in the ``<footer class="footnotes">`` block of
      the same §. What the marker adds is the pointer, and the association
      survives positionally.

    ``scripts/audit_render_bytes.py`` subtracts exactly this in its *adjusted*
    XML variant, the way it already subtracts the not-in-force elision, and
    keeps it visible in *raw*. That subtraction is only honest while this
    docstring is true: if the drop ever widens beyond these markers, say so
    here, or the audit will quietly absorb the difference.
    """
    value_span = elem.find(".//span[@class='legalArticleValue']")
    title_span = elem.find(".//span[@class='legalArticleTitle']")
    value = _span_text(value_span)
    title = _span_text(title_span)
    if value and title:
        return _atx_heading("###", f"{value}. {title}")
    if value:
        return _atx_heading("###", value)
    return _atx_heading("###", _inline(elem))


def _is_block_element(elem: etree._Element) -> bool:
    """Whether ``_render_element`` renders ``elem`` as a block, not inline.

    ``_render_mixed_article`` uses this to keep block children out of the
    inline accumulator. Mirrors the ``_render_element`` dispatch so a block
    nested inside a mixed article renders the same as one at top level, instead
    of being flattened through the inline path.
    """
    return elem.tag in _BLOCK_TAGS or elem.tag in _WRAPPER_TAGS


def _render_mixed_article(
    elem: etree._Element,
    *,
    skip_tags: frozenset[str] = frozenset(),
) -> str:
    """Render an ``<article>`` as a run of inline paragraphs and block children.

    The general renderer for every article that is not a plain paragraph or a
    blockquote: footnotes (``footnote``: label span + "Jf. <a/>" reference
    text), ``defaultP`` / ``centeredP`` notes, and the ``legalArticle``
    container (a header plus nested paragraph articles). Block children (a
    ``legalArticleHeader``, a nested list, a table, a ``<div role="heading">``,
    a bare ``<p>``) go through ``_render_element`` so a header stays a ``###``
    heading and a table a GFM block; genuinely-inline children (spans, links,
    ``<br/>``) accumulate into the paragraphs between them, so no text is lost.

    ``skip_tags`` names children the caller renders itself; their tails are
    still consumed as the parent's inline text. ``_list_item_lines`` uses it
    for nested ``<ul>``/``<ol>``, which need the list walk's own indentation
    rather than a block rendered in place.
    """
    blocks: list[str] = []
    inline: list[str] = [_escape_inline_text(elem.text or "")]
    for child in elem:
        if child.tag in skip_tags:
            _append_inline(inline, _escape_inline_text(child.tail or ""))
        elif _is_block_element(child):
            blocks.append(_flush_inline(inline))
            blocks.append(_render_element(child))
            inline = [_escape_inline_text(child.tail or "")]
        else:
            _append_inline(inline, _inline_for_child(child))
            _append_inline(inline, _escape_inline_text(child.tail or ""))
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
    """Text of a heading's value/title span, with footnote markers delimited.

    A title span can carry a marker — ``§ 19. (… samhøve med
    trygdeavtalelova<sup>1</sup>)`` — and reading the subtree as raw text fused
    it into the preceding word while the same marker in body text came out as
    ``[^1]``: one source, two renderings, inside a single document.

    Deliberately NOT ``_inline``: that also applies emphasis, and a value span
    of ``§ 1-<em>1</em>`` would render ``### § 1-*1*``, which
    ``headings.parse_section_heading`` cannot read — trading a fused word for
    an unaddressable §. Only the footnote marker needs a delimiter here.

    Stops before the span's own tail, which is what the caller needs;
    ``tostring(method="text")`` would swallow it.
    """
    if span is None:
        return ""
    return _escape_inline_text(_span_inner(span).strip())


def _span_inner(elem: etree._Element) -> str:
    """Text of ``elem``'s subtree, with footnote markers delimited.

    Recurses over DIRECT children rather than walking ``iter()``: a marker is
    emitted from its whole subtree at once, so descending into it again would
    emit its descendants twice (``<sup>1<em>2</em></sup>`` came out as
    ``[^12]2``).

    A ``<br/>`` becomes a newline, which ``_atx_heading`` then spills into a
    following paragraph. No heading span in the current corpus carries one (0 of
    5,914 documents), so this changes no output today — but reading the subtree
    as raw text would fuse the two lines into one word, the exact defect
    ``_render_mixed_article`` exists to prevent, and Lovdata has added markup
    shapes before.
    """
    parts: list[str] = [elem.text or ""]
    for child in elem:
        if child.tag == "br":
            parts.append("\n")
        elif child.tag == "sup" and _FOOTNOTE_REFERENCE_CLASS in (child.get("class") or "").split():
            parts.append(f"[^{''.join(child.itertext()).strip()}]")  # type: ignore[arg-type]
        else:
            parts.append(_span_inner(child))
        parts.append(child.tail or "")
    return "".join(parts)


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
        lines.extend(_list_item_lines(li, prefix=prefix, indent=indent))
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


def _list_item_lines(li: etree._Element, *, prefix: str, indent: str) -> list[str]:
    """One ``<li>`` as Markdown lines: the marker first, content column after.

    A block child other than a nested list — in this corpus always an
    ``<article>``, 184,344 of them across 3,478 documents — used to go through
    the item's inline accumulator, which fused the item's own text into the
    block's first word and flattened any table the block contained. Rendering
    it as a block and re-indenting to the item's content column keeps the
    boundaries and keeps the item a single list item.
    """
    body = _render_mixed_article(li, skip_tags=_LIST_TAGS).rstrip("\n")
    if not body:
        return [f"{indent}{prefix.rstrip()}"]
    first, *rest = body.split("\n")
    pad = " " * len(prefix)
    return [
        f"{indent}{prefix}{first}",
        *(f"{indent}{pad}{line}" if line else "" for line in rest),
    ]


def _inline(elem: etree._Element) -> str:
    parts = [_escape_inline_text(elem.text or "")]
    for child in elem:
        _append_inline(parts, _inline_for_child(child))
        _append_inline(parts, _escape_inline_text(child.tail or ""))
    return "".join(parts).strip()


def _append_inline(parts: list[str], chunk: str) -> None:
    """Append ``chunk``, escaping a leading ``(`` that a preceding ``]`` would
    turn into a link destination.

    ``_escape_inline_text`` cannot see this case: the bracket and the paren come
    from different nodes. It happens when the renderer's own footnote marker
    supplies the bracket and the source's next text node the paren —
    ``på 2<sup class="footnotereference">7</sup>(2)`` rendered as ``2[^7](2)``,
    a link whose destination was "2". A ``]`` this renderer opened as real link
    text always arrives inside one chunk, so its ``(`` is never reached here.
    """
    if chunk.startswith("(") and _last_char(parts) == "]":
        chunk = "\\" + chunk
    parts.append(chunk)


def _last_char(parts: list[str]) -> str:
    return next((part[-1] for part in reversed(parts) if part), "")


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
    if tag == "sup" and _FOOTNOTE_REFERENCE_CLASS in (child.get("class") or "").split():
        return _render_footnote_reference(child)
    return _inline(child)


def _render_footnote_reference(elem: etree._Element) -> str:
    """Render a footnote marker as ``[^n]`` rather than as a bare number.

    The marker sits flush against the word it annotates, so rendering its text
    alone fused the two: ``Amtskasserere<sup>1</sup>`` became
    ``Amtskasserere1``. Nothing is lost, but the word stops being the word —
    7,940 markers across 664 documents produced a token no reader will ever
    type. Measured consequence, on the live corpus before this change:
    ``verify_quote`` REJECTED the faithful quote of ``§ 8`` in
    ``lov-om-omordning-af-det-civile-embedsverk`` and accepted only the variant
    carrying the digit — an anti-hallucination guard calling real statutory
    text a hallucination.

    ``[^n]`` is GFM's own footnote-reference syntax, and the delimiter is the
    point: ``\\w+`` tokenization now yields ``amtskasserere`` and ``1``
    separately, so BM25 and the embeddings index the real word, while the
    pointer to the note survives.

    The reference is deliberately left orphaned — the corpus emits no matching
    ``[^n]: …`` definition. Real GFM footnotes need a document-unique label, and
    these are not: labels repeat inside a single document in 383 of the 1,092
    documents carrying references (8,402 repeated occurrences; ``sf-20221027-1901``
    has 732 references drawn from 12 distinct labels). Pairing them would mean
    inventing identifiers the source does not provide. GFM renders an unmatched
    reference literally, which states the pointer without inventing a target.
    """
    return f"[^{_inline(elem)}]"


def _render_anchor(elem: etree._Element) -> str:
    text = _inline(elem)
    href = elem.get("href") or ""
    if href:
        return f"[{text}]({_escape_href(href)})"
    return text
