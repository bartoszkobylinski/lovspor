"""Applying a pinned owner change: refusals, the merge gate, the run-end check."""

from pathlib import Path

import pytest

from lovspor.owner_changes import Pin
from lovspor.owner_changes_apply import (
    Applied,
    PinRefusedError,
    changed_since_pin,
    merge_pin,
    plan,
    scope_violations,
)
from tests.unit.publication_seal_support import git, hermetic_git

DOC = "lokale-forskrifter/5025/doc.md"
OTHER = "lokale-forskrifter/manifest.json"


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    hermetic_git(monkeypatch, tmp_path)
    repo = tmp_path / "lovverk"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    _write(repo, {DOC: "line 1\nline 2\nline 3\n", OTHER: "{}\n", "README.md": "readme\n"})
    _commit(repo, "seed")
    return repo


def _write(repo: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def _branch(repo: Path, files: dict[str, str], name: str = "owner") -> str:
    """An owner branch from the current main with ``files`` written; back on main after."""
    git(repo, "checkout", "-q", "-b", name)
    _write(repo, files)
    tip = _commit(repo, "owner change")
    git(repo, "checkout", "-q", "main")
    return tip


def _pin(commit: str, attempt: int = 1) -> Pin:
    return Pin(line=1, commit=commit, pull_request=42, attempt=attempt, reason="test")


def _apply(repo: Path, pin: Pin) -> Applied:
    base = plan(repo, pin, pin.commit, attempt_used=False)
    assert base is not None
    return merge_pin(repo, pin, base, f"owner-change: lovverk#42 {pin.commit} attempt 1")


def _refusal(repo: Path, pin: Pin, pr_head: str | None = None, used: bool = False) -> str:
    with pytest.raises(PinRefusedError) as caught:
        plan(repo, pin, pr_head or pin.commit, attempt_used=used)
    return caught.value.code


def test_pin_is_merged_and_the_merge_equals_the_pin(corpus: Path) -> None:
    tip = _branch(corpus, {DOC: "line 1\nline 2 repealed\nline 3\n"})
    main_before = _commit_on_main(corpus, {"lover/x.md": "sync output\n"})
    applied = _apply(corpus, _pin(tip))
    assert git(corpus, "rev-parse", "HEAD^1") == main_before
    assert git(corpus, "rev-parse", "HEAD^2") == tip
    assert (corpus / DOC).read_text(encoding="utf-8") == "line 1\nline 2 repealed\nline 3\n"
    assert (corpus / "lover/x.md").exists()
    assert changed_since_pin(corpus, [applied]) == []


def _commit_on_main(repo: Path, files: dict[str, str]) -> str:
    _write(repo, files)
    return _commit(repo, "sync")


def test_pin_already_on_main_is_skipped(corpus: Path) -> None:
    tip = _branch(corpus, {DOC: "changed\n"})
    git(corpus, "merge", "-q", "--no-ff", "--no-edit", tip)
    assert plan(corpus, _pin(tip), tip, attempt_used=False) is None


def test_consumed_attempt_is_refused(corpus: Path) -> None:
    tip = _branch(corpus, {DOC: "changed\n"})
    assert _refusal(corpus, _pin(tip), used=True) == "attempt-consumed"


def test_moved_pull_request_head_is_refused_as_stale(corpus: Path) -> None:
    tip = _branch(corpus, {DOC: "changed\n"})
    assert _refusal(corpus, _pin(tip), pr_head="b" * 40) == "stale-pin"


def test_merge_commit_inside_the_pin_is_refused(corpus: Path) -> None:
    side = _branch(corpus, {OTHER: '{"a": 1}\n'}, name="side")
    git(corpus, "checkout", "-q", "-b", "owner")
    _write(corpus, {DOC: "changed\n"})
    _commit(corpus, "owner edit")
    git(corpus, "merge", "-q", "--no-ff", "--no-edit", side)
    tip = git(corpus, "rev-parse", "HEAD")
    git(corpus, "checkout", "-q", "main")
    assert _refusal(corpus, _pin(tip)) == "merge-commit"


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "lover/lov-1.md",
        "history/by-id/x.md",
        "scripts/x.py",
        ".github/workflows/x.yml",
        "lokale-forskrifter/.gitattributes",
        "lokale-forskrifter/5025/.hidden.md",
        "lokale-forskrifter-x/a.md",
    ],
)
def test_path_outside_the_scope_is_refused(corpus: Path, path: str) -> None:
    tip = _branch(corpus, {DOC: "changed\n", path: "x\n"})
    assert _refusal(corpus, _pin(tip)) == "out-of-scope"


def test_executable_file_is_refused(corpus: Path) -> None:
    git(corpus, "checkout", "-q", "-b", "owner")
    (corpus / DOC).chmod(0o755)
    tip = _commit(corpus, "chmod")
    git(corpus, "checkout", "-q", "main")
    assert _refusal(corpus, _pin(tip)) == "out-of-scope"


def test_symlink_is_refused(corpus: Path) -> None:
    git(corpus, "checkout", "-q", "-b", "owner")
    (corpus / "lokale-forskrifter/5025/link.md").symlink_to("doc.md")
    tip = _commit(corpus, "link")
    git(corpus, "checkout", "-q", "main")
    assert _refusal(corpus, _pin(tip)) == "out-of-scope"


