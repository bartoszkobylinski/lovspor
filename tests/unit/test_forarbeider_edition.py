"""What a saved landing page proves about a proposition's edition (#784).

The fixtures are synthetic. Their structure was read off one real landing page
saved privately on 2026-11-05: a ``data-doc-meta="corrected-edition-date"``
notice in the article header, and an ``h3.edition`` heading over a link list to
the original edition and the rettebrev. No source text is reproduced.
"""

from datetime import date
from pathlib import Path

import pytest

from lovspor.forarbeider.edition import EditionFacts, read_landing
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason

FIXTURES = Path(__file__).parents[1] / "fixtures" / "forarbeider"
CORRECTED = FIXTURES / "landing-ls-corrected.html"
PLAIN = FIXTURES / "landing-l-plain.html"
LS_URL = "https://www.regjeringen.no/no/dokumenter/prop.-900-ls-20992100/id9000002/"
L_URL = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/"
ASSETS = "https://www.regjeringen.no/contentassets/00000000000000000000000000000002/no"
NOTICE = '<div data-doc-meta="corrected-edition-date">(Korrigert utgave per 13.5.2100)</div>'
BLOCK_START = '<h3 class="edition">'


def _corrected(old: str = "", new: str = "") -> bytes:
    text = CORRECTED.read_text(encoding="utf-8")
    if old:
        assert old in text, old
        text = text.replace(old, new)
    return text.encode()


def _refusal(payload: bytes, url: str = LS_URL) -> RefusalReason:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_landing(payload, url)
    return caught.value.reason


def test_a_stated_correction_is_read_with_its_date_and_links() -> None:
    locator, facts = read_landing(CORRECTED.read_bytes(), LS_URL)

    assert locator.document_id == 9000002
    assert locator.chapter is None
    assert facts == EditionFacts(
        kind="corrected",
        corrected_on=date(2100, 5, 13),
        original_edition_url=f"{ASSETS}/opri/prp209921000900000dddopri.pdf",
        rettebrev_urls=(f"{ASSETS}/rett/rettebrev.pdf",),
    )


def test_a_page_stating_no_edition_is_unknown_not_original() -> None:
    _, facts = read_landing(PLAIN.read_bytes(), L_URL)

    assert facts.kind == "unknown"
    assert facts.corrected_on is None
    assert facts.original_edition_url is None
    assert facts.rettebrev_urls == ()


def test_dc_date_is_never_read_as_the_correction_date() -> None:
    _, facts = read_landing(_corrected('content="2100-03-27"', 'content="2100-01-01"'), LS_URL)

    assert facts.corrected_on == date(2100, 5, 13)


def test_dc_date_alone_does_not_make_a_plain_page_corrected() -> None:
    payload = PLAIN.read_text(encoding="utf-8").replace("2100-03-20", "2100-05-13").encode()

    assert read_landing(payload, L_URL)[1].kind == "unknown"


@pytest.mark.parametrize(
    "url",
    [
        f"{LS_URL}?ch=1",
        f"{LS_URL}?utm_source=x",
        f"{LS_URL}?ch=1&utm_source=x",
        "http://www.regjeringen.no/no/dokumenter/prop.-900-ls-20992100/id9000002/",
        "https://regjeringen.no/no/dokumenter/prop.-900-ls-20992100/id9000002/",
        f"{LS_URL}#top",
    ],
    ids=["chapter", "extra-query", "chapter-and-query", "http", "bare-host", "fragment"],
)
def test_only_the_exact_landing_url_is_accepted(url: str) -> None:
    assert _refusal(CORRECTED.read_bytes(), url) in {
        RefusalReason.LANDING_PAGE_REQUIRED,
        RefusalReason.UNEXPECTED_QUERY,
        RefusalReason.INVALID_SOURCE_URL,
        RefusalReason.HOST_NOT_ALLOWED,
    }


def test_a_chapter_url_is_named_as_not_a_landing_page() -> None:
    assert _refusal(CORRECTED.read_bytes(), f"{LS_URL}?ch=1") is RefusalReason.LANDING_PAGE_REQUIRED


def test_a_page_saved_from_another_document_is_refused() -> None:
    other = "https://www.regjeringen.no/no/dokumenter/prop.-900-ls-20992100/id9000003/"

    assert _refusal(CORRECTED.read_bytes(), other) is RefusalReason.CONFLICTING_IDENTITY


def test_a_redirected_save_whose_og_url_differs_is_refused() -> None:
    payload = _corrected(
        'property="og:url" content="https://www.regjeringen.no/no/dokumenter/prop.-900-ls-20992100/id9000002/"',
        'property="og:url" content="https://www.regjeringen.no/no/dokumenter/prop.-900-ls-20992100/id9000009/"',
    )

    assert _refusal(payload) is RefusalReason.CONFLICTING_IDENTITY


def test_a_heading_naming_another_proposition_is_refused() -> None:
    payload = _corrected("<h1>Prop. 900 LS", "<h1>Prop. 902 LS")

    assert _refusal(payload) is RefusalReason.CONFLICTING_IDENTITY


def test_a_cut_page_is_refused() -> None:
    assert _refusal(CORRECTED.read_bytes()[:-40]) is RefusalReason.INCOMPLETE_PAGE


def test_a_page_that_is_not_a_proposition_is_refused() -> None:
    payload = _corrected('content="Proposisjon"', 'content="Melding"')

    assert _refusal(payload) is RefusalReason.UNSUPPORTED_DOCUMENT_TYPE


def test_bytes_that_are_not_utf8_are_refused() -> None:
    assert _refusal(_corrected() + b"\xff") is RefusalReason.UNREADABLE_PAGE


