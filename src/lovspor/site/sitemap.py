"""``/sitemap-site.xml``: the sitemap of site pages (ADR-0014 Decision 3, ADR:680-685).

One ``<urlset>`` listing exactly the emitted canonical site pages, in
emission order, and nothing else: JSON artifacts, the corpus namespaces
(they have ``/sitemap.xml`` and its shards, ADR-0013 Decision 7) and the
capability document are not pages. No ``lastmod``: a site page has no
corpus commit to borrow a change time from, and the builder writes no
time of its own (Decision 3).

The header and namespace strings are the corpus generator's, reused so
the two sitemaps cannot drift apart in the boilerplate a crawler parses.
"""

import html as html_escape
from collections.abc import Iterable

from lovspor.publish.sitemaps import _SITEMAP_NS, _XML_HEADER
from lovspor.site.routes import EmittedPage, canonical_url


def sitemap_site_xml(pages: Iterable[EmittedPage]) -> bytes:
    """The one ``<urlset>`` of every page in ``pages``, as served bytes."""
    rows = "".join(
        f"<url><loc>{html_escape.escape(canonical_url(page.path))}</loc></url>\n" for page in pages
    )
    return f'{_XML_HEADER}<urlset xmlns="{_SITEMAP_NS}">\n{rows}</urlset>\n'.encode()
