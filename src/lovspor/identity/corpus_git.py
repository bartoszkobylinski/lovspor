"""Read-only git access to a lovverk checkout for the identity migration (#871).

Every command here reads: ``rev-parse``, ``log``, ``ls-tree`` and
``cat-file``. ``--no-optional-locks`` keeps git from refreshing the index on
the way, so a dry run against an operator's clone leaves no trace in it.
List arguments only, never a shell.
"""

import subprocess
from pathlib import Path

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


def list_files(repo: Path, commit: str, directories: list[str]) -> list[str]:
    """Every file path under ``directories`` at ``commit``, sorted."""
    out = _git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--", *directories)
    return sorted(path for path in out.decode().split("\0") if path)
