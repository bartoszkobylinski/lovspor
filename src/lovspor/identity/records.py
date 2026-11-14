"""A manifest record never disappears (#871, ADR-0026 draft point 1, guard 5a).

The lovverk manifest lost records more than once (the 2026-07-02 sync
``f6d7e25bb`` dropped 76 of 77 tombstones); with no record a slug looked free
and was handed to a later document. ``replace_manifest`` is the one way the
sync writes a manifest: it reads the manifest it is about to replace and
refuses, before anything reaches disk, a write that would drop any of its
``doc_id``s.
"""

from collections.abc import Collection, Mapping
from pathlib import Path

from lovspor.errors import ManifestRecordLossError
from lovspor.storage.manifest import Manifest, ManifestRecord, read_manifest, write_manifest


def ensure_no_record_loss(
    prior_ids: Collection[str],
    documents: Mapping[str, ManifestRecord],
) -> None:
    """Raise unless every id in ``prior_ids`` keeps a record in ``documents``.

    A record may change status (``current`` and ``removed`` both count);
    only its absence is a loss.
    """
    missing = sorted(set(prior_ids) - set(documents))
    if missing:
        raise ManifestRecordLossError(
            f"refusing to write a manifest that drops {len(missing)} prior record(s): "
            f"{', '.join(missing)} — a record may change status but never disappear (#871)",
        )


def replace_manifest(manifest: Manifest, path: Path) -> None:
    """Write ``manifest`` to ``path`` unless it drops a record the file holds.

    The file on disk is the manifest being replaced; a first write (no file)
    has nothing to lose. An unreadable prior manifest raises ``ParseError``
    from ``read_manifest``: the guard fails closed rather than skip the check.
    """
    if path.exists():
        ensure_no_record_loss(read_manifest(path).documents.keys(), manifest.documents)
    write_manifest(manifest, path)
