"""Reading an undated listing page — a regulation overview (issue #514).

Every fixture is hand-written, modelled on the *structure* of the overview pages
saved on 2026-10-03 (``~/lovspor-ops/listing-entry-points-2026-10-03/``), never
copied from them: observed municipal HTML stays out of this repository
(ADR-0010 §5). The shapes are the ones those pages actually use — a ``<main>``
holding a grid ``<nav>`` of sub-pages, a whole-body ``<article>`` with one page
``<time>``, a section menu ``<nav>`` beside the article, breadcrumbs written
absolute with ``:443``, document links with and without a file suffix.
"""

from datetime import UTC, datetime, timedelta

import pytest

from lovspor.errors import ParseError
from lovspor.observatory.discovery import Candidate
from lovspor.observatory.freshness import CaptureState, worth_capturing
from lovspor.observatory.listing import LISTING_METHOD
from lovspor.observatory.listing_content import (
    _origin,
    is_content_link,
    parse_undated_listing,
    read_listing,
)

HOST = "https://www.example.invalid"
PAGE_URL = f"{HOST}/politikk/reglement/"


def _page(body: str, head: str = "") -> bytes:
    return f"<html><head>{head}</head><body>{body}</body></html>".encode()


def _urls(payload: bytes, url: str = PAGE_URL) -> list[str]:
    return [entry.url for entry in read_listing(payload, url).entries]


class TestTheModeIsChosenByThePage:
    def test_a_page_with_dated_entries_is_read_as_a_dated_list(self) -> None:
        readout = read_listing(
            _page(
                '<main><ul><li><time datetime="2026-08-01">d</time><a href="/a">A</a></li>'
                '<li><a href="/undated">U</a></li></ul></main>'
            ),
            PAGE_URL,
        )

        assert readout.undated is False
        assert [(e.url, e.site_reported_lastmod) for e in readout.entries] == [
            (f"{HOST}/a", "2026-08-01")
        ]
        assert readout.skipped_without_date == 1

    def test_a_page_with_no_dated_entry_is_read_as_an_overview(self) -> None:
        readout = read_listing(
            _page(
                '<main><p><a href="/download/forskrift-om-gebyr.pdf">Forskrift</a></p>'
                '<p><a href="/politikk/reglement/delegering">Delegering</a></p></main>'
            ),
            PAGE_URL,
        )

        assert readout.undated is True
        assert readout.skipped_without_date == 0
        assert [(e.url, e.site_reported_lastmod) for e in readout.entries] == [
            (f"{HOST}/download/forskrift-om-gebyr.pdf", None),
            (f"{HOST}/politikk/reglement/delegering", None),
        ]

    def test_a_whole_body_article_with_a_page_date_is_read_as_an_overview(self) -> None:
        """The false positive of issue #514: one page ``<time>`` used to date
        every link, share buttons included."""
        readout = read_listing(
            _page(
                "<main><article>"
                '<p>Sist endret <time datetime="2026-04-21T12:34:20+02:00">x</time></p>'
                '<p><a href="/download/18.2e/1762/Politisk_reglement.pdf">Reglement</a></p>'
                '<p><a href="/download/18.e7/1776/Finansreglement">Finans</a></p>'
                '<p><a href="https://www.facebook.com/sharer.php?u=https://x.invalid/">Del</a></p>'
                "</article></main>"
            ),
            PAGE_URL,
        )

        assert readout.undated is True
        assert [(e.url, e.site_reported_lastmod) for e in readout.entries] == [
            (f"{HOST}/download/18.2e/1762/Politisk_reglement.pdf", None),
            (f"{HOST}/download/18.e7/1776/Finansreglement", None),
        ]


