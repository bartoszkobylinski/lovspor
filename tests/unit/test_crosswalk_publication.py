"""Failure boundaries of the crosswalk command's write-once output contract."""

from pathlib import Path

import pytest

from lovspor.crosswalk import commands
from lovspor.crosswalk.errors import CrosswalkError


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
