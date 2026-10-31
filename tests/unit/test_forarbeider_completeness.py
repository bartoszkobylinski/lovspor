"""A cut-off saved page is refused: lxml would repair it into a shorter list (#738)."""

from pathlib import Path

import pytest

from lovspor.forarbeider.completeness import require_whole_page
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.page import read_proposition

WHOLE = (Path(__file__).parents[1] / "fixtures" / "forarbeider" / "prop-l.html").read_text(
    encoding="utf-8"
)
URL = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=1"
ENDING = "</body>\n</html>\n"
AFTER_FIRST_ITEM = WHOLE[: WHOLE.index("</li>") + len("</li>")]
MID_NAVIGATION = WHOLE[: WHOLE.index("<li>Vedlegg</li>")]
MID_TAG = WHOLE[: WHOLE.index('title="Åpne Tilråding"')]


def _detail_of(text: str) -> str:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_proposition(text.encode(), URL)
    assert caught.value.reason is RefusalReason.INCOMPLETE_PAGE
    return caught.value.detail


def test_the_fixture_really_ends_as_the_cases_assume() -> None:
    assert WHOLE.endswith(ENDING)


@pytest.mark.parametrize(
    "cut",
    [AFTER_FIRST_ITEM, MID_NAVIGATION, MID_TAG, WHOLE.removesuffix(ENDING)],
    ids=["after-first-item", "mid-navigation", "mid-tag", "no-closing-tags"],
)
def test_truncation_is_refused(cut: str) -> None:
    assert _detail_of(cut) == "document end"


def test_only_the_html_closing_tag_missing_is_refused() -> None:
    assert _detail_of(WHOLE.removesuffix("</html>\n")) == "document end"


def test_only_the_body_closing_tag_missing_is_refused() -> None:
    assert _detail_of(WHOLE.replace("</body>\n", "")) == "document end"


@pytest.mark.parametrize(
    "cut",
    [AFTER_FIRST_ITEM, MID_NAVIGATION],
    ids=["after-first-item", "mid-navigation"],
)
def test_a_cut_navigation_with_closing_tags_added_is_refused(cut: str) -> None:
    assert _detail_of(cut + "</ul></nav></main>" + ENDING) == "page footer"


def test_an_unclosed_item_is_refused_whatever_repairs_it() -> None:
    unclosed = WHOLE.replace("<li>Vedlegg</li>", "<li>Vedlegg")
    assert _detail_of(unclosed) == "chapter list"


def test_an_end_tag_nothing_opened_is_refused() -> None:
    assert _detail_of(WHOLE.replace("<li>Vedlegg</li>", "</li></li>")) == "chapter list"


def test_every_navigation_copy_must_be_closed() -> None:
    marker = '<nav class="longdoc-nav">'
    assert _detail_of(WHOLE.replace(marker, "<ul data-longdoc-table-of-content>" + marker)) == (
        "chapter list"
    )


def test_a_whole_page_passes() -> None:
    assert read_proposition(WHOLE.encode(), URL).chapters[-1].chapter_number == 5


@pytest.mark.parametrize(
    "page",
    [
        WHOLE.rstrip(),
        WHOLE.replace(ENDING, "</BODY>\n\n  </HTML >\r\n"),
        WHOLE.replace("<ul class", "<UL class").replace("</ul>\n</nav>", "</UL>\n</nav>"),
    ],
    ids=["no-final-newline", "capitals-and-spacing", "capital-list-tags"],
)
def test_the_checks_read_html_not_one_spelling_of_it(page: str) -> None:
    require_whole_page(page)


def test_a_page_with_no_navigation_is_left_to_the_navigation_check() -> None:
    require_whole_page("<html><body></body></html>")


def test_a_refusal_quotes_nothing_from_the_page() -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        require_whole_page(AFTER_FIRST_ITEM)
    assert str(caught.value) == "incomplete_page: document end"