class TestTheContentRegion:
    def test_header_and_footer_outside_main_are_not_proposed(self) -> None:
        assert _urls(
            _page(
                '<header><a href="/tjenester">Tjenester</a></header>'
                '<main><a href="/politikk/reglement/a">A</a></main>'
                '<footer><a href="/personvern">Personvern</a></footer>'
            )
        ) == [f"{HOST}/politikk/reglement/a"]

    def test_a_nav_inside_main_is_the_overview_and_is_kept(self) -> None:
        """One cleared page lays its sub-pages out as a ``<nav>`` grid inside
        ``<main>``; dropping every ``nav`` would propose nothing there."""
        assert _urls(
            _page(
                '<header><nav><a href="/menu">Meny</a></nav></header>'
                '<main><nav class="menu-grid"><a href="/politikk/reglement/godtgjoring/">G</a>'
                '<a href="/okonomireglement/">Ø</a></nav></main>'
            )
        ) == [f"{HOST}/politikk/reglement/godtgjoring/", f"{HOST}/okonomireglement/"]

    def test_without_main_nav_and_aside_are_chrome(self) -> None:
        assert _urls(
            _page(
                '<nav><a href="/menu">Meny</a></nav>'
                '<aside><a href="/aktuelt">Aktuelt</a></aside>'
                '<div><a href="/politikk/reglement/a">A</a></div>'
            )
        ) == [f"{HOST}/politikk/reglement/a"]

    @pytest.mark.parametrize("role", ["banner", "contentinfo", "search", " Search "])
    def test_chrome_landmark_roles_are_dropped_even_inside_main(self, role: str) -> None:
        assert _urls(
            _page(
                f'<main><div role="{role}"><a href="/chrome">C</a></div>'
                '<a href="/politikk/reglement/a">A</a></main>'
            )
        ) == [f"{HOST}/politikk/reglement/a"]

    @pytest.mark.parametrize("role", ["navigation", "complementary"])
    def test_navigation_roles_are_dropped_only_without_main(self, role: str) -> None:
        body = f'<div role="{role}"><a href="/side">S</a></div><a href="/doc">D</a>'

        assert _urls(_page(body)) == [f"{HOST}/doc"]
        assert _urls(_page(f"<main>{body}</main>")) == [f"{HOST}/side", f"{HOST}/doc"]

    def test_header_and_footer_inside_main_are_dropped(self) -> None:
        assert _urls(
            _page(
                '<main><header><a href="/x">X</a></header><a href="/doc">D</a>'
                '<footer><a href="/y">Y</a></footer></main>'
            )
        ) == [f"{HOST}/doc"]

    def test_an_article_narrows_the_region_past_a_section_menu(self) -> None:
        """A page that marks its own text as an article has said where its
        content is; the section menu beside it is not the overview."""
        assert _urls(
            _page(
                '<main><nav class="side"><a href="/politikk/valg/">Valg</a>'
                '<a href="/politikk/beredskap/">Beredskap</a></nav>'
                '<article><ul><li><a href="/getfile.php/1/Reglement/kommunestyret.pdf">K</a></li>'
                '<li><a href="/getfile.php/2/Reglement/formannskap.pdf">F</a></li></ul></article>'
                "</main>"
            )
        ) == [
            f"{HOST}/getfile.php/1/Reglement/kommunestyret.pdf",
            f"{HOST}/getfile.php/2/Reglement/formannskap.pdf",
        ]

    def test_every_outermost_article_with_links_counts_and_nested_ones_are_not_doubled(
        self,
    ) -> None:
        assert _urls(
            _page(
                '<main><a href="/outside">O</a>'
                '<article><a href="/one">1</a><article><a href="/two">2</a></article></article>'
                "<article><p>No links here.</p></article>"
                '<article><a href="/three">3</a></article></main>'
            )
        ) == [f"{HOST}/one", f"{HOST}/two", f"{HOST}/three"]

    def test_an_article_without_links_does_not_narrow_the_region(self) -> None:
        assert _urls(
            _page('<main><article><p>Intro.</p></article><a href="/doc">D</a></main>')
        ) == [f"{HOST}/doc"]

    def test_scripts_are_not_content(self) -> None:
        assert _urls(
            _page(
                '<main><noscript><a href="/enable-js">JS</a></noscript>'
                '<template><a href="/tpl">T</a></template><a href="/doc">D</a></main>'
            )
        ) == [f"{HOST}/doc"]


