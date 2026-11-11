"""Read-only Git for the forarbeider release-checkout guard (#738, B1a).

The forarbeider package starts no process (``tests/unit/test_forarbeider_boundary.py``),
so the one Git capability the guard needs lives here, as narrow as it can be:

- an exact allowlist of argument vectors, every one read-only, checked before
  any process starts, so a write (``add``, ``config --add``, ``update-index``,
  ``notes``, ``fetch`` ...) cannot be passed even through a cast;
- ``--no-optional-locks``: without it ``git status`` refreshes and rewrites
  ``.git/index``, and a "read-only" status becomes a write;
- fsmonitor and the untracked cache forced off, so no configured helper runs
  and no cache answers for the worktree;
- a minimal environment built here, never inherited: ``GIT_DIR``,
  ``GIT_WORK_TREE``, ``GIT_INDEX_FILE`` and the rest could point Git at
  another repository, and the system and global config are ignored, so only
  the checkout's own configuration counts;
- list arguments only, no shell, a timeout, stdin closed.

Only :mod:`lovspor.forarbeider.release_checkout` imports this module.
"""

import os
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lovspor.errors import LovsporError

TIMEOUT_SECONDS = 60
#: Every argument vector Git may be run with. Exact matches, never prefixes.
ALLOWED: frozenset[tuple[str, ...]] = frozenset(
    {
        ("version",),
        (
            "rev-parse",
            "--path-format=absolute",
            "--show-toplevel",
            "--absolute-git-dir",
            "--git-common-dir",
            "--is-shallow-repository",
            "--is-bare-repository",
        ),
        ("rev-parse", "--verify", "--quiet", "HEAD^{commit}"),
        ("rev-list", "--max-parents=0", "HEAD"),
        ("symbolic-ref", "--quiet", "HEAD"),
        ("config", "--local", "--list", "-z"),
        ("status", "--porcelain=v2", "-z", "--untracked-files=all", "--ignored=no"),
        ("ls-files", "-v", "-z"),
        ("ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--", "forarbeider"),
    }
)
_PREFIX = (
    "--no-optional-locks",
    "--no-pager",
    "--literal-pathspecs",
    "--no-replace-objects",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "core.ignoreStat=false",
)


class GitReadError(LovsporError):
    """Git could not be run, ran too long, or was asked for something not allowed."""


class GitReading(BaseModel):
    """What one allowed Git command answered: its exit code and its raw standard output."""

    model_config = ConfigDict(frozen=True)

    code: int
    out: bytes


def git_environment(checkout: Path) -> dict[str, str]:
    """The whole environment Git gets; nothing is inherited but ``PATH``."""
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CEILING_DIRECTORIES": str(checkout.parent),
    }


def read_git(checkout: Path, argv: tuple[str, ...]) -> GitReading:
    """Run one allowed, read-only Git command in ``checkout``.

    Raises:
        GitReadError: ``argv`` is not allowed, or Git could not run or timed out.
    """
    if argv not in ALLOWED:
        raise GitReadError(f"git argument vector not allowed: {argv[:1]}")
    try:
        # S603/S607: list arguments from the allowlist above, never a shell string.
        done = subprocess.run(  # noqa: S603
            ["git", *_PREFIX, "-C", str(checkout), *argv],  # noqa: S607
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=TIMEOUT_SECONDS,
            check=False,
            env=git_environment(checkout),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GitReadError(f"git {argv[0]} could not run") from error
    return GitReading(code=done.returncode, out=done.stdout)
