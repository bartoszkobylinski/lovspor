"""The body of one chapter, read from the region the source itself marks.

Allowlist only. ``div[data-longdoc-content]`` holds ``data-longdoc-section``
divs, read on nine pages of Prop. 54 LS and Prop. 112 L on 2026-11-01. Each tag
and wrapper class below was seen there, and anything else withholds the body
rather than being read past: text that lands outside a known element is never
recovered with ``text_content()``.

What cannot be reproduced is found first and named, whatever else the chapter
holds. Text candidates are only prompts for a human reviewer: they never clear
anything, and missing one says nothing about the chapter.
"""

import re
from collections.abc import Iterable

from lxml import html
from pydantic import BaseModel, ConfigDict

from lovspor.forarbeider.chapter_models import (
    WITHHOLDING,
    BlockKind,
    HoldReason,
    TextBlock,
    normalized,
)

_REGION = "//main//div[@data-longdoc-content]"
_FOOTNOTES = (
    ".//*[contains(@class, 'K-NOTE-FOTNOTE') or contains(@class, 'footnotecontainer')"
    " or @data-footnote]"
)
_ANNEX_MARK = ".//*[contains(@class, 'K-DEL-VEDLEGG') or starts-with(@id, 'ved')]"
_ANNEX_TITLES = (
    "//ul[@data-longdoc-table-of-content]/li[not(a)][starts-with(normalize-space(), 'Vedlegg')]"
    "/following-sibling::li/a"
)
_ANNEX_HEADING = re.compile(r"vedlegg\b", re.IGNORECASE)

_MEDIA = {
    "img": HoldReason.IMAGE,
    "picture": HoldReason.IMAGE,
    "svg": HoldReason.IMAGE,
    "table": HoldReason.TABLE,
    "figure": HoldReason.FIGURE,
    "video": HoldReason.FIGURE,
    "audio": HoldReason.FIGURE,
    "iframe": HoldReason.FIGURE,
    "object": HoldReason.FIGURE,
    "embed": HoldReason.FIGURE,
    "canvas": HoldReason.FIGURE,
}
_CANDIDATES = (
    (HoldReason.HEARING_PARTICIPANTS, re.compile(r"høring", re.IGNORECASE)),
    (
        HoldReason.PERSONAL_DATA,
        re.compile(
            r"[\w.+-]+@[\w-]+\.[\w.-]+"
            r"|(?:\+47|\btlf\.?|\btelefon)[:\s]*\d(?:\s?\d){7}"
            r"|(?<!\d)\d{6}\s?\d{5}(?!\d)",
            re.IGNORECASE,
        ),
    ),
    (
        HoldReason.SENSITIVE_DATA,
        re.compile(
            r"helseopplysning|diagnose|straffbar|straffedom|etnisk|religiøs|seksuell"
            r"|fagforening|biometri",
            re.IGNORECASE,
        ),
    ),
    (
        HoldReason.THIRD_PARTY_WORK,
        re.compile(r"©|copyright|gjengitt med tillatelse", re.IGNORECASE),
    ),
)

_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_INLINE = {"i", "b", "em", "strong", "u", "sub", "sup", "a", "span"}
_WRAPPERS = {
    "K-DEL-KONGESIDE",
    "K-DEL-LEDD",
    "K-DEL-LOVDEL",
    "K-DEL-LOVER",
    "K-DEL-LOVKAP",
    "K-DEL-PARAGRAF",
    "K-DEL-VEDTAKDEL",
    "K-DEL-VEDTAKDELER",
}
#: Named by the scans above, so reading past them here adds nothing false.
_EXCLUDED = {"K-TBL", "footnotecontainer", "K-DEL-VEDLEGG"}

_Style = tuple[BlockKind, int]
_PLAIN: _Style = ("paragraph", 0)


