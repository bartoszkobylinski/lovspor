"""What one saved landing page states about its proposition's edition (#784).

The landing page is edition evidence only: it never passes through the chapter
reader, and nothing here reads body text. The markup was read off one real
landing page of a corrected proposition, saved privately on 2026-11-05:

- a ``data-doc-meta="corrected-edition-date"`` element in the article header,
  stating the correction date, and
- an ``h3.edition`` heading over a ``ul.link-list`` linking the original
  edition and the rettebrev as PDFs under ``/contentassets/``.

Both or neither. A page with neither is ``unknown``, never ``original``: no
observed page states that it is the original edition. ``DC.Date`` is the
statsråd date and is never read. Anything else that speaks of an edition is
refused, because an unrecognised notice must not read as "no notice".
"""

import re
from datetime import date

from lxml import html
from pydantic import BaseModel, ConfigDict

from lovspor.forarbeider.completeness import require_whole_page
from lovspor.forarbeider.edition_models import EditionKind
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.identity import (
    ALLOWED_HOST,
    SourceLocator,
    parse_reference,
    parse_source_url,
)
from lovspor.forarbeider.page import (
    _SCRIPT_TITLE_CONTROL,
    _clean,
    _document,
    _require_identity,
    _require_proposition,
    _required_meta,
    _text,
)

_HEADER = "//main//header[contains(concat(' ', normalize-space(@class), ' '), ' article-header ')]"
_NOTICES = "//*[@data-doc-meta]"
_NOTICE_KIND = "corrected-edition-date"
_NOTICE = re.compile(
    r"\(Korrigert utgave per (?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>\d{4})\)"
)
_BLOCKS = "//h3[contains(concat(' ', normalize-space(@class), ' '), ' edition ')]"
_BLOCK_HEADING = "Opprinnelig utgave og rettebrev"
_FOOTERS = "//footer"
_FOOTER_CLASS = "page-footer"
_ASSET = re.compile(r"/contentassets/[0-9a-f]{32}/no/(?P<kind>opri|rett)/[A-Za-z0-9_-]+\.pdf")
_LABELS = {"opri": "Opprinnelig utgave", "rett": "Rettebrev"}
_EDITION_WORDING = re.compile(
    r"korrigert\s*utgave|opprinnelig\s*utgave|rette\s*brev", re.IGNORECASE
)
_EDITION_LINKS = "//a[contains(@href, '/opri/') or contains(@href, '/rett/')]"
_VISIBLE_TEXT = "//body//text()[not(ancestor::script) and not(ancestor::style)]"


