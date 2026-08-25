"""Reading a listing page as a source of candidates (issue #151, part 2).

Discovery reads sitemaps, sitemap indexes, Atom and RSS. **116 of Norway's 358
municipalities publish none of them** — 23% of the population — and for those a
capture is structurally a no-op: it proposes nothing and says nothing was
changed. What they do publish is an ordinary overview page, the kind a person
reads: kunngjøringer, høringer, planer.

This module reads one of those pages into the same `DiscoveredLink` the sitemap
reader produces, so everything downstream — freshness, the domain guard, rate
limiting, the log — is unchanged.

Three deliberate limits, because each one is a place this could start inventing
things:

**It is structural, never CMS-specific.** No selector here names a vendor.
A listing entry is a link that carries a machine-readable date, and that is a
property of HTML rather than of Episerver or ACOS. A parser tuned to one
municipality's markup is a parser that silently returns nothing for the next
one, which is the failure this module exists to remove.

**It does not run JavaScript, and says so.** Where a listing is assembled in
the browser there is nothing in the served bytes to read, and the honest answer
is a refusal naming that — the same posture as `mutation not applicable`. Half
a listing is worse than none: it looks like a complete one.

**A date is required, not optional.** The date is what freshness uses to
decline work later, and a listing entry without one would be re-fetched on
every sweep forever. Entries without a machine-readable date are reported as
skipped rather than proposed, so the count of what was ignored stays visible.
"""

from typing import NamedTuple
from urllib.parse import urljoin

from lxml import etree, html

from lovspor.errors import ParseError

#: How a candidate from this reader is labelled in the observation log, beside
#: ``sitemap``. The provenance of a proposal is evidence: a URL proposed by
#: reading a human-facing page was not published in a machine index, and a
#: reader of the archive should not have to guess which it was.
LISTING_METHOD = "listing"

#: Only elements that can carry a date this module will trust. `<time
#: datetime="...">` is the one place HTML defines a machine-readable date;
#: everything else on a listing page is prose, and prose dates are a locale
#: guessing game this module refuses to play.
_TIME_TAG = "time"
_DATETIME_ATTR = "datetime"

#: Hrefs that address something other than a document to observe. Judged on the
#: raw attribute, before any joining: a bare fragment survives absolutisation as
#: a full URL and would propose the listing page as one of its own entries.
_NOT_A_DOCUMENT = ("#", "mailto:", "tel:", "javascript:", "data:")


def safe_html_parser() -> html.HTMLParser:
    """The hardened parser used for every listing page.

    ``no_network`` so a served page cannot make this process fetch anything,
    and ``huge_tree`` off to bound expansion.

    There is deliberately no ``resolve_entities=False`` here, even though the
    XML side sets it: lxml's HTML parser does not accept the argument, and it
    does not substitute DOCTYPE entities in the first place — a document
    declaring an external entity comes back with the literal ``&xxe;`` in its
    text. Passing a flag that the parser rejects would have been a comment
    claiming a protection the code does not have, which is worse than the gap
    it pretends to close. A test pins the behaviour this relies on.
    """
    return html.HTMLParser(
        no_network=True,
        huge_tree=False,
        remove_comments=True,
    )


def _closest_listing_item(anchor: etree._Element) -> etree._Element | None:
    """The row this link belongs to, or None when it is not in one.

    Walks up to the nearest list item or table row rather than trusting the
    anchor's siblings: a date and its link are reliably inside the same entry
    and unreliably adjacent to each other.
    """
    for ancestor in anchor.iterancestors():
        if _tag_of(ancestor) in {"li", "article", "tr"}:
            return ancestor
    return None


def _tag_of(element: etree._Element) -> str:
    tag = element.tag
    return tag.lower() if isinstance(tag, str) else ""


def _item_date(item: etree._Element) -> str | None:
    """The entry's machine-readable date, verbatim and unparsed.

    Kept as the page wrote it, like the sitemap reader keeps ``lastmod``: this
    is the site's own claim, and turning it into a timestamp here would dress
    an unverified string up as a recorded fact.
    """
    for element in item.iter():
        if _tag_of(element) != _TIME_TAG:
            continue
        stamp = element.get(_DATETIME_ATTR)
        if stamp and stamp.strip():
            return stamp.strip()
    return None


class ListingEntry(NamedTuple):
    """One dated link read off a listing page.

    Deliberately not discovery's ``DiscoveredLink``: this module reads HTML and
    knows nothing about sitemaps, nesting or discovery methods. Importing that
    vocabulary here would point the dependency backwards — and did, until
    Python refused the resulting import cycle that neither ruff nor mypy saw.
    """

    url: str
    site_reported_lastmod: str


class ListingReadout(NamedTuple):
    """What one listing page yielded, including what it did not.

    ``skipped_without_date`` is reported rather than dropped: a page where most
    entries carry no date is a page this reader is reading badly, and a caller
    that only ever sees the links it produced has no way to notice.
    """

    entries: tuple[ListingEntry, ...]
    skipped_without_date: int


def parse_listing(payload: bytes, document_url: str) -> ListingReadout:
    """Read the dated entries out of a listing page.

    Args:
        payload: the served bytes, exactly as fetched.
        document_url: where they came from, used to resolve relative links —
            listing markup uses them constantly, unlike the sitemap protocol.

    Raises:
        ParseError: nothing in the served HTML looks like a dated listing.
            That is a refusal, not an empty result, because "this page has no
            entries today" and "this reader cannot see this page's entries" are
            different facts and only one of them is about the source.
    """
    try:
        root: html.HtmlElement = html.fromstring(
            payload, base_url=document_url, parser=safe_html_parser()
        )
    except (etree.ParserError, etree.XMLSyntaxError, ValueError) as exc:
        raise ParseError(f"{document_url}: unreadable listing page: {exc}") from exc
    entries, skipped = _entries(root, document_url)
    if not entries:
        raise ParseError(
            f"{document_url}: no dated listing entries in the served HTML "
            f"({skipped} undated link(s) seen) — the page may be assembled in the browser, "
            "which this reader deliberately does not do"
        )
    return ListingReadout(entries=entries, skipped_without_date=skipped)


def _entries(root: etree._Element, document_url: str) -> tuple[tuple[ListingEntry, ...], int]:
    """Every dated entry, in document order, each URL proposed once.

    The href is judged raw and joined here rather than through lxml's
    `make_links_absolute`, for two reasons found by testing rather than by
    reading: that helper honours a `<base href>` the page supplies even when
    told not to resolve one — so a served page could move these proposals to a
    host the source was never cleared for — and it rewrites a bare `#fragment`
    into a full URL, which then passes every filter and proposes the listing
    page itself as one of its own entries.

    `urljoin` against the fetched URL does neither.
    """
    seen: dict[str, ListingEntry] = {}
    skipped = 0
    for anchor in root.iter("a"):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(_NOT_A_DOCUMENT):
            continue
        item = _closest_listing_item(anchor)
        stamp = _item_date(item) if item is not None else None
        if stamp is None:
            skipped += 1
            continue
        url = urljoin(document_url, href)
        if url not in seen:
            seen[url] = ListingEntry(url=url, site_reported_lastmod=stamp)
    return tuple(seen.values()), skipped
