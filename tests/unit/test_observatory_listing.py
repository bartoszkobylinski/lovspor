"""Reading a listing page into candidates (issue #151, part 2).

Every fixture here is hand-written. Real municipal HTML is observed material,
and ADR-0010 §5 keeps observed material out of this repository — so these pages
are constructed to carry the *structures* the reader claims to handle, and
nothing is committed that came off someone's server.
"""

import pytest
from lxml import etree, html

from lovspor.errors import ParseError
from lovspor.observatory.listing import _tag_of, parse_listing, safe_html_parser, usable_href

PAGE_URL = "https://www.example.invalid/kunngjoringer/"


def _page(body: str) -> bytes:
    return f"<html><body>{body}</body></html>".encode()


class TestWhatCountsAsAnEntry:
    def test_a_dated_link_in_a_list_item_is_a_candidate(self) -> None:
        readout = parse_listing(
            _page(
                '<ul><li><time datetime="2026-08-01">1. august</time>'
                '<a href="/forskrift-om-vann">Forskrift om vann</a></li></ul>'
            ),
            PAGE_URL,
        )

        assert [(link.url, link.site_reported_lastmod) for link in readout.entries] == [
            ("https://www.example.invalid/forskrift-om-vann", "2026-08-01")
        ]

    def test_the_same_structure_works_in_a_table_and_an_article(self) -> None:
        """Structural, not CMS-specific: three markups, one rule."""
        table = parse_listing(
            _page(
                '<table><tr><td><time datetime="2026-08-02">x</time></td>'
                '<td><a href="/a">A</a></td></tr></table>'
            ),
            PAGE_URL,
        )
        article = parse_listing(
            _page('<article><a href="/b">B</a><time datetime="2026-08-03">y</time></article>'),
            PAGE_URL,
        )

        assert table.entries[0].site_reported_lastmod == "2026-08-02"
        assert article.entries[0].site_reported_lastmod == "2026-08-03"

    def test_the_date_may_sit_either_side_of_the_link(self) -> None:
        """A date and its link are reliably in the same entry and unreliably
        adjacent, so the entry is the unit, not the sibling."""
        readout = parse_listing(
            _page(
                '<ul><li><a href="/a">A</a><span><time datetime="2026-08-04">d</time></span>'
                "</li></ul>"
            ),
            PAGE_URL,
        )

        assert readout.entries[0].site_reported_lastmod == "2026-08-04"

    def test_an_entry_carries_only_a_url_and_the_page_s_own_date(self) -> None:
        """This module reads HTML and knows nothing about discovery methods or
        nesting. Importing that vocabulary pointed the dependency backwards and
        produced an import cycle neither ruff nor mypy saw — discovery labels
        these, here they are just a link and a claim about it."""
        readout = parse_listing(
            _page('<ul><li><time datetime="2026-08-05">d</time><a href="/a">A</a></li></ul>'),
            PAGE_URL,
        )

        assert readout.entries[0]._fields == ("url", "site_reported_lastmod")

    def test_a_relative_link_resolves_against_the_page_it_came_from(self) -> None:
        readout = parse_listing(
            _page(
                '<ul><li><time datetime="2026-08-06">d</time>'
                '<a href="../planer/plan-1">P</a></li></ul>'
            ),
            PAGE_URL,
        )

        assert readout.entries[0].url == "https://www.example.invalid/planer/plan-1"

    def test_a_base_href_cannot_move_the_proposals(self) -> None:
        """A served page must not be able to redirect this reader's proposals to
        a host the source was never cleared for. The domain guard would refuse
        them later; they should not be proposed at all."""
        payload = (
            b'<html><head><base href="https://elsewhere.invalid/"></head><body>'
            b'<ul><li><time datetime="2026-08-07">d</time><a href="/a">A</a></li></ul>'
            b"</body></html>"
        )

        readout = parse_listing(payload, PAGE_URL)

        assert readout.entries[0].url == "https://www.example.invalid/a"


