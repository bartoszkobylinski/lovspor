"""Where the crosswalk producer may write (ADR-0017 D2).

Raw Lovdata archives and every producer output stay outside Git checkouts
(clones, worktrees and submodules alike, gitignored directories included) and
outside the Lovverk corpus. Each check runs on the fully resolved path before
anything is created, so a symlink cannot carry a write into a checkout and a
refusal leaves no file behind.
"""

import os
from pathlib import Path

from lovspor.crosswalk.errors import CrosswalkError

LOVVERK_ENV = "LOVVERK_CORPUS_PATH"


def _is_lovverk(directory: Path) -> bool:
    return (directory / "manifest.json").is_file() and (directory / "lover").is_dir()


def _owner(resolved: Path) -> str | None:
    corpus = os.environ.get(LOVVERK_ENV)
    if corpus and resolved.is_relative_to(Path(corpus).resolve()):
        return f"inside the Lovverk corpus {corpus}"
    for directory in (resolved, *resolved.parents):
        # `.git` is a directory in a clone and a file in a worktree or submodule.
        if (directory / ".git").exists():
            return f"inside the Git checkout {directory}"
        if _is_lovverk(directory):
            return f"inside the Lovverk corpus {directory}"
    return None


def writable_target(path: Path, what: str) -> Path:
    """The resolved location for a new write, or a refusal before anything is created."""
    resolved = path.resolve()
    owner = _owner(resolved)
    if owner is not None:
        raise CrosswalkError(f"refusing to write the {what} {owner}: {path}")
    return resolved


def new_cache(cache: Path) -> Path:
    """A capture directory that is new or empty, a real directory, and outside every checkout."""
    if cache.is_symlink():
        raise CrosswalkError(f"refusing a capture cache that is a symlink: {cache}")
    resolved = writable_target(cache, "capture cache")
    if resolved.exists() and any(resolved.iterdir()):
        raise CrosswalkError(f"capture cache is not empty; use a new directory: {cache}")
    return resolved


def real_directory(directory: Path, inside: Path) -> Path:
    """Create ``directory`` and refuse it unless it is a real directory under ``inside``."""
    writable_target(directory, "staging directory")
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.resolve().is_relative_to(inside.resolve()):
        raise CrosswalkError(f"refusing an unsafe staging directory: {directory}")
    return directory
