"""Corpus-pin primitives for LLHB freezing and validation (FREEZE.md §3).

Stage 2 provides the reusable pieces only: reading and verifying the
corpus state a dataset pins. It does NOT freeze anything — no lock file
is produced here.

A pin is the pair (full lovverk commit SHA, manifest ``generated_at``)
per ADR-0003: git history is the corpus version store, the manifest is
authoritative for membership. Verification fails closed: a dirty
working tree or a different HEAD means the checkout cannot be trusted
to serve the pinned corpus state.
"""

import subprocess
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, field_validator

from lovspor.errors import LovsporError
from lovspor.storage.manifest import ManifestRecord, read_manifest

_FULL_SHA_LENGTH = 40


class CorpusPinError(LovsporError):
    """The corpus checkout does not match the expected pinned state."""


class CorpusPin(BaseModel, frozen=True):
    lovverk_commit: str
    manifest_generated_at: datetime

    @field_validator("lovverk_commit")
    @classmethod
    def _full_sha(cls, value: str) -> str:
        if len(value) != _FULL_SHA_LENGTH or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("lovverk_commit must be a full 40-char lowercase hex SHA")
        return value


def _git(corpus_path: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell, repo-local
        ["git", "-C", str(corpus_path), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise CorpusPinError(
            f"git {' '.join(args)} failed in {corpus_path}: {result.stderr.strip()}",
        )
    return result.stdout.strip()


def git_head_sha(corpus_path: Path) -> str:
    """Full HEAD commit SHA of the corpus checkout."""
    return _git(corpus_path, "rev-parse", "HEAD")


def working_tree_clean(corpus_path: Path) -> bool:
    """True when ``git status --porcelain`` reports nothing."""
    return _git(corpus_path, "status", "--porcelain") == ""


def current_pin(corpus_path: Path) -> CorpusPin:
    """The pin describing the checkout as it stands (no cleanliness claim)."""
    manifest = read_manifest(corpus_path / "manifest.json")
    return CorpusPin(
        lovverk_commit=git_head_sha(corpus_path),
        manifest_generated_at=manifest.generated_at,
    )


def verify_pin(corpus_path: Path, pin: CorpusPin) -> None:
    """Fail closed unless the checkout serves exactly the pinned state.

    Requires HEAD == pinned SHA, a clean working tree (a dirty tree can
    serve content from no commit at all), AND the manifest
    ``generated_at`` matching the pin — the FREEZE.md §3 cross-check. A
    lock carrying the right SHA but the wrong timestamp is malformed and
    must not verify (Codex, PR #16 finding 2).
    """
    head = git_head_sha(corpus_path)
    if head != pin.lovverk_commit:
        raise CorpusPinError(
            f"corpus HEAD {head} does not match pinned commit {pin.lovverk_commit}",
        )
    if not working_tree_clean(corpus_path):
        raise CorpusPinError(
            f"corpus working tree at {corpus_path} is dirty; "
            "pinned material must come from a clean checkout",
        )
    generated_at = read_manifest(corpus_path / "manifest.json").generated_at
    if generated_at != pin.manifest_generated_at:
        raise CorpusPinError(
            f"manifest generated_at {generated_at.isoformat()} does not match the "
            f"pinned {pin.manifest_generated_at.isoformat()}; the pin is malformed "
            "or the manifest was rewritten",
        )


def document_pin(record: ManifestRecord) -> dict[str, str | int | None]:
    """Per-document pin fields FREEZE.md §3.3 requires in the lock file."""
    return {
        "xml_hash": record.xml_hash,
        "renderer_version": record.renderer_version,
        "embedding_space_id": record.embedding_space_id,
        "embedding_hash": record.embedding_hash,
    }
