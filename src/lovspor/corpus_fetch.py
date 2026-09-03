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
from lovspor.temporal_attestation import (
    ATTESTATION_FETCH_REFSPEC,
    ATTESTATION_NOTES_REF,
    refspec_transports_registry,
)

LOVVERK_REPO_URL = "https://github.com/bartoszkobylinski/lovverk.git"


class CorpusFetchError(LovsporError):
    """Cloning or updating the lovverk corpus failed."""


class FetchResult(BaseModel):
    """Outcome of a :func:`fetch_corpus` call."""

    model_config = ConfigDict(frozen=True)

    path: Path
    action: Literal["cloned", "updated", "unchanged"]


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


def is_corpus(path: Path) -> bool:
    """True when ``path`` holds a usable lovverk corpus (has a manifest).

    ``manifest.json`` is the corpus signature the MCP server requires (see
    ``mcp._CorpusStore``); a directory that merely exists is not a corpus.
    """
    return (path / "manifest.json").is_file()


def _git_capture(args: Sequence[str], cwd: Path) -> str | None:
    """Run a read-only git command, returning stripped stdout or None on failure."""
    try:
        # S603/S607: trusted git command, list args, no shell (see _git).
        result = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return result.stdout.strip()


def _origin_url(path: Path) -> str | None:
    """The clone's ``origin`` remote URL, or None if it cannot be read.

    Read from config directly: ``git remote get-url`` fatals when ANY
    fetch refspec in the remote's config is syntactically invalid (e.g.
    a wildcard on one side only), and that must not make a real lovverk
    clone unrecognisable — recognising it is exactly what lets the
    update path repair the bad refspec (codex-tests round 5, PR #230).
    """
    return _git_capture(["config", "--get", "remote.origin.url"], path)


def _same_repo(left: str, right: str) -> bool:
    """Compare remote URLs, ignoring a trailing ``.git`` or ``/``."""

    def norm(url: str) -> str:
        return url.rstrip("/").removesuffix(".git")

    return norm(left) == norm(right)


def _is_corpus_clone(path: Path, repo_url: str) -> bool:
    """True when ``path`` is a lovverk clone of ``repo_url``.

    A git repo carrying a corpus manifest whose ``origin`` points at
    ``repo_url``. The origin check stops an unrelated repo that merely
    happens to contain a ``manifest.json`` from being fast-forwarded.
    """
    if not (path / ".git").is_dir() or not is_corpus(path):
        return False
    origin = _origin_url(path)
    return origin is not None and _same_repo(origin, repo_url)


def _is_shallow(dest: Path) -> bool:
    """True when the clone's git history is shallow."""
    return _git_capture(["rev-parse", "--is-shallow-repository"], dest) == "true"


def _ensure_attestation_refspec(dest: Path) -> None:
    """Make every future fetch/pull transport the attestation registry.

    ADR-0012 point 2c: the registry travels as git notes, which a plain
    clone or pull never fetches — so without this refspec a real remote
    attestation reads as a false local absence. Configuring it here makes
    registry synchronisation part of the supported acquisition contract
    (idempotent: the refspec is added once). The glob form is deliberate —
    see ``ATTESTATION_FETCH_REFSPEC``.

    Repairs, not merely adds: a refspec that only MENTIONS the notes ref
    on one side (an unrelated branch mapped into the notes namespace, or
    the notes ref fetched out to a branch) does not transport the
    registry, and its bare form breaks every later fetch/pull against an
    origin lacking its source ref. Such lines are removed as
    misconfigurations of this contract's namespace before the canonical
    refspec is installed (codex-tests, PR #230).
    """
    existing = _git_capture(["config", "--get-all", "remote.origin.fetch"], dest) or ""
    lines = existing.splitlines()
    for line in lines:
        if ATTESTATION_NOTES_REF in line and not refspec_transports_registry(line):
            _git(
                ["config", "--fixed-value", "--unset-all", "remote.origin.fetch", line],
                cwd=dest,
            )
    if not any(refspec_transports_registry(line) for line in lines):
        _git(["config", "--add", "remote.origin.fetch", ATTESTATION_FETCH_REFSPEC], cwd=dest)


def fetch_corpus(
    dest: Path,
    *,
    repo_url: str = LOVVERK_REPO_URL,
    full_history: bool = False,
) -> FetchResult:
    """Clone the lovverk corpus to ``dest``, or fast-forward it if already there.

    Refuses to touch a path that exists but is not a lovverk clone (a file, or
    a non-empty directory), so a stray ``--dest`` can never clobber unrelated
    files. ``pull --ff-only`` keeps updates honest (never a surprise merge).
    The result reports ``cloned``, ``updated`` (a pull moved HEAD), or
    ``unchanged`` (already current).

    Both paths make the temporal attestation registry part of the supported
    acquisition contract (ADR-0012 point 2c): the notes refspec is
    configured so every fetch/pull transports ``refs/notes/
    temporal-attestations``, and a fresh clone fetches it immediately —
    a real remote attestation must never read as a false local absence.

    By default the clone is shallow (``--depth 1``) to keep the download
    small — sufficient for current-law tools, but the git-log-based
    time-machine tools (``get_law_at``, ``diff_law_versions``) then reach
    only as far back as the clone. ``full_history=True`` clones the complete
    history, and on an existing shallow clone deepens it in place with
    ``fetch --unshallow`` — additive, never rewriting history (ADR-0003:
    deployments exposing temporal tools require complete history).
    """
    dest = dest.expanduser()
    if _is_corpus_clone(dest, repo_url):
        if full_history and _is_shallow(dest):
            _git(["fetch", "--unshallow"], cwd=dest)
        _ensure_attestation_refspec(dest)
        before = _git_capture(["rev-parse", "HEAD"], dest)
        _git(["pull", "--ff-only"], cwd=dest)
        after = _git_capture(["rev-parse", "HEAD"], dest)
        action: Literal["updated", "unchanged"] = "updated" if after != before else "unchanged"
        return FetchResult(path=dest.resolve(), action=action)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise CorpusFetchError(
            f"{dest} exists and is not a lovverk clone; refusing to overwrite it.",
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    clone_args = ["clone", repo_url, str(dest)]
    if not full_history:
        clone_args[1:1] = ["--depth", "1"]
    _git(clone_args, cwd=dest.parent)
    _ensure_attestation_refspec(dest)
    _git(["fetch", "origin"], cwd=dest)
    return FetchResult(path=dest.resolve(), action="cloned")
