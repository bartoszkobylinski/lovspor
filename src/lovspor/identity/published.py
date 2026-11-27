"""The owner each address of a corpus revision published (#911).

An address's published owner at a commit is what a reader of that commit is
served:

- a ``current`` record's address is owned by that record (200);
- otherwise a slug history ``<directory>/history/<slug>.json`` names its owner
  (the 301 or 410 page of that slug);
- otherwise the one ``removed`` record on the address owns it.

The migration's base is the parent of the migration commit (the oldest commit
that added ``identity.json``), or ``HEAD`` while the migration is not committed
yet. A root migration commit has no base.
"""

import json
from functools import lru_cache
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from lovspor.errors import IdentitySchemaError
from lovspor.identity.corpus_git import (
    MANIFEST_PATH,
    first_commit_adding,
    has_commits,
    list_files,
    parents_of,
    read_blob,
    read_blobs,
    resolve_commit,
)
from lovspor.identity.registry import DIRECTORY_OF_DATASET, record_address
from lovspor.identity.schema import IDENTITY_FILENAME
from lovspor.storage.manifest import Manifest

Address = tuple[str, str]


def migration_base(repo: Path) -> str | None:
    """The commit whose published owners the registry keeps; ``None`` when there is none."""
    if not has_commits(repo):
        return None
    head = resolve_commit(repo, "HEAD")
    migration = first_commit_adding(repo, head, IDENTITY_FILENAME)
    if migration is None:
        return head
    parents = parents_of(repo, migration)
    return parents[0] if parents else None


def _doc_id(blob: bytes | None) -> str | None:
    try:
        data = json.loads((blob or b"").decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    value = data.get("doc_id") if isinstance(data, dict) else None
    return value if isinstance(value, str) else None


def _history_owners(repo: Path, commit: str) -> dict[Address, str]:
    directories = sorted(DIRECTORY_OF_DATASET.values())
    paths = [
        path
        for path in list_files(repo, commit, [f"{d}/history" for d in directories])
        if len(PurePosixPath(path).parts) == 3 and path.endswith(".json")  # noqa: PLR2004
    ]
    owners = {}
    for path, blob in read_blobs(repo, commit, paths).items():
        named = _doc_id(blob)
        if named is not None:
            owners[(PurePosixPath(path).parts[0], PurePosixPath(path).stem)] = named
    return owners


def _base_manifest(repo: Path, commit: str) -> Manifest | None:
    blob = read_blob(repo, commit, MANIFEST_PATH)
    if blob is None:
        return None
    try:
        return Manifest.model_validate_json(blob)
    except ValidationError as exc:
        raise IdentitySchemaError(f"{commit}: unreadable {MANIFEST_PATH}: {exc}") from exc


def _record_owners(manifest: Manifest | None, status: str) -> dict[Address, str]:
    """``address -> doc_id`` of the records of ``status``; an address two of them share
    is left out (only a removed record can share one, and then neither owns it here)."""
    seen: dict[Address, list[str]] = {}
    for doc_id, record in (manifest.documents if manifest else {}).items():
        if record.status == status:
            seen.setdefault(record_address(record), []).append(doc_id)
    return {address: ids[0] for address, ids in seen.items() if len(ids) == 1}


@lru_cache(maxsize=8)
def published_owners(repo: Path, commit: str) -> dict[Address, str]:
    """Every address the corpus at ``commit`` published, with its owner (memoised)."""
    manifest = _base_manifest(repo, commit)
    return {
        **_record_owners(manifest, "removed"),
        **_history_owners(repo, commit),
        **_record_owners(manifest, "current"),
    }
