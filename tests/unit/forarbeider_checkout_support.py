"""Scratch lovverk release clones for the B1a checkout-guard tests (#738). No real corpus.

A clone is a new ``git init`` in ``tmp_path`` holding the Lovdata root shape
(``manifest.json``, ``lover/``, ``forskrifter/``) as one placeholder commit,
with ``origin`` set to the trusted lovverk URL and the batch branch checked
out. Git runs with the caller's global and system config ignored, so a
developer's signing or hook settings cannot change what the tests build.
"""

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

TRUSTED_URL = "https://github.com/bartoszkobylinski/lovverk.git"
BRANCH = "forarbeider/batch-2100-04-10"
_ROOT_MANIFEST = {"documents": {}, "generated_at": None, "version": 1}
_HERMETIC = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_AUTHOR_DATE": "2026-04-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-04-01T00:00:00Z",
}


def git(repo: Path, *args: str, check: bool = True) -> str:
    """Git in ``repo`` with a fixed identity and no outside configuration."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    done = subprocess.run(
        ["git", "-c", "user.name=Operator", "-c", "user.email=op@example.invalid", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=check,
        env={**env, **_HERMETIC},
    )
    return done.stdout


def release_clone(base: Path, branch: str = BRANCH, name: str = "lovverk-release") -> Path:
    """A clone with the lovverk root shape, the trusted origin and ``branch`` checked out."""
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    clone = base / name
    clone.mkdir(mode=0o700)
    for directory in ("lover", "forskrifter"):
        (clone / directory).mkdir()
        (clone / directory / "README.md").write_text("Synthetic placeholder.\n")
    (clone / "manifest.json").write_text(json.dumps(_ROOT_MANIFEST) + "\n")
    git(clone, "init", "--quiet", "-b", "main")
    git(clone, "add", "-A")
    git(clone, "commit", "--quiet", "-m", "chore: synthetic root")
    git(clone, "remote", "add", "origin", TRUSTED_URL)
    git(clone, "switch", "--quiet", "-c", branch)
    return clone


def root_commit(clone: Path) -> str:
    return git(clone, "rev-list", "--max-parents=0", "HEAD").strip()


def trust(monkeypatch: pytest.MonkeyPatch, *clones: Path) -> None:
    """Make the scratch clones' root commits the trusted ones, for this test only."""
    roots = frozenset(root_commit(clone) for clone in clones)
    monkeypatch.setattr("lovspor.forarbeider.release_checkout.TRUSTED_ROOT_COMMITS", roots)


def commit_all(clone: Path, message: str) -> None:
    git(clone, "add", "-A")
    git(clone, "commit", "--quiet", "-m", message)


def snapshot(*roots: Path) -> dict[str, tuple[bytes | None, int, int]]:
    """Every entry below ``roots``, ``.git`` included, links not followed: bytes, mode, mtime."""
    found: dict[str, tuple[bytes | None, int, int]] = {}
    for root in roots:
        for folder, directories, files in os.walk(root):
            for name in [folder, *(str(Path(folder) / item) for item in directories + files)]:
                status = os.lstat(name)
                regular = stat.S_ISREG(status.st_mode)
                payload = Path(name).read_bytes() if regular else None
                found[name] = (payload, status.st_mode, status.st_mtime_ns)
    return found
