"""The site-framed not-found page Caddy serves for every 404 (#662)."""

import re

import pytest

from lovspor.publish.pages import SITE_ORIGIN
from lovspor.site.errors import SiteBuildError
from lovspor.site.not_found import NOT_FOUND_NAME, NOT_FOUND_PATH, render_not_found
from lovspor.site.routes import emitted_pages
from lovspor.site.scan import scan_page


def _emitted() -> dict[str, str]:
    return {page.path: "" for page in emitted_pages()}


class TestNotFoundPage:
    def test_the_file_is_the_one_the_fragment_rewrites_to(self) -> None:
        assert NOT_FOUND_NAME == "404.html"
        assert NOT_FOUND_PATH == "/404.html"

    def test_it_is_framed_by_the_site_chrome_in_norwegian(self) -> None:
        markup = render_not_found(_emitted())

        assert markup.startswith("<!doctype html>")
        assert '<html lang="nb">' in markup
        assert '<a class="brand" href="/">' in markup
        assert '<nav class="site"' in markup
        assert "<footer>" in markup
        assert markup.count("<h1>") == 1

    def test_it_has_its_own_canonical_and_no_alternates(self) -> None:
        markup = render_not_found(_emitted())

        assert f'<link rel="canonical" href="{SITE_ORIGIN}/404.html">' in markup
        assert "hreflang" not in markup.split("<body>")[0]
        assert '<nav class="switch"' not in markup

    def test_it_speaks_all_three_languages_and_points_home_in_each(self) -> None:
        markup = render_not_found(_emitted())

        assert '<section lang="en">' in markup
        assert '<section lang="pl">' in markup
        for home in ('href="/"', 'href="/en/"', 'href="/pl/"'):
            assert home in markup
        assert 'href="/lov/"' in markup and 'href="/forskrift/"' in markup

    def test_it_passes_the_page_scan_under_its_own_path(self) -> None:
        scan_page(NOT_FOUND_PATH, render_not_found(_emitted()))

    def test_it_types_no_status_code(self) -> None:
        """The numerals rule: no digit outside a ``data-literal``, so no bare 404."""
        body = re.sub(r"<style>.*?</style>", "", render_not_found(_emitted()), flags=re.S)

        assert "404" not in re.sub(r'href="[^"]*"', "", body)

    def test_it_is_deterministic(self) -> None:
        assert render_not_found(_emitted()) == render_not_found(_emitted())

    def test_a_link_to_a_page_the_tree_does_not_serve_is_refused(self) -> None:
        pages = {path: "" for path in _emitted() if path != "/pl/"}

        with pytest.raises(SiteBuildError, match="/pl/"):
            render_not_found(pages)
