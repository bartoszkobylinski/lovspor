"""Read-only git access to a lovverk checkout for the identity migration (#871).

Every command here reads: ``rev-parse``, ``rev-list``, ``log``, ``ls-tree``
and ``cat-file``. ``--no-optional-locks`` keeps git from refreshing the index on
the way, so a dry run against an operator's clone leaves no trace in it.
List arguments only, never a shell.
"""

import subprocess
from collections.abc import Iterator
from pathlib import Path

from lovspor.errors import IdentityMigrationError

MANIFEST_PATH = "manifest.json"
_REVISION_CHUNK = 16
#: ``--no-replace-objects``: a ``refs/replace/`` entry must not rewrite the
#: history the identity guards read (#871, N1).
_GIT = ("git", "--no-optional-locks", "--no-replace-objects", "-c", "core.quotePath=false")
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


def toplevel(repo: Path) -> Path:
    """The root of the work tree ``repo`` lies in."""
    return Path(_git(repo, "rev-parse", "--show-toplevel").decode().strip())


def is_shallow(repo: Path) -> bool:
    """True when ``repo`` is a shallow clone, whose history is cut off."""
    return _git(repo, "rev-parse", "--is-shallow-repository").decode().strip() == "true"


def has_commits(repo: Path) -> bool:
    """False only for a repository no ref points into: nothing was ever committed."""
    return bool(_git(repo, "rev-list", "-n", "1", "--all").strip())


def last_commit_touching(repo: Path, rev: str, path: str) -> str | None:
    """The newest commit reachable from ``rev`` that added, changed or deleted ``path``.

    ``--full-history`` follows every parent of a merge, so a merge that drops
    the file does not hide the side that added it; ``-1`` stops at the first
    match, so a corpus that has the file pays for a short walk only.
    """
    out = _git(repo, "log", "-1", "--full-history", "--format=%H", rev, "--", path)
    return out.decode().strip() or None


def first_commit_adding(repo: Path, rev: str, path: str) -> str | None:
    """The oldest commit reachable from ``rev`` that added ``path``, every merge side followed."""
    out = _git(
        repo,
        "log",
        "--full-history",
        "--topo-order",
        "--diff-filter=A",
        "--format=%H",
        rev,
        "--",
        path,
    )
    commits = out.decode().split()
    return commits[-1] if commits else None


def commits_touching_since(repo: Path, rev: str, since: str, path: str) -> list[str]:
    """``since``, then every later commit ``rev`` reaches that touched ``path``, oldest first.

    "After" is everything ``rev`` reaches that ``since``'s parents do not, so a
    side branch merged after ``since`` is walked too.
    """
    args = ("--full-history", "--topo-order", "--reverse", "--format=%H")
    out = _git(repo, "log", *args, rev, "--not", f"{since}^@", "--", path)
    later = [commit for commit in out.decode().split() if commit != since]
    return [since, *later]


def iter_revisions(repo: Path, commits: list[str], path: str) -> Iterator[bytes | None]:
    """``path`` at each of ``commits`` in order, through one ``cat-file --batch`` per chunk.

    Chunked so that hundreds of revisions of a megabyte-sized file never sit in
    memory at once.
    """
    for start in range(0, len(commits), _REVISION_CHUNK):
        chunk = commits[start : start + _REVISION_CHUNK]
        yield from read_blobs_at(repo, [(commit, path) for commit in chunk])


def read_blobs_at(repo: Path, objects: list[tuple[str, str]]) -> list[bytes | None]:
    """``commit:path`` for each pair through one ``cat-file --batch``; ``None`` when missing."""
    if not objects:
        return []
    request = "".join(f"{commit}:{path}\n" for commit, path in objects).encode()
    return _parse_batch(_git(repo, "cat-file", "--batch", stdin=request), len(objects))


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


def list_files(repo: Path, commit: str, directories: list[str]) -> list[str]:
    """Every file path under ``directories`` at ``commit``, sorted."""
    out = _git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--", *directories)
    return sorted(path for path in out.decode().split("\0") if path)