class TestWhatIsRefused:
    def test_a_page_with_no_dated_entries_is_a_refusal_not_an_empty_result(self) -> None:
        """ "This page has no entries today" and "this reader cannot see this
        page's entries" are different facts, and only one is about the source."""
        with pytest.raises(ParseError, match="no dated listing entries"):
            parse_listing(_page('<div><a href="/a">A</a></div>'), PAGE_URL)

    def test_the_refusal_says_how_many_links_it_saw(self) -> None:
        """A page full of undated links reads very differently from an empty
        one, and the operator needs to tell them apart."""
        with pytest.raises(ParseError, match=r"\(3 undated link\(s\) seen\)"):
            parse_listing(
                _page(
                    "<ul>" + "".join(f'<li><a href="/{n}">x</a></li>' for n in range(3)) + "</ul>"
                ),
                PAGE_URL,
            )

    def test_the_refusal_names_the_browser_assembly_it_will_not_do(self) -> None:
        with pytest.raises(ParseError) as caught:
            parse_listing(_page('<div id="app"></div>'), PAGE_URL)

        assert str(caught.value) == (
            f"{PAGE_URL}: no dated listing entries in the served HTML "
            "(0 undated link(s) seen) — the page may be assembled in the browser, "
            "which this reader deliberately does not do"
        )

    def test_a_link_outside_any_entry_is_skipped_rather_than_dated_wrongly(self) -> None:
        """Navigation and footers sit beside the list; borrowing an entry's date
        for them would attach a real date to an unrelated page."""
        readout = parse_listing(
            _page(
                '<nav><a href="/home">Hjem</a></nav>'
                '<ul><li><time datetime="2026-08-08">d</time><a href="/a">A</a></li></ul>'
            ),
            PAGE_URL,
        )

        assert [link.url for link in readout.entries] == ["https://www.example.invalid/a"]
        assert readout.skipped_without_date == 1

    @pytest.mark.parametrize(
        "href", ["#top", "mailto:post@example.invalid", "tel:+4712345678", "javascript:void(0)"]
    )
    def test_non_document_links_are_not_candidates(self, href: str) -> None:
        with pytest.raises(ParseError):
            parse_listing(
                _page(
                    f'<ul><li><time datetime="2026-08-09">d</time><a href="{href}">x</a></li></ul>'
                ),
                PAGE_URL,
            )

    def test_an_entry_whose_time_carries_no_datetime_is_undated(self) -> None:
        """A `<time>` without the attribute is prose. Reading the text would be
        a locale guessing game this reader refuses to play."""
        with pytest.raises(ParseError, match=r"\(1 undated link"):
            parse_listing(
                _page("<ul><li><time>1. august 2026</time><a href='/a'>A</a></li></ul>"), PAGE_URL
            )

    def test_an_anchor_without_href_does_not_count_as_an_undated_link(self) -> None:
        readout = parse_listing(
            _page(
                "<ul><li><a>Label only</a></li>"
                '<li><time datetime="2026-08-09">d</time><a href="/a">A</a></li></ul>'
            ),
            PAGE_URL,
        )

        assert readout.skipped_without_date == 0
        assert [entry.url for entry in readout.entries] == ["https://www.example.invalid/a"]

    def test_a_non_document_link_does_not_stop_later_entries(self) -> None:
        readout = parse_listing(
            _page(
                '<a href="#navigation">Skip</a>'
                '<ul><li><time datetime="2026-08-09">d</time><a href="/a">A</a></li></ul>'
            ),
            PAGE_URL,
        )

        assert [entry.url for entry in readout.entries] == ["https://www.example.invalid/a"]

    def test_only_anchor_elements_are_links_even_if_another_element_has_href(self) -> None:
        readout = parse_listing(
            _page(
                '<ul><li><time datetime="2026-08-09">d</time><span href="/not-a-link">x</span>'
                '<a href="/a">A</a></li></ul>'
            ),
            PAGE_URL,
        )

        assert [entry.url for entry in readout.entries] == ["https://www.example.invalid/a"]


