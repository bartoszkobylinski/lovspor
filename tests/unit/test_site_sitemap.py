"""``/sitemap-site.xml``: exactly the emitted canonical site pages (ADR-0014 Decision 3)."""

import re

from lxml import etree

from lovspor.publish.pages import SITE_ORIGIN
from lovspor.site.routes import EmittedPage, Localised, SiteRoute, emitted_pages
from lovspor.site.sitemap import sitemap_site_xml

_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _locs(payload: bytes) -> list[str]:
    parser = etree.XMLParser(resolve_entities=False, huge_tree=False)
    root = etree.fromstring(payload, parser)
    assert root.tag == "{http://www.sitemaps.org/schemas/sitemap/0.9}urlset"
    return [element.text or "" for element in root.findall("sm:url/sm:loc", _NS)]


class TestSitemapSiteXml:
    def test_lists_exactly_the_emitted_pages_as_absolute_urls(self) -> None:
        pages = emitted_pages()

        locs = _locs(sitemap_site_xml(pages))

        assert locs == [f"{SITE_ORIGIN}{page.path}" for page in pages]
        assert len(set(locs)) == len(pages) == 23

    def test_one_urlset_with_the_xml_header_and_no_lastmod(self) -> None:
        """No builder-generated time in the output tree (ADR:645-648): a
        site page has no corpus commit to borrow a ``lastmod`` from."""
        payload = sitemap_site_xml(emitted_pages())

        assert payload.startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="')
        assert payload.endswith(b"</urlset>\n")
        assert b"lastmod" not in payload
        assert payload.count(b"<urlset") == 1

    def test_no_json_or_xml_artifact_is_a_page(self) -> None:
        payload = sitemap_site_xml(emitted_pages())

        assert not re.search(rb"\.(json|xml)</loc>", payload)

    def test_is_deterministic_for_the_same_pages(self) -> None:
        assert sitemap_site_xml(emitted_pages()) == sitemap_site_xml(emitted_pages())

    def test_escapes_a_location(self) -> None:
        route = SiteRoute(
            path="/a-b/",
            template="placeholder",
            status="planned",
            title=Localised(nb="t", en="t"),
            description=Localised(nb="d", en="d"),
        )
        page = EmittedPage(path="/a-b/?x=1&y=2", lang="nb", route=route)

        payload = sitemap_site_xml((page,))

        assert b"&amp;" in payload
        assert _locs(payload) == [f"{SITE_ORIGIN}/a-b/?x=1&y=2"]

    def test_bytes_are_one_url_per_line_and_nothing_between(self) -> None:
        route = SiteRoute(
            path="/a/",
            template="placeholder",
            status="planned",
            title=Localised(nb="t", en="t"),
            description=Localised(nb="d", en="d"),
        )
        pages = (
            EmittedPage(path="/a/", lang="nb", route=route),
            EmittedPage(path="/en/a/", lang="en", route=route),
        )

        assert sitemap_site_xml(pages) == (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            b"<url><loc>https://lovspor.no/a/</loc></url>\n"
            b"<url><loc>https://lovspor.no/en/a/</loc></url>\n"
            b"</urlset>\n"
        )

    def test_empty_page_set_is_an_empty_urlset(self) -> None:
        assert _locs(sitemap_site_xml(())) == []
