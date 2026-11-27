"""Git for AR7a: the monotonic pin of a witness log, on a *local bare* authority (#738).

The forarbeider package starts no process, so this is the one Git capability
the AR7a checkpoint authority needs, as narrow as :mod:`lovspor.git_publish`:

- every argument vector must fit one of :data:`COMMANDS` exactly before any
  process starts. There is **no force shape**: no ``--force``, ``-f``,
  ``--force-with-lease``, ``--delete``, ``--mirror`` or ``--all``, and a refspec
  must be ``<40-hex>:refs/heads/witness/<name>`` with no leading ``+``;
- the authority must be an **absolute local path** ending in ``.git``. A URL,
  an ``scp``-style host or a relative path has no shape: this module cannot
  reach GitHub or any network remote;
- the pin moves only by a push of a commit whose parent is the pin the caller
  read. That is the compare-and-swap: Git refuses a push that is not a
  fast-forward, so a pin that moved meanwhile (to a sibling, a descendant or
  anywhere else) refuses it, and receive-pack updates the ref under its lock,
  so of two concurrent pushes at most one lands;
- that Git refuses, not this module: the authority's own
  ``receive.denyNonFastForwards`` and ``receive.denyDeletes`` (which Git
  applies to ``refs/heads/*`` only) refuse a forced rewind, a forced sibling
  and a deletion from any other client. :func:`settings` reads both;
- hooks, fsmonitor, replace objects and grafts are off; the environment is
  built here, never inherited.

Only :mod:`lovspor.forarbeider.witness_checkpoint` imports this module.
"""

import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, NamedTuple

from lovspor.errors import LovsporError

TIMEOUT_SECONDS = 60
CHECKPOINT_REF = re.compile(r"refs/heads/witness/[a-z0-9][a-z0-9-]{0,62}")
_OBJECT = re.compile(r"[0-9a-f]{40}")
_REFSPEC = re.compile(r"[0-9a-f]{40}:refs/heads/witness/[a-z0-9][a-z0-9-]{0,62}")
_SETTINGS = ("receive.denyNonFastForwards", "receive.denyDeletes")
_IDENTITY = b"lovspor-witness-authority <authority@lovspor.invalid>"


class GitCheckpointError(LovsporError):
    """Git could not run, ran too long, or was asked for something not allowed."""


def _object(part: str) -> bool:
    return _OBJECT.fullmatch(part) is not None


def _local_bare(part: str) -> bool:
    return (
        Path(part).is_absolute()
        and os.path.normpath(part) == part
        and "://" not in part
        and part.endswith(".git")
    )


def _ref(part: str) -> bool:
    return CHECKPOINT_REF.fullmatch(part) is not None


def _refspec(part: str) -> bool:
    return _REFSPEC.fullmatch(part) is not None


def _setting(part: str) -> bool:
    return part in _SETTINGS