class TestWhatIsADocumentLink:
    def test_breadcrumbs_and_the_page_itself_are_not_proposed(self) -> None:
        assert _urls(
            _page(
                '<main><ol class="breadcrumb"><li><a href="/">Forside</a></li>'
                '<li><a href="/politikk">Politikk</a></li>'
                '<li><a href="/politikk/reglement/">Reglement</a></li></ol>'
                '<a href="/politikk/reglement/delegering/">Delegering</a></main>'
            )
        ) == [f"{HOST}/politikk/reglement/delegering/"]

    def test_breadcrumbs_written_absolute_with_the_default_port_are_still_ancestors(
        self,
    ) -> None:
        assert _urls(
            _page(
                '<main><a href="https://www.example.invalid:443/">Forside</a>'
                '<a href="https://www.example.invalid:443/politikk/">P</a>'
                '<a href="https://www.example.invalid:443/politikk/reglement/">R</a>'
                '<a href="https://www.example.invalid:443/politikk/reglement/godtgjoring/">G</a>'
                "</main>"
            )
        ) == ["https://www.example.invalid:443/politikk/reglement/godtgjoring/"]

    def test_a_fragment_is_dropped_and_a_link_to_this_page_s_section_is_not_proposed(
        self,
    ) -> None:
        assert _urls(
            _page(
                '<main><a href="#svid10_33a">Barn og oppvekst</a>'
                '<a href="/politikk/reglement/#del-2">Del 2</a>'
                '<a href="/forskrift-om-gebyr#kapittel-2">Gebyr</a>'
                '<a href="/forskrift-om-gebyr">Gebyr igjen</a></main>'
            )
        ) == [f"{HOST}/forskrift-om-gebyr"]

    @pytest.mark.parametrize(
        "href",
        [
            "mailto:?subject=Tips&body=https://x.invalid",
            "tel:+4712345678",
            "javascript:window.print()",
            "ftp://www.example.invalid/reglement.pdf",
        ],
    )
    def test_non_web_links_are_not_proposed(self, href: str) -> None:
        assert _urls(_page(f'<main><a href="{href}">x</a><a href="/doc">D</a></main>')) == [
            f"{HOST}/doc"
        ]

    @pytest.mark.parametrize(
        "path",
        [
            "/sok?query=forskrift",
            "/system/sok",
            "/search/",
            "/login",
            "/logg-inn",
            "/logginn",
            "/innlogging/",
            "/logout",
            "/logg-ut",
            "/cookies",
            "/om-nettstedet/informasjonskapsler/",
            "/share",
            "/del",
            "/55.html?query=forskrift",
            "/finn?q=x",
            "/x?Search=y",
            "/redirect?to=https://elsewhere.invalid/",
        ],
    )
    def test_site_functions_are_not_documents(self, path: str) -> None:
        assert is_content_link(f"{HOST}{path}", PAGE_URL) is False

    @pytest.mark.parametrize(
        "path",
        [
            "/download/18.33a/1741/Forskrift%20om%20gebyr.pdf",
            "/download/18.33a/1741/Reglement",
            "/globalassets/reglement/delegering.DOCX",
            "/dokumenter/vedtekter.odt",
            "/getfile.php/1360632/Reglement/kommunestyret.pdf",
            "/tjenester/politikk/reglementer/okonomireglement",
            "/sokeskjema-tilskudd",
            "/delegeringsreglement",
            "/side?id=3",
        ],
    )
    def test_pages_and_documents_are_content(self, path: str) -> None:
        assert is_content_link(f"{HOST}{path}", PAGE_URL) is True

    @pytest.mark.parametrize(
        "suffix", ["jpg", "JPEG", "png", "gif", "svg", "webp", "ico", "css", "js"]
    )
    def test_images_and_assets_are_not_documents(self, suffix: str) -> None:
        assert is_content_link(f"{HOST}/bilder/logo.{suffix}", PAGE_URL) is False

    def test_an_off_domain_link_is_left_to_the_domain_guard(self) -> None:
        """Discovery's guard is the one place a host is judged against the
        source, and it records the refusal. Judging it here as well would be a
        second implementation free to drift from the first."""
        assert _urls(
            _page(
                '<main><a href="https://lovdata.no/dokument/LF/forskrift/2024-03-20-547">F</a></main>'
            )
        ) == ["https://lovdata.no/dokument/LF/forskrift/2024-03-20-547"]

    def test_an_ancestor_path_on_another_host_is_a_document_link(self) -> None:
        assert is_content_link("https://other.invalid/politikk/", PAGE_URL) is True

    def test_an_ancestor_path_with_a_query_is_not_the_ancestor(self) -> None:
        assert is_content_link(f"{HOST}/politikk?id=4", PAGE_URL) is True

    def test_a_sibling_whose_name_extends_an_ancestor_is_not_one(self) -> None:
        assert is_content_link(f"{HOST}/politikk/regl", PAGE_URL) is True
        assert is_content_link(f"{HOST}/politikk-og-planer/", PAGE_URL) is True

    def test_another_port_is_another_site(self) -> None:
        assert is_content_link("https://www.example.invalid:8443/politikk/", PAGE_URL) is True

    def test_the_default_port_follows_the_scheme(self) -> None:
        assert _origin("http://h.invalid/") == ("http", "h.invalid", 80)
        assert _origin("https://h.invalid/") == ("https", "h.invalid", 443)
        assert _origin("https://h.invalid:8443/") == ("https", "h.invalid", 8443)

    def test_each_url_is_proposed_once_in_document_order(self) -> None:
        assert _urls(
            _page(
                '<main><a href="/b">B</a><a href="/a">A</a>'
                '<a href="https://www.example.invalid/b">B again</a></main>'
            )
        ) == [f"{HOST}/b", f"{HOST}/a"]

    def test_a_base_href_cannot_move_the_proposals(self) -> None:
        assert _urls(
            _page(
                '<main><a href="/a">A</a></main>', head='<base href="https://elsewhere.invalid/">'
            )
        ) == [f"{HOST}/a"]


