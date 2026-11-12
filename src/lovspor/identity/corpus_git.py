"""Read-only git access to a lovverk checkout for the identity migration (#871).

Every command here reads: ``rev-parse``, ``log``, ``rev-list``,
``diff-tree``, ``ls-tree`` and ``cat-file``. ``--no-optional-locks`` keeps
git from refreshing the index on the way, so a dry run against an operator's
clone leaves no trace in it.
List arguments only, never a shell.
"""

import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lovspor.errors import IdentityMigrationError

MANIFEST_PATH = "manifest.json"
_GIT = ("git", "--no-optional-locks", "-c", "core.quotePath=false")
_BATCH_MISSING = b" missing\n"


def _git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    """Run one read-only git command in ``repo``; raise on failure."""
    try:
        result = subprocess.run(  # noqa: S603
            [*_GIT, *args],
            cwd=repo,
            input=stdin,
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError) as exc:
        raise IdentityMigrationError(f"git {' '.join(args[:2])} failed in {repo}: {exc}") from exc
    return result.stdout


def resolve_commit(repo: Path, ref: str) -> str:
    """``ref`` as a full commit sha, so the plan names its input exactly."""
    return _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").decode().strip()


def manifest_commits(repo: Path, base: str) -> list[str]:
    """Every commit reachable from ``base`` that touched the manifest, oldest first.

    ``--topo-order`` puts every parent before its children, so a walk in this
    order never sees a revision before the one it was built on.
    """
    out = _git(repo, "log", "--topo-order", "--reverse", "--format=%H", base, "--", MANIFEST_PATH)
    return out.decode().split()


def read_blob(repo: Path, commit: str, path: str) -> bytes | None:
    """The file ``path`` at ``commit``, or ``None`` when the commit has no such file."""
    out = _git(repo, "cat-file", "--batch", stdin=f"{commit}:{path}\n".encode())
    return next(iter(_parse_batch(out, 1)))


def read_blobs(repo: Path, commit: str, paths: list[str]) -> dict[str, bytes | None]:
    """Many files at ``commit`` through one ``cat-file --batch``; ``None`` for a missing one."""
    if not paths:
        return {}
    request = "".join(f"{commit}:{path}\n" for path in paths).encode()
    out = _git(repo, "cat-file", "--batch", stdin=request)
    return dict(zip(paths, _parse_batch(out, len(paths)), strict=True))


def _parse_batch(out: bytes, count: int) -> list[bytes | None]:
    """Split ``cat-file --batch`` output into ``count`` blobs (``None`` when missing)."""
    blobs: list[bytes | None] = []
    offset = 0
    for _ in range(count):
        header_end = out.index(b"\n", offset) + 1
        header = out[offset:header_end]
        if header.endswith(_BATCH_MISSING):
            blobs.append(None)
            offset = header_end
            continue
        size = int(header.split()[2])
        blobs.append(out[header_end : header_end + size])
        offset = header_end + size + 1
    return blobs


class PathCommit(BaseModel):
    """One commit that touched a path: its parent, author date, subject and numstat rows."""

    model_config = ConfigDict(frozen=True)

    sha: str
    parent: str | None
    date: str
    subject: str
    stat_lines: list[str]


_COMMIT_MARK = "__COMMIT__"
_LITERAL = "--literal-pathspecs"
_LOG_PATH = (
    _LITERAL,
    "log",
    "--topo-order",
    "--no-renames",
    "--numstat",
    f"--format={_COMMIT_MARK}%n%H%n%P%n%aI%n%s",
)
_DELETED = (_LITERAL, "diff-tree", "-r", "-z", "--name-only", "--no-renames", "--diff-filter=D")
_RENAMES = (_LITERAL, "diff-tree", "-r", "-M", "--numstat")


def path_commits(repo: Path, tip: str, path: str) -> list[PathCommit]:
    """Every commit reachable from ``tip`` that touched ``path`` itself, newest first.

    No ``--follow`` and no rename detection: the path is taken literally, so
    a commit that moved a file in or out shows as an add or a delete.
    """
    out = _git(repo, *_LOG_PATH, tip, "--", path)
    return [_path_commit(block) for block in out.decode().split(f"{_COMMIT_MARK}\n")[1:]]


def _path_commit(block: str) -> PathCommit:
    sha, parents, date, subject, *stats = block.split("\n")
    parent = parents.split()[0] if parents.split() else None
    rows = [row for row in stats if row]
    return PathCommit(sha=sha, parent=parent, date=date, subject=subject, stat_lines=rows)


def read_revision_blobs(repo: Path, specs: list[str]) -> list[bytes | None]:
    """The blobs named ``<rev>:<path>`` through one ``cat-file --batch``; ``None`` when missing."""
    if not specs:
        return []
    out = _git(repo, "cat-file", "--batch", stdin="".join(f"{s}\n" for s in specs).encode())
    return _parse_batch(out, len(specs))


def rename_row(repo: Path, commit: PathCommit, path: str) -> str | None:
    """The numstat rename row of a file moved to ``path`` at ``commit``, if it was moved there.

    Rename detection runs over ``path`` and the files ``commit`` deleted in
    the same directory only, so one destination is matched against its
    candidate sources and git's rename limit is never reached.
    """
    if commit.parent is None:
        return None
    directory = path.rsplit("/", 1)[0] if "/" in path else "."
    deleted = _git(repo, *_DELETED, commit.parent, commit.sha, "--", directory)
    sources = [p for p in deleted.decode().split("\0") if p]
    if not sources:
        return None
    rows = _git(repo, *_RENAMES, commit.parent, commit.sha, "--", path, *sources)
    return next((row for row in rows.decode().splitlines() if " => " in row), None)


def topo_commits(repo: Path, tip: str, paths: list[str]) -> list[str]:
    """Every commit reachable from ``tip`` that touched one of ``paths``, newest first."""
    out = _git(repo, _LITERAL, "rev-list", "--topo-order", tip, "--", *paths)
    return out.decode().split()


def list_files(repo: Path, commit: str, directories: list[str]) -> list[str]:
    """Every file path under ``directories`` at ``commit``, sorted."""
    out = _git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--", *directories)
    return sorted(path for path in out.decode().split("\0") if path)
