"""Sitemap and robots artifacts (ADR-0013 Decision 7).

Generated from the same inventory traversal as the pages, so the
sitemap URL set and the emitted canonical indexable page set (document
pages, provision pages, browse indexes) cannot drift apart — the
emitter's tests assert the equality. Document entries carry ``lastmod``
from the last corpus commit touching the document's own Markdown —
authoritative change state, never site build time. Provision entries
carry no ``lastmod`` in v1: inheriting the parent's value would
advertise every provision of an act as changed when one section
changed. ``site-manifest.json`` and generated maps are artifacts, not
pages, and stay outside the sitemap set.

The JSON twins are crawlable from their own index (#340). They are not
pages either, so they are not in ``sitemap.xml``: that index *is* the
emitted page set, an equality the emitter asserts, and putting artifacts
in it would retire the contract rather than extend it. They get
``sitemaps/companions.xml`` beside it, declared in ``robots.txt`` — the
sitemap protocol's own way to admit a sitemap whose URLs lie outside its
own path.
"""

import html as html_escape

from pydantic import BaseModel, ConfigDict

from lovspor.publish.browse import BROWSE_ROUTES, browse_index_url
from lovspor.publish.inventory import PublishInventory, Route
from lovspor.publish.pages import COMPANION_NAME, SITE_ORIGIN, document_url, provision_url

SITEMAP_URL_LIMIT = 50_000
"""The sitemap protocol's ceiling per file; shards split deterministically."""

COMPANION_STEM = "companions"
COMPANION_INDEX = f"sitemaps/{COMPANION_STEM}.xml"

_XML_HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n'
_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

_ROUTE_STEMS: tuple[tuple[Route, str], ...] = (("lov", "lover"), ("forskrift", "forskrifter"))

Entry = tuple[str, str | None]
"""One sitemap row before it is rendered: a site path and its ``lastmod``."""


class SourceRevision(BaseModel):
    """One path's last corpus commit: the sha and its committer time."""

    model_config = ConfigDict(frozen=True)

    sha: str
    committed_at: str


def sitemap_files(
    inventory: PublishInventory,
    revisions: dict[str, SourceRevision],
) -> dict[str, bytes]:
    """Every sitemap artifact, keyed by site-root-relative path."""
    entries = _page_entries(inventory, revisions)
    files: dict[str, bytes] = {}
    for stem, rows in entries:
        files.update(_shards(stem, _rows(rows)))
    files["sitemaps/indexes.xml"] = _urlset(_browse_rows())
    files["sitemap.xml"] = _sitemap_index(list(files))
    files.update(_companion_files(entries))
    return files


def robots_txt() -> bytes:
    """One ``User-agent: *`` group: ``/mcp`` closed, everything else open.

    Under RFC 9309 an absent rule already means allowed, so the explicit
    ``Allow: /`` only documents the ADR's allow list; the single
    Disallow is the entire policy. A future AI-crawler rule must be an
    explicit, dated edit with a reason — never a silent default.

    Two ``Sitemap`` lines, because there are two sets: the pages, and the
    machine-readable twin of each page (#340). Declaring the companion
    index here is what lets it name URLs outside its own ``/sitemaps/``
    path, and it keeps this file a constant of the engine — the corpus can
    grow by a hundred thousand twins without moving a byte of it.
    """
    return (
        "User-agent: *\nAllow: /\nDisallow: /mcp\n"
        f"Sitemap: {SITE_ORIGIN}/sitemap.xml\n"
        f"Sitemap: {SITE_ORIGIN}/{COMPANION_INDEX}\n"
    ).encode()


def _page_entries(
    inventory: PublishInventory,
    revisions: dict[str, SourceRevision],
) -> list[tuple[str, list[Entry]]]:
    """Each page sitemap's stem and its rows, in emission order."""
    found = [(stem, _document_entries(inventory, route, revisions)) for route, stem in _ROUTE_STEMS]
    found.append(("paragrafer", _provision_entries(inventory)))
    return found


def _companion_files(entries: list[tuple[str, list[Entry]]]) -> dict[str, bytes]:
    """The twins of the same pages, under their own index (#340).

    Derived from the page rows themselves, so a twin cannot be listed for
    a page that is not published, nor a page's twin go missing. Each one
    inherits its page's ``lastmod`` — the twin changes exactly when the
    page does — so no time is invented here either. A corpus with no
    documents gets no shard and therefore no index, rather than an index
    of nothing.
    """
    rows = [row for _stem, page_rows in entries for row in _rows(page_rows, COMPANION_NAME)]
    files = _shards(COMPANION_STEM, rows)
    if files:
        files[COMPANION_INDEX] = _sitemap_index(list(files))
    return files


def _rows(entries: list[Entry], suffix: str = "") -> list[str]:
    return [_url_row(f"{url}{suffix}", lastmod) for url, lastmod in entries]


def _document_entries(
    inventory: PublishInventory,
    route: Route,
    revisions: dict[str, SourceRevision],
) -> list[Entry]:
    return sorted(
        (document_url(plan), revisions[plan.markdown_path].committed_at)
        for plan in inventory.documents
        if plan.route == route
    )


def _provision_entries(inventory: PublishInventory) -> list[Entry]:
    return [
        (url, None)
        for url in sorted(
            provision_url(plan, provision.pid)
            for plan in inventory.documents
            if not plan.duplicate_pids
            for provision in plan.provisions
        )
    ]


def _browse_rows() -> list[str]:
    return [_url_row(browse_index_url(route), None) for route in BROWSE_ROUTES]


def _url_row(path: str, lastmod: str | None) -> str:
    loc = html_escape.escape(f"{SITE_ORIGIN}{path}")
    if lastmod is None:
        return f"<url><loc>{loc}</loc></url>"
    return f"<url><loc>{loc}</loc><lastmod>{html_escape.escape(lastmod)}</lastmod></url>"


def _shards(stem: str, rows: list[str]) -> dict[str, bytes]:
    """Deterministic split at the ceiling; an empty row set emits no shard."""
    return {
        f"sitemaps/{stem}-{number}.xml": _urlset(rows[start : start + SITEMAP_URL_LIMIT])
        for number, start in enumerate(range(0, len(rows), SITEMAP_URL_LIMIT), start=1)
    }


def _urlset(rows: list[str]) -> bytes:
    body = "".join(f"{row}\n" for row in rows)
    return f'{_XML_HEADER}<urlset xmlns="{_SITEMAP_NS}">\n{body}</urlset>\n'.encode()


def _sitemap_index(names: list[str]) -> bytes:
    rows = "".join(
        f"<sitemap><loc>{html_escape.escape(SITE_ORIGIN + '/' + name)}</loc></sitemap>\n"
        for name in names
    )
    return f'{_XML_HEADER}<sitemapindex xmlns="{_SITEMAP_NS}">\n{rows}</sitemapindex>\n'.encode()
