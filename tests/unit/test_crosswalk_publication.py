"""Failure boundaries of the crosswalk command's write-once output contract."""

from pathlib import Path

import pytest

from lovspor.crosswalk import commands
from lovspor.crosswalk.errors import CrosswalkError


@pytest.mark.parametrize("target_exists", [True, False], ids=["live-link", "dangling-link"])
def test_write_once_preserves_a_destination_symlink(tmp_path: Path, target_exists: bool) -> None:
    """commands.write_once promises that an existing file is never replaced."""
    target = tmp_path / "target.jsonl"
    original = b'{"previous":true}\n'
    if target_exists:
        target.write_bytes(original)
    output = tmp_path / "release.jsonl"
    output.symlink_to(target.name)

    with pytest.raises(CrosswalkError):
        commands.write_once(output, b'{"fresh":true}\n')

    assert output.is_symlink()
    assert output.readlink() == Path(target.name)
    if target_exists:
        assert target.read_bytes() == original
    else:
        assert not target.exists()
    assert not (tmp_path / ".release.jsonl.partial").exists()
    assert {path.name for path in tmp_path.iterdir()} == (
        {"release.jsonl", "target.jsonl"} if target_exists else {"release.jsonl"}
    )


def test_empty_output_is_published_once(tmp_path: Path) -> None:
    """An empty canonical JSONL is valid, and its output still cannot be replaced."""
    output = tmp_path / "empty.jsonl"
    commands.write_once(output, b"")
    assert output.is_file()
    assert output.read_bytes() == b""
    assert list(tmp_path.iterdir()) == [output]

    with pytest.raises(CrosswalkError, match="refusing to overwrite"):
        commands.write_once(output, b'{"fresh":true}\n')
    assert output.read_bytes() == b""
    assert list(tmp_path.iterdir()) == [output]


def test_partial_staging_write_is_never_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "release.jsonl"
    staging = tmp_path / ".release.jsonl.partial"
    write_bytes = Path.write_bytes

    def interrupted_write(path: Path, data: bytes) -> int:
        assert path == staging
        write_bytes(path, data[:3])
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", interrupted_write)
    with pytest.raises(CrosswalkError, match="could not write.*disk full"):
        commands.write_once(output, b'{"complete":true}\n')

    assert not output.exists()
    assert not staging.exists()
    assert list(tmp_path.iterdir()) == []


def test_destination_created_during_publication_is_never_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "release.jsonl"
    staging = tmp_path / ".release.jsonl.partial"
    link = commands.os.link
    existing = b'{"previous":true}\n'
    fresh = b'{"fresh":true}\n'

    def competing_publication(source: Path, destination: Path) -> None:
        assert source == staging
        assert source.read_bytes() == fresh
        assert destination == output
        destination.write_bytes(existing)
        link(source, destination)

    monkeypatch.setattr(commands.os, "link", competing_publication)
    with pytest.raises(CrosswalkError, match="could not write"):
        commands.write_once(output, fresh)

    assert output.read_bytes() == existing
    assert not staging.exists()
    assert list(tmp_path.iterdir()) == [output]
