"""Classify upstream documents against the prior manifest.

This is a pure function: given the current set of ``doc_id -> xml_hash``
pairs from upstream and the manifest from the previous run, return four
disjoint sorted lists of document IDs covering every classification:

- ``new``       — present upstream, absent from the manifest's current set
- ``changed``   — present in both, hashes differ
- ``removed``   — current in manifest, absent from upstream
- ``unchanged`` — present in both, hashes match

A document with manifest ``status="removed"`` is treated as not present
in the manifest's current set; if it reappears upstream it is classified
as ``new``, not as a hash diff against the stale removed-snapshot hash.
"""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from lovspor.storage.manifest import Manifest


class ChangeSet(BaseModel):
    """Disjoint partition of documents by sync action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    new: list[str]
    changed: list[str]
    removed: list[str]
    unchanged: list[str]


def detect_changes(
    upstream: Mapping[str, str],
    manifest: Manifest,
) -> ChangeSet:
    """Compare upstream hashes against the prior manifest.

    All output lists are sorted alphabetically by ``doc_id`` for
    deterministic serialization and reproducible test assertions.
    """
    upstream_ids = set(upstream)
    manifest_current = {
        doc_id: rec for doc_id, rec in manifest.documents.items() if rec.status == "current"
    }
    manifest_ids = set(manifest_current)

    new_ids = sorted(upstream_ids - manifest_ids)
    removed_ids = sorted(manifest_ids - upstream_ids)
    changed_ids: list[str] = []
    unchanged_ids: list[str] = []
    for doc_id in sorted(upstream_ids & manifest_ids):
        if upstream[doc_id] != manifest_current[doc_id].xml_hash:
            changed_ids.append(doc_id)
        else:
            unchanged_ids.append(doc_id)

    return ChangeSet(
        new=new_ids,
        changed=changed_ids,
        removed=removed_ids,
        unchanged=unchanged_ids,
    )
