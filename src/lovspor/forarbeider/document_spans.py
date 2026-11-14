"""The entries of one chapter region: spans where the shape is known, gaps where not (FC3).

Unlike :mod:`lovspor.forarbeider.chapter_body`, nothing here withholds a
chapter. Each element is read on its own: a heading, paragraph or list item
becomes a span; a quotation or list opens a numbered group around the spans
inside it; anything else (a table, a footnote, a figure, an unknown wrapper,
hidden markup, text outside any element) becomes one gap at that place,
carrying the text it held, and the walk goes on after it.

Quotations are spans like any other: a ``blockquote`` neither hides the text
around it nor stops the walk. Adjacent groups stay apart by their numbers, so
nothing merges two quotations into one.
"""

import re
from dataclasses import dataclass, replace

from lxml import html

from lovspor.forarbeider.chapter_models import normalized
from lovspor.forarbeider.document_models import Gap, GapCode, GapScope, Span, SpanKind, text_digest

_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_INLINE = frozenset({"i", "b", "em", "strong", "u", "sub", "sup", "a", "span"})
#: Wrapper classes read on the sampled pages; a ``div`` with any other class is a gap.
_WRAPPERS = frozenset(
    {
        "K-DEL-KONGESIDE",
        "K-DEL-LEDD",
        "K-DEL-LOVDEL",
        "K-DEL-LOVER",
        "K-DEL-LOVKAP",
        "K-DEL-PARAGRAF",
        "K-DEL-VEDTAKDEL",
        "K-DEL-VEDTAKDELER",
    }
)
_GAP_TAGS = {
    "table": GapCode.TABLE,
    "img": GapCode.IMAGE,
    "picture": GapCode.IMAGE,
    "svg": GapCode.IMAGE,
    "figure": GapCode.FIGURE,
    "video": GapCode.FIGURE,
    "audio": GapCode.FIGURE,
    "iframe": GapCode.FIGURE,
    "object": GapCode.FIGURE,
    "embed": GapCode.FIGURE,
    "canvas": GapCode.FIGURE,
}
_GAP_CLASSES = {
    "K-TBL": GapCode.TABLE,
    "footnotecontainer": GapCode.FOOTNOTE,
    "K-NOTE-FOTNOTE": GapCode.FOOTNOTE,
    "K-DEL-VEDLEGG": GapCode.ATTACHMENT,
}
_HIDING_STYLE = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\b", re.IGNORECASE
)
_SECTION_MARK = "data-longdoc-section"


@dataclass(frozen=True)
class _Style:
    """What the spans inside an element are: kind, level and the groups around them."""

    kind: SpanKind = "paragraph"
    level: int = 0
    quote_group: int | None = None
    quote_depth: int = 0
    list_group: int | None = None
    list_item: int | None = None


_Segment = str | tuple[GapCode, html.HtmlElement]


def _merged(segments: list[_Segment]) -> list[_Segment]:
    """Neighbouring text segments joined, gap segments kept in place."""
    merged: list[_Segment] = []
    for segment in segments:
        if isinstance(segment, str) and merged and isinstance(merged[-1], str):
            merged[-1] += segment
        else:
            merged.append(segment)
    return merged


def is_hidden(element: html.HtmlElement) -> bool:
    """Hidden by an explicit statement on the element, never by a class or a style sheet."""
    stated = str(element.get("aria-hidden")).strip().lower() == "true"
    styled = _HIDING_STYLE.search(str(element.get("style"))) is not None
    return stated or styled or element.get("hidden") is not None


def _classes(element: html.HtmlElement) -> set[str]:
    return set((element.get("class") or "").split())


def _gap_code(element: html.HtmlElement) -> GapCode | None:
    """The gap an element is as a whole, before its content is looked at."""
    if is_hidden(element):
        return GapCode.HIDDEN_CONTENT
    if element.get("data-footnote") is not None:
        return GapCode.FOOTNOTE
    by_class = sorted(_GAP_CLASSES[name] for name in _classes(element) & _GAP_CLASSES.keys())
    return by_class[0] if by_class else _GAP_TAGS.get(element.tag)


def _is_inline(child: html.HtmlElement) -> bool:
    return not isinstance(child.tag, str) or child.tag == "br" or child.tag in _INLINE


def _text_of(element: html.HtmlElement) -> str | None:
    return normalized(element.text_content()) or None


