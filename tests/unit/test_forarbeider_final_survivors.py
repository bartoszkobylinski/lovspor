"""Real-input attempts to distinguish the final chapter and staging mutants."""

from pathlib import Path

import pytest
from lxml import html

from lovspor.forarbeider.chapter_body import read_body
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.staging import write_observation


@pytest.mark.parametrize("payload", ["", "æ\n"])
@pytest.mark.parametrize("difference", ["same", "shorter", "one_extra", "two_extra", "large"])
def test_existing_private_observation_byte_boundaries(
    tmp_path: Path, payload: str, difference: str
) -> None:
    expected = payload.encode()
    existing = {
        "same": expected,
        "shorter": expected[:-1],
        "one_extra": expected + b"x",
        "two_extra": expected + b"xy",
        "large": expected + b"x" * 131072,
    }[difference]
    path = tmp_path / "observation.json"
    path.write_bytes(existing)
    path.chmod(0o600)
    if existing == expected:
        assert write_observation(path, payload) is False
    else:
        with pytest.raises(ForarbeiderRefusedError) as caught:
            write_observation(path, payload)
        assert caught.value.reason is RefusalReason.OBSERVATION_EXISTS
    assert path.read_bytes() == existing
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("attrs", ["", 'aria-hidden="" style=""', 'aria-hidden="false"'])
def test_non_elements_and_false_visibility_do_not_hide_body(attrs: str) -> None:
    root = html.fromstring(
        '<main><div data-longdoc-content><div id="a" data-longdoc-section>'
        '<!-- hidden aria-hidden="true" style="display:none" -->'
        '<?probe hidden="hidden"?><h1>Title</h1>'
        f"<p {attrs}>Visible</p></div></div></main>"
    )
    body = read_body(root, "Title")
    assert body.reasons == frozenset()
    assert body.blocks is not None
    assert [block.text for block in body.blocks] == ["Title", "Visible"]


@pytest.mark.parametrize("group", ["<ul><li>Item</li></ul>", "<blockquote>Quote</blockquote>"])
def test_first_group_is_not_mistaken_for_an_adjacent_group(group: str) -> None:
    root = html.fromstring(
        '<main><div data-longdoc-content><div id="a" data-longdoc-section>'
        f"{group}<h1>Title</h1><p>Visible</p></div></div></main>"
    )
    body = read_body(root, "Title")
    # Starting before the chapter heading is ambiguous, but it is not a pair
    # of adjacent groups. This exercises the initial sentinel at zero blocks.
    assert "ambiguous_boundary" in body.reasons
    assert "unsupported_structure" not in body.reasons
    assert body.blocks is None
