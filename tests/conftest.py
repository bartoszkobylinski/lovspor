"""Shared pytest fixtures."""

import pytest

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