Part = str | Callable[[str], bool]
COMMANDS: Final[tuple[tuple[Part, ...], ...]] = (
    ("init", "--bare", "--quiet", "--template=", _local_bare),
    ("config", "--local", "--get", _setting),
    ("ls-remote", _local_bare, _ref),
    ("cat-file", "commit", _object),
    ("cat-file", "blob", _object),
    ("ls-tree", "-z", _object),
    ("rev-list", "--count", _object),
    ("rev-list", "--count", "--min-parents=2", _object),
    ("fetch", "--quiet", "--no-tags", "--no-write-fetch-head", _local_bare, _ref),
    ("hash-object", "-t", "blob", "-w", "--stdin"),
    ("mktree", "-z"),
    ("hash-object", "-t", "commit", "-w", "--stdin"),
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


class Answer(NamedTuple):
    code: int
    out: bytes
    err: bytes


def allowed(argv: tuple[str, ...]) -> bool:
    def fits(shape: tuple[Part, ...]) -> bool:
        if len(argv) != len(shape):
            return False
        return all(p(a) if callable(p) else p == a for a, p in zip(argv, shape, strict=True))

    return any(fits(shape) for shape in COMMANDS)


def _environment(directory: Path, at: datetime | None) -> dict[str, str]:
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
    if at is not None:
        found["GIT_COMMITTER_NAME"] = "lovspor-witness-authority"
        found["GIT_COMMITTER_EMAIL"] = "authority@lovspor.invalid"
        found["GIT_COMMITTER_DATE"] = f"{int(at.timestamp())} +0000"
    return found


def run(
    directory: Path, argv: tuple[str, ...], stdin: bytes = b"", at: datetime | None = None
) -> Answer:
    """Run one allowed command in ``directory``.

    Raises:
        GitCheckpointError: ``argv`` fits no shape, or Git could not run or timed out.
    """
    if not allowed(argv):
        raise GitCheckpointError(f"git argument vector not allowed: {argv[:1]}")
    try:
        # S603/S607: list arguments of a checked shape, never a shell string.
        done = subprocess.run(  # noqa: S603
            ["git", *_PREFIX, "-C", str(directory), *argv],  # noqa: S607
            input=stdin,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
            env=_environment(directory, at),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GitCheckpointError(f"git {argv[0]} could not run") from error
    return Answer(done.returncode, done.stdout, done.stderr)


def _out(directory: Path, argv: tuple[str, ...], stdin: bytes = b"") -> bytes:
    answer = run(directory, argv, stdin)
    if answer.code != 0:
        raise GitCheckpointError(f"git {argv[0]} refused")
    return answer.out


# --- reading the authority -----------------------------------------------------------------------


class Settings(NamedTuple):
    deny_non_fast_forwards: bool
    deny_deletes: bool


def settings(bare: Path) -> Settings:
    """Whether the authority itself refuses a rewind and a deletion of ``refs/heads/*``.

    Raises:
        GitCheckpointError: the authority cannot be read.
    """
    if not (bare / "HEAD").is_file():
        raise GitCheckpointError("authority is not a repository")

    def flag(name: str) -> bool:
        answer = run(bare, ("config", "--local", "--get", name))
        if answer.code not in (0, 1):
            raise GitCheckpointError("git config refused")
        return answer.out.strip() == b"true"

    return Settings(*(flag(name) for name in _SETTINGS))


def tip(bare: Path, ref: str) -> str | None:
    """The commit ``ref`` names on the authority, ``None`` when there is no such ref."""
    out = _out(bare.parent, ("ls-remote", str(bare), ref))
    lines = [line.split() for line in out.decode("ascii", "replace").splitlines()]
    named = [fields[0] for fields in lines if len(fields) == 2 and fields[1] == ref]  # noqa: PLR2004
    if len(named) > 1:
        raise GitCheckpointError("ls-remote named the ref twice")
    return named[0] if named else None


class Pinned(NamedTuple):
    """One commit of the authority's ref: its files, its parent and its depth."""

    commit: str
    parent: str | None
    files: dict[str, bytes]
    commits: int
    merges: int


def pinned(bare: Path, commit: str) -> Pinned:
    """Everything a verifier judges about ``commit``, read from the authority.

    Raises:
        GitCheckpointError: any object cannot be read.
    """
    raw = _out(bare, ("cat-file", "commit", commit))
    header = raw.split(b"\n\n", 1)[0].splitlines()
    parents = [line[7:].decode() for line in header if line.startswith(b"parent ")]
    files: dict[str, bytes] = {}
    for record in _out(bare, ("ls-tree", "-z", commit)).split(b"\0"):
        if record:
            meta, _, path = record.partition(b"\t")
            mode, kind, oid = meta.decode().split()
            if kind != "blob" or mode != "100644":
                raise GitCheckpointError("authority tree holds no plain file")
            files[path.decode()] = _out(bare, ("cat-file", "blob", oid))
    commits = int(_out(bare, ("rev-list", "--count", commit)))
    merges = int(_out(bare, ("rev-list", "--count", "--min-parents=2", commit)))
    return Pinned(commit, parents[0] if len(parents) == 1 else None, files, commits, merges)


# --- moving the pin ------------------------------------------------------------------------------

Outcome = Literal["advanced", "moved", "unknown"]


class Target(NamedTuple):
    """The authority repository and the one ref of it that pins the log."""

    bare: Path
    ref: str


def advance(
    target: Target, expected: str | None, files: Mapping[str, bytes], at: datetime
) -> Outcome:
    """Move the target ref from ``expected`` (``None``: create it) to a commit of ``files``, or not.

    ``advanced``: the ref names the new commit; ``moved``: Git refused, and the
    ref names something other than ``expected`` (another writer won);
    ``unknown``: anything else, including an authority that cannot be read.
    """
    bare, ref = target
    try:
        with tempfile.TemporaryDirectory(prefix="lovspor-checkpoint-") as scratch:
            work = Path(os.path.realpath(scratch)) / "work.git"
            _out(work.parent, ("init", "--bare", "--quiet", "--template=", str(work)))
            if expected is not None:
                _out(
                    work, ("fetch", "--quiet", "--no-tags", "--no-write-fetch-head", str(bare), ref)
                )
            commit = _commit(work, expected, files, at)
            run(work, ("push", "--porcelain", str(bare), f"{commit}:{ref}"), at=at)
            landed = tip(bare, ref)
    except (GitCheckpointError, OSError):
        return "unknown"
    if landed == commit:
        return "advanced"
    return "moved" if landed != expected else "unknown"


def _commit(work: Path, parent: str | None, files: Mapping[str, bytes], at: datetime) -> str:
    entries = b""
    for name in sorted(files):
        blob = _out(work, ("hash-object", "-t", "blob", "-w", "--stdin"), files[name]).strip()
        entries += b"100644 blob %s\t%s\0" % (blob, name.encode())
    tree = _out(work, ("mktree", "-z"), entries).strip()
    who = _IDENTITY + f" {int(at.timestamp())} +0000".encode()
    lines = [b"tree " + tree]
    if parent is not None:
        lines.append(b"parent " + parent.encode())
    lines += [b"author " + who, b"committer " + who, b"", b"witness checkpoint", b""]
    raw = b"\n".join(lines)
    return _out(work, ("hash-object", "-t", "commit", "-w", "--stdin"), raw).strip().decode()
