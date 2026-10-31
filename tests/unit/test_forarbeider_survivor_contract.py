"""Exercise saved-page boundaries without replacing the implementation."""

from pathlib import Path

import pytest

from lovspor.forarbeider.commands import MAX_HTML_BYTES, _saved_page
from lovspor.forarbeider.completeness import require_whole_page
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason


def test_footer_before_navigation_does_not_prove_page_complete() -> None:
    page = (
        "<html><body><footer>Earlier section</footer>"
        "<ul data-longdoc-table-of-content><li>Chapter</li></ul></body></html>"
    )
    with pytest.raises(ForarbeiderRefusedError) as caught:
        require_whole_page(page)
    assert caught.value.reason is RefusalReason.INCOMPLETE_PAGE
    assert caught.value.detail == "page footer"


def test_closed_earlier_list_does_not_hide_torn_navigation() -> None:
    page = (
        "<html><body><ul><li>Earlier menu</li></ul>"
        "<ul data-longdoc-table-of-content><li>Chapter</ul>"
        "<footer>End</footer></body></html>"
    )
    with pytest.raises(ForarbeiderRefusedError) as caught:
        require_whole_page(page)
    assert caught.value.reason is RefusalReason.INCOMPLETE_PAGE
    assert caught.value.detail == "chapter list"


@pytest.mark.parametrize("tags", [("uL", "Ul", "Li", "lI"), ("UL", "ul", "li", "LI")])
def test_mixed_case_navigation_tags_close(tags: tuple[str, str, str, str]) -> None:
    opening, closing, item_opening, item_closing = tags
    require_whole_page(
        f"<html><body><{opening} data-longdoc-table-of-content>"
        f"<{item_opening}>Chapter</{item_closing}></{closing}>"
        "<footer>End</footer></body></html>"
    )


@pytest.mark.parametrize("extra", [0, 1, 2, 4096])
def test_saved_page_size_boundary(tmp_path: Path, extra: int) -> None:
    source = tmp_path / "page.html"
    # A sparse real file exercises the reader without allocating a large fixture.
    with source.open("wb") as stream:
        stream.truncate(MAX_HTML_BYTES + extra)
    if extra:
        with pytest.raises(ForarbeiderRefusedError) as caught:
            _saved_page(source)
        assert caught.value.reason is RefusalReason.INPUT_TOO_LARGE
        assert caught.value.detail == str(source)
    else:
        assert _saved_page(source) == b"\0" * MAX_HTML_BYTES
    assert source.stat().st_size == MAX_HTML_BYTES + extra
