"""Whether the parsed body is what the saved page says, or what lxml made of it.

lxml answers broken markup with a tree and no error: ``<p>en<p>to`` becomes two
closed paragraphs, a boundary no byte of the page states. So the tags of the
region as written are compared with the tree, open by open and close by close.

Omitting ``</p>`` or ``</li>`` is valid HTML, and it withholds here all the same:
it cannot be told from damage, and all nine sampled pages close every element.
The cost is a held result without a body, never a refused page. The other
structure ``<br>``, ``<img>`` and their kin have is the void elements' own.
"""

from html.parser import HTMLParser

from lxml import etree, html

from lovspor.forarbeider.chapter_body import ChapterBody
from lovspor.forarbeider.chapter_models import HoldReason

_REGION = "//main//div[@data-longdoc-content]"
_REGION_MARK = "data-longdoc-content"
_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

_Event = tuple[str, str]


class _WrittenRegion(HTMLParser):
    """The open and close events of the first marked ``div``, as the bytes state them."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[_Event] = []
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._depth and (self.events or not _is_region(tag, attrs)):
            return
        self.events.append(("start", tag))
        if tag in _VOID:
            self.events.append(("end", tag))
        else:
            self._depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """``<div/>`` opens a ``div`` in a browser: lxml alone would read it as closed."""
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self._depth and tag not in _VOID:
            self.events.append(("end", tag))
            self._depth -= 1


def _is_region(tag: str, attrs: list[tuple[str, str | None]]) -> bool:
    return tag == "div" and any(name == _REGION_MARK for name, _ in attrs)


def _parsed_events(region: html.HtmlElement) -> list[_Event]:
    walk = etree.iterwalk(region, events=("start", "end"))
    return [(event, node.tag) for event, node in walk if isinstance(node.tag, str)]


def tree_states_source(region: html.HtmlElement, text: str) -> bool:
    """``region`` has exactly the tags of the first marked region of ``text``, in order."""
    written = _WrittenRegion()
    written.feed(text)
    written.close()
    return written.events == _parsed_events(region)


def withhold_if_repaired(body: ChapterBody, root: html.HtmlElement, text: str) -> ChapterBody:
    """``body``, or no body at all when its one region is not what ``text`` states."""
    regions = root.xpath(_REGION)
    if len(regions) != 1 or tree_states_source(regions[0], text):
        return body
    return ChapterBody(blocks=None, reasons=body.reasons | {HoldReason.MALFORMED_BODY})
