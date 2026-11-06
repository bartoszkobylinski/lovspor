"""Lovtidend and consolidated documents as the replay sees them.

Both datasets share one HTML-in-XML dialect: a ``header`` key-info list and a
``main`` body of ``section``/``article`` elements. An act's body is read into
an ordered provision tree; an amending law's body into its parts (``I``, ``II``
…), each a flat sequence of nodes that :mod:`lovspor.legaltime.instructions`
interprets. Markup is carried verbatim; only text is normalised.
"""

from datetime import datetime
from hashlib import sha256
from typing import Literal

from lxml import etree
from pydantic import BaseModel, ConfigDict

from lovspor.legaltime.errors import LegalTimeDocumentError
from lovspor.legaltime.text import collapse
from lovspor.parsing.xml_normalizer import safe_parser

_NOTE_CLASSES = frozenset({"changesToParent", "footnotereference", "footnoteLabel"})
_TRANSPARENT = frozenset({"document-change", "listArticle"})
_LIST_TAGS = frozenset({"ul", "ol", "li"})
_LEDD_CLASSES = frozenset({"legalP", "numberedLegalP"})
_BLOCK_TAGS = frozenset({"article", "p", "ul", "ol", "li", "div", "section", "h2", "h3", "h4"})
NodeKind = Literal["paragraph", "provision", "ledd", "title", "other"]


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Header(Frozen):
    refid: str
    legacy_id: str
    dokid: str
    title: str
    date_in_force: str
    published_at: datetime | None
    changes_to_documents: tuple[str, ...]


class Ledd(Frozen):
    xml: str
    text: str


class Provision(Frozen):
    key: str
    label: str
    heading: str
    ledd: tuple[Ledd, ...]


class Node(Frozen):
    kind: NodeKind
    css_class: str
    text: str
    provision: Provision | None = None
    ledd: Ledd | None = None
    change_part: tuple[str, ...] = ()


class Part(Frozen):
    label: str
    nodes: tuple[Node, ...]


class Document(Frozen):
    member: str
    sha256: str
    header: Header
    provisions: tuple[Provision, ...]
    parts: tuple[Part, ...]


def _css(element: etree._Element) -> str:
    return element.get("class") or ""


def _is_note(element: etree._Element) -> bool:
    return _css(element) in _NOTE_CLASSES


def element_text(element: etree._Element) -> str:
    """Normalised text of an element, with editorial notes and footnotes left out."""
    pieces: list[str] = [element.text or ""]
    for child in element:
        gap = " " if child.tag in _BLOCK_TAGS else ""
        if not _is_note(child):
            pieces.extend((gap, element_text(child), gap))
        pieces.append(child.tail or "")
    return collapse("".join(pieces))


def inner_xml(element: etree._Element) -> str:
    children = "".join(etree.tostring(child, encoding="unicode") for child in element)
    return (element.text or "") + children


def _header_values(root: etree._Element, name: str) -> list[str]:
    cells = root.findall(f"./body/header/dl/dd[@class='{name}']")
    return [collapse("".join(str(t) for t in cell.itertext())) for cell in cells]


def _header_list(root: etree._Element, name: str) -> tuple[str, ...]:
    cells = root.findall(f"./body/header/dl/dd[@class='{name}']")
    return tuple(
        collapse("".join(str(t) for t in item.itertext()))
        for cell in cells
        for item in cell.iter("li")
    )


def _published(raw: list[str]) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw[0], "%Y-%m-%d %H:%M")
    except ValueError:
        try:
            return datetime.strptime(raw[0], "%Y-%m-%d")
        except ValueError:
            return None


def parse_header(root: etree._Element) -> Header:
    refid = _header_values(root, "refid")
    if len(refid) != 1 or not refid[0]:
        raise LegalTimeDocumentError("document header carries no single refid")
    return Header(
        refid=refid[0],
        legacy_id="".join(_header_values(root, "legacyID")),
        dokid="".join(_header_values(root, "dokid")),
        title="".join(_header_values(root, "title")),
        date_in_force="".join(_header_values(root, "dateInForce")),
        published_at=_published(_header_values(root, "dateOfPublication")),
        changes_to_documents=_header_list(root, "changesToDocuments"),
    )


def _ledd(element: etree._Element) -> Ledd:
    return Ledd(xml=inner_xml(element), text=element_text(element))


def _provision(article: etree._Element) -> Provision:
    key = article.get("data-name") or ""
    values = [e for e in article.iter("span") if _css(e) == "legalArticleValue"]
    titles = [e for e in article.iter("span") if _css(e) == "legalArticleTitle"]
    ledd = tuple(_ledd(child) for child in article if _css(child) == "legalP")
    return Provision(
        key=key,
        label=element_text(values[0]) if values else key,
        heading=element_text(titles[0]) if titles else "",
        ledd=ledd,
    )


def _provisions(main: etree._Element) -> tuple[Provision, ...]:
    return tuple(_provision(a) for a in main.iter("article") if _css(a) == "legalArticle")


def _node(element: etree._Element) -> Node:
    css = _css(element)
    if css == "futureLegalArticle":
        return Node(kind="provision", css_class=css, text="", provision=_provision(element))
    if css in _LEDD_CLASSES:
        return Node(kind="ledd", css_class=css, text=element_text(element), ledd=_ledd(element))
    if css == "futuretitle":
        return Node(kind="title", css_class=css, text=element_text(element))
    if css == "defaultP":
        return Node(kind="paragraph", css_class=css, text=element_text(element))
    return Node(kind="other", css_class=css or element.tag, text=element_text(element))


def _nodes(elements: list[etree._Element], change_part: tuple[str, ...] = ()) -> list[Node]:
    nodes: list[Node] = []
    for element in elements:
        if _css(element) == "change":
            parts = tuple((element.get("data-change-part") or "").split())
            nodes.extend(_nodes(list(element), parts))
        elif _css(element) in _TRANSPARENT or element.tag in _LIST_TAGS:
            nodes.extend(_nodes(list(element), change_part))
        elif element.tag == "section":
            nodes.extend(_part(element).nodes)
        else:
            nodes.append(_node(element).model_copy(update={"change_part": change_part}))
    return nodes


def _part(section: etree._Element) -> Part:
    headings = [e for e in section if e.tag in {"h2", "h3"}]
    label = element_text(headings[0]) if headings else ""
    body = [e for e in section if e.tag not in {"h2", "h3"}]
    return Part(label=label, nodes=tuple(_nodes(body)))


def _parts(main: etree._Element) -> tuple[Part, ...]:
    """The body's parts: a label-less lead part for anything outside a section."""
    lead = _nodes([e for e in main if e.tag not in {"section", "h1"}])
    sections = [_part(s) for s in main if s.tag == "section"]
    return tuple(([Part(label="", nodes=tuple(lead))] if lead else []) + sections)


def parse_document(body: bytes, member: str) -> Document:
    """Parse one member. Raises ``LegalTimeDocumentError`` on a foreign shape."""
    try:
        root = etree.fromstring(body, parser=safe_parser(remove_blank_text=False))
    except etree.XMLSyntaxError as exc:
        raise LegalTimeDocumentError(f"invalid XML in {member}") from exc
    main = root.find("./body/main")
    if main is None:
        raise LegalTimeDocumentError(f"no document body in {member}")
    return Document(
        member=member,
        sha256=sha256(body).hexdigest(),
        header=parse_header(root),
        provisions=_provisions(main),
        parts=_parts(main),
    )
