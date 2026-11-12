"""Plan the one-off identity repair of a lovverk corpus (#871, migration plan §3).

``plan_migration`` reads a corpus at a base commit through git only and
computes, without writing anything:

1. **Record recovery.** Every ``doc_id`` some manifest revision held and the
   base manifest does not gets its last-seen record back as a tombstone
   (``status: removed``), its provenance in ``removed_reason``: the commit it
   was last seen in and the first commit it was missing from.
2. **Registry.** Each address's owner is its first holder in the walk; an
   existing ``slugs.json`` at the base is kept as it is and only extended.
3. **U1 renames.** A current document on an address another document owns
   gets a new slug from the existing collision rule (``resolve_collisions``).
4. **Collisions.** A restored tombstone whose address the base gives another
   document is listed.

The plan also lists every reused address (more than one holder), every
``history/<slug>.json`` at the base whose ``doc_id`` is not the address's
owner (the history rebuild this plan does not perform), and every
``doc_id`` named by a history file that no manifest revision ever held: the
one fact the walk cannot restore, which blocks the plan.
"""

from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict

from lovspor.identity.registry import SlugOwner, SlugRegistry, record_address
from lovspor.identity.walk import ManifestWalk
from lovspor.rendering.slug import resolve_collisions
from lovspor.storage.manifest import Manifest, ManifestRecord

RESTORED_REASON = "restored-tombstone (#871): last seen in {seen}, missing since {missing}"


class RestoredTombstone(BaseModel):
    model_config = ConfigDict(frozen=True)

    doc_id: str
    directory: str
    slug: str
    title: str | None
    last_status: str
    last_seen_commit: str
    missing_since_commit: str


class LaterHolder(BaseModel):
    model_config = ConfigDict(frozen=True)

    doc_id: str
    status: str
    restored: bool


class ReusedSlug(BaseModel):
    model_config = ConfigDict(frozen=True)

    directory: str
    slug: str
    owner: str
    owner_since_commit: str
    later_holders: list[LaterHolder]


class Rename(BaseModel):
    model_config = ConfigDict(frozen=True)

    doc_id: str
    directory: str
    from_slug: str
    to_slug: str


class Collision(BaseModel):
    model_config = ConfigDict(frozen=True)

    restored_doc_id: str
    directory: str
    slug: str
    holder_at_base: str
    holder_status: str


class RegistryConflict(BaseModel):
    model_config = ConfigDict(frozen=True)

    directory: str
    slug: str
    registered_owner: str
    first_holder: str


def restored_record(doc_id: str, walk: ManifestWalk, base_commit: str) -> ManifestRecord:
    """The tombstone of a document absent at the base, rebuilt from its last record.

    Keeps what ``_tombstone`` keeps (type, hash, path, dataset, last_seen,
    slug, title) and drops the rest; nothing is invented.
    """
    seen = walk.last_seen[doc_id]
    last = ManifestRecord.model_validate(seen.record)
    missing = seen.missing_since or base_commit
    return ManifestRecord(
        doc_type=last.doc_type,
        xml_hash=last.xml_hash,
        markdown_path=last.markdown_path,
        source_dataset=last.source_dataset,
        last_seen=last.last_seen,
        status="removed",
        slug=last.slug,
        title=last.title,
        removed_reason=RESTORED_REASON.format(seen=seen.commit, missing=missing),
    )


def restored_tombstones(
    walk: ManifestWalk, base: Manifest, base_commit: str
) -> list[RestoredTombstone]:
    """Every id the walk saw and the base lacks, sorted by ``doc_id``."""
    restored: list[RestoredTombstone] = []
    for doc_id in sorted(set(walk.last_seen) - set(base.documents)):
        record = restored_record(doc_id, walk, base_commit)
        directory, slug = record_address(record)
        restored.append(
            RestoredTombstone(
                doc_id=doc_id,
                directory=directory,
                slug=slug,
                title=record.title,
                last_status=str(walk.last_seen[doc_id].record.get("status")),
                last_seen_commit=walk.last_seen[doc_id].commit,
                missing_since_commit=walk.last_seen[doc_id].missing_since or base_commit,
            ),
        )
    return restored


def build_registry(
    walk: ManifestWalk, existing: SlugRegistry | None
) -> tuple[SlugRegistry, list[RegistryConflict]]:
    """``existing`` (or an empty registry) extended with each address's first holder.

    An existing entry is never changed; one whose owner differs from the
    walk's first holder is returned as a conflict for review.
    """
    registry = existing or SlugRegistry()
    conflicts: list[RegistryConflict] = []
    for directory, claims in sorted(walk.first_holder.items()):
        for slug, claim in sorted(claims.items()):
            held = registry.owner_of(directory, slug)
            if held is None:
                owner = SlugOwner(owner=claim.doc_id, assigned_at=claim.assigned_at)
                registry = registry.assign(directory, slug, owner)
            elif held != claim.doc_id:
                conflicts.append(
                    RegistryConflict(
                        directory=directory,
                        slug=slug,
                        registered_owner=held,
                        first_holder=claim.doc_id,
                    )
                )
    return registry, conflicts


def u1_renames(base: Manifest, registry: SlugRegistry) -> list[Rename]:
    """Current documents on an address they do not own, each with its new slug.

    The whole current set of a directory goes through ``resolve_collisions``
    with the registry as owner map, exactly as a sync would: a document that
    owns its slug keeps it, any other is deflected by the collision rule.
    """
    renames: list[Rename] = []
    by_directory: dict[str, dict[str, str]] = {}
    for doc_id, record in base.documents.items():
        if record.status == "current":
            directory, slug = record_address(record)
            by_directory.setdefault(directory, {})[doc_id] = slug
    for directory, slugs in sorted(by_directory.items()):
        resolved = resolve_collisions(slugs, registry.ownership(directory))
        for doc_id in sorted(slugs):
            if resolved[doc_id] != slugs[doc_id]:
                renames.append(
                    Rename(
                        doc_id=doc_id,
                        directory=directory,
                        from_slug=slugs[doc_id],
                        to_slug=resolved[doc_id],
                    )
                )
    return renames


def renamed_record(record: ManifestRecord, rename: Rename) -> ManifestRecord:
    """``record`` moved to ``rename.to_slug`` in the same directory."""
    path = str(PurePosixPath(record.markdown_path).with_name(f"{rename.to_slug}.md"))
    return record.model_copy(update={"slug": rename.to_slug, "markdown_path": path})
