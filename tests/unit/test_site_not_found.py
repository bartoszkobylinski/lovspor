"""The site-framed not-found page Caddy serves for every 404 (#662)."""

import re
from pathlib import Path

import pytest
from jinja2 import DictLoader

from lovspor.publish.pages import SITE_ORIGIN
from lovspor.site.errors import SiteBuildError
from lovspor.site.not_found import NOT_FOUND_NAME, NOT_FOUND_PATH, render_not_found, write_not_found
from lovspor.site.routes import emitted_pages
from lovspor.site.scan import scan_page
from lovspor.site.templates import site_environment


def _emitted() -> dict[str, str]:
    return {page.path: "" for page in emitted_pages()}


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ("", b""),
        (
            "\x00\x7f\u0080\u07ff\u0800\ud7ff\ue000\uffff\U00010000\U0010ffff",
            b"\x00\x7f\xc2\x80\xdf\xbf\xe0\xa0\x80\xed\x9f\xbf"
            b"\xee\x80\x80\xef\xbf\xbf\xf0\x90\x80\x80\xf4\x8f\xbf\xbf",
        ),
        (
            "<p>blå — żółć 😀</p>\r\n",
            b"<p>bl\xc3\xa5 \xe2\x80\x94 \xc5\xbc\xc3\xb3\xc5\x82\xc4\x87 \xf0\x9f\x98\x80</p>\r\n",
        ),
    ],
)
def test_write_not_found_preserves_exact_utf8_bytes(
    tmp_path: Path, markup: str, expected: bytes
) -> None:
    write_not_found(tmp_path, markup)

    assert (tmp_path / "404.html").read_bytes() == expected
    assert list(tmp_path.iterdir()) == [tmp_path / "404.html"]


@pytest.mark.parametrize("markup", ["\ud800", "\udfff", "before\ud800after", "\ud800\udc00"])
def test_write_not_found_refuses_unpaired_surrogates(tmp_path: Path, markup: str) -> None:
    with pytest.raises(UnicodeEncodeError):
        write_not_found(tmp_path, markup)

    assert not (tmp_path / "404.html").exists()


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

    def test_supplied_pages_are_also_link_checked(self) -> None:
        """The combined tree must pass the link check, including its emitted pages."""
        pages = _emitted()
        pages["/en/"] = '<a href="/gone/">Missing page</a>'

        with pytest.raises(SiteBuildError, match="page /en/ links to '/gone/'"):
            render_not_found(pages)

    @pytest.mark.parametrize(
        ("before", "after", "message"),
        [
            ("</h1>", " 404</h1>", "numeral outside the fact mechanism"),
            ("</body>", "<script></script></body>", "outside the no-script rule"),
            (f"{SITE_ORIGIN}/404.html", f"{SITE_ORIGIN}/", "rel=canonical"),
        ],
    )
    def test_invalid_template_output_is_refused_during_rendering(
        self, monkeypatch: pytest.MonkeyPatch, before: str, after: str, message: str
    ) -> None:
        """The non-route page must pass the same scans before it can be written."""
        markup = render_not_found(_emitted())
        assert before in markup
        environment = site_environment()
        environment.loader = DictLoader({"pages/not-found.html": markup.replace(before, after)})
        monkeypatch.setattr("lovspor.site.not_found.site_environment", lambda: environment)

        with pytest.raises(SiteBuildError, match=message) as caught:
            render_not_found(_emitted())
        assert NOT_FOUND_PATH in str(caught.value)
