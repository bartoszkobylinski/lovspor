"""The registry keeps every address owner the corpus published before it (#911).

``require_registry_history`` freezes every entry from the migration commit on,
and ``registry_content`` holds the registry to the manifest and the slug
histories it ships with. Neither looks behind the migration commit: a
migration that gives a published address to another document, and rewrites
that address's slug history and manifest to match, passes both. So the
registry is also held to the owners its base published:

- a ``current`` record's address is owned by that record (served 200);
- otherwise a slug history ``<directory>/history/<slug>.json`` names its owner
  (the 301 or 410 page of that slug);
- otherwise the one ``removed`` record on the address owns it.

The base is the parent of the migration commit (the oldest commit that added
``identity.json``), or ``HEAD`` while the migration is not committed yet. A
root migration commit has no published owners to keep.

A first holder is a historical owner too: a ``threshold-exception`` entry,
where the migration's U1 threshold kept a later holder over the document that
held the address first, is a reassignment of that first holder.

Both are refused unless listed, with both ``doc_id``s, in
``owner_reassignments.json``: the reviewed list of what the #871 migration of
lovverk changes, taken from the ``identity-plan`` report at lovverk
``c310127e`` (threshold ``f6d7e25bb``, ``SHA256SUMS`` sha256 ``955f53de``): its
``u1_renames`` and ``history_owner_mismatches`` (``reassignments``, against the
base) and its ``threshold_exceptions``. A listed change whose ``doc_id``s differ
from what the corpus shows is no exception.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from lovspor.errors import IdentityMigrationError, IdentitySchemaError
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
from lovspor.identity.registry import (
    DIRECTORY_OF_DATASET,
    THRESHOLD_EXCEPTION,
    SlugRegistry,
    record_address,
)
from lovspor.identity.schema import IDENTITY_FILENAME
from lovspor.storage.manifest import Manifest

REASSIGNMENTS_PATH = Path(__file__).with_name("owner_reassignments.json")
_LISTED = 5
Address = tuple[str, str]
#: ``address -> (owner at the base, owner in the registry)``.
Allowed = dict[Address, tuple[str, str]]


class OwnerReassignment(BaseModel):
    """One reviewed owner change of a published address."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    directory: str
    slug: str
    from_doc_id: str
    to_doc_id: str
    kind: Literal["u1-rename", "history-repair"]
    reason: str


class ThresholdException(BaseModel):
    """One reviewed ``threshold-exception`` entry: ``owner`` keeps an address ``first_holder``
    held first."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    directory: str
    slug: str
    first_holder: str
    owner: str
    reason: str


class ReassignmentList(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    reassignments: tuple[OwnerReassignment, ...]
    threshold_exceptions: tuple[ThresholdException, ...]


@dataclass(frozen=True)
class Reviewed:
    """The reviewed list: owner changes against the base, and threshold exceptions."""

    changes: Allowed
    threshold: Allowed


def _unique(pairs: list[tuple[Address, tuple[str, str]]], path: Path) -> Allowed:
    allowed = dict(pairs)
    if len(allowed) != len(pairs):
        raise IdentitySchemaError(f"{path}: an address is listed twice")
    return allowed


def load_reassignments(path: Path | None = None) -> Reviewed:
    """The reviewed list (``REASSIGNMENTS_PATH`` by default); refused when unreadable or
    when an address is listed twice."""
    path = REASSIGNMENTS_PATH if path is None else path
    try:
        listed = ReassignmentList.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise IdentitySchemaError(f"{path}: unreadable owner reassignment list: {exc}") from exc
    changes = [((r.directory, r.slug), (r.from_doc_id, r.to_doc_id)) for r in listed.reassignments]
    threshold = [
        ((t.directory, t.slug), (t.first_holder, t.owner)) for t in listed.threshold_exceptions
    ]
    return Reviewed(changes=_unique(changes, path), threshold=_unique(threshold, path))


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


def reassigned(owners: dict[Address, str], registry: SlugRegistry, allowed: Allowed) -> list[str]:
    """Every published address whose registry owner is another, and not as listed."""
    found = []
    for (directory, slug), held in sorted(owners.items()):
        owner = registry.owner_of(directory, slug)
        if owner != held and allowed.get((directory, slug)) != (held, owner):
            found.append(f"{directory}/{slug}: {held} at the base, {owner} in the registry")
    return found


def unlisted_threshold_exceptions(registry: SlugRegistry, threshold: Allowed) -> list[str]:
    """Every ``threshold-exception`` entry the reviewed list does not name as it stands."""
    found = []
    for directory, entries in sorted(registry.slugs.items()):
        for slug, entry in sorted(entries.items()):
            if entry.basis != THRESHOLD_EXCEPTION:
                continue
            first = entry.prior_holders[0].doc_id if entry.prior_holders else None
            if threshold.get((directory, slug)) != (first, entry.owner):
                found.append(f"{directory}/{slug}: {entry.owner} kept over first holder {first}")
    return found


def require_owner_continuity(repo: Path, registry: SlugRegistry, reviewed: Reviewed) -> None:
    """Refuse ``registry`` when it re-owns an address the migration's base published, or
    keeps a later holder over a first holder, unless the reviewed list names the change."""
    try:
        base = migration_base(repo)
        owners = {} if base is None else published_owners(repo.resolve(), base)
    except IdentityMigrationError as exc:
        raise IdentitySchemaError(f"{repo}: the migration's base cannot be read: {exc}") from exc
    found = reassigned(owners, registry, reviewed.changes)
    found += unlisted_threshold_exceptions(registry, reviewed.threshold)
    if found:
        raise IdentitySchemaError(
            f"{repo}: the registry changes {len(found)} historical owner(s), first "
            f"{found[:_LISTED]}: an owner the corpus published, and a first holder, is kept "
            f"unless the change is listed in {REASSIGNMENTS_PATH.name} (#911)",
        )
