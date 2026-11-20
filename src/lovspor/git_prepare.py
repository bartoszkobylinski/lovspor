"""Git for the AR4a isolated release clone: clone, check out, read; never publish (#738).

The forarbeider package starts no process (``tests/unit/test_forarbeider_boundary.py``),
so the Git a publication *preparation* needs lives here, as narrow as
:mod:`lovspor.git_read` and as strict:

- every argument vector must fit one of :data:`COMMANDS` exactly, each part a
  literal or a checked shape (an absolute path, a 40-hex commit), before any
  process starts. ``commit``, ``push``, ``fetch``, ``add``, ``tag``,
  ``update-ref`` and every other write to history or a remote have no shape:
  nothing here can publish, whatever a caller passes;
- the clone is made with ``--no-local --no-hardlinks``, so it shares no object
  file with the source, and ``--template=`` so no hook is copied in; its one
  remote is removed at once, so nothing pushed from it could reach anything;
- hooks, fsmonitor, replace objects and grafts are off, and the environment
  is built here, never inherited, as :mod:`lovspor.git_read` builds its own
  (that module stays imported by the release-checkout guard alone);
- list arguments only, no shell, a timeout, stdin closed.

Only :mod:`lovspor.forarbeider.publication_clone` imports this module.
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


def _absolute(part: str) -> bool:
    return Path(part).is_absolute() and os.path.normpath(part) == part


def _commit(part: str) -> bool:
    return _COMMIT.fullmatch(part) is not None


def _verified(part: str) -> bool:
    return part in {"HEAD^{commit}", "refs/heads/main^{commit}"} or (
        part.endswith("^{commit}") and _commit(part.removesuffix("^{commit}"))
    )


#: Every shape Git may run with: a literal must match, a predicate must hold.
Part = str | Callable[[str], bool]
COMMANDS: Final[tuple[tuple[Part, ...], ...]] = (
    (
        "clone",
        "--no-local",
        "--no-hardlinks",
        "--no-checkout",
        "--template=",
        "--quiet",
        "--",
        _absolute,
        _absolute,
    ),
    ("checkout", "--quiet", "--detach", _commit),
    ("remote", "remove", "origin"),
    ("remote",),
    ("rev-parse", "--verify", "--quiet", _verified),
    ("status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=matching"),
)
_PREFIX = (
    "--no-optional-locks",
    "--no-pager",
    "--literal-pathspecs",
    "--no-replace-objects",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "core.symlinks=false",
)


def git_environment(directory: Path) -> dict[str, str]:
    """The whole environment Git gets; nothing is inherited but ``PATH``."""
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CEILING_DIRECTORIES": str(directory.parent),
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_GRAFT_FILE": os.devnull,
    }


class GitPrepareError(LovsporError):
    """Git could not run, ran too long, or was asked for something not allowed."""


class GitAnswer(BaseModel):
    """What one allowed command answered: its exit code and its raw standard output."""

    model_config = ConfigDict(frozen=True)

    code: int
    out: bytes


def _fits(argv: tuple[str, ...], shape: tuple[Part, ...]) -> bool:
    if len(argv) != len(shape):
        return False
    return all(p(a) if callable(p) else p == a for a, p in zip(argv, shape, strict=True))


def allowed(argv: tuple[str, ...]) -> bool:
    return any(_fits(argv, shape) for shape in COMMANDS)


def run_git(directory: Path, argv: tuple[str, ...]) -> GitAnswer:
    """Run one allowed command in ``directory``.

    Raises:
        GitPrepareError: ``argv`` fits no shape, or Git could not run or timed out.
    """
    if not allowed(argv):
        raise GitPrepareError(f"git argument vector not allowed: {argv[:1]}")
    try:
        # S603/S607: list arguments of a checked shape, never a shell string.
        done = subprocess.run(  # noqa: S603
            ["git", *_PREFIX, "-C", str(directory), *argv],  # noqa: S607
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=TIMEOUT_SECONDS,
            check=False,
            env=git_environment(directory),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GitPrepareError(f"git {argv[0]} could not run") from error
    return GitAnswer(code=done.returncode, out=done.stdout)
