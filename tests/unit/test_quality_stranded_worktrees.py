"""``scripts/quality/check_stranded_worktrees.py`` (issue #621), on real git worktrees.

The operator's question: which worktrees hold work that exists nowhere
else? Each state is built with git itself in a temporary repository with a
bare remote, and the script must name it without touching anything.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "quality" / "check_stranded_worktrees.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_stranded_worktrees", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load()


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True, timeout=60
    )
    return done.stdout.strip()


def _commit(cwd: Path, name: str) -> None:
    (cwd / name).write_text(name, encoding="utf-8")
    _git(cwd, "add", name)
    _git(cwd, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", name)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A clone of a bare remote, on ``main`` with one pushed commit."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(remote), str(clone))
    _git(clone, "checkout", "-q", "-b", "main")
    _commit(clone, "base")
    _git(clone, "push", "-q", "-u", "origin", "main")
    return clone


def _worktree(repo: Path, name: str, *, pushed: bool, detached: bool = False) -> Path:
    path = repo.parent / name
    if detached:
        _git(repo, "worktree", "add", "-q", "--detach", str(path), "HEAD")
        return path
    _git(repo, "worktree", "add", "-q", "-b", name, str(path), "main")
    if pushed:
        _git(path, "push", "-q", "-u", "origin", name)
    return path


def _by_branch(findings: list[object]) -> dict[str, list[str]]:
    return {f.worktree.branch or "detached": f.flags for f in findings}  # type: ignore[attr-defined]


class TestStates:
    @pytest.mark.parametrize("change", ["staged-add", "staged-delete", "unstaged-delete"])
    def test_index_changes_and_tracked_deletions_are_dirty(self, repo: Path, change: str) -> None:
        path = _worktree(repo, "w-index", pushed=True)
        if change == "staged-add":
            (path / "new").write_text("pending", encoding="utf-8")
            _git(path, "add", "new")
        elif change == "staged-delete":
            _git(path, "rm", "base")
        else:
            (path / "base").unlink()
        before = _git(path, "status", "--porcelain")

        assert _by_branch(check.scan(repo)) == {"w-index": ["dirty"]}
        assert _git(path, "status", "--porcelain") == before

    @pytest.mark.parametrize("local_commit", [False, True], ids=["behind", "diverged"])
    def test_only_local_commits_count_as_ahead(self, repo: Path, local_commit: bool) -> None:
        path = _worktree(repo, "w-tracking", pushed=True)
        _commit(repo, "remote-only")
        _git(repo, "push", "-q", "origin", "HEAD:refs/heads/w-tracking")
        if local_commit:
            _commit(path, "local-only")

        expected = ["ahead 1"] if local_commit else []
        assert _by_branch(check.scan(repo)) == {"w-tracking": expected}

    @pytest.mark.parametrize("detached", [False, True], ids=["branch", "detached"])
    def test_a_remote_descendant_preserves_the_worktree_commit(
        self, repo: Path, detached: bool
    ) -> None:
        path = _worktree(repo, "w-ancestor", pushed=False, detached=detached)
        _commit(path, "shared-work")
        _git(repo, "merge", "--ff-only", str(_git(path, "rev-parse", "HEAD")))
        _commit(repo, "remote-descendant")
        _git(repo, "push", "-q", "origin", "main")

        key = "detached" if detached else "w-ancestor"
        assert _by_branch(check.scan(repo)) == {key: []}

    def test_a_pushed_clean_worktree_is_not_stranded(self, repo: Path) -> None:
        _worktree(repo, "w-clean", pushed=True)

        assert _by_branch(check.scan(repo)) == {"w-clean": []}

    def test_commits_ahead_of_the_upstream_are_stranded(self, repo: Path) -> None:
        path = _worktree(repo, "w-ahead", pushed=True)
        _commit(path, "fix-1")
        _commit(path, "fix-2")

        assert _by_branch(check.scan(repo)) == {"w-ahead": ["ahead 2"]}

    def test_a_branch_never_pushed_is_stranded(self, repo: Path) -> None:
        path = _worktree(repo, "w-new", pushed=False)
        _commit(path, "only-here")

        assert _by_branch(check.scan(repo)) == {"w-new": ["no upstream"]}

    def test_a_branch_without_upstream_whose_head_a_remote_holds_is_not(self, repo: Path) -> None:
        _worktree(repo, "w-same", pushed=False)  # still at origin/main's commit

        assert _by_branch(check.scan(repo)) == {"w-same": []}

    def test_staged_and_modified_tracked_files_are_dirty(self, repo: Path) -> None:
        path = _worktree(repo, "w-dirty", pushed=True)
        (path / "base").write_text("changed", encoding="utf-8")

        assert _by_branch(check.scan(repo)) == {"w-dirty": ["dirty"]}

    def test_untracked_files_count_only_when_asked(self, repo: Path) -> None:
        path = _worktree(repo, "w-untracked", pushed=True)
        (path / "scratch.txt").write_text("x", encoding="utf-8")

        assert _by_branch(check.scan(repo)) == {"w-untracked": []}
        assert _by_branch(check.scan(repo, untracked=True)) == {"w-untracked": ["dirty"]}

    def test_a_detached_head_is_stranded_only_when_no_remote_holds_it(self, repo: Path) -> None:
        path = _worktree(repo, "w-detached", pushed=False, detached=True)
        assert _by_branch(check.scan(repo)) == {"detached": []}

        _commit(path, "detached-work")

        assert _by_branch(check.scan(repo)) == {"detached": ["detached"]}

    def test_a_prunable_worktree_is_named_and_not_examined(self, repo: Path) -> None:
        path = _worktree(repo, "w-gone", pushed=True)
        for file in path.iterdir():
            if file.is_file():
                file.unlink()
        (path / ".git").unlink(missing_ok=True)
        path.rmdir()

        assert _by_branch(check.scan(repo)) == {"w-gone": ["prunable"]}


class TestTheCommand:
    def test_untracked_option_reports_and_preserves_untracked_work(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = _worktree(repo, "w-scratch", pushed=True)
        scratch = path / "scratch.txt"
        scratch.write_bytes(b"uncommitted work\n")

        assert check.main(["--repo", str(repo)]) == 0
        assert capsys.readouterr().out == ""
        assert check.main(["--repo", str(repo), "--untracked"]) == 1
        out, err = capsys.readouterr()
        assert out.splitlines() == [f"{path}\tw-scratch\tdirty"]
        assert "1 stranded of 1 linked worktrees" in err
        assert scratch.read_bytes() == b"uncommitted work\n"
        assert _git(path, "status", "--porcelain") == "?? scratch.txt"

    def test_prunable_is_reported_but_does_not_fail_or_prune(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = _worktree(repo, "w-missing", pushed=False)
        for file in path.iterdir():
            file.unlink()
        path.rmdir()
        before = _git(repo, "worktree", "list", "--porcelain")

        assert check.main(["--repo", str(repo)]) == 0

        out, err = capsys.readouterr()
        assert out.splitlines() == [f"{path}\tw-missing\tprunable"]
        assert "0 stranded of 1 linked worktrees (1 prunable)" in err
        assert _git(repo, "worktree", "list", "--porcelain") == before
        assert not path.exists()

    def test_it_lists_the_stranded_ones_and_exits_1_without_changing_anything(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        clean = _worktree(repo, "w-clean", pushed=True)
        ahead = _worktree(repo, "w-ahead", pushed=True)
        _commit(ahead, "fix")
        before = (_git(ahead, "rev-parse", "HEAD"), _git(clean, "status", "--porcelain"))

        code = check.main(["--repo", str(repo)])

        out, err = capsys.readouterr()
        assert code == 1
        assert out.splitlines() == [f"{ahead}\tw-ahead\tahead 1"]
        assert "1 stranded of 2 linked worktrees (0 prunable); nothing was changed" in err
        assert (_git(ahead, "rev-parse", "HEAD"), _git(clean, "status", "--porcelain")) == before

    def test_all_lists_the_clean_ones_and_exits_0_when_nothing_is_stranded(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        clean = _worktree(repo, "w-clean", pushed=True)

        code = check.main(["--repo", str(repo), "--all"])

        out, _ = capsys.readouterr()
        assert code == 0
        assert out.splitlines() == [f"{clean}\tw-clean\tclean"]

    def test_the_main_worktree_is_never_listed(self, repo: Path) -> None:
        (repo / "base").write_text("dirty main", encoding="utf-8")

        assert check.scan(repo) == []
