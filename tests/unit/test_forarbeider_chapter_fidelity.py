"""What v1 cannot state faithfully is held, and an unsafe output is never vouched for (#738)."""

import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.forarbeider.chapter import build_chapter_result
from lovspor.forarbeider.chapter_models import ChapterResult, HoldReason
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.staging import write_observation

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


@posix_owner_only
def test_same_bytes_at_0600_are_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "chapter.json"
    assert write_observation(target, "x") is True
    assert write_observation(target, "x") is False
    assert list(tmp_path.iterdir()) == [target]


@posix_owner_only
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o620, 0o602, 0o601, 0o610])
def test_same_bytes_others_can_reach_are_refused_and_left_alone(tmp_path: Path, mode: int) -> None:
    target = tmp_path / "chapter.json"
    target.write_text("x", encoding="utf-8")
    target.chmod(mode)
    with pytest.raises(ForarbeiderRefusedError) as refused:
        write_observation(target, "x")
    assert refused.value.reason is RefusalReason.OBSERVATION_UNSAFE
    assert str(refused.value) == f"observation_unsafe: {target}"
    assert stat.S_IMODE(target.stat().st_mode) == mode
    assert target.read_bytes() == b"x"
    assert list(tmp_path.iterdir()) == [target]


@posix_owner_only
def test_owner_read_only_same_bytes_are_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "chapter.json"
    target.write_text("x", encoding="utf-8")
    target.chmod(0o400)
    assert write_observation(target, "x") is False


@posix_owner_only
@pytest.mark.parametrize("mode", [0o600, 0o644])
def test_different_bytes_are_a_collision_at_any_mode(tmp_path: Path, mode: int) -> None:
    target = tmp_path / "chapter.json"
    target.write_text("old", encoding="utf-8")
    target.chmod(mode)
    with pytest.raises(ForarbeiderRefusedError) as refused:
        write_observation(target, "new")
    assert refused.value.reason is RefusalReason.OBSERVATION_EXISTS
    assert target.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [target]


@posix_owner_only
def test_a_longer_existing_file_is_a_collision(tmp_path: Path) -> None:
    target = tmp_path / "chapter.json"
    target.write_text("xy", encoding="utf-8")
    target.chmod(0o600)
    with pytest.raises(ForarbeiderRefusedError) as refused:
        write_observation(target, "x")
    assert refused.value.reason is RefusalReason.OBSERVATION_EXISTS


@posix_owner_only
def test_a_symlink_at_the_output_is_not_followed(tmp_path: Path) -> None:
    private = tmp_path / "elsewhere.txt"
    private.write_text("x", encoding="utf-8")
    private.chmod(0o600)
    target = tmp_path / "chapter.json"
    target.symlink_to(private)
    with pytest.raises(ForarbeiderRefusedError) as refused:
        write_observation(target, "x")
    assert refused.value.reason is RefusalReason.OBSERVATION_UNSAFE
    assert target.is_symlink()
    assert sorted(tmp_path.iterdir()) == sorted([private, target])


@posix_owner_only
def test_a_dangling_symlink_at_the_output_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "chapter.json"
    target.symlink_to(tmp_path / "missing")
    with pytest.raises(ForarbeiderRefusedError) as refused:
        write_observation(target, "x")
    assert refused.value.reason is RefusalReason.OBSERVATION_UNSAFE
    assert not (tmp_path / "missing").exists()


@posix_owner_only
def test_a_second_name_for_the_file_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "chapter.json"
    target.write_text("x", encoding="utf-8")
    target.chmod(0o600)
    os.link(target, tmp_path / "other.json")
    with pytest.raises(ForarbeiderRefusedError) as refused:
        write_observation(target, "x")
    assert refused.value.reason is RefusalReason.OBSERVATION_UNSAFE


@posix_owner_only
def test_a_fifo_at_the_output_neither_blocks_nor_passes(tmp_path: Path) -> None:
    target = tmp_path / "chapter.json"
    os.mkfifo(target, 0o600)
    with pytest.raises(ForarbeiderRefusedError) as refused:
        write_observation(target, "x")
    assert refused.value.reason is RefusalReason.OBSERVATION_UNSAFE


@posix_owner_only
def test_a_file_of_another_owner_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "chapter.json"
    target.write_text("x", encoding="utf-8")
    target.chmod(0o600)
    owner = target.stat().st_uid
    monkeypatch.setattr(os, "geteuid", lambda: owner + 1)
    with pytest.raises(ForarbeiderRefusedError) as refused:
        write_observation(target, "x")
    assert refused.value.reason is RefusalReason.OBSERVATION_UNSAFE
