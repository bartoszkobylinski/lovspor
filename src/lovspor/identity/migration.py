"""Plan the one-off identity repair of a lovverk corpus (#871, migration plan §3).

``plan_migration`` reads a corpus at a base commit through git only and
computes, without writing anything:

1. **Record recovery.** Every ``doc_id`` some manifest revision held and the
   base manifest does not gets its last-seen record back as a tombstone
   (``status: removed``), its provenance in ``removed_reason``: the commit it
   was last seen in and the first commit it was missing from.
2. **Registry.** Each address's owner is decided by the U1 threshold, a
   manifest revision the owner names (lovverk ``f6d7e25bb`` for #871): a
   reuse at or before the threshold is history the public URL already
   follows, so the owner is the document that took the address last at or
   before the threshold, and every earlier holder is recorded on the entry
   as a prior holder with its manifest evidence. An address nobody held by
   the threshold is owned by its first holder; a takeover after the threshold
   never changes the owner (U1). An existing ``slugs.json`` at the base is
   kept as it is and only extended.
3. **U1 renames.** A current document on an address another document owns
   gets a new slug from the existing collision rule (``resolve_collisions``):
   only later holders that took the address after the threshold.
4. **Collisions.** A restored tombstone whose address the base gives another
   document is listed.

The plan also lists the fate of every reused address (``audit``), every
``history/<slug>.json`` at the base whose ``doc_id`` is not the address's
owner (the history rebuild this plan does not perform), and every
``doc_id`` named by a history file that no manifest revision ever held: the
one fact the walk cannot restore, which blocks the plan.
"""

from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict

from lovspor.identity.registry import PriorHolder, SlugOwner, SlugRegistry, record_address
from lovspor.identity.walk import HolderSpan, ManifestWalk
from lovspor.rendering.slug import identity_candidates, resolve_collisions
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


class Rename(BaseModel):
    model_config = ConfigDict(frozen=True)

    doc_id: str
    directory: str
    from_slug: str
    to_slug: str
    suffix_note: str | None = None


class RegistryConflict(BaseModel):
    model_config = ConfigDict(frozen=True)

    directory: str
    slug: str
    registered_owner: str
    planned_owner: str


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


def threshold_owner(spans: list[HolderSpan], walk: ManifestWalk, threshold: int) -> HolderSpan:
    """The holder that owns an address under the U1 threshold (a walk position).

    The document that took the address last at or before the threshold; of
    several taking it in one revision, the first in walk order (smallest
    ``doc_id``). With no holder by then, the first holder. ``threshold`` -1
    means no reuse is grandfathered: the first holder always owns.
    """
    taken = [span for span in spans if walk.position(span.first_commit) <= threshold]
    if not taken:
        return spans[0]
    latest = max(walk.position(span.first_commit) for span in taken)
    return next(span for span in taken if walk.position(span.first_commit) == latest)


def owner_entry(spans: list[HolderSpan], walk: ManifestWalk, threshold: int) -> SlugOwner:
    """The registry entry of one address: its owner and every holder before it."""
    owner = threshold_owner(spans, walk, threshold)
    prior = spans[: spans.index(owner)]
    return SlugOwner(
        owner=owner.doc_id,
        assigned_at=owner.assigned_at,
        prior_holders=tuple(
            PriorHolder(doc_id=s.doc_id, first_commit=s.first_commit, last_commit=s.last_commit)
            for s in prior
        ),
    )


def build_registry(
    walk: ManifestWalk, existing: SlugRegistry | None, threshold: int
) -> tuple[SlugRegistry, list[RegistryConflict]]:
    """``existing`` (or an empty registry) extended with each address's owner entry.

    An existing entry is never changed; one whose owner differs from the
    owner this plan computes is returned as a conflict for review.
    """
    registry = existing or SlugRegistry()
    conflicts: list[RegistryConflict] = []
    for directory, by_slug in sorted(walk.holders.items()):
        for slug, spans in sorted(by_slug.items()):
            entry = owner_entry(spans, walk, threshold)
            held = registry.owner_of(directory, slug)
            if held is None:
                registry = registry.assign(directory, slug, entry)
            elif held != entry.owner:
                conflicts.append(
                    RegistryConflict(
                        directory=directory,
                        slug=slug,
                        registered_owner=held,
                        planned_owner=entry.owner,
                    )
                )
    return registry, conflicts


def suffix_note(registry: SlugRegistry, directory: str, rename: Rename) -> str | None:
    """Why a rename fell back to the bare ``-<doc_id>`` suffix; ``None`` when it did not.

    The collision rule prefers a readable suffix (``-<year>``, then
    ``-<yyyy-mm-dd>``) and uses the ``doc_id`` only when both are taken.
    """
    candidates = identity_candidates(rename.from_slug, rename.doc_id)
    if rename.to_slug != candidates[-1]:
        return None
    taken = [
        f"{slug} (owned by {registry.owner_of(directory, slug) or 'nobody; held by a sibling'})"
        for slug in candidates[:-1]
    ]
    return "readable suffixes unavailable: " + ", ".join(taken)


def _current_slugs(base: Manifest) -> dict[str, dict[str, str]]:
    """``directory -> doc_id -> slug`` of the base's current documents."""
    by_directory: dict[str, dict[str, str]] = {}
    for doc_id, record in base.documents.items():
        if record.status == "current":
            directory, slug = record_address(record)
            by_directory.setdefault(directory, {})[doc_id] = slug
    return by_directory


def u1_renames(base: Manifest, registry: SlugRegistry) -> list[Rename]:
    """Current documents on an address they do not own, each with its new slug.

    The whole current set of a directory goes through ``resolve_collisions``
    with the registry as owner map, exactly as a sync would: a document that
    owns its slug keeps it, any other is deflected by the collision rule.
    """
    renames: list[Rename] = []
    for directory, slugs in sorted(_current_slugs(base).items()):
        resolved = resolve_collisions(slugs, registry.ownership(directory))
        for doc_id in sorted(d for d in slugs if resolved[d] != slugs[d]):
            rename = Rename(
                doc_id=doc_id,
                directory=directory,
                from_slug=slugs[doc_id],
                to_slug=resolved[doc_id],
            )
            note = suffix_note(registry, directory, rename)
            renames.append(rename.model_copy(update={"suffix_note": note}))
    return renames


def renamed_record(record: ManifestRecord, rename: Rename) -> ManifestRecord:
    """``record`` moved to ``rename.to_slug`` in the same directory."""
    path = str(PurePosixPath(record.markdown_path).with_name(f"{rename.to_slug}.md"))
    return record.model_copy(update={"slug": rename.to_slug, "markdown_path": path})
