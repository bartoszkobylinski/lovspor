"""Sitemap and robots artifacts (ADR-0013 Decision 7).

Generated from the same inventory traversal as the pages, so the
sitemap URL set and the emitted canonical indexable page set (document
pages, provision pages, browse indexes) cannot drift apart — the
emitter's tests assert the equality. Document entries carry ``lastmod``
from the last corpus commit touching the document's own Markdown —
authoritative change state, never site build time. Provision entries
carry no ``lastmod`` in v1: inheriting the parent's value would
advertise every provision of an act as changed when one section
changed. Companion JSON files, ``site-manifest.json`` and generated
maps are artifacts, not pages, and stay outside the sitemap set.
"""

import html as html_escape

from pydantic import BaseModel, ConfigDict

from lovspor.publish.browse import BROWSE_ROUTES, browse_index_url
from lovspor.publish.inventory import PublishInventory, Route
from lovspor.publish.pages import SITE_ORIGIN, document_url, provision_url

SITEMAP_URL_LIMIT = 50_000
"""The sitemap protocol's ceiling per file; shards split deterministically."""

_XML_HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n'
_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

_ROUTE_STEMS: tuple[tuple[Route, str], ...] = (("lov", "lover"), ("forskrift", "forskrifter"))


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
    files: dict[str, bytes] = {}
    for route, stem in _ROUTE_STEMS:
        files.update(_shards(stem, _document_rows(inventory, route, revisions)))
    files.update(_shards("paragrafer", _provision_rows(inventory)))
    files["sitemaps/indexes.xml"] = _urlset(_browse_rows())
    files["sitemap.xml"] = _sitemap_index(list(files))
    return files


def robots_txt() -> bytes:
    """One ``User-agent: *`` group: ``/mcp`` closed, everything else open.

    Under RFC 9309 an absent rule already means allowed, so the explicit
    ``Allow: /`` only documents the ADR's allow list; the single
    Disallow is the entire policy. A future AI-crawler rule must be an
    explicit, dated edit with a reason — never a silent default.
    """
    return (
        f"User-agent: *\nAllow: /\nDisallow: /mcp\nSitemap: {SITE_ORIGIN}/sitemap.xml\n"
    ).encode()


def _document_rows(
    inventory: PublishInventory,
    route: Route,
    revisions: dict[str, SourceRevision],
) -> list[str]:
    entries = sorted(
        (document_url(plan), revisions[plan.markdown_path].committed_at)
        for plan in inventory.documents
        if plan.route == route
    )
    return [_url_row(url, lastmod) for url, lastmod in entries]


def _provision_rows(inventory: PublishInventory) -> list[str]:
    urls = sorted(
        provision_url(plan, provision.pid)
        for plan in inventory.documents
        if not plan.duplicate_pids
        for provision in plan.provisions
    )
    return [_url_row(url, None) for url in urls]


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
