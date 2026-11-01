"""Behavioral regressions for the chapter mutation survivors."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from lxml import html
from pydantic import ValidationError

from lovspor.forarbeider.chapter_body import _body, _wrapper_classes, read_body
from lovspor.forarbeider.chapter_models import (
    ChapterResult,
    TextBlock,
    normalized,
    read_chapter,
    result_text,
)
from lovspor.forarbeider.commands import chapter_impl
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.staging import write_observation
from tests.unit.test_forarbeider_chapter import FIXTURE, HEAD, URL, page, result, section
from tests.unit.test_forarbeider_chapter_models import valid


@pytest.mark.parametrize("text", ["\u00a0X\u00a0", " X ", "\u2003text\u2003"])
def test_normalization_preserves_non_source_characters(text: str) -> None:
    assert normalized(text) == text.strip(" ")


def test_deepest_html_heading_is_supported() -> None:
    assert TextBlock(kind="heading", level=6, text="Title").level == 6


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"blocks": None, "text_sha256": None}, "a body exists exactly when nothing withholds it"),
        ({"text_sha256": "0" * 64}, "text_sha256 does not cover the body"),
    ],
)
def test_invalid_body_has_precise_diagnostic(changes: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError) as caught:
        ChapterResult.model_validate({**valid(), **changes})
    assert caught.value.errors()[0]["msg"] == f"Value error, {message}"


@pytest.mark.parametrize("exists", [False, True])
def test_read_refusal_names_the_input(tmp_path: Path, exists: bool) -> None:
    path = tmp_path / "chapter.json"
    if exists:
        path.write_bytes(b'{"blocks":')
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_chapter(path)
    reason = "schema_invalid" if exists else "input_unreadable"
    assert str(caught.value) == f"{reason}: {path}"


@pytest.mark.parametrize("collision", [False, True])
def test_write_refusal_names_the_output(tmp_path: Path, collision: bool) -> None:
    if collision:
        path = tmp_path / "chapter.json"
        path.write_text("old", encoding="utf-8")
    else:
        blocker = tmp_path / "blocker"
        blocker.write_bytes(b"x")
        path = blocker / "chapter.json"
    with pytest.raises(ForarbeiderRefusedError) as caught:
        write_observation(path, "new")
    reason = "observation_exists" if collision else "output_write_failed"
    assert str(caught.value) == f"{reason}: {path}"
    if collision:
        assert path.read_text(encoding="utf-8") == "old"


def test_processing_instruction_does_not_become_body_text() -> None:
    held = result(section("<p>before<?probe hidden>after</p>"))
    assert held.blocks is not None
    assert held.blocks[-1].text == "beforeafter"


@pytest.mark.parametrize("tag", ["div", "span"])
def test_unmarked_or_non_div_section_is_ambiguous(tag: str) -> None:
    attribute = "" if tag == "div" else " data-longdoc-section"
    root = html.fromstring(
        f"<main><div data-longdoc-content>{HEAD}"
        f'<{tag} id="extra"{attribute}><p>text</p></{tag}></div></main>'
    )
    body = read_body(root, "2 Bakgrunn")
    assert body.blocks is None
    assert "ambiguous_boundary" in body.reasons


@pytest.mark.parametrize("tag", ["h1", "h6"])
def test_annex_heading_at_extreme_levels_is_named(tag: str) -> None:
    root = html.fromstring(
        '<main><div data-longdoc-content><div id="a" data-longdoc-section>'
        f"<{tag}>Vedlegg 1</{tag}><p>text</p></div></div></main>"
    )
    assert "annex" in read_body(root, "Vedlegg 1").reasons


def test_wrapper_with_all_allowlisted_classes_is_read() -> None:
    classes = (
        "K-DEL-KONGESIDE K-DEL-LEDD K-DEL-LOVDEL K-DEL-LOVER K-DEL-LOVKAP "
        "K-DEL-PARAGRAF K-DEL-VEDTAKDEL K-DEL-VEDTAKDELER"
    )
    held = result(section(f'<div class="{classes}"><p>text</p></div>'))
    assert held.blocks is not None
    assert held.blocks[-1].text == "text"


@pytest.mark.parametrize("inner", ["<b>first</b> last", "first<br>last", "first<!-- note -->last"])
def test_inline_only_list_items_keep_text(inner: str) -> None:
    held = result(section(f"<ul><li>{inner}</li></ul>"))
    assert held.blocks is not None
    assert held.blocks[-1] == TextBlock(
        kind="list_item", level=1, text="firstlast" if "<!--" in inner else "first last"
    )


@pytest.mark.parametrize("withheld", [False, True])
def test_summary_reports_body_and_reason_separator(tmp_path: Path, withheld: bool) -> None:
    source = tmp_path / "page.html"
    source.write_bytes(
        page(section('<p>text</p><img src="x">')) if withheld else FIXTURE.read_bytes()
    )
    target = tmp_path / "chapter.json"
    printed = chapter_impl(source, URL, "2100-04-01T12:00:00Z", target)
    summary = (
        "body withheld, reasons: review_required, image"
        if withheld
        else "9 blocks, reasons: review_required"
    )
    assert printed == f"held regjeringen-9000001-ch2: {summary} at {target}"
    assert (
        read_chapter(target).blocks is None if withheld else read_chapter(target).blocks is not None
    )


def test_accounting_guard_rejects_missing_text() -> None:

    body = _body(
        (TextBlock(kind="paragraph", text="first"),),
        set(),
        html.fromstring("<div><p>first</p><p>missing</p></div>"),
    )
    assert body.blocks is None
    assert body.reasons == {"incomplete_extraction"}


def test_classless_element_has_no_wrapper_classes() -> None:

    assert _wrapper_classes(html.fromstring("<div><p>text</p></div>")) == set()


def test_publication_leaves_only_the_complete_observation(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "chapter.json"
    assert write_observation(target, "æ\n") is True
    assert target.read_bytes() == "æ\n".encode()
    assert write_observation(target, "æ\n") is False
    assert list(target.parent.iterdir()) == [target]


def test_cleanup_tolerates_staging_file_already_removed(tmp_path: Path) -> None:
    """A separate interpreter confines the filesystem audit observer to this test."""

    script = """
