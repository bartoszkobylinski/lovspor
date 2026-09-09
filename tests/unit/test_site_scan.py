"""Mutation-focused specifications for the post-render scanner."""

import pytest

from lovspor.publish.pages import SITE_ORIGIN
from lovspor.site.errors import SiteBuildError
from lovspor.site.scan import _asset_urls, _scan, check_links, scan_page


def _page(body: str, head: str = "") -> str:
    return (
        "<html><head><title>safe</title>"
        '<meta name="description" content="safe">'
        f'<link rel="canonical" href="{SITE_ORIGIN}/x/">{head}'
        f"</head><body>{body}</body></html>"
    )


def test_srcset_candidates_are_comma_delimited() -> None:
    assert _asset_urls("/one.png 1x, /two.png 2x") == ["/one.png", "/two.png"]


def test_scanner_collects_only_anchor_hrefs_and_empty_values() -> None:
    scanner = _scan(
        _page('<a href="">empty</a><div href="/not-a-link/">text</div>').replace(
            'content="safe"', 'content=""'
        )
    )

    assert scanner.descriptions == [""]
    assert scanner.links == [""]


def test_scanner_decodes_character_references_before_numeral_check() -> None:
    with pytest.raises(SiteBuildError, match="numeral"):
        scan_page("/x/", _page("&#49;"))


@pytest.mark.parametrize(
    ("markup", "message"),
    [
        ("<script></script>", "<script> element"),
        ('<p onclick="go()">x</p>', "onclick handler on <p>"),
        ('<p style="url(https://example.invalid/x)">x</p>', "style attribute"),
        ("<style>@import url(/x)</style>", "@import or external url() in <style>"),
    ],
)
def test_scanner_reports_the_exact_no_script_finding(markup: str, message: str) -> None:
    with pytest.raises(SiteBuildError, match=message.replace("(", "\\(").replace(")", "\\)")):
        scan_page("/x/", _page(markup))


def test_nested_end_tags_restore_body_text_scanning() -> None:
    with pytest.raises(SiteBuildError, match="numeral"):
        scan_page("/x/", _page("<div><span>safe</span></div>7"))


def test_title_is_scanned_but_ordinary_head_text_is_not() -> None:
    with pytest.raises(SiteBuildError, match="numeral"):
        scan_page("/x/", _page("safe").replace("<title>safe</title>", "<title>7</title>"))


def test_text_after_body_is_not_scanned_as_body_content() -> None:
    scan_page("/x/", _page("safe") + "7")


def test_style_element_matching_is_case_insensitive() -> None:
    with pytest.raises(SiteBuildError, match="@import or external"):
        scan_page("/x/", _page("<STYLE>@import url(/x)</STYLE>"))


def test_empty_link_fields_are_preserved_for_validation() -> None:
    scanner = _scan(_page("safe", '<link href="">'))

    assert scanner.alternate_links == []
    assert "<link rel=''" in scanner.findings[0]


def test_fragment_link_does_not_stop_validation_of_later_links() -> None:
    pages = {"/x/": _page('<a href="#ok">fragment</a><a href="/missing/">missing</a>')}

    with pytest.raises(SiteBuildError, match="page /x/ links to '/missing/'"):
        check_links(pages)


def test_reciprocity_error_names_the_page() -> None:
    head = f'<link rel="alternate" hreflang="en" href="{SITE_ORIGIN}/elsewhere/">'

    with pytest.raises(SiteBuildError, match="page /x/.*name the page itself once"):
        check_links({"/x/": _page("safe", head)})


def test_a_literal_stays_marked_after_a_nested_element_closes() -> None:
    """Closing ``<b>`` pops ``<b>`` and nothing above it: the ``data-literal``
    span still excludes the ``2`` that follows."""
    scan_page("/x/", _page("<p><span data-literal>v<b>x</b>2</span></p>"))


def test_a_canonical_link_without_href_is_reported_as_the_empty_href() -> None:
    markup = _page("safe").replace(
        f'<link rel="canonical" href="{SITE_ORIGIN}/x/">', '<link rel="canonical">'
    )

    with pytest.raises(SiteBuildError, match=r"rel=canonical is \[''\], not exactly itself once"):
        scan_page("/x/", markup)


def test_fragment_protocol_relative_and_scheme_links_are_not_checked_against_the_tree() -> None:
    check_links(
        {
            "/x/": _page(
                '<a href="#top">a</a><a href="//example.com/">b</a>'
                '<a href="https://example.com/">c</a><a href="mailto:x@example.com">d</a>'
            )
        }
    )


def test_head_text_outside_the_title_is_not_scanned() -> None:
    """Page text is the body, the title and the description (module doc):
    stray head text is not rendered, and is not the numeral scan's business."""
    scan_page("/x/", _page("safe").replace("<head>", "<head>7"))
