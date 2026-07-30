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
        # encoding pinned to UTF-8: corpus paths are UTF-8 on disk
        # (Norwegian slugs carry æ/ø/å), and locale-dependent decoding
        # would corrupt them on runners with a non-UTF-8 locale.
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
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

    Orphan paths — those that are neither on disk nor tracked in the
    current ``HEAD`` — are silently dropped. This is robust
    engineering against state inconsistency: previous syncs that
    crashed mid-way can leave the manifest claiming a path that no
    longer exists in the working tree or the index. Without this
    filter, the next sync's per-doc commit fails with
    ``pathspec 'X' did not match any files`` and the corpus stays
    stuck. The filter lets the sync make forward progress and the
    inconsistency self-heals through subsequent action passes.
    Production crash 2026-05-05 (third occurrence in the
    path-cascade class) was the orphan-path variant; PR #45's
    universal collision detector handles within-sync collisions but
    cannot remediate manifest-vs-tree drift left by prior crashes.
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
    rel = _drop_orphan_paths(repo_abs, rel)
    if not rel:
        return
    _run(["add", "--", *rel], cwd=repo)


def _drop_orphan_paths(repo_abs: Path, rel_paths: list[str]) -> list[str]:
    """Return only paths that are either present on disk or tracked
    in the current HEAD. Orphan references (manifest claims them but
    they are gone from both the working tree and the index) are
    dropped — staging them would crash with ``pathspec did not match
    any files``.

    Real ``git ls-files`` failures (e.g. ``repo_abs`` is not a git
    repository, or git itself errors) propagate as
    :class:`GitCommandError` via :func:`_run` — silently swallowing
    them would mask configuration problems behind a no-op ``add``.
    Plain ``ls-files`` (no ``--error-unmatch``) is used so unknown
    paths produce empty output rather than a non-zero exit, letting
    the orphan filter do its job in the success path.

    ``-z`` (NUL-delimited output) is mandatory, not cosmetic: without
    it ``git ls-files`` C-quotes non-ASCII paths whenever
    ``core.quotePath`` is on (the default), so a tracked
    ``forskrifter/endr-i-økodesignforskriften.md`` came back as
    ``"forskrifter/endr-i-\\303\\270kodesignforskriften.md"`` and never
    matched the raw UTF-8 string — the deletion was misclassified as an
    orphan and silently dropped (corpus defect of 2026-07-24: manifest
    tombstone with the ``.md``/``.bin`` never deleted; see
    docs/evidence/corpus-integrity-root-cause-2026-07-30.md §2.2).
    With ``-z`` git emits paths verbatim regardless of configuration.
    """
    if not rel_paths:
        return rel_paths
    result = _run(["ls-files", "-z", "--", *rel_paths], cwd=repo_abs)
    tracked = {p for p in result.stdout.split("\0") if p}
    kept: list[str] = []
    for p in rel_paths:
        if p in tracked or (repo_abs / p).exists():
            kept.append(p)
    return kept


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


def has_uncommitted_changes(repo: Path) -> bool:
    """True if the worktree or index is dirty (any modified, staged,
    deleted, or untracked-and-unignored path).

    A sync writes the manifest — the change-detection source of truth —
    then commits it. If a prior run crashed in that window the manifest
    is left ahead of git; the next run would read it, see 'nothing
    changed', and silently drop the work. A dirty tree at sync start is
    exactly that residue, so the caller aborts. Ignored files (per
    ``.gitignore``) never count; ``git status --porcelain`` omits them.
    """
    result = _run(["status", "--porcelain"], cwd=repo)
    return bool(result.stdout.strip())
