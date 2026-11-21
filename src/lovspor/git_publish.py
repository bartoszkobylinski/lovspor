"""Git for AR4b-1 path B: build one commit object and fast-forward a *local bare* remote (#738).

The forarbeider package starts no process, so this is the one Git capability
path B needs, as narrow as :mod:`lovspor.git_prepare`:

- every argument vector must fit one of :data:`COMMANDS` exactly before any
  process starts. There is **no force shape**: no ``--force``, ``-f``,
  ``--force-with-lease``, ``--delete``, ``--mirror`` or ``--all``, and a refspec
  must be ``<40-hex>:refs/heads/main`` with no leading ``+``, so Git itself
  refuses a non-fast-forward update;
- the push target must be an **absolute local path** ending in ``.git``. A URL,
  an ``scp``-style host or a relative path has no shape: this module cannot
  reach GitHub or any network remote, so it can only move a disposable bare
  repository;
- ``git add`` takes one publication path at a time, by the AR4a tree grammar,
  into a **separate index file** inside ``.git``, so the clone's own index,
  and with it the AR4a read-back, is never touched;
- ``git init`` makes only a bare repository at such a path, with no
  template, so no hook or other file is copied into it;
- hooks, fsmonitor, replace objects and grafts are off; the environment is
  built here, never inherited.

Only :mod:`lovspor.forarbeider.publication_path_b` imports this module.
"""

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

from lovspor.errors import LovsporError

TIMEOUT_SECONDS = 120
_COMMIT = re.compile(r"[0-9a-f]{40}")
_TREE_FILE = re.compile(
    r"INDEX\.md|catalogue\.json|documents/regjeringen-[1-9]\d{0,11}\.md|releases/[0-9a-f]{64}\.json"
)
_REFSPEC = re.compile(r"[0-9a-f]{40}:refs/heads/main")


def _commit(part: str) -> bool:
    return _COMMIT.fullmatch(part) is not None


def _tree_file(part: str) -> bool:
    return _TREE_FILE.fullmatch(part) is not None


def _local_bare(part: str) -> bool:
    return (
        Path(part).is_absolute()
        and os.path.normpath(part) == part
        and "://" not in part
        and part.endswith(".git")
    )


def _refspec(part: str) -> bool:
    return _REFSPEC.fullmatch(part) is not None


Part = str | Callable[[str], bool]
COMMANDS: Final[tuple[tuple[Part, ...], ...]] = (
    ("init", "--bare", "--quiet", "--template=", "--initial-branch=main", _local_bare),
    ("read-tree", _commit),
    ("add", "--", _tree_file),
    ("write-tree",),
    ("hash-object", "-t", "commit", "-w", "--stdin"),
    ("cat-file", "commit", _commit),
    ("diff-tree", "--no-commit-id", "--name-only", "-r", "-z", _commit, _commit),
    ("ls-tree", "-r", "-z", _commit),
    ("ls-remote", _local_bare, "refs/heads/main"),
    ("push", "--porcelain", _local_bare, _refspec),
)
_PREFIX = (
    "--no-pager",
    "--literal-pathspecs",
    "--no-replace-objects",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "push.default=nothing",
    "-c",
    "push.followTags=false",
)


class GitPublishError(LovsporError):
    """Git could not run, ran too long, or was asked for something not allowed."""


class GitAnswer(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: int
    out: bytes
    err: bytes


def _fits(argv: tuple[str, ...], shape: tuple[Part, ...]) -> bool:
    if len(argv) != len(shape):
        return False
    return all(p(a) if callable(p) else p == a for a, p in zip(argv, shape, strict=True))


def allowed(argv: tuple[str, ...]) -> bool:
    return any(_fits(argv, shape) for shape in COMMANDS)


def environment(directory: Path, index: Path | None) -> dict[str, str]:
    """The whole environment Git gets; nothing is inherited but ``PATH``."""
    found = {
        "PATH": os.environ.get("PATH", os.defpath),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CEILING_DIRECTORIES": str(directory.parent),
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_GRAFT_FILE": os.devnull,
    }
    if index is not None:
        found["GIT_INDEX_FILE"] = str(index)
    return found


def run_git(
    directory: Path, argv: tuple[str, ...], stdin: bytes | None = None, index: Path | None = None
) -> GitAnswer:
    """Run one allowed command in ``directory``; ``index`` names a private index file.

    Raises:
        GitPublishError: ``argv`` fits no shape, the index is outside ``.git``,
            or Git could not run or timed out.
    """
    if not allowed(argv):
        raise GitPublishError(f"git argument vector not allowed: {argv[:1]}")
    if index is not None and index.parent != directory / ".git":
        raise GitPublishError("the index file lives in the clone's .git")
    try:
        # S603/S607: list arguments of a checked shape, never a shell string.
        done = subprocess.run(  # noqa: S603
            ["git", *_PREFIX, "-C", str(directory), *argv],  # noqa: S607
            input=stdin if stdin is not None else b"",
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
            env=environment(directory, index),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GitPublishError(f"git {argv[0]} could not run") from error
    return GitAnswer(code=done.returncode, out=done.stdout, err=done.stderr)
