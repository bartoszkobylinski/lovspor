"""Tests for lovspor.atomic_io."""

from pathlib import Path

import pytest

from lovspor.atomic_io import atomic_write_bytes, atomic_write_text


def test_atomic_write_text_writes_content(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_atomic_write_text_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "f.txt"
    atomic_write_text(target, "deep")
    assert target.read_text(encoding="utf-8") == "deep"


def test_atomic_write_text_leaves_no_tmp_file(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "x")
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_text_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("OLD", encoding="utf-8")
    atomic_write_text(target, "NEW")
    assert target.read_text(encoding="utf-8") == "NEW"


def test_atomic_write_text_preserves_original_and_cleans_tmp_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed replace must leave the previous file intact (no torn write a
    concurrent reader could see) and must not leave a stray .tmp behind."""
    target = tmp_path / "f.txt"
    target.write_text("ORIGINAL", encoding="utf-8")

    def boom(self: Path, _target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_text(target, "NEW")

    assert target.read_text(encoding="utf-8") == "ORIGINAL"
    assert not (tmp_path / "f.txt.tmp").exists()


def test_atomic_write_bytes_writes_payload_verbatim(tmp_path: Path) -> None:
    """Captured bodies are arbitrary bytes: no decode, no newline translation."""
    target = tmp_path / "blob"
    payload = b"\x00\xff\r\n not text \xc3\xb8"

    atomic_write_bytes(target, payload)

    assert target.read_bytes() == payload


def test_atomic_write_bytes_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "blobs" / "ab" / "abc"

    atomic_write_bytes(target, b"x")

    assert target.read_bytes() == b"x"


def test_atomic_write_bytes_leaves_no_tmp_file(tmp_path: Path) -> None:
    target = tmp_path / "blob"

    atomic_write_bytes(target, b"x")

    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_bytes_preserves_original_and_cleans_tmp_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "blob"
    target.write_bytes(b"ORIGINAL")

    def boom(self: Path, _target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_bytes(target, b"NEW")

    assert target.read_bytes() == b"ORIGINAL"
    assert not (tmp_path / "blob.tmp").exists()
