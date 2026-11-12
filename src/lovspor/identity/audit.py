"""The owner's review tables of the identity-migration plan (#871).

Two tables answer "what happens to this address, and why": the fate of every
reused slug (each holder in order with its manifest evidence, its role under
the U1 threshold and whether it moves), and an audit of every restored
tombstone that shares its address with a document at the base.
"""

from collections import Counter

from pydantic import BaseModel, ConfigDict

from lovspor.identity.migration import Rename, RestoredTombstone
from lovspor.identity.registry import SlugRegistry, record_address
from lovspor.identity.walk import HolderSpan, ManifestWalk
from lovspor.storage.manifest import Manifest

PRE = "pre-threshold"
POST = "post-threshold"
SAME = "same-revision"


class PlanFacts(BaseModel):
    """What the review tables are computed from."""

    model_config = ConfigDict(frozen=True)

    walk: ManifestWalk
    registry: SlugRegistry
    planned: Manifest
    restored: list[RestoredTombstone]
    renames: list[Rename]
    threshold: int


class HolderFate(BaseModel):
    model_config = ConfigDict(frozen=True)

    doc_id: str
    first_commit: str
    last_commit: str
    role: str
    status: str
    restored: bool
    renamed_to: str | None
    slug_after_plan: str


class SlugFate(BaseModel):
    """One reused address: its owner after the plan and every holder in order."""

    model_config = ConfigDict(frozen=True)

    directory: str
    slug: str
    owner: str
    reuse: list[str]
    holders: list[HolderFate]


class CollisionAudit(BaseModel):
    """A restored tombstone on an address the base gives another document."""

    model_config = ConfigDict(frozen=True)

    directory: str
    slug: str
    restored_doc_id: str
    last_seen_commit: str
    missing_since_commit: str
    holder_at_base: str
    holder_status: str
    owner: str | None
    owning_side: str
    outcome: str


def _role(span: HolderSpan, owner: str, owner_index: int, index: int) -> str:
    if span.doc_id == owner:
        return "owner"
    return "prior" if index < owner_index else "later"


def _holder_fates(facts: PlanFacts, spans: list[HolderSpan], owner: str) -> list[HolderFate]:
    owner_index = next(i for i, span in enumerate(spans) if span.doc_id == owner)
    restored = {tomb.doc_id for tomb in facts.restored}
    moved = {rename.doc_id: rename.to_slug for rename in facts.renames}
    return [
        HolderFate(
            doc_id=span.doc_id,
            first_commit=span.first_commit,
            last_commit=span.last_commit,
            role=_role(span, owner, owner_index, index),
            status=facts.planned.documents[span.doc_id].status,
            restored=span.doc_id in restored,
            renamed_to=moved.get(span.doc_id),
            slug_after_plan=record_address(facts.planned.documents[span.doc_id])[1],
        )
        for index, span in enumerate(spans)
    ]


def _reuse_kinds(facts: PlanFacts, spans: list[HolderSpan], fates: list[HolderFate]) -> list[str]:
    """Which reuses an address saw: before the owner, after the threshold, or with it."""
    kinds: set[str] = set()
    for span, fate in zip(spans, fates, strict=True):
        if fate.role == "prior":
            kinds.add(PRE)
        elif fate.role == "later":
            after = facts.walk.position(span.first_commit) > facts.threshold
            kinds.add(POST if after else SAME)
    return sorted(kinds)


def slug_fates(facts: PlanFacts) -> list[SlugFate]:
    """The fate of every address more than one document held, sorted by address."""
    fates: list[SlugFate] = []
    for directory, by_slug in sorted(facts.walk.holders.items()):
        for slug, spans in sorted(by_slug.items()):
            owner = facts.registry.owner_of(directory, slug)
            if owner is None or len(spans) < 2:  # noqa: PLR2004
                continue
            holders = _holder_fates(facts, spans, owner)
            reuse = _reuse_kinds(facts, spans, holders)
            fates.append(
                SlugFate(directory=directory, slug=slug, owner=owner, reuse=reuse, holders=holders)
            )
    return fates


def _outcome(audit: CollisionAudit, renamed_to: str | None, recorded: bool) -> str:
    """What happens at the address, in one sentence."""
    if renamed_to is not None:
        return f"the holder moves to {renamed_to}; the slug stays with {audit.owner}"
    if audit.owning_side == "holder" and recorded:
        return "the holder keeps the slug; the restored document is recorded as a prior holder"
    if audit.owning_side == "holder":
        return "the holder keeps the slug; the restored document held it later, a tombstone"
    if audit.holder_status == "removed":
        return f"no file moves: both are tombstones; the slug stays with {audit.owner}"
    return f"the current holder stays; the slug stays with {audit.owner}"


def _audit_one(
    facts: PlanFacts, tomb: RestoredTombstone, holder: str, base: Manifest
) -> CollisionAudit:
    entry = facts.registry.slugs.get(tomb.directory, {}).get(tomb.slug)
    owner = None if entry is None else entry.owner
    audit = CollisionAudit(
        directory=tomb.directory,
        slug=tomb.slug,
        restored_doc_id=tomb.doc_id,
        last_seen_commit=tomb.last_seen_commit,
        missing_since_commit=tomb.missing_since_commit,
        holder_at_base=holder,
        holder_status=base.documents[holder].status,
        owner=owner,
        owning_side={tomb.doc_id: "restored", holder: "holder"}.get(owner or "", "other"),
        outcome="",
    )
    moved = {rename.doc_id: rename.to_slug for rename in facts.renames}
    recorded = entry is not None and tomb.doc_id in {p.doc_id for p in entry.prior_holders}
    return audit.model_copy(update={"outcome": _outcome(audit, moved.get(holder), recorded)})


def collision_audit(facts: PlanFacts, base: Manifest) -> list[CollisionAudit]:
    """Every restored tombstone whose address the base gives another document."""
    by_address: dict[tuple[str, str], list[str]] = {}
    for doc_id in sorted(base.documents):
        by_address.setdefault(record_address(base.documents[doc_id]), []).append(doc_id)
    return [
        _audit_one(facts, tomb, holder, base)
        for tomb in facts.restored
        for holder in by_address.get((tomb.directory, tomb.slug), [])
    ]


def outcome_counts(audits: list[CollisionAudit]) -> dict[str, int]:
    """Collisions per owning side, and how many move a current document."""
    counts = Counter(f"owned_by_{audit.owning_side}" for audit in audits)
    counts["holder_moves"] = sum(1 for audit in audits if "moves to" in audit.outcome)
    return dict(sorted(counts.items()))