def test_out_of_scope_change_reverted_later_in_the_pin_is_still_refused(corpus: Path) -> None:
    """Each commit is checked, not only the net range."""
    git(corpus, "checkout", "-q", "-b", "owner")
    _write(corpus, {"README.md": "sneaked\n"})
    _commit(corpus, "touch readme")
    _write(corpus, {"README.md": "readme\n", DOC: "changed\n"})
    tip = _commit(corpus, "restore readme")
    git(corpus, "checkout", "-q", "main")
    assert git(corpus, "diff", "--name-only", "HEAD", tip) == DOC
    assert _refusal(corpus, _pin(tip)) == "out-of-scope"


def test_path_changed_on_main_since_the_pin_branched_is_refused(corpus: Path) -> None:
    tip = _branch(corpus, {DOC: "line 1\nowner\nline 3\n"})
    _commit_on_main(corpus, {DOC: "line 1\nline 2\nline 3 main\n"})
    assert _refusal(corpus, _pin(tip)) == "overlap"


def test_rename_against_an_edit_on_main_is_refused(corpus: Path) -> None:
    """A rename-following merge would carry main's edit onto the new path."""
    git(corpus, "checkout", "-q", "-b", "owner")
    git(corpus, "mv", DOC, "lokale-forskrifter/5025/renamed.md")
    tip = _commit(corpus, "rename")
    git(corpus, "checkout", "-q", "main")
    _commit_on_main(corpus, {DOC: "line 1\nline 2\nline 3 edited\n"})
    assert _refusal(corpus, _pin(tip)) == "overlap"


def test_conflicting_merge_is_refused_and_head_is_unchanged(corpus: Path) -> None:
    tip = _branch(corpus, {DOC: "owner\n"})
    head = _commit_on_main(corpus, {DOC: "main\n"})
    base = git(corpus, "merge-base", "HEAD", tip)
    with pytest.raises(PinRefusedError) as caught:
        merge_pin(corpus, _pin(tip), base, "owner-change")
    assert caught.value.code == "conflict"
    assert git(corpus, "rev-parse", "HEAD") == head
    assert git(corpus, "status", "--porcelain") == ""


def test_merge_that_differs_from_the_pin_is_undone(corpus: Path) -> None:
    """The gate itself, reached past ``plan``: a clean merge of a file both
    sides edited is not the pinned change."""
    tip = _branch(corpus, {DOC: "line 1\nline 2\nline 3 owner\n"})
    head = _commit_on_main(corpus, {DOC: "line 1 main\nline 2\nline 3\n"})
    base = git(corpus, "merge-base", "HEAD", tip)
    with pytest.raises(PinRefusedError) as caught:
        merge_pin(corpus, _pin(tip), base, "owner-change")
    assert caught.value.code == "gate"
    assert git(corpus, "rev-parse", "HEAD") == head
    assert git(corpus, "status", "--porcelain") == ""


def test_pinned_file_changed_after_the_merge_is_reported(corpus: Path) -> None:
    tip = _branch(corpus, {DOC: "owner\n", OTHER: '{"n": 1}\n'})
    applied = _apply(corpus, _pin(tip))
    _commit_on_main(corpus, {DOC: "rewritten by the sync\n"})
    assert changed_since_pin(corpus, [applied]) == [f"lovverk#42: {DOC}"]


def test_pinned_file_mode_changed_after_the_merge_is_reported(corpus: Path) -> None:
    tip = _branch(corpus, {DOC: "owner\n"})
    applied = _apply(corpus, _pin(tip))
    (corpus / DOC).chmod(0o755)
    _commit(corpus, "chmod")
    assert changed_since_pin(corpus, [applied]) == [f"lovverk#42: {DOC}"]


def test_dirty_work_tree_is_refused_before_merging(corpus: Path) -> None:
    tip = _branch(corpus, {DOC: "owner\n"})
    head = git(corpus, "rev-parse", "HEAD")
    _write(corpus, {"README.md": "uncommitted\n"})
    base = git(corpus, "merge-base", "HEAD", tip)
    with pytest.raises(PinRefusedError) as caught:
        merge_pin(corpus, _pin(tip), base, "owner-change")
    assert caught.value.code == "dirty"
    assert git(corpus, "rev-parse", "HEAD") == head
    assert (corpus / "README.md").read_text(encoding="utf-8") == "uncommitted\n"


def test_pinned_deletion_restored_after_the_merge_is_reported(corpus: Path) -> None:
    git(corpus, "checkout", "-q", "-b", "owner")
    git(corpus, "rm", "-q", DOC)
    tip = _commit(corpus, "delete")
    git(corpus, "checkout", "-q", "main")
    applied = _apply(corpus, _pin(tip))
    assert changed_since_pin(corpus, [applied]) == []
    _commit_on_main(corpus, {DOC: "back\n"})
    assert changed_since_pin(corpus, [applied]) == [f"lovverk#42: {DOC}"]


def test_scope_violations_lists_each_problem_once(corpus: Path) -> None:
    tip = _branch(corpus, {"README.md": "a\n", "lover/x.md": "b\n"})
    base = git(corpus, "merge-base", "HEAD", tip)
    assert scope_violations(corpus, base, tip) == [
        "README.md: outside lokale-forskrifter/",
        "lover/x.md: outside lokale-forskrifter/",
    ]