def test_a_page_without_one_article_header_is_refused() -> None:
    payload = _corrected('<header class="article-header">', '<header class="other">')

    assert _refusal(payload) is RefusalReason.DOM_CHANGED


def test_a_notice_without_the_edition_links_is_conflicting() -> None:
    payload = _corrected(BLOCK_START, '<h3 class="related">')

    assert _refusal(payload) is RefusalReason.EDITION_CONFLICTING


def test_edition_links_without_a_notice_are_conflicting() -> None:
    assert _refusal(_corrected(NOTICE, "")) is RefusalReason.EDITION_CONFLICTING


def test_two_notices_are_conflicting_even_when_they_agree() -> None:
    assert _refusal(_corrected(NOTICE, NOTICE + NOTICE)) is RefusalReason.EDITION_CONFLICTING


def test_two_notices_with_different_dates_are_conflicting() -> None:
    other = NOTICE.replace("13.5.2100", "14.5.2100")

    assert _refusal(_corrected(NOTICE, NOTICE + other)) is RefusalReason.EDITION_CONFLICTING


def test_two_edition_blocks_are_conflicting() -> None:
    block = (
        '<div><h3 class="edition">Opprinnelig utgave og rettebrev</h3>'
        '<ul class="link-list"></ul></div>'
    )

    assert _refusal(_corrected("<footer", f"{block}<footer")) is RefusalReason.EDITION_CONFLICTING


def test_two_original_editions_are_conflicting() -> None:
    link = (
        '<li><a href="/contentassets/00000000000000000000000000000002/no/opri/b.pdf">'
        "Opprinnelig utgave (1 MB)</a></li>"
    )

    assert _refusal(_corrected("</ul>", f"{link}</ul>")) is RefusalReason.EDITION_CONFLICTING


def test_a_link_labelled_against_its_target_is_conflicting() -> None:
    payload = _corrected(">Rettebrev 5. mai 2100", ">Opprinnelig utgave")

    assert _refusal(payload) is RefusalReason.EDITION_CONFLICTING


def test_a_heading_promising_a_rettebrev_without_one_is_conflicting() -> None:
    link = (
        '<li><a href="/contentassets/00000000000000000000000000000002/no/rett/rettebrev.pdf" '
        'title=" Til bruk i Acrobat reader" rel="nofollow">Rettebrev 5. mai 2100 (10,0 kB)</a></li>'
    )

    assert _refusal(_corrected(link, "")) is RefusalReason.EDITION_CONFLICTING


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("(Korrigert utgave per 13.5.2100)", "(Korrigert utgave 13.5.2100)"),
        ("(Korrigert utgave per 13.5.2100)", "(Korrigert utgave per 31.2.2100)"),
        ("(Korrigert utgave per 13.5.2100)", "(Korrigert utgave per 13.5.2100, se rettebrev)"),
        ('data-doc-meta="corrected-edition-date"', 'data-doc-meta="withdrawn-edition-date"'),
        ("Opprinnelig utgave og rettebrev</h3>", "Tidligere utgaver</h3>"),
        ("/no/opri/prp209921000900000dddopri.pdf", "/no/opri/prp.docx"),
        ("/contentassets/00000000000000000000000000000002/no/rett/", "https://example.org/rett/"),
        ('<ul class="link-list">', '<ul class="other-list">'),
    ],
    ids=[
        "notice-wording",
        "impossible-date",
        "notice-extra-text",
        "other-doc-meta",
        "heading-wording",
        "link-not-pdf",
        "link-off-site",
        "list-class",
    ],
)
def test_an_edited_or_unfamiliar_edition_dom_is_refused(old: str, new: str) -> None:
    assert _refusal(_corrected(old, new)) in {
        RefusalReason.EDITION_UNRECOGNIZED,
        RefusalReason.EDITION_CONFLICTING,
    }


def test_a_notice_outside_the_article_header_is_unrecognized() -> None:
    moved = _corrected(NOTICE, "").decode().replace("<footer", f"{NOTICE}<footer").encode()

    assert _refusal(moved) is RefusalReason.EDITION_UNRECOGNIZED


@pytest.mark.parametrize(
    "stray",
    [
        "<p>Korrigert utgave</p>",
        "<p>Se rettebrev</p>",
        "<p>opprinnelig utgave</p>",
        '<p><a href="/contentassets/00000000000000000000000000000002/no/rett/x.pdf">x</a></p>',
    ],
    ids=["corrected-words", "rettebrev-word", "original-words", "stray-rett-link"],
)
def test_edition_wording_on_a_plain_page_is_unrecognized_not_unknown(stray: str) -> None:
    payload = PLAIN.read_text(encoding="utf-8").replace("<footer", f"{stray}<footer").encode()

    assert _refusal(payload, L_URL) is RefusalReason.EDITION_UNRECOGNIZED


def test_edition_wording_inside_a_script_is_not_page_text() -> None:
    script = "<script>var label = 'Korrigert utgave';</script>"
    payload = PLAIN.read_text(encoding="utf-8").replace("<footer", f"{script}<footer").encode()

    assert read_landing(payload, L_URL)[1].kind == "unknown"


def test_a_browser_rendered_landing_page_is_refused() -> None:
    control = '<h2 class="subheader">Tittel<button data-show-doctitle="1"></button></h2>'
    payload = _corrected('<h2 class="subheader">', f'{control}<h2 class="subheader">')

    assert _refusal(payload) is RefusalReason.BROWSER_RENDERED_PAGE


def test_a_refusal_names_no_page_text() -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_landing(_corrected("og rettebrev</h3>", "hemmelig</h3>"), LS_URL)

    assert "hemmelig" not in str(caught.value)
    assert "Strukturell" not in str(caught.value)
