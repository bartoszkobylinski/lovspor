"""The staging output path: outside every Git repository (#738)."""

from pathlib import Path

import pytest

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.staging import checked_output


def _refused(raw: Path, forbidden: tuple[Path, ...] = ()) -> str:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        checked_output(raw, forbidden)
    assert caught.value.reason is RefusalReason.UNSAFE_OUTPUT_PATH
    return caught.value.detail


def test_path_outside_any_repository_is_returned_resolved(tmp_path: Path) -> None:
    target = tmp_path / "staging" / "a.json"
    assert checked_output(target, ()) == target.resolve()


def test_relative_path_is_refused() -> None:
    assert _refused(Path("a.json")) == "a.json"


def test_only_json_is_written(tmp_path: Path) -> None:
    _refused(tmp_path / "a.txt")


def test_directory_target_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "dir.json"
    target.mkdir()
    _refused(target)


@pytest.mark.parametrize("nested", ["a.json", "x/y/a.json"])
def test_inside_a_git_checkout_is_refused(tmp_path: Path, nested: str) -> None:
    (tmp_path / ".git").mkdir()
    _refused(tmp_path / nested)


def test_git_worktree_file_counts_as_a_checkout(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    _refused(tmp_path / "a.json")


def test_symlink_into_a_checkout_is_refused(tmp_path: Path) -> None:
    checkout = tmp_path / "lovverk"
    (checkout / ".git").mkdir(parents=True)
    link = tmp_path / "staging"
    link.symlink_to(checkout)
    _refused(link / "a.json")


def test_forbidden_tree_without_git_is_refused(tmp_path: Path) -> None:
    tree = tmp_path / "corpus"
    tree.mkdir()
    _refused(tree / "a.json", (tree,))
    _refused(tree / "x" / "a.json", (tree,))


def test_sibling_of_a_forbidden_tree_is_allowed(tmp_path: Path) -> None:
    tree = tmp_path / "corpus"
    target = tmp_path / "corpus-staging" / "a.json"
    assert checked_output(target, (tree,)) == target.resolve()


@pytest.mark.parametrize("existing", [False, True])
def test_file_symlink_into_checkout_is_refused(tmp_path: Path, existing: bool) -> None:
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    target = checkout / "record.json"
    if existing:
        target.write_bytes(b"existing metadata\n")
    link = tmp_path / "staging.json"
    link.symlink_to(target)
    _refused(link)
    assert link.is_symlink()
    if existing:
        assert target.read_bytes() == b"existing metadata\n"
    else:
        assert not target.exists()
