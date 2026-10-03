"""Reading an undated listing page: a regulation overview (issue #514).

The dated reader in :mod:`lovspor.observatory.listing` fits hearing and
announcement archives, where every entry carries a ``<time datetime>``. The
pages a municipality keeps its local law on — "Lokale forskrifter",
"Reglement og vedtekter", "Styrende dokumenter" — are not dated lists: they are
an overview of links to pages and documents. Of 25 such pages cleared for
registration on 2026-10-03, 20 had no dated entry at all and were refused, so
the source would have proposed nothing every night.

This reader proposes the links in the page's **content region** instead, with
no date. Three choices keep it from inventing things:

**The region is the page's own statement of where its content is.** ``<main>``
when the page has one, else ``<body>`` — the same rule
:mod:`lovspor.observatory.document_report` measures documents by. Inside it,
site chrome is dropped by what HTML says it is (``header``, ``footer``, the
ARIA landmarks for them); ``nav`` and ``aside`` are dropped only when there is
no ``<main>``, because inside one a ``nav`` is the overview itself on at least
one cleared page. Nothing names a CMS.

**Undated is not "refetch forever".** A candidate with no ``lastmod`` is
judged by freshness on our own record of the URL — left alone for 24 h after a
sighting, doubling per unchanged re-capture to a week (issues #209, #415) —
and a URL that only ever fails backs off on its own record (#204). Nothing new
is needed here, and nothing here may fake a date to get the cheaper path.

**The domain is not judged here.** Discovery's guard is the one place a host is
checked against the source, and an off-domain link proposed here is refused
there with its reason recorded. A second implementation of that check would be
one more place for the two to drift apart.
"""

import re
from urllib.parse import parse_qsl, urldefrag, urljoin, urlsplit

from lxml import html

from lovspor.errors import ParseError
from lovspor.observatory.document_report import _decoded, _region
from lovspor.observatory.listing import (
    ListingEntry,
    ListingReadout,
    parse_listing,
    usable_href,
)

#: Site chrome wherever it sits: the page header, the page footer, and the
#: landmark roles for them and for search.
_CHROME_TAGS = ("header", "footer", "script", "style", "noscript", "template")
_CHROME_ROLES = frozenset({"banner", "contentinfo", "search"})

#: Chrome only when the page did not say where its content is. Inside a
#: ``<main>`` a ``nav`` can be the overview itself.
_LANDMARK_TAGS = ("nav", "aside")
_LANDMARK_ROLES = frozenset({"navigation", "complementary"})

#: Path segments that name a function of the site rather than a document.
_SITE_FUNCTION = re.compile(
    r"^(?:sok|search|s[øo]k|login|logg-?inn|innlogging|logout|logg-?ut|"
    r"cookies?|informasjonskapsler|share|del)$",
    re.IGNORECASE,
)
#: Query keys that make a link a search result rather than a document.
_SEARCH_KEYS = frozenset({"q", "query", "search", "sok", "searchquery"})
#: Files that are never a regulation: images, styling, scripts.
_NOT_A_TEXT = re.compile(r"\.(?:jpe?g|png|gif|svg|webp|ico|css|js)$", re.IGNORECASE)
_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def read_listing(payload: bytes, document_url: str) -> ListingReadout:
    """A registered listing page, dated if it can be, else undated.

    The mode is chosen by the page, not by configuration: a page with at least
    one dated entry is read as a dated list, exactly as before issue #514; a
    page with none is read as an overview. A registry field naming the mode
    would be one more thing an operator must set correctly per page, and the
    served HTML already answers the question every time it is read.

    Raises:
        ParseError: the page is unreadable, or neither reading finds a link.
    """
    try:
        return parse_listing(payload, document_url)
    except ParseError:
        return parse_undated_listing(payload, document_url)


