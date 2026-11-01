"""Observable contracts for the remaining staging and chapter survivors."""

import os
from pathlib import Path

import pytest
from lxml import html

from lovspor.forarbeider.chapter_body import read_body
from lovspor.forarbeider.chapter_markup import tree_states_source
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.staging import write_observation


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_unsafe_existing_observation_names_path(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "original.json"
    target.write_bytes(b"record")
    target.chmod(0o600)
    path = tmp_path / "observation.json"
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, path)
    else:
        os.mkfifo(path, 0o600)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        write_observation(path, "record")
    assert caught.value.reason is RefusalReason.OBSERVATION_UNSAFE
    assert caught.value.detail == str(path)
    assert str(caught.value) == f"observation_unsafe: {path}"
    assert target.read_bytes() == b"record"
    assert set(tmp_path.iterdir()) == {target, path}


@pytest.mark.parametrize("extra", ["", "x", "xy", "x" * 65536])
def test_existing_observation_requires_exact_bytes(tmp_path: Path, extra: str) -> None:
    path = tmp_path / "observation.json"
    original = ("record" + extra).encode()
    path.write_bytes(original)
    path.chmod(0o600)
    if extra:
        with pytest.raises(ForarbeiderRefusedError) as caught:
            write_observation(path, "record")
        assert caught.value.reason is RefusalReason.OBSERVATION_EXISTS
        assert caught.value.detail == str(path)
    else:
        assert write_observation(path, "record") is False
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("attrs", ["", 'aria-hidden="" style=""', 'aria-hidden="false"'])
def test_comments_and_empty_visibility_attributes_preserve_body(attrs: str) -> None:
    root = html.fromstring(
        '<main><div data-longdoc-content><div id="a" data-longdoc-section>'
        f'<h1>Title</h1><!-- hidden aria-hidden="true" style="display:none" -->'
        f"<p {attrs}>Visible</p><ul><li>Item</li></ul></div></div></main>"
    )
    body = read_body(root, "Title")
    assert body.reasons == frozenset()
    assert body.blocks is not None
    assert [(block.kind, block.level, block.text) for block in body.blocks] == [
        ("heading", 1, "Title"),
        ("paragraph", 0, "Visible"),
        ("list_item", 1, "Item"),
    ]


def test_self_closing_marked_region_is_compared_without_crashing() -> None:
    text = "<main><div data-longdoc-content/><p>Outside</p></main>"
    root = html.fromstring(text)
    region = root.xpath("//div[@data-longdoc-content]")[0]
    # The source opened a non-void element without closing it; lxml repaired it.
    assert tree_states_source(region, text) is False