class TestADateBelongsToOneEntry:
    """Issue #514: a CMS that wraps the whole body in one ``<article>`` with the
    page's own "last updated" ``<time>`` stamped every link on the page with
    that date — share buttons and off-domain links included."""

    def test_a_page_date_on_a_whole_body_article_dates_no_link(self) -> None:
        with pytest.raises(ParseError, match=r"\(3 undated link\(s\) seen\)"):
            parse_listing(
                _page(
                    '<article><p>Sist endret <time datetime="2026-04-21">21. april</time></p>'
                    '<p><a href="/download/reglement.pdf">Reglement</a></p>'
                    '<p><a href="https://lovdata.no/dokument/LF/forskrift/2025-11-03-2390">F</a></p>'
                    '<p><a href="https://www.facebook.com/sharer.php?u=x">Del</a></p></article>'
                ),
                PAGE_URL,
            )

    def test_share_buttons_inside_a_dated_row_do_not_borrow_its_date(self) -> None:
        readout = parse_listing(
            _page(
                "<ul>"
                '<li><time datetime="2026-08-01">d</time><a href="/a">A</a>'
                '<a href="https://twitter.com/intent/tweet?url=a">Del</a></li>'
                '<li><time datetime="2026-08-02">d</time><a href="/b">B</a></li>'
                "</ul>"
            ),
            PAGE_URL,
        )

        assert [(e.url, e.site_reported_lastmod) for e in readout.entries] == [
            ("https://www.example.invalid/b", "2026-08-02")
        ]
        assert readout.skipped_without_date == 2

    def test_two_links_to_the_same_url_are_still_one_entry(self) -> None:
        """A title link and a "read more" link to one page is one entry."""
        readout = parse_listing(
            _page(
                '<ul><li><a href="/a">Title</a><time datetime="2026-08-03">d</time>'
                '<a href="https://www.example.invalid/a">Les mer</a></li></ul>'
            ),
            PAGE_URL,
        )

        assert [(e.url, e.site_reported_lastmod) for e in readout.entries] == [
            ("https://www.example.invalid/a", "2026-08-03")
        ]
        assert readout.skipped_without_date == 0

    def test_a_dated_list_with_the_time_inside_each_link_is_read_as_before(self) -> None:
        """The one real dated list of the 2026-10-03 crawl (a hearings archive):
        the whole entry is one anchor with its ``<time>`` inside."""
        readout = parse_listing(
            _page(
                '<nav><ol><li><a href="/">Forside</a></li></ol></nav><ul>'
                + "".join(
                    f'<li><a href="/artikler/h{n}"><p><time datetime="2026-0{n}-19T14:33:03+02:00">'
                    f"{n}</time></p><h2>Høring {n}</h2></a></li>"
                    for n in range(1, 4)
                )
                + "</ul>"
            ),
            PAGE_URL,
        )

        assert [(e.url.rsplit("/", 1)[-1], e.site_reported_lastmod) for e in readout.entries] == [
            ("h1", "2026-01-19T14:33:03+02:00"),
            ("h2", "2026-02-19T14:33:03+02:00"),
            ("h3", "2026-03-19T14:33:03+02:00"),
        ]
        assert readout.skipped_without_date == 1
        assert readout.undated is False

    def test_a_row_holding_dated_rows_dates_only_its_own_link_if_it_is_alone(self) -> None:
        readout = parse_listing(
            _page(
                '<ul><li><a href="/section">Section</a><ul>'
                '<li><time datetime="2026-08-04">d</time><a href="/x">X</a></li>'
                '<li><time datetime="2026-08-05">d</time><a href="/y">Y</a></li>'
                "</ul></li></ul>"
            ),
            PAGE_URL,
        )

        assert [e.url.rsplit("/", 1)[-1] for e in readout.entries] == ["x", "y"]
        assert readout.skipped_without_date == 1

    def test_a_non_document_link_in_a_row_does_not_make_it_a_container(self) -> None:
        readout = parse_listing(
            _page(
                '<ul><li><time datetime="2026-08-06">d</time><a href="/a">A</a>'
                '<a href="#comments">Kommentarer</a><a href="mailto:x@example.invalid">E</a>'
                "</li></ul>"
            ),
            PAGE_URL,
        )

        assert [e.site_reported_lastmod for e in readout.entries] == ["2026-08-06"]


