"""Slug derivation for human-readable Markdown filenames.

A slug is the filesystem and URL representation of a legal document
chosen so that browsing the corpus on GitHub or in a file manager
shows ``skatteloven.md`` rather than the opaque ``nl-19990326-014.md``.

Preference chain for the source string:

    short_title                — Lovdata's official kortform (``Skatteloven``)
        ↓ if missing
    title minus bracket content  — strip ``(opplæringslova)`` etc.
        ↓ if both missing
    doc_id                     — last-resort defensive fallback

After choosing the source, ``_slugify`` lowercases, replaces non-slug
characters with hyphens, and collapses runs of hyphens. Norwegian
``æøå`` and the German ``äöü`` are preserved — modern URL handling
and GitHub UI both render them correctly, and Norwegian readers
expect them.

The result is then capped at ``_MAX_SLUG_BYTES`` UTF-8 bytes so the
final filename (slug + ``.md`` + collision suffix) stays under POSIX
NAME_MAX (255 bytes). EU implementation forskrifter sometimes have
250-500 character titles with no ``short_title``; without a cap the
slug overflows and sync crashes with ``OSError: File name too long``
on Linux/macOS filesystems (observed in production 2026-04-27).
Truncation prefers a hyphen boundary when one exists in the byte-
truncated prefix; for the theoretical case of a single token longer
than 200 bytes with no internal hyphen (not observed in real Lovdata
data), the raw byte-truncated form is used.

Slug collisions across the same dataset are rare but possible (and
become more likely once truncation is in play). They are resolved by
``resolve_collisions`` deterministically: docs are sorted by
``doc_id``, the first occurrence keeps the bare slug, subsequent
occurrences get ``-2``, ``-3``, …

**Permanent slug ownership (ADR-0003 identity guarantees).** Every slug
the corpus has ever published is permanently reserved for the doc_id
that first published it — current AND tombstoned records alike; a
tombstone never releases its slug. ``resolve_collisions`` accepts the
prior manifest's ownership map so a *different* doc_id whose preferred
slug is already owned gets a deterministic disambiguated slug derived
from its own immutable identity (see ``_identity_candidates``) instead
of silently overwriting the owner's file and fusing two documents'
git histories under one path (root-cause analysis 2026-07-30, defect
3: ``forskrift-om-omregningsfaktorer``). ``evaluate_slug_ownership``
recomputes ownership over a complete manifest and reports which
records would change path under this rule — the dry-run companion.
"""

import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Protocol

from pydantic import BaseModel, ConfigDict

_BRACKET_CONTENT = re.compile(r"[\[(].*?[\])]")
_NON_SLUG_CHAR = re.compile(r"[^a-z0-9æøåäöü]+")
_HYPHEN_RUN = re.compile(r"-+")

_MAX_SLUG_BYTES = 200
"""UTF-8 byte cap on slugs.

POSIX NAME_MAX is 255. We reserve 55 bytes of headroom for the ``.md``
extension (3 bytes), a collision suffix like ``-99`` (3 bytes), an
ownership-disambiguation suffix like ``-2026-07-10`` or the doc-id
fallback ``-sf-20260710-1545`` (max ~20 bytes), and comfortable margin
for any future filename-suffix conventions.
"""

_DOC_ID_REF_DATE = re.compile(r"^[a-z]+-(\d{4})(\d{2})(\d{2})-\d+$")
"""Doc ids embed the act's upstream ref date.

``sf-20260710-1545`` is Lovdata's ``forskrift/2026-07-10-1545``: prefix,
ref date, serial. The date is part of the document's immutable identity,
which makes it the preferred source for ownership-disambiguation
suffixes — stable across syncs and independent of processing order.
"""


def derive_slug(short_title: str | None, title: str, doc_id: str) -> str:
    """Compute the base slug for a single document.

    The result is non-empty: if every preferred source slugifies to the
    empty string (e.g., title is only punctuation), ``doc_id`` is the
    final fallback so the slug is always usable as a filename.

    The result is also length-capped at ``_MAX_SLUG_BYTES`` UTF-8 bytes.
    """
    candidate = short_title or _strip_brackets(title) or doc_id
    slug = _slugify(candidate.strip()) or doc_id
    return _cap_length(slug) or doc_id


