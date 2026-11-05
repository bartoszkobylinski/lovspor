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


def test_existing_regular_staging_file_is_preserved(tmp_path: Path) -> None:
    """_claim_staging promises that an existing file belongs to someone else."""
    output = tmp_path / "release.jsonl"
    staging = tmp_path / ".release.jsonl.partial"
    existing = b'{"another_writer":true}\n'
    staging.write_bytes(existing)

    with pytest.raises(CrosswalkError, match="refusing to reuse the staging file"):
        commands.write_once(output, b'{"fresh":true}\n')

    assert staging.read_bytes() == existing
    assert not output.exists()
    assert list(tmp_path.iterdir()) == [staging]


def test_failed_link_cleans_staging_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed publication leaves no output or stale claim on the staging name."""
    output = tmp_path / "release.jsonl"
    staging = tmp_path / ".release.jsonl.partial"
    data = b'{"complete":true}\n'

    def failed_link(source: Path, destination: Path) -> None:
        assert source == staging
        assert source.read_bytes() == data
        assert destination == output
        raise OSError("hard links unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(commands.os, "link", failed_link)
        with pytest.raises(CrosswalkError, match="could not write.*hard links unavailable"):
            commands.write_once(output, data)

    assert list(tmp_path.iterdir()) == []
    commands.write_once(output, data)
    assert output.read_bytes() == data
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
