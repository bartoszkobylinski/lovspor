"""GPT-6 review of PR #791: a landing page must be whole, and its edition list read whole.

Both checks are grounded in the one real landing page read on 2026-11-05: one
``footer.page-footer``, a direct child of ``body`` after ``main``; and an
edition block that is a ``div`` holding exactly ``h3.edition`` and
``ul.link-list``, whose every item is ``li > a`` with nothing else in it.
"""

from pathlib import Path

import pytest

from lovspor.forarbeider.edition import read_landing
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason

FIXTURES = Path(__file__).parents[1] / "fixtures" / "forarbeider"
CORRECTED = FIXTURES / "landing-ls-corrected.html"
PLAIN = FIXTURES / "landing-l-plain.html"
LS_URL = "https://www.regjeringen.no/no/dokumenter/prop.-900-ls-20992100/id9000002/"
L_URL = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/"
FOOTER = '<footer class="page-footer"></footer>'
ASSETS = "/contentassets/00000000000000000000000000000002/no"
RETT = f"{ASSETS}/rett/rettebrev.pdf"
RETT_ITEM = (
    f'<li><a href="{RETT}" title=" Til bruk i Acrobat reader" rel="nofollow">'
    "Rettebrev 5. mai 2100 (10,0 kB)</a></li>"
)
PAGES = {"corrected": (CORRECTED, LS_URL), "plain": (PLAIN, L_URL)}


def _page(page: str, old: str, new: str) -> tuple[bytes, str]:
    fixture, url = PAGES[page]
    text = fixture.read_text(encoding="utf-8")
    assert old in text, old
    return text.replace(old, new).encode(), url


def _reason(payload: bytes, url: str) -> RefusalReason:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_landing(payload, url)
    return caught.value.reason


@pytest.mark.parametrize("page", ["corrected", "plain"])
@pytest.mark.parametrize(
    "footer",
    [
        "",
        FOOTER + FOOTER,
        '<footer class="other-footer"></footer>',
        "<footer></footer>",
        f"<div>{FOOTER}</div>",
        '<div class="page-footer"></div>',
    ],
    ids=["missing", "duplicated", "wrong-class", "no-class", "wrapped", "not-a-footer"],
)
def test_a_landing_page_without_its_one_footer_is_incomplete(page: str, footer: str) -> None:
    payload, url = _page(page, FOOTER, footer)

    assert _reason(payload, url) is RefusalReason.INCOMPLETE_PAGE


@pytest.mark.parametrize("page", ["corrected", "plain"])
@pytest.mark.parametrize(
    ("anchor", "placed"),
    [
        ("</main>", f"{FOOTER}</main>"),
        ('<main id="mainContent">', f'{FOOTER}<main id="mainContent">'),
    ],
    ids=["inside-main", "before-main"],
)
def test_a_footer_that_is_not_after_main_is_incomplete(page: str, anchor: str, placed: str) -> None:
    fixture, url = PAGES[page]
    text = fixture.read_text(encoding="utf-8").replace(FOOTER, "").replace(anchor, placed)

    assert _reason(text.encode(), url) is RefusalReason.INCOMPLETE_PAGE


@pytest.mark.parametrize("page", ["corrected", "plain"])
def test_the_observed_footer_still_passes(page: str) -> None:
    fixture, url = PAGES[page]

    read_landing(fixture.read_bytes(), url)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("</ul>", "<li><span>Additional attachment</span></li></ul>"),
        ("</ul>", "<li></li></ul>"),
        ("</ul>", "<li>Vedlegg</li></ul>"),
        ("</ul>", '<li><a href="/no/dokumenter/vedlegg/">Vedlegg</a></li></ul>'),
        ("</ul>", "<p>x</p></ul>"),
        ("</ul>", "Vedlegg</ul>"),
        (
            RETT_ITEM,
            RETT_ITEM.replace("<li><a", "<li><span><a").replace("</a></li>", "</a></span></li>"),
        ),
        (RETT_ITEM, RETT_ITEM.replace("</a></li>", '</a><a href="/x">x</a></li>')),
        (RETT_ITEM, RETT_ITEM.replace("</a></li>", "</a> og mer</li>")),
        (RETT_ITEM, RETT_ITEM.replace("<li><a", "<li> Se <a")),
        (RETT_ITEM, RETT_ITEM.replace("5. mai 2100", "<b>5. mai 2100</b>")),
        (RETT_ITEM, RETT_ITEM.replace("<li>", '<li class="extra">')),
        (RETT_ITEM, RETT_ITEM + "<!-- note -->"),
        ("</ul>", '</ul><ul class="link-list"></ul>'),
        ("</ul>", "</ul><p>Mer</p>"),
        ('<h3 class="edition">', '<p>Merknad</p><h3 class="edition">'),
        ("</ul>", "</ul>Merknad"),
        ('<h3 class="edition">', 'Merknad<h3 class="edition">'),
        ("</h3>", "</h3>Merknad"),
        ('<ul class="link-list">', '<ul class="link-list">Merknad'),
    ],
    ids=[
        "extra-li-span",
        "empty-li",
        "li-text-only",
        "extra-unrecognised-link",
        "non-li-in-list",
        "text-in-list",
        "nested-wrapper",
        "two-anchors",
        "text-after-anchor",
        "text-before-anchor",
        "element-inside-anchor",
        "li-attribute",
        "comment-in-list",
        "second-list",
        "extra-block-element",
        "element-before-heading",
        "text-after-list",
        "text-before-heading",
        "text-after-heading",
        "text-at-list-start",
    ],
)
def test_every_edition_list_entry_is_read_or_the_page_is_refused(old: str, new: str) -> None:
    payload, url = _page("corrected", old, new)

    assert _reason(payload, url) is RefusalReason.EDITION_UNRECOGNIZED


def test_several_rettebrev_links_are_kept_in_source_order() -> None:
    second = RETT_ITEM.replace("rettebrev.pdf", "rettebrev2.pdf")
    payload, url = _page("corrected", RETT_ITEM, RETT_ITEM + "\n      " + second)

    _, facts = read_landing(payload, url)

    assert facts.rettebrev_urls == (
        f"https://www.regjeringen.no{RETT}",
        f"https://www.regjeringen.no{ASSETS}/rett/rettebrev2.pdf",
    )
