"""The chapter a saved page is, proved by its own paging links, not by the URL given (#738)."""

import re
from pathlib import Path

import pytest

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.page import build_record, read_proposition
from tests.unit.test_forarbeider_page import L_URL, RETRIEVED

PAGE = (Path(__file__).parents[1] / "fixtures" / "forarbeider" / "prop-l.html").read_text(
    encoding="utf-8"
)
PATH = "/no/dokumenter/prop.-901-l-20992100/id9000001/"


def _link(kind: str, href: str | None) -> str:
    return "" if href is None else f'<a href="{href}" data-longdoc-{kind}>{kind}</a>'


def _paged(previous: str | None, following: str | None) -> bytes:
    links = _link("prev", previous) + _link("next", following)
    page, count = re.subn(r'<div class="longdoc-paging.*?</div>\n</div>', links, PAGE, flags=re.S)
    assert count == 1
    return page.encode()


def _refusal(payload: bytes, chapter: int) -> ForarbeiderRefusedError:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_proposition(payload, f"{L_URL}?ch={chapter}")
    return caught.value


def test_identical_bytes_prove_one_chapter_only() -> None:
    payload = PAGE.encode()
    record = build_record(payload, f"{L_URL}?ch=1", RETRIEVED)
    assert record.source_page_url == f"{L_URL}?ch=1"
    refusal = _refusal(payload, 2)
    assert refusal.reason is RefusalReason.PAGE_CHAPTER_UNPROVED
    assert refusal.detail == f"{L_URL}?ch=2"


@pytest.mark.parametrize(
    ("previous", "following", "chapter"),
    [
        (PATH, f"{PATH}?ch=2", 1),
        (f"{PATH}?ch=1", f"{PATH}?ch=3", 2),
        (f"{PATH}?ch=3", f"{PATH}?ch=5", 4),
        (f"{PATH}?ch=4", None, 5),
    ],
    ids=["first", "second", "second-to-last", "last"],
)
def test_the_page_proves_its_own_chapter(
    previous: str, following: str | None, chapter: int
) -> None:
    payload = _paged(previous, following)
    assert read_proposition(payload, f"{L_URL}?ch={chapter}").locator.chapter == chapter
    assert _refusal(payload, chapter % 5 + 1).reason is RefusalReason.PAGE_CHAPTER_UNPROVED


@pytest.mark.parametrize(
    ("previous", "following", "chapter"),
    [
        (None, None, 1),
        (None, f"{PATH}?ch=2", 1),
        (PATH, None, 1),
        (f"{PATH}?ch=1", None, 2),
        (f"{PATH}?ch=4", f"{PATH}?ch=6", 5),
        (f"{PATH}?ch=5", None, 6),
        (f"{PATH}?ch=1", f"{PATH}?ch=2", 2),
        (f"{PATH}?ch=2", f"{PATH}?ch=3", 2),
    ],
    ids=[
        "no-paging",
        "no-previous",
        "no-next-before-the-end",
        "no-next-mid-document",
        "next-past-the-inventory",
        "past-the-inventory",
        "next-is-itself",
        "previous-is-itself",
    ],
)
def test_paging_that_does_not_prove_the_chapter_is_refused(
    previous: str | None, following: str | None, chapter: int
) -> None:
    refusal = _refusal(_paged(previous, following), chapter)
    assert refusal.reason is RefusalReason.PAGE_CHAPTER_UNPROVED


def test_copies_of_a_paging_link_may_repeat_but_not_disagree() -> None:
    twice = _paged(PATH, f"{PATH}?ch=2").replace(
        b"</main>", _link_bytes(f"{PATH}?ch=2") + b"</main>"
    )
    assert read_proposition(twice, f"{L_URL}?ch=1").locator.chapter == 1
    apart = _paged(PATH, f"{PATH}?ch=2").replace(
        b"</main>", _link_bytes(f"{PATH}?ch=3") + b"</main>"
    )
    assert _refusal(apart, 1).reason is RefusalReason.PAGE_CHAPTER_UNPROVED


def _link_bytes(href: str) -> bytes:
    return _link("next", href).encode()


def test_a_paging_link_to_another_document_is_refused() -> None:
    foreign = "/no/dokumenter/prop.-901-l-20992100/id9000009/?ch=2"
    assert _refusal(_paged(PATH, foreign), 1).reason is RefusalReason.CHAPTER_FOREIGN


def test_a_paging_link_with_no_href_proves_nothing() -> None:
    bare = PAGE.replace(f'href="{PATH}" data-longdoc-prev', "data-longdoc-prev").encode()
    assert _refusal(bare, 1).reason is RefusalReason.PAGE_CHAPTER_UNPROVED


def test_repeated_previous_links_must_agree() -> None:
    payload = _paged(f"{PATH}?ch=1", f"{PATH}?ch=3")
    repeated = payload.replace(b"</main>", _link("prev", f"{PATH}?ch=1").encode() + b"</main>")
    assert read_proposition(repeated, f"{L_URL}?ch=2").locator.chapter == 2
    conflicting = payload.replace(b"</main>", _link("prev", PATH).encode() + b"</main>")
    refusal = _refusal(conflicting, 2)
    assert refusal.reason is RefusalReason.PAGE_CHAPTER_UNPROVED
    assert refusal.detail == f"{L_URL}?ch=2"


def test_only_chapter_links_back_to_landing_and_has_no_next() -> None:
    """The documented first/last paging rules also apply when both are chapter 1."""
    payload = _paged(PATH, None)
    start = payload.index(b'<ul class="longdoc-table-of-content"')
    end = payload.index(b"</nav>", start)
    navigation = (
        "<ul data-longdoc-table-of-content><li>"
        f'<a href="{PATH}?ch=1">Eneste kapittel</a></li></ul>\n'
    ).encode()
    payload = payload[:start] + navigation + payload[end:]
    record = build_record(payload, f"{L_URL}?ch=1", RETRIEVED)
    assert record.source_page_url == f"{L_URL}?ch=1"
    assert [(c.chapter_number, c.title, c.url) for c in record.chapters] == [
        (1, "Eneste kapittel", f"{L_URL}?ch=1")
    ]
    assert _refusal(payload, 2).reason is RefusalReason.PAGE_CHAPTER_UNPROVED
