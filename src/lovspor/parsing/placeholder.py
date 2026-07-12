"""Detect Lovdata documents that carry an error notice instead of legal text.

Lovdata's public-data feed occasionally serves a document whose ``<main>``
holds only an editorial notice — ``<span class="errorMessage">Vi klarer
dessverre ikke vise hele dokumentet.</span>`` ("we are unfortunately unable to
show the whole document") — in place of the law. The XML is well-formed and
``/list`` still reports the document as current, so nothing in the archive
metadata marks it unavailable. Only the body distinguishes it.

Such a document must not enter the corpus. Publishing it would present an
empty document as current Norwegian law; and left in the sync's upstream set
it is unrenderable, because the notice is block-level text that the renderer
correctly refuses to drop silently — so it raises ``RenderError``, gets
skipped, keeps its stale ``renderer_version``, and is promoted for re-render
again on the very next sync. Forever.

Detection keys on Lovdata's own ``errorMessage`` class rather than the
Norwegian sentence, so a reworded notice is still caught. It is deliberately
narrow in the other direction: a document that carries an error notice *and*
real legal content is not a placeholder, and a document that merely renders to
nothing is not one either. If Lovdata changes the markup, such a document
starts failing loudly again instead of disappearing quietly — the safe
direction for a corpus consumed as ground truth.
"""

from io import BytesIO

from lxml import etree

from lovspor.parsing.xml_normalizer import safe_parser

_ERROR_MESSAGE_CLASS = "errorMessage"

# Elements that carry actual legal text. Their presence means the document has
# content worth rendering, whatever else sits alongside it.
_CONTENT_TAGS = frozenset({"article", "section"})


def is_content_placeholder(xml_bytes: bytes) -> bool:
    """True when ``<main>`` holds Lovdata's error notice and no legal content.

    Never raises: malformed XML and a missing ``<main>`` both return ``False``,
    leaving the existing render path to fail and skip the document as it
    already does. A parse failure must never become a silent corpus removal.
    """
    try:
        tree = etree.parse(BytesIO(xml_bytes), parser=safe_parser(remove_blank_text=False))
    except etree.XMLSyntaxError:
        return False
    main = tree.find(".//main")
    if main is None:
        return False
    return _has_error_notice(main) and not _has_legal_content(main)


def _has_error_notice(main: etree._Element) -> bool:
    """True when any descendant is tagged with Lovdata's ``errorMessage`` class."""
    return any(_ERROR_MESSAGE_CLASS in (elem.get("class") or "").split() for elem in main.iter())


def _has_legal_content(main: etree._Element) -> bool:
    """True when ``<main>`` still carries text-bearing legal structure."""
    return any(etree.QName(elem).localname in _CONTENT_TAGS for elem in main.iter())