class EditionFacts(BaseModel):
    """The edition a landing page states, as it states it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: EditionKind
    corrected_on: date | None
    original_edition_url: str | None
    rettebrev_urls: tuple[str, ...]


_UNKNOWN = EditionFacts(
    kind="unknown", corrected_on=None, original_edition_url=None, rettebrev_urls=()
)


def read_landing(payload: bytes, landing_url: str) -> tuple[SourceLocator, EditionFacts]:
    """The document ``payload`` is the landing page of, and the edition it states.

    Raises:
        ForarbeiderRefusedError: the page is refused, with a typed reason.
    """
    locator = parse_source_url(landing_url)
    if locator.chapter is not None:
        raise ForarbeiderRefusedError(RefusalReason.LANDING_PAGE_REQUIRED, landing_url)
    text = _text(payload)
    root = _document(text)
    _require_proposition(root)
    require_whole_page(text)
    _require_identity(root, locator)
    _require_landing_footer(root)
    header = _require_landing_header(root, locator)
    return locator, _edition(root, header)


def _require_landing_footer(root: html.HtmlElement) -> None:
    """The page reaches its one ``footer.page-footer``, a child of ``body`` after ``main``.

    ``require_whole_page`` proves the source closes; a landing page has no
    chapter navigation for it to check, so the footer is what proves the page
    was not cut and closed again. An absent notice reads as ``unknown`` only on
    a page proved whole.
    """
    footers = root.xpath(_FOOTERS)
    footer = footers[0] if len(footers) == 1 else None
    if (
        footer is None
        or footer.getparent() is not root.body
        or _FOOTER_CLASS not in footer.classes
        or not footer.xpath("preceding::main")
    ):
        raise ForarbeiderRefusedError(RefusalReason.INCOMPLETE_PAGE, "page footer")


def _require_landing_header(root: html.HtmlElement, locator: SourceLocator) -> html.HtmlElement:
    headers = root.xpath(_HEADER)
    if len(headers) != 1 or len(headers[0].xpath("./h1")) != 1:
        raise ForarbeiderRefusedError(RefusalReason.DOM_CHANGED, "article-header")
    if headers[0].xpath(_SCRIPT_TITLE_CONTROL):
        raise ForarbeiderRefusedError(RefusalReason.BROWSER_RENDERED_PAGE, "subheader")
    stated = {
        "h1": _clean(headers[0].xpath("./h1")[0].text_content()),
        "og:title": _required_meta(root, ("property", "og:title"), RefusalReason.DOM_CHANGED),
        "DC.Title": _required_meta(root, ("name", "DC.Title"), RefusalReason.TITLE_MISSING),
    }
    stated["DC.Title"] = stated["DC.Title"].partition(" - ")[0]
    for name, reference in stated.items():
        if parse_reference(reference) != locator.reference:
            raise ForarbeiderRefusedError(RefusalReason.CONFLICTING_IDENTITY, name)
    return headers[0]


def _edition(root: html.HtmlElement, header: html.HtmlElement) -> EditionFacts:
    notices, blocks = root.xpath(_NOTICES), root.xpath(_BLOCKS)
    if len(notices) > 1 or len(blocks) > 1:
        raise ForarbeiderRefusedError(RefusalReason.EDITION_CONFLICTING, "edition notice")
    if bool(notices) != bool(blocks):
        raise ForarbeiderRefusedError(RefusalReason.EDITION_CONFLICTING, "edition notice")
    if not notices:
        _require_no_edition_wording(root)
        return _UNKNOWN
    corrected_on = _corrected_on(notices[0], header)
    anchors = _edition_anchors(blocks[0])
    original, rettebrev = _edition_links(blocks[0], anchors)
    _require_no_edition_wording(root, (notices[0], blocks[0], *anchors))
    return EditionFacts(
        kind="corrected",
        corrected_on=corrected_on,
        original_edition_url=original,
        rettebrev_urls=rettebrev,
    )


def _corrected_on(notice: html.HtmlElement, header: html.HtmlElement) -> date:
    unrecognized = ForarbeiderRefusedError(RefusalReason.EDITION_UNRECOGNIZED, _NOTICE_KIND)
    if (
        notice.get("data-doc-meta") != _NOTICE_KIND
        or notice.getparent() is not header
        or len(notice)
    ):
        raise unrecognized
    match = _NOTICE.fullmatch(_clean(notice.text_content()))
    if match is None:
        raise unrecognized
    try:
        return date(int(match["year"]), int(match["month"]), int(match["day"]))
    except ValueError as error:
        raise unrecognized from error


def _blank(*texts: str | None) -> bool:
    return not "".join(text or "" for text in texts).strip()


def _edition_anchors(heading: html.HtmlElement) -> list[html.HtmlElement]:
    """Every link of the edition list, once the block is exactly ``h3`` + ``ul.link-list``.

    Each item must be ``li > a`` and nothing else, so no entry is skipped: an
    unfamiliar one refuses the page instead of dropping out of the result.
    """
    block = list(heading.getparent())
    listing = block[-1]
    if (
        block != [heading, listing]
        or listing.tag != "ul"
        or "link-list" not in listing.classes
        or not _blank(heading.getparent().text, heading.tail, listing.text, listing.tail)
    ):
        raise ForarbeiderRefusedError(RefusalReason.EDITION_UNRECOGNIZED, "edition block")
    return [_item_anchor(item) for item in listing]


def _item_anchor(item: html.HtmlElement) -> html.HtmlElement:
    anchors = list(item) if item.tag == "li" and not item.attrib else []
    anchor = anchors[0] if len(anchors) == 1 else None
    if (
        anchor is None
        or anchor.tag != "a"
        or len(anchor)
        or not _blank(item.text, anchor.tail, item.tail)
    ):
        raise ForarbeiderRefusedError(RefusalReason.EDITION_UNRECOGNIZED, "edition list item")
    return anchor


def _edition_links(
    heading: html.HtmlElement, anchors: list[html.HtmlElement]
) -> tuple[str, tuple[str, ...]]:
    if _clean(heading.text_content()) != _BLOCK_HEADING:
        raise ForarbeiderRefusedError(RefusalReason.EDITION_UNRECOGNIZED, "edition heading")
    found: dict[str, list[str]] = {"opri": [], "rett": []}
    for link in anchors:
        kind, url = _edition_link(link)
        found[kind].append(url)
    if len(found["opri"]) != 1 or not found["rett"]:
        raise ForarbeiderRefusedError(RefusalReason.EDITION_CONFLICTING, "edition links")
    return found["opri"][0], tuple(found["rett"])


def _edition_link(link: html.HtmlElement) -> tuple[str, str]:
    href = link.get("href")
    asset = None if href is None else _ASSET.fullmatch(href)
    if asset is None:
        raise ForarbeiderRefusedError(RefusalReason.EDITION_UNRECOGNIZED, "edition link")
    if not _clean(link.text_content()).startswith(_LABELS[asset["kind"]]):
        raise ForarbeiderRefusedError(RefusalReason.EDITION_CONFLICTING, "edition link label")
    return asset["kind"], f"https://{ALLOWED_HOST}{href}"


def _require_no_edition_wording(
    root: html.HtmlElement, recognised: tuple[html.HtmlElement, ...] = ()
) -> None:
    """Refuse edition wording or edition links anywhere but in the nodes just read.

    The page's text is searched as one string, so wording split by inline
    markup (``Korrigert <em>utgave</em>``) or across a line is still found. Only
    the own text of a recognised node is left out, never its tail.
    """
    for link in root.xpath(_EDITION_LINKS):
        if link not in recognised:
            raise ForarbeiderRefusedError(RefusalReason.EDITION_UNRECOGNIZED, "edition link")
    nodes = root.xpath(_VISIBLE_TEXT)
    text = "".join(node for node in nodes if not (node.is_text and node.getparent() in recognised))
    if _EDITION_WORDING.search(text):
        raise ForarbeiderRefusedError(RefusalReason.EDITION_UNRECOGNIZED, "edition wording")