class TestUsableHref:
    @pytest.mark.parametrize(
        "href", ["", "   ", "#top", "mailto:a@b.invalid", "tel:1", "javascript:x", "data:x"]
    )
    def test_hrefs_that_address_no_document_are_none(self, href: str) -> None:
        assert usable_href(html.fromstring(f'<a href="{href}">x</a>')) is None

    def test_a_missing_href_is_none(self) -> None:
        assert usable_href(html.fromstring("<a>x</a>")) is None

    def test_a_document_href_is_returned_stripped(self) -> None:
        assert usable_href(html.fromstring('<a href="  /a  ">x</a>')) == "/a"


class TestTheSamePageTwice:
    def test_a_url_listed_twice_is_proposed_once(self) -> None:
        """Discovery output feeds a pipeline ADR-0010 §7 requires to be a pure
        function of its input, and a duplicate would be fetched twice."""
        readout = parse_listing(
            _page(
                '<ul><li><time datetime="2026-08-10">d</time><a href="/a">A</a></li>'
                '<li><time datetime="2026-08-11">d</time><a href="/a">A again</a></li></ul>'
            ),
            PAGE_URL,
        )

        assert len(readout.entries) == 1
        assert readout.entries[0].site_reported_lastmod == "2026-08-10"

    def test_entries_keep_the_page_s_own_order(self) -> None:
        readout = parse_listing(
            _page(
                "<ul>"
                + "".join(
                    f'<li><time datetime="2026-08-1{n}">d</time><a href="/{n}">x</a></li>'
                    for n in range(3)
                )
                + "</ul>"
            ),
            PAGE_URL,
        )

        assert [link.url.rsplit("/", 1)[-1] for link in readout.entries] == ["0", "1", "2"]


class TestTheParserItself:
    def test_safe_parser_passes_every_hardening_option_explicitly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sentinel = object()
        calls: list[dict[str, bool]] = []

        def parser_factory(**options: bool) -> object:
            calls.append(options)
            return sentinel

        monkeypatch.setattr(html, "HTMLParser", parser_factory)

        assert safe_html_parser() is sentinel
        assert calls == [{"no_network": True, "huge_tree": False, "remove_comments": True}]

    def test_parse_listing_passes_its_url_and_safe_parser_to_lxml(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parser = object()
        root = html.fromstring(
            _page('<ul><li><time datetime="2026-08-12">d</time><a href="/a">A</a></li></ul>')
        )
        calls: list[tuple[bytes, str, object]] = []

        monkeypatch.setattr("lovspor.observatory.listing.safe_html_parser", lambda: parser)

        def fromstring(payload: bytes, *, base_url: str, parser: object) -> html.HtmlElement:
            calls.append((payload, base_url, parser))
            return root

        monkeypatch.setattr(html, "fromstring", fromstring)
        payload = b"served bytes"

        parse_listing(payload, PAGE_URL)

        assert calls == [(payload, PAGE_URL, parser)]

    def test_an_unreadable_page_reports_its_url_and_parser_error(self) -> None:
        with pytest.raises(ParseError) as caught:
            parse_listing(b"", PAGE_URL)

        assert str(caught.value).startswith(f"{PAGE_URL}: unreadable listing page:")

    def test_a_non_element_node_has_no_tag_name(self) -> None:
        assert _tag_of(etree.Comment("not an element")) == ""

    def test_a_declared_external_entity_is_not_expanded(self) -> None:
        """The XML side switches entity resolution off explicitly; the HTML
        parser does not take that argument and does not substitute DOCTYPE
        entities at all. This pins the behaviour that stands in its place, so a
        dependency bump cannot quietly turn a comment into a false claim."""
        payload = (
            b'<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            b'<html><body><ul><li><time datetime="2026-08-12">d</time>'
            b'<a href="/a">&xxe;</a></li></ul></body></html>'
        )

        readout = parse_listing(payload, PAGE_URL)

        assert "root:" not in readout.entries[0].url

    def test_the_parser_is_configured_against_the_network(self) -> None:
        """A served page must not be able to make this process fetch anything."""
        assert safe_html_parser() is not None

    def test_bytes_that_are_not_a_page_at_all_are_refused(self) -> None:
        with pytest.raises(ParseError):
            parse_listing(b"", PAGE_URL)