class TestWhatIsReported:
    def test_links_not_proposed_are_counted_across_the_whole_page(self) -> None:
        readout = read_listing(
            _page(
                '<header><a href="/menu">M</a></header>'
                '<main><a href="/">Forside</a><a href="#x">anchor</a>'
                '<a href="/doc">D</a><a href="/doc">D again</a></main>'
                '<footer><a href="/personvern">P</a></footer>'
            ),
            PAGE_URL,
        )

        assert [e.url for e in readout.entries] == [f"{HOST}/doc"]
        assert readout.not_proposed == 4

    def test_a_page_with_no_content_link_is_a_refusal_naming_what_it_saw(self) -> None:
        with pytest.raises(ParseError) as caught:
            read_listing(
                _page('<header><a href="/menu">M</a></header><main><div id="app"></div></main>'),
                PAGE_URL,
            )

        assert str(caught.value) == (
            f"{PAGE_URL}: no dated listing entries and no content links in the served HTML "
            "(1 link(s) seen) — the page may be assembled in the browser, "
            "which this reader deliberately does not do"
        )

    def test_bytes_that_are_not_a_page_at_all_are_refused(self) -> None:
        with pytest.raises(ParseError, match="unreadable listing page"):
            read_listing(b"", PAGE_URL)

    def test_a_frameset_has_no_content_region(self) -> None:
        with pytest.raises(ParseError, match="no body"):
            parse_undated_listing(b'<html><frameset><frame src="/a"></frameset></html>', PAGE_URL)

    def test_an_undeclared_encoding_is_read_as_utf8(self) -> None:
        readout = read_listing(
            '<main><a href="/forskrift-om-økonomisk">Ø</a></main>'.encode(), PAGE_URL
        )

        assert readout.entries[0].url == f"{HOST}/forskrift-om-økonomisk"


class TestUndatedIsNotRefetchedEveryNight:
    """An undated proposal has no ``lastmod`` to decline work with, so freshness
    falls back on its own record of the URL (issues #209, #415): left alone for
    24 hours after a sighting, longer for every unchanged re-capture."""

    NOW = datetime(2026, 10, 3, 3, 0, tzinfo=UTC)

    def _candidate(self) -> Candidate:
        entry = read_listing(
            _page('<main><a href="/reglement.pdf">R</a></main>'), PAGE_URL
        ).entries[0]
        return Candidate(
            url=entry.url,
            discovery_method=LISTING_METHOD,
            found_in=PAGE_URL,
            site_reported_lastmod=entry.site_reported_lastmod,
        )

    def test_a_proposal_seen_last_night_is_not_fetched_again(self) -> None:
        candidate = self._candidate()
        state = CaptureState({candidate.url: self.NOW - timedelta(hours=23)}, {}, {})

        assert worth_capturing(candidate, state, self.NOW) is False

    def test_a_proposal_never_seen_is_fetched(self) -> None:
        assert worth_capturing(self._candidate(), CaptureState.empty(), self.NOW) is True
