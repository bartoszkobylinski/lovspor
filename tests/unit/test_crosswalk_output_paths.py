"""Producer outputs obey the same path rules as captures, and their staging is never reused."""

import os
from pathlib import Path

import pytest

from lovspor.crosswalk import commands
from lovspor.crosswalk.commands import write_once
from lovspor.crosswalk.errors import CrosswalkError


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_output_inside_a_git_worktree_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    repo = tmp_path / "wt"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /elsewhere\n")
    with pytest.raises(CrosswalkError, match="crosswalk output inside the Git checkout"):
        write_once(repo / "out.jsonl", b"x\n")
    assert _tree(repo) == [".git"]


def test_output_through_a_symlink_into_a_checkout_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    link = tmp_path / "out"
    link.symlink_to(repo)
    with pytest.raises(CrosswalkError, match="Git checkout"):
        write_once(link / "out.jsonl", b"x\n")
    assert _tree(repo) == [".git"]


def test_an_existing_staging_file_is_never_reused_or_removed(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.write_bytes(b"keep\n")
    staging = tmp_path / ".out.jsonl.partial"
    staging.symlink_to(victim)
    with pytest.raises(CrosswalkError) as caught:
        write_once(tmp_path / "out.jsonl", b"x\n")
    assert str(caught.value) == f"refusing to reuse the staging file {staging}"
    assert victim.read_bytes() == b"keep\n"
    assert staging.is_symlink()
    assert not (tmp_path / "out.jsonl").exists()


def test_a_dangling_symlink_output_is_never_written_through(tmp_path: Path) -> None:
    output = tmp_path / "out.jsonl"
    output.symlink_to(tmp_path / "elsewhere.jsonl")
    with pytest.raises(CrosswalkError, match="refusing to overwrite"):
        write_once(output, b"x\n")
    assert not (tmp_path / "elsewhere.jsonl").exists()


def test_a_dangling_staging_symlink_is_never_followed(tmp_path: Path) -> None:
    staging = tmp_path / ".out.jsonl.partial"
    staging.symlink_to(tmp_path / "planted")
    with pytest.raises(CrosswalkError, match="refusing to reuse the staging file"):
        write_once(tmp_path / "out.jsonl", b"x\n")
    assert not (tmp_path / "planted").exists()
    assert staging.is_symlink()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unwritable_directory_names_the_output_it_could_not_write(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        with pytest.raises(CrosswalkError) as caught:
            write_once(locked / "out.jsonl", b"x\n")
    finally:
        locked.chmod(0o755)
    assert str(caught.value).startswith(f"could not write {locked / 'out.jsonl'}: ")
    assert _tree(locked) == []


def test_a_staging_file_removed_by_someone_else_still_ends_in_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out.jsonl"

    def vanished(source: Path, destination: Path) -> None:
        Path(source).unlink()
        raise OSError("staging vanished")

    monkeypatch.setattr(commands.os, "link", vanished)
    with pytest.raises(CrosswalkError, match="could not write .*staging vanished"):
        write_once(output, b"x\n")
    assert _tree(tmp_path) == []


@pytest.mark.parametrize("failure", ["write", "link"])
def test_failed_output_publication_removes_partial_bytes_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """write_once promises a whole new file or none, including after a short write."""
    output = tmp_path / "out.jsonl"
    staging = tmp_path / ".out.jsonl.partial"
    data = b'{"complete":true}\n'
    original_write = Path.write_bytes

    def failed_write(path: Path, body: bytes) -> int:
        assert path == staging
        original_write(path, body[:4])
        raise OSError("disk full")

    def failed_link(source: Path, destination: Path) -> None:
        assert source == staging
        assert destination == output
        assert source.read_bytes() == data
        raise OSError("link refused")

    with monkeypatch.context() as patch:
        if failure == "write":
            patch.setattr(Path, "write_bytes", failed_write)
        else:
            patch.setattr(commands.os, "link", failed_link)
        with pytest.raises(CrosswalkError, match="could not write"):
            write_once(output, data)

    assert _tree(tmp_path) == []
    write_once(output, data)
    assert output.read_bytes() == data
    assert _tree(tmp_path) == ["out.jsonl"]


def test_output_created_during_publication_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The initial existence check cannot authorize replacing a concurrent output."""
    output = tmp_path / "out.jsonl"
    original_link = os.link

    def competing_writer(source: Path, destination: Path) -> None:
        destination.write_bytes(b"other writer\n")
        original_link(source, destination)

    monkeypatch.setattr(commands.os, "link", competing_writer)
    with pytest.raises(CrosswalkError, match="could not write"):
        write_once(output, b"our output\n")

    assert output.read_bytes() == b"other writer\n"
    assert _tree(tmp_path) == ["out.jsonl"]
