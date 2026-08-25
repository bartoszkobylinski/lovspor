"""Shared pytest fixtures."""

from pathlib import Path

import pytest

from lovspor.exclusive_workload import ENV_LOCK_PATH

# Repo-targeting variables git exports to hook subprocesses. Anything spawned
# with these inherited operates on the EXPORTING repo, not on the cwd repo.
_GIT_REPO_ENV_VARS = (
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
)


@pytest.fixture(autouse=True)
def _hermetic_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip repo-targeting ``GIT_*`` vars inherited from a parent git process.

    When the suite runs inside a pre-commit hook of a linked worktree, git
    exports an absolute ``GIT_DIR``/``GIT_INDEX_FILE``; every test-spawned
    ``git init``/``git commit`` in a tmpdir then silently targets the real
    repo's index — failing the test AND staging destructive changes in the
    developer's worktree. In a primary checkout the leak is masked only by
    accident: the exported ``GIT_INDEX_FILE`` is the relative ``.git/index``,
    which happens to resolve inside the tmpdir's own ``.git``.
    """
    for name in _GIT_REPO_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _hermetic_exclusive_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the host-level workload lock at the test's own tmpdir.

    The default lives under the developer's ``~/.local/state``; a suite
    sharing it with a live benchmark or sweep would either refuse (a test
    failing for a reason outside the test) or, worse, hold the real lock
    against the real workload for the length of a test.
    """
    monkeypatch.setenv(ENV_LOCK_PATH, str(tmp_path / "exclusive-workload.lock"))