import os
import sys
from pathlib import Path
from lovspor.forarbeider.staging import write_observation

removed = False
def remove_before_cleanup(event, args):
    global removed
    if event == "os.remove" and not removed:
        path = Path(args[0])
        if path.parent == Path(sys.argv[1]):
            removed = True
            path.unlink()

sys.addaudithook(remove_before_cleanup)
target = Path(sys.argv[1]) / "chapter.json"
assert write_observation(target, "complete") is True
assert removed
assert target.read_text() == "complete"
assert list(target.parent.iterdir()) == [target]
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr


def test_chapter_requires_a_positive_selected_chapter() -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        result(url=URL.removesuffix("?ch=2"))
    assert caught.value.reason.value == "chapter_page_required"


def test_serialized_chapter_preserves_unicode() -> None:

    text = result_text(result())
    assert "æøå" in text
    assert "\\u00e6" not in text


def test_publication_does_not_require_writable_global_temp_directory(tmp_path: Path) -> None:
    default = tmp_path / "global-temp"
    default.mkdir()
    destination = tmp_path / "observations"
    destination.mkdir()
    script = """
import sys
import tempfile
from pathlib import Path
from lovspor.forarbeider.staging import write_observation

cached_default = Path(tempfile.gettempdir())
assert cached_default == Path(sys.argv[1])
cached_default.rmdir()
try:
    target = Path(sys.argv[2]) / "chapter.json"
    assert write_observation(target, "complete") is True
    assert target.read_text() == "complete"
    assert list(target.parent.iterdir()) == [target]
finally:
    cached_default.mkdir(mode=0o700)
"""

    completed = subprocess.run(
        [sys.executable, "-c", script, str(default), str(destination)],
        env={**os.environ, "TMPDIR": str(default), "TEMP": str(default), "TMP": str(default)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_unpublished_observation_is_hidden_until_complete(tmp_path: Path) -> None:
    """Directory consumers must not see an unfinished observation as a normal file."""
    script = """
import sys
from pathlib import Path
from lovspor.forarbeider.staging import write_observation

folder = Path(sys.argv[1])
observed = []
def inspect_before_publication(event, args):
    if event == "os.link":
        entries = list(folder.iterdir())
        assert len(entries) == 1
        staged = entries[0]
        assert staged.name.startswith(".")
        assert staged.read_bytes() == b"complete"
        observed.append(True)

sys.addaudithook(inspect_before_publication)
target = folder / "chapter.json"
assert write_observation(target, "complete") is True
assert observed == [True]
assert list(folder.iterdir()) == [target]
assert target.read_bytes() == b"complete"
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("query", ["", "?ch=0", "?ch=-1", "?ch=1", "?ch=2"])
def test_chapter_selection_never_falls_back(query: str) -> None:
    url = URL.split("?")[0] + query
    if query == "?ch=2":
        assert result(url=url).chapter_number == 2
    else:
        with pytest.raises(ForarbeiderRefusedError):
            result(url=url)


def test_list_depth_starts_at_one_and_increases_only_for_nested_lists() -> None:
    held = result(
        section(
            '<div class="K-DEL-LEDD"><ul><li><p>outer</p>'
            "<ul><li>inner</li></ul></li></ul></div><ul><li>next</li></ul>"
        )
    )
    assert held.blocks is not None
    assert [(block.level, block.text) for block in held.blocks if block.kind == "list_item"] == [
        (1, "outer"),
        (2, "inner"),
        (1, "next"),
    ]
