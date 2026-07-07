"""Fetch and refresh a local lovverk corpus clone for MCP consumers.

`lovspor mcp` reads a local clone of the lovverk corpus. This module gives
consumers a one-command way to obtain and update that clone, so they never
need to know the GitHub URL or manage git plumbing by hand. It mirrors the
subprocess-git safety posture of ``sync/git_commit.py``: every git call uses
list arguments (never a shell string), so shell injection is structurally
impossible and ``shell=True`` never appears.
"""

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from lovspor.errors import LovsporError

LOVVERK_REPO_URL = "https://github.com/bartoszkobylinski/lovverk.git"


class CorpusFetchError(LovsporError):
    """Cloning or updating the lovverk corpus failed."""


class FetchResult(BaseModel):
    """Outcome of a :func:`fetch_corpus` call."""

    model_config = ConfigDict(frozen=True)

    path: Path
    action: Literal["cloned", "updated"]


def default_corpus_path() -> Path:
    """Default local corpus location.

    ``$XDG_CACHE_HOME/lovverk`` when the XDG variable is set, else
    ``~/.cache/lovverk``. This is where ``fetch-corpus`` clones by default and
    where ``mcp`` looks when no ``--corpus-path`` is given.
    """
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return (base / "lovverk").expanduser()


def _git(args: Sequence[str], cwd: Path) -> None:
    try:
        # S603/S607: git is a trusted system command invoked with list args
        # (never a shell string), matching sync/git_commit.py's posture.
        subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise CorpusFetchError(
            f"git {' '.join(args)} (cwd={cwd}) exited {exc.returncode}: {stderr}",
        ) from exc
    except FileNotFoundError as exc:  # pragma: no cover - requires PATH manipulation
        raise CorpusFetchError(f"git executable not found: {exc}") from exc


def _is_corpus_clone(path: Path) -> bool:
    """True when ``path`` is an existing lovverk clone (git repo + manifest)."""
    return (path / ".git").is_dir() and (path / "manifest.json").is_file()


def fetch_corpus(dest: Path, *, repo_url: str = LOVVERK_REPO_URL) -> FetchResult:
    """Clone the lovverk corpus to ``dest``, or fast-forward it if already there.

    Refuses to touch a path that exists but is not a lovverk clone (a file, or
    a non-empty directory), so a stray ``--dest`` can never clobber unrelated
    files. A shallow clone keeps the download small; ``pull --ff-only`` keeps
    updates honest (never a surprise merge).
    """
    dest = dest.expanduser()
    if _is_corpus_clone(dest):
        _git(["pull", "--ff-only"], cwd=dest)
        return FetchResult(path=dest.resolve(), action="updated")
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise CorpusFetchError(
            f"{dest} exists and is not a lovverk clone; refusing to overwrite it.",
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git(["clone", "--depth", "1", repo_url, str(dest)], cwd=dest.parent)
    return FetchResult(path=dest.resolve(), action="cloned")
