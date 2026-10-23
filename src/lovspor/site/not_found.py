"""The site-framed page every 404 is answered with (#662).

Not a route: ``/404.html`` has no twin, no place in the sitemap and none in
``SiteBuildReport.pages``. The release fragment's ``handle_errors`` block
rewrites a not-found error to it, from the same release's ``site/`` tree,
with the error's own status kept — so a mistyped law slug gets the site's
chrome and a way home instead of Caddy's empty body. It is rendered through
``_base.html`` with its own canonical, and scanned and link-checked against
the emitted pages like every page.
"""

from collections.abc import Mapping
from pathlib import Path

from lovspor.site.routes import canonical_url
from lovspor.site.scan import check_links, scan_page
from lovspor.site.templates import site_environment

NOT_FOUND_NAME = "404.html"
NOT_FOUND_PATH = f"/{NOT_FOUND_NAME}"
TEMPLATE = "pages/not-found.html"

_CONTEXT: dict[str, object] = {
    "lang": "nb",
    "title": "Siden finnes ikke — lovspor",
    "description": "Adressen fører ikke til noen side på lovspor.no.",
    "canonical": canonical_url(NOT_FOUND_PATH),
    "alternates": (),
    "language_switch": (),
    "corpus": False,
}


def render_not_found(pages: Mapping[str, str]) -> str:
    """The page's markup, refused if it fails the scan or links outside ``pages``."""
    markup = site_environment().get_template(TEMPLATE).render(_CONTEXT)
    scan_page(NOT_FOUND_PATH, markup)
    check_links({**pages, NOT_FOUND_PATH: markup})
    return markup


def write_not_found(site: Path, markup: str) -> None:
    (site / NOT_FOUND_NAME).write_bytes(markup.encode("utf-8"))
