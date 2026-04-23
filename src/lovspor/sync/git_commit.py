"""Thin wrappers around the ``git`` CLI for the corpus push pipeline.

Why subprocess + git CLI rather than GitPython:

- Single repo, a handful of operations (add, commit, push, status).
  Importing a full library and adding a transitive dependency is
  disproportionate to the surface area used.
- ``git`` itself is the most portable git engine: same behavior in dev,
  CI, and production. No risk of GitPython lagging behind core git
  semantics.
- All commands are invoked with list arguments (never a shell string),
  so shell injection is structurally impossible. ``shell=True`` is
  forbidden by ``CLAUDE.md`` and absent from this module.

The orchestrator (next PR) composes these primitives into the per-
document commit loop documented in ``docs/architecture.md``.
"""

import subprocess
from collections.abc import Sequence
from pathlib import Path

from lovspor.errors import LovsporError

_NO_STAGED_CHANGES = 0
_HAS_STAGED_CHANGES = 1


class GitCommandError(LovsporError):
    """A ``git`` invocation exited non-zero."""


def _run(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        # S603/S607 suppressed: git is a trusted system command, args are
        # always built from list literals or sanitized inputs (never shell
        # strings), and PATH-based resolution is the standard expectation
        # for invoking system git.
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise GitCommandError(
            f"git {' '.join(args)} (cwd={cwd}) exited {exc.returncode}: {stderr}",
        ) from exc
    except FileNotFoundError as exc:  # pragma: no cover - requires PATH manipulation to trigger
        raise GitCommandError(
            f"git executable not found: {exc}",
        ) from exc


def add(repo: Path, paths: Sequence[Path | str]) -> None:
    """Stage ``paths`` (relative or absolute) for the next commit.

    No-op for empty ``paths``. Absolute paths are translated to
    ``repo``-relative for git's ``add`` argument list. ``repo`` itself
    may be relative (e.g. ``Path("./lovverk")``); it is resolved to
    absolute before computing the relative-to comparison so that
    relative-repo + absolute-path combinations work correctly.
    """
    if not paths:
        return
    repo_abs = repo.resolve()
    rel: list[str] = []
    for raw in paths:
        path = Path(raw)
        if path.is_absolute():
            rel.append(str(path.relative_to(repo_abs)))
        else:
            rel.append(str(path))
    _run(["add", "--", *rel], cwd=repo)


def has_staged_changes(repo: Path) -> bool:
    """True if the index differs from HEAD (commit-able state).

    Uses ``git diff --cached --quiet`` directly rather than ``_run`` because
    we want to inspect the exit code (0 = no diff, 1 = diff) rather than
    raise on non-zero.
    """
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],  # noqa: S607
        cwd=repo,
        check=False,
    )
    if result.returncode == _NO_STAGED_CHANGES:
        return False
    if result.returncode == _HAS_STAGED_CHANGES:
        return True
    # Defensive: git diff --cached --quiet only documents 0/1 exit codes.
    # An unexpected code would indicate a git crash or signal interruption.
    raise GitCommandError(  # pragma: no cover
        f"git diff --cached --quiet (cwd={repo}) returned unexpected code {result.returncode}",
    )


def commit(repo: Path, message: str) -> None:
    """Create a commit with ``message``. Caller must ensure something is staged.

    Use ``has_staged_changes`` first if you need to avoid an empty-commit
    error.
    """
    _run(["commit", "-m", message], cwd=repo)


def push(repo: Path, *, remote: str = "origin", branch: str = "main") -> None:
    """``git push <remote> <branch>`` from ``repo``."""
    _run(["push", remote, branch], cwd=repo)


def status_porcelain(repo: Path) -> str:
    """Return ``git status --porcelain`` output. Empty string = clean."""
    return _run(["status", "--porcelain"], cwd=repo).stdout
