"""The bounded reader stops one byte past its limit and refuses only beyond it."""

import io
from pathlib import Path

import pytest

from lovspor.crosswalk.commands import read_bounded, read_capped
from lovspor.crosswalk.errors import CrosswalkError


def test_capped_read_stops_one_byte_past_the_limit() -> None:
    stream = io.BytesIO(b"x" * 10)
    assert read_capped(stream, 4) == b"xxxxx"
    assert stream.tell() == 5


def test_capped_read_returns_a_short_input_whole() -> None:
    stream = io.BytesIO(b"abc")
    assert read_capped(stream, 4) == b"abc"


def test_bounded_read_accepts_exactly_the_limit(tmp_path: Path) -> None:
    path = tmp_path / "in"
    path.write_bytes(b"x" * 4)
    assert read_bounded(path, 4, "thing") == b"xxxx"


def test_bounded_read_refuses_one_byte_more(tmp_path: Path) -> None:
    path = tmp_path / "in"
    path.write_bytes(b"x" * 5)
    with pytest.raises(CrosswalkError) as caught:
        read_bounded(path, 4, "thing")
    assert str(caught.value) == f"thing too large: {path}"


def test_bounded_read_names_an_unreadable_input(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(CrosswalkError) as caught:
        read_bounded(missing, 4, "thing")
    assert str(caught.value) == f"thing unreadable: {missing}"
