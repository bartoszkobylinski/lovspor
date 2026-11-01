"""What v1 cannot state faithfully is held, and an unsafe output is never vouched for (#738)."""

import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.forarbeider.chapter import build_chapter_result
from lovspor.forarbeider.chapter_models import ChapterResult, HoldReason

FIXTURE = Path(__file__).parents[1] / "fixtures" / "forarbeider" / "chapter-l.html"
URL = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=2"
WHEN = datetime(2100, 4, 1, 12, tzinfo=UTC)
HEAD = '<div id="kap2" data-longdoc-section><h1 class="K-DEL-KAPITTEL">2 Bakgrunn</h1></div>'
_BODY = re.compile(r"<!--BODY-->.*<!--/BODY-->", re.DOTALL)

posix_owner_only = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")


def page(body: str) -> bytes:
    text = FIXTURE.read_text(encoding="utf-8")
    return _BODY.sub(lambda _: body, text).encode()


def section(inner: str) -> str:
    return f'{HEAD}<div id="kap2-1" data-longdoc-section>{inner}</div>'


def result(inner: str) -> ChapterResult:
    return build_chapter_result(page(section(inner)), URL, WHEN)


def items(held: ChapterResult) -> list[tuple[int, str]]:
    assert held.blocks is not None
    return [(block.level, block.text) for block in held.blocks if block.kind == "list_item"]


def withheld_for(held: ChapterResult, reason: HoldReason) -> bool:
    return held.blocks is None and held.text_sha256 is None and reason in held.hold_reasons


def test_two_paragraphs_of_one_item_are_not_two_items() -> None:
    one_item = result("<ul><li><p>A</p><p>B</p></li></ul>")
    two_items = result("<ul><li><p>A</p></li><li><p>B</p></li></ul>")
    assert withheld_for(one_item, HoldReason.UNSUPPORTED_STRUCTURE)
    assert items(two_items) == [(1, "A"), (1, "B")]
    assert one_item.text_sha256 != two_items.text_sha256


@pytest.mark.parametrize(
    "structure",
    [
        "<ul><li><p>A</p><ul><li><p>B</p></li></ul><p>C</p></li></ul>",
        "<ul><li><p>A</p></li><li><ul><li><p>B</p></li></ul></li></ul>",
        "<ul><li><p>A</p><ul><li><p>B</p></li></ul><ul><li><p>C</p></li></ul></li></ul>",
        "<ul><li><p>A</p><blockquote>B</blockquote></li></ul>",
        '<ul><li><div class="K-DEL-LEDD"><p>A</p></div></li></ul>',
        "<ul><li><p>A</p></li><li></li><li><p>B</p></li></ul>",
        "<ul><li><p>A</p></li><li><p> </p></li></ul>",
        "<ul><li><p></p><ul><li><p>B</p></li></ul></li></ul>",
        "<ul><li>A<ul><li>B</li></ul></li></ul>",
        "<ul><li><p>A</p><ul><li>B</li></ul>tail</li></ul>",
        "<ul><li><p>A</p>tail</li></ul>",
        "<ul><li>A</li></ul><ul><li>B</li></ul>",
        '<ul><li>A</li></ul><div class="K-DEL-LEDD"><ul><li>B</li></ul></div>',
        "<ul><li><p>A</p><ul><li>B</li></ul></li></ul><ul><li>C</li></ul>",
        "<blockquote>A</blockquote><blockquote>B</blockquote>",
        "<blockquote><p>A</p></blockquote><blockquote><p>B</p></blockquote>",
    ],
)
def test_a_structure_the_blocks_would_flatten_is_withheld(structure: str) -> None:
    assert withheld_for(result(structure), HoldReason.UNSUPPORTED_STRUCTURE)


@pytest.mark.parametrize(
    ("structure", "expected"),
    [
        ("<ul><li>A</li><li>B</li></ul>", [(1, "A"), (1, "B")]),
        ("<ul><li><i>A</i> to</li></ul>", [(1, "A to")]),
        ("<ul><li><p>A</p></li><!-- c --><li><p>B</p></li></ul>", [(1, "A"), (1, "B")]),
        (
            "<ul><li><p>A</p><ul><li><p>B</p></li><li><p>C</p></li></ul></li><li><p>D</p></li></ul>",
            [(1, "A"), (2, "B"), (2, "C"), (1, "D")],
        ),
        (
            "<ul><li><p>A</p><ul><li><p>B</p><ul><li>C</li></ul></li></ul></li></ul>",
            [(1, "A"), (2, "B"), (3, "C")],
        ),
        ("<ul><li>A</li></ul><p>skille</p><ul><li>B</li></ul>", [(1, "A"), (1, "B")]),
        ("<ul><li>A</li></ul><blockquote>sitat</blockquote>", [(1, "A")]),
    ],
)
def test_one_block_per_item_keeps_its_order_and_depth(
    structure: str, expected: list[tuple[int, str]]
) -> None:
    held = result(structure)
    assert items(held) == expected
    assert HoldReason.UNSUPPORTED_STRUCTURE not in held.hold_reasons


def test_quotes_apart_stay_readable() -> None:
    held = result("<blockquote>A</blockquote><p>skille</p><blockquote>B</blockquote>")
    assert held.blocks is not None
    assert [block.text for block in held.blocks if block.kind == "quote"] == ["A", "B"]