class EntryWalker:
    """Walks one chapter region in source order and collects its entries."""

    def __init__(self, part_id: str, region: html.HtmlElement) -> None:
        self.entries: list[Span | Gap] = []
        self._part_id = part_id
        self._region = region
        self._section = "region"
        self._counts: dict[str, int] = {}
        self._seen: set[str] = set()
        self._quotes = 0
        self._lists = 0
        self._items: dict[int, int] = {}

    def walk(self) -> list[Span | Gap]:
        self._flow(self._region, _Style())
        return self.entries

    def _flow(self, container: html.HtmlElement, style: _Style) -> None:
        """Every child of ``container`` in order, and any text lying loose between them."""
        self._loose(container.text, container)
        for child in container:
            if isinstance(child.tag, str):
                self._block(child, style)
            self._loose(child.tail, container)

    def _loose(self, text: str | None, container: html.HtmlElement) -> None:
        if text and normalized(text):
            self._gap(GapCode.BARE_TEXT, container, normalized(text))

    def _block(self, element: html.HtmlElement, style: _Style) -> None:
        code = _gap_code(element)
        tag = element.tag
        if code is not None:
            self._gap(code, element, _text_of(element))
        elif tag in _HEADINGS:
            self._emit(element, replace(style, kind="heading", level=int(tag[1])))
        elif tag == "p":
            self._emit(element, style)
        elif tag == "blockquote":
            self._quote(element, style)
        elif tag == "ul":
            self._list(element, style)
        elif tag == "div" and element.get(_SECTION_MARK) is not None:
            self._section_div(element, style)
        elif tag == "div" and _classes(element) <= _WRAPPERS:
            self._flow(element, style)
        else:
            self._gap(GapCode.UNSUPPORTED_STRUCTURE, element, _text_of(element))

    def _section_div(self, element: html.HtmlElement, style: _Style) -> None:
        outer = self._section
        self._section = self._section_name(element)
        self._flow(element, style)
        self._section = outer

    def _section_name(self, element: html.HtmlElement) -> str:
        """The section's own id; a repeated one is a gap and gets a numbered suffix."""
        name = element.get("id") or "section"
        if name in self._seen:
            copy = 2
            while f"{name}~{copy}" in self._seen:
                copy += 1
            self._section = name = f"{name}~{copy}"
            self._gap(GapCode.DUPLICATE_SECTION_ID, element, None, scope="part")
        self._seen.add(name)
        return name

    def _quote(self, element: html.HtmlElement, style: _Style) -> None:
        if style.quote_group is None:
            self._quotes += 1
        group = self._quotes if style.quote_group is None else style.quote_group
        inner = replace(
            style, kind="paragraph", level=0, quote_group=group, quote_depth=style.quote_depth + 1
        )
        self._leaf_or_flow(element, inner)

    def _list(self, element: html.HtmlElement, style: _Style) -> None:
        nested = style.kind == "list_item"
        if not nested:
            self._lists += 1
        group = self._lists if style.list_group is None or not nested else style.list_group
        inner = replace(style, kind="list_item", level=style.level + 1 if nested else 1)
        inner = replace(inner, list_group=group)
        self._loose(element.text, element)
        for item in element:
            if item.tag == "li" and _gap_code(item) is None:
                self._items[group] = self._items.get(group, 0) + 1
                self._leaf_or_flow(item, replace(inner, list_item=self._items[group]))
            elif isinstance(item.tag, str):
                code = _gap_code(item) or GapCode.UNSUPPORTED_STRUCTURE
                self._gap(code, item, _text_of(item))
            self._loose(item.tail, element)

    def _leaf_or_flow(self, element: html.HtmlElement, style: _Style) -> None:
        if all(_is_inline(child) for child in element):
            self._emit(element, style)
        else:
            self._flow(element, style)

    def _emit(self, element: html.HtmlElement, style: _Style) -> None:
        """Text of ``element`` as spans, cut around every inline element that is a gap.

        A hidden or unsupported inline element becomes one gap carrying its own
        text at its place, so its text is never inside a span. The spans either
        side are numbered by ``segment``; an uncut element is one span without.
        """
        pieces = _merged(self._segments(element))
        cut = any(not isinstance(piece, str) for piece in pieces)
        segment = 0
        for piece in pieces:
            if not isinstance(piece, str):
                self._gap(piece[0], piece[1], _text_of(piece[1]))
            elif text := normalized(piece):
                segment += 1
                self._span(element, style, text, segment if cut else None)

    def _span(
        self, element: html.HtmlElement, style: _Style, text: str, segment: int | None
    ) -> None:
        self.entries.append(
            Span(
                id=self._next_id(),
                text=text,
                text_sha256=text_digest(text),
                section_id=self._section,
                locator=self._locator(element),
                source_line=element.sourceline,
                segment=segment,
                **vars(style),
            )
        )

    def _segments(self, element: html.HtmlElement) -> list[_Segment]:
        segments: list[_Segment] = [element.text or ""]
        for child in element:
            segments += self._child_segments(child)
            segments.append(child.tail or "")
        return segments

    def _child_segments(self, child: html.HtmlElement) -> list[_Segment]:
        """An allowed inline child's own segments; any other element is one gap segment."""
        if not isinstance(child.tag, str):
            return []
        if child.tag == "br":
            return [" "]
        if child.tag in _INLINE and not is_hidden(child):
            return self._segments(child)
        return [(_gap_code(child) or GapCode.UNSUPPORTED_INLINE, child)]

    def _gap(
        self, code: GapCode, element: html.HtmlElement, text: str | None, scope: GapScope = "span"
    ) -> None:
        self.entries.append(
            Gap(
                id=self._next_id(),
                code=code,
                scope=scope,
                locator=self._locator(element),
                source_line=element.sourceline,
                text=text,
                detail=str(element.tag),
            )
        )

    def _next_id(self) -> str:
        count = self._counts.get(self._section, 0) + 1
        self._counts[self._section] = count
        return f"{self._part_id}/{self._section}/{count}"

    def _locator(self, element: html.HtmlElement) -> str:
        """The element's path below the region: sections by id, everything else by position."""
        steps: list[str] = []
        node = element
        while node is not self._region and node.getparent() is not None:
            parent = node.getparent()
            if node.get(_SECTION_MARK) is not None and node.get("id"):
                steps.append(f'{node.tag}[@id="{node.get("id")}"]')
            else:
                same = [sibling for sibling in parent if sibling.tag == node.tag]
                steps.append(f"{node.tag}[{same.index(node) + 1}]")
            node = parent
        return "/".join(reversed(steps)) or "."