def resolve_collisions(
    slugs_by_doc: dict[str, str],
    prior_owner: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Disambiguate duplicate slugs, honouring permanent prior ownership.

    ``prior_owner`` maps every slug the prior manifest has ever published
    (current AND removed records — a tombstone never releases its slug)
    to its owning ``doc_id``. Ownership is permanent (ADR-0003): a doc
    whose preferred slug is owned by a *different* doc_id is deflected by
    ``_claim_slug`` — first to its own prior slug (same-id stability),
    then to an identity-derived suffix (see ``_identity_candidates``) —
    before sibling collisions are resolved. Passing ``None`` (or ``{}``)
    reproduces the historical intra-tarball behaviour exactly.

    The result is a pure function of the inputs: per-doc claims consult
    only ``prior_owner``, and the sibling pass sorts docs — owners of
    their claimed slug first (an owner can never be suffix-shuffled off
    its own slug by a lexically smaller sibling), then by ``doc_id`` —
    so iteration order of the input dicts is irrelevant. Among genuinely
    new sibling collisions the smallest ``doc_id`` keeps the bare slug
    and the next gets ``-2``, ``-3``, … as before. Generated suffixes
    skip natural base slugs (a duplicate ``foo`` cannot overwrite a doc
    whose own slug is already ``foo-2``) and prior-owned slugs alike.
    """
    owner = dict(prior_owner or {})
    prior_slug_of = {owner_id: slug for slug, owner_id in sorted(owner.items())}
    claimed = {
        doc_id: _claim_slug(slug, doc_id, owner, prior_slug_of.get(doc_id))
        for doc_id, slug in slugs_by_doc.items()
    }
    natural_slugs = set(claimed.values())
    taken: set[str] = set()
    resolved: dict[str, str] = {}
    for doc_id in sorted(claimed, key=lambda d: (owner.get(claimed[d]) != d, d)):
        slug = claimed[doc_id]
        final_slug = slug
        suffix = 1
        while (
            final_slug in taken
            or (final_slug in natural_slugs and final_slug != slug)
            or owner.get(final_slug, doc_id) != doc_id
        ):
            suffix += 1
            final_slug = f"{slug}-{suffix}"
        taken.add(final_slug)
        resolved[doc_id] = final_slug
    return resolved


def _claim_slug(
    preferred: str,
    doc_id: str,
    owner: Mapping[str, str],
    own_prior_slug: str | None,
) -> str:
    """First candidate slug not permanently owned by a different doc_id.

    Candidate order: the preferred slug (kept when unowned or owned by
    this very doc_id — tombstones and resurrections included), then the
    doc's own prior slug (same-id stability: a doc deflected from its
    preferred slug keeps the slug it already publishes rather than
    inventing a new one), then the identity-derived suffixes. The last
    identity candidate embeds ``doc_id`` itself, so the chain always
    terminates deterministically.
    """
    fallback = _identity_candidates(preferred, doc_id)
    prior: tuple[str, ...] = () if own_prior_slug is None else (own_prior_slug,)
    for candidate in (preferred, *prior, *fallback):
        if owner.get(candidate, doc_id) == doc_id:
            return candidate
    return fallback[-1]


def _identity_candidates(slug: str, doc_id: str) -> tuple[str, ...]:
    """Deterministic disambiguation suffixes from the doc's own identity.

    Preference order, all derived from the immutable ref date embedded
    in ``doc_id`` (same inputs -> same slugs, independent of processing
    order): the ref-date year (``forskrift-om-omregningsfaktorer-2026``),
    the full ref date (``…-2026-07-10``), and finally the doc-id suffix
    (``…-sf-20260710-1545``). Ids without an embedded date fall straight
    to the doc-id suffix.
    """
    match = _DOC_ID_REF_DATE.match(doc_id)
    if match is None:
        return (f"{slug}-{doc_id}",)
    year, month, day = match.groups()
    return (
        f"{slug}-{year}",
        f"{slug}-{year}-{month}-{day}",
        f"{slug}-{doc_id}",
    )


class SlugPathRecord(Protocol):
    """Structural view of a manifest record: only the path is needed."""

    @property
    def markdown_path(self) -> str: ...


class SlugOwnershipChange(BaseModel):
    """One record whose path would change under permanent slug ownership."""

    model_config = ConfigDict(frozen=True)

    doc_id: str
    owner_doc_id: str
    slug: str
    new_slug: str
    markdown_path: str
    new_markdown_path: str


def evaluate_slug_ownership(
    manifest_documents: Mapping[str, SlugPathRecord],
) -> tuple[SlugOwnershipChange, ...]:
    """Report which manifest records would change path under ownership.

    Pure function, no I/O: recomputes preferred ownership across the
    complete manifest — current AND removed records — and returns the
    records that occupy a ``markdown_path`` owned by a different doc_id.
    For each conflicted path the owner is the smallest claimant
    ``doc_id``: ids embed the upstream ref date, so the smallest id is
    the earliest act — the original publisher — and in every observed
    takeover the later act is the usurper. Every other claimant is
    assigned its first free ``_identity_candidates`` slug, checked
    against every path the manifest already reserves. Deterministic and
    independent of the input mapping's iteration order.

    This powers the dry-run report mandated by the corpus-integrity
    root-cause analysis (2026-07-30, defect 3).
    """
    claims: dict[str, list[str]] = {}
    for doc_id, record in manifest_documents.items():
        claims.setdefault(record.markdown_path, []).append(doc_id)
    reserved = set(claims)
    changes: list[SlugOwnershipChange] = []
    for path in sorted(path for path, ids in claims.items() if len(ids) > 1):
        changes.extend(_ownership_changes_for_path(path, sorted(claims[path]), reserved))
    return tuple(changes)


def _ownership_changes_for_path(
    path: str,
    claimants: list[str],
    reserved: set[str],
) -> list[SlugOwnershipChange]:
    """Reassign every non-owner claimant of ``path`` a free identity slug.

    ``claimants`` must be sorted; the first entry is the owner and keeps
    the path. ``reserved`` is mutated: each new path is added so later
    reassignments cannot land on it.
    """
    owner, *usurpers = claimants
    base_slug = PurePosixPath(path).stem
    changes: list[SlugOwnershipChange] = []
    for doc_id in usurpers:
        new_slug, new_path = _first_free_identity_slug(path, base_slug, doc_id, reserved)
        reserved.add(new_path)
        changes.append(
            SlugOwnershipChange(
                doc_id=doc_id,
                owner_doc_id=owner,
                slug=base_slug,
                new_slug=new_slug,
                markdown_path=path,
                new_markdown_path=new_path,
            ),
        )
    return changes


def _first_free_identity_slug(
    path: str,
    base_slug: str,
    doc_id: str,
    reserved: set[str],
) -> tuple[str, str]:
    """First identity candidate whose path is not already reserved.

    The final candidate embeds ``doc_id`` and is used unconditionally
    when everything before it is taken — it cannot collide with another
    document's path because doc ids are unique.
    """
    candidates = _identity_candidates(base_slug, doc_id)
    for slug in candidates:
        candidate_path = str(PurePosixPath(path).with_name(f"{slug}.md"))
        if candidate_path not in reserved:
            return slug, candidate_path
    last = candidates[-1]
    return last, str(PurePosixPath(path).with_name(f"{last}.md"))


def _strip_brackets(text: str) -> str:
    return _BRACKET_CONTENT.sub("", text).strip()


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = _NON_SLUG_CHAR.sub("-", text)
    text = _HYPHEN_RUN.sub("-", text)
    return text.strip("-")


def _cap_length(slug: str) -> str:
    """Cap ``slug`` at ``_MAX_SLUG_BYTES`` UTF-8 bytes.

    When the byte-truncated prefix contains a hyphen at position > 0,
    trim back to it so the filename ends at a word boundary. When no
    such hyphen exists (single long token — not observed in real
    Lovdata data but well-defined here), the raw byte-truncated form
    is returned as-is.

    Why bytes not characters: POSIX NAME_MAX is 255 *bytes* regardless
    of encoding. Norwegian ``æøå`` are 2 UTF-8 bytes each, so a
    char-based cap over-counts the safe budget and still risks overflow
    on heavily Unicode titles.
    """
    encoded = slug.encode("utf-8")
    if len(encoded) <= _MAX_SLUG_BYTES:
        return slug
    truncated = encoded[:_MAX_SLUG_BYTES].decode("utf-8", errors="ignore")
    last_hyphen = truncated.rfind("-")
    if last_hyphen > 0:
        truncated = truncated[:last_hyphen]
    return truncated.strip("-")
