"""Producer outputs obey the same path rules as captures, and their staging is never reused."""

from pathlib import Path

import pytest

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