class ChapterBody(BaseModel):
    """The blocks of a chapter, ``None`` when any reason withholds them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocks: tuple[TextBlock, ...] | None
    reasons: frozenset[HoldReason]


def read_body(root: html.HtmlElement, chapter_title: str) -> ChapterBody:
    """What the page proves about the body of the chapter called ``chapter_title``."""
    regions = root.xpath(_REGION)
    if len(regions) != 1:
        return _withheld({HoldReason.AMBIGUOUS_BOUNDARY if regions else HoldReason.DAMAGED_DOM})
    region = regions[0]
    walker = _Walker()
    for section in _sections(region):
        walker.flow(section, _PLAIN)
    reasons = (
        walker.reasons
        | _boundary_reasons(region, chapter_title, walker.blocks)
        | _source_reasons(region)
        | _navigation_reasons(root, chapter_title)
    )
    return _body(tuple(walker.blocks), reasons, region)


def accounted_for(region: html.HtmlElement, blocks: Iterable[TextBlock]) -> bool:
    """Every non-space character of the region is in ``blocks``, in the same order."""
    return _squeezed("".join(block.text for block in blocks)) == _squeezed(region.text_content())


def _squeezed(text: str) -> str:
    return "".join(text.split())


def _withheld(reasons: set[HoldReason]) -> ChapterBody:
    return ChapterBody(blocks=None, reasons=frozenset(reasons))


def _body(
    blocks: tuple[TextBlock, ...], reasons: set[HoldReason], region: html.HtmlElement
) -> ChapterBody:
    if not WITHHOLDING.intersection(reasons) and not accounted_for(region, blocks):
        reasons = reasons | {HoldReason.INCOMPLETE_EXTRACTION}
    if WITHHOLDING.intersection(reasons):
        return _withheld(reasons)
    return ChapterBody(blocks=blocks, reasons=frozenset(reasons))


def _sections(region: html.HtmlElement) -> list[html.HtmlElement]:
    return [child for child in region if _is_section(child)]


def _is_section(element: html.HtmlElement) -> bool:
    return element.tag == "div" and element.get("data-longdoc-section") is not None


def _bare_text(element: html.HtmlElement) -> bool:
    """Text sitting directly in ``element``, where no allowlisted block holds it."""
    texts = [element.text, *(child.tail for child in element)]
    return any(text and text.strip() for text in texts)


def _boundary_reasons(
    region: html.HtmlElement, title: str, blocks: list[TextBlock]
) -> set[HoldReason]:
    reasons = _section_reasons(region)
    headings = region.xpath(".//h1")
    if not headings:
        reasons.add(HoldReason.DAMAGED_DOM)
    elif (
        len(headings) > 1
        or " ".join(headings[0].text_content().split()) != title
        or (blocks and (blocks[0].kind, blocks[0].level) != ("heading", 1))
    ):
        reasons.add(HoldReason.AMBIGUOUS_BOUNDARY)
    if all(block.kind == "heading" for block in blocks):
        reasons.add(HoldReason.INCOMPLETE_EXTRACTION)
    return reasons


def _section_reasons(region: html.HtmlElement) -> set[HoldReason]:
    children = [child for child in region if isinstance(child.tag, str)]
    identifiers = [child.get("id") for child in children]
    understood = all(_is_section(child) for child in children) and not _bare_text(region)
    if understood and None not in identifiers and len(set(identifiers)) == len(identifiers):
        return set()
    return {HoldReason.AMBIGUOUS_BOUNDARY}


def _source_reasons(region: html.HtmlElement) -> set[HoldReason]:
    reasons = {_MEDIA[node.tag] for node in region.iter(*_MEDIA)}
    if region.xpath(_FOOTNOTES):
        reasons.add(HoldReason.FOOTNOTES)
    if _is_annex(region):
        reasons.add(HoldReason.ANNEX)
    if region.xpath(".//blockquote"):
        reasons.add(HoldReason.QUOTATION)
    text = region.text_content()
    return reasons | {reason for reason, pattern in _CANDIDATES if pattern.search(text)}


def _navigation_reasons(root: html.HtmlElement, title: str) -> set[HoldReason]:
    """The source's own divider: every chapter listed after it is an annex."""
    listed = {" ".join(link.text_content().split()) for link in root.xpath(_ANNEX_TITLES)}
    return {HoldReason.ANNEX} if title in listed else set()


def _is_annex(region: html.HtmlElement) -> bool:
    headings = region.xpath(".//h1 | .//h2 | .//h3 | .//h4 | .//h5 | .//h6")
    stated = any(_ANNEX_HEADING.match(heading.text_content().strip()) for heading in headings)
    return stated or bool(region.xpath(_ANNEX_MARK))


class _Walker:
    """Turns allowlisted elements into blocks, and anything else into ``damaged_dom``."""

    def __init__(self) -> None:
        self.blocks: list[TextBlock] = []
        self.reasons: set[HoldReason] = set()

    def flow(self, container: html.HtmlElement, style: _Style) -> None:
        """Blocks inside ``container``, which may hold no text of its own."""
        if _bare_text(container):
            self.reasons.add(HoldReason.DAMAGED_DOM)
        for child in container:
            if isinstance(child.tag, str):
                self._block(child, style)

    def _block(self, element: html.HtmlElement, style: _Style) -> None:
        tag = element.tag
        if tag in _HEADINGS and style[0] != "quote":
            self._emit(("heading", int(tag[1])), element)
        elif tag == "p":
            self._emit(style, element)
        elif tag == "blockquote":
            self._leaf_or_flow(element, ("quote", 0))
        elif tag == "ul" and style[0] != "quote":
            self._list(element, style)
        elif tag == "div" and _wrapper_classes(element) <= _WRAPPERS and element.get("class"):
            self.flow(element, style)
        elif tag not in _MEDIA and not _wrapper_classes(element) & _EXCLUDED:
            self.reasons.add(HoldReason.DAMAGED_DOM)

    def _list(self, element: html.HtmlElement, style: _Style) -> None:
        depth = style[1] + 1 if style[0] == "list_item" else 1
        if _bare_text(element):
            self.reasons.add(HoldReason.DAMAGED_DOM)
        for item in element:
            if item.tag == "li":
                self._leaf_or_flow(item, ("list_item", depth))
            elif isinstance(item.tag, str):
                self.reasons.add(HoldReason.DAMAGED_DOM)

    def _leaf_or_flow(self, element: html.HtmlElement, style: _Style) -> None:
        if all(_is_inline(child) for child in element):
            self._emit(style, element)
        else:
            self.flow(element, style)

    def _emit(self, style: _Style, element: html.HtmlElement) -> None:
        text = normalized(self._inline(element))
        if text.strip():
            self.blocks.append(TextBlock(kind=style[0], level=style[1], text=text))

    def _inline(self, element: html.HtmlElement) -> str:
        parts = [element.text or ""]
        for child in element:
            parts.extend((self._inline_child(child), child.tail or ""))
        return "".join(parts)

    def _inline_child(self, child: html.HtmlElement) -> str:
        if child.tag == "br":
            return " "
        if child.tag in _INLINE:
            return self._inline(child)
        if isinstance(child.tag, str) and child.tag not in _MEDIA:
            self.reasons.add(HoldReason.DAMAGED_DOM)
        return ""


def _is_inline(child: html.HtmlElement) -> bool:
    return not isinstance(child.tag, str) or child.tag == "br" or child.tag in _INLINE


def _wrapper_classes(element: html.HtmlElement) -> set[str]:
    return set((element.get("class") or "").split())