def parse_undated_listing(payload: bytes, document_url: str) -> ListingReadout:
    """Every content link on an overview page, in document order, once each.

    Raises:
        ParseError: no content region, or no link in it — "this reader cannot
            see this page's entries", kept apart from an empty result for the
            reason :func:`~lovspor.observatory.listing.parse_listing` does.
    """
    region, _ = _region(_decoded(payload, "text/html"))
    if region is None:
        raise ParseError(f"{document_url}: unreadable listing page: no body")
    links_on_page = _count_links(region.getroottree().getroot())
    _drop_chrome(region, inside_main=region.tag == "main")
    entries = _content_entries(_articles_or(region), document_url)
    if not entries:
        raise ParseError(
            f"{document_url}: no dated listing entries and no content links in the served "
            f"HTML ({links_on_page} link(s) seen) — the page may be assembled in the browser, "
            "which this reader deliberately does not do"
        )
    return ListingReadout(
        entries=entries,
        skipped_without_date=0,
        undated=True,
        not_proposed=links_on_page - len(entries),
    )


def _count_links(root: html.HtmlElement) -> int:
    return sum(1 for anchor in root.iter("a") if usable_href(anchor) is not None)


def _drop_chrome(region: html.HtmlElement, *, inside_main: bool) -> None:
    tags = _CHROME_TAGS if inside_main else _CHROME_TAGS + _LANDMARK_TAGS
    roles = _CHROME_ROLES if inside_main else _CHROME_ROLES | _LANDMARK_ROLES
    doomed = [
        element
        for element in region.iterdescendants()
        if isinstance(element, html.HtmlElement)
        and (element.tag in tags or (element.get("role") or "").strip().lower() in roles)
    ]
    for element in doomed:
        element.drop_tree()


def _articles_or(region: html.HtmlElement) -> list[html.HtmlElement]:
    """The region's outermost ``<article>`` elements that hold a link, if any.

    A page that marks its own text as an article has said where its content is
    more precisely than ``<main>`` does: the section menu beside it is not the
    overview. A page that marks nothing keeps the whole region.
    """
    articles = [
        element
        for element in region.iter("article")
        if isinstance(element, html.HtmlElement)
        and not any(_tag(ancestor) == "article" for ancestor in element.iterancestors())
        and _count_links(element)
    ]
    return articles or [region]


def _tag(element: html.HtmlElement) -> str:
    return element.tag if isinstance(element.tag, str) else ""


def _content_entries(
    regions: list[html.HtmlElement], document_url: str
) -> tuple[ListingEntry, ...]:
    seen: dict[str, ListingEntry] = {}
    anchors = (anchor for region in regions for anchor in region.iter("a"))
    for anchor in anchors:
        href = usable_href(anchor)
        if href is None:
            continue
        url = urldefrag(urljoin(document_url, href)).url
        if url not in seen and is_content_link(url, document_url):
            seen[url] = ListingEntry(url=url, site_reported_lastmod=None)
    return tuple(seen.values())


def is_content_link(url: str, document_url: str) -> bool:
    """Whether ``url``, found in the content region, names a document.

    Not the page itself or one of its ancestors — that is the breadcrumb trail,
    which every page carries and no overview lists — and not a function of the
    site: search, login, cookie settings, a share button.
    """
    parts = urlsplit(url)
    if parts.scheme not in _SCHEMES or _NOT_A_TEXT.search(parts.path):
        return False
    if _is_ancestor_or_self(url, document_url):
        return False
    if any(_SITE_FUNCTION.match(segment) for segment in parts.path.split("/")):
        return False
    return not any(
        key.lower() in _SEARCH_KEYS or value.startswith(("http://", "https://"))
        for key, value in parse_qsl(parts.query)
    )


def _is_ancestor_or_self(url: str, document_url: str) -> bool:
    """Whether ``url`` is this page or one of the pages above it on the site.

    Hosts are compared by name and effective port, because a CMS writing its
    breadcrumbs absolute as ``https://host:443/`` still means this site.
    """
    link, page = urlsplit(url), urlsplit(document_url)
    if _origin(url) != _origin(document_url) or link.query:
        return False
    link_path = link.path.rstrip("/") + "/"
    return (page.path.rstrip("/") + "/").startswith(link_path)


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    port = parts.port or _DEFAULT_PORTS.get(parts.scheme)
    return parts.scheme, (parts.hostname or ""), port
