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

Slug collisions across the same dataset are rare but possible. They
are resolved by ``resolve_collisions`` deterministically: docs are
sorted by ``doc_id``, the first occurrence keeps the bare slug,
subsequent occurrences get ``-2``, ``-3``, …
"""

import re

_BRACKET_CONTENT = re.compile(r"[\[(].*?[\])]")
_NON_SLUG_CHAR = re.compile(r"[^a-z0-9æøåäöü]+")
_HYPHEN_RUN = re.compile(r"-+")


def derive_slug(short_title: str | None, title: str, doc_id: str) -> str:
    """Compute the base slug for a single document.

    The result is non-empty: if every preferred source slugifies to the
    empty string (e.g., title is only punctuation), ``doc_id`` is the
    final fallback so the slug is always usable as a filename.
    """
    candidate = short_title or _strip_brackets(title) or doc_id
    return _slugify(candidate.strip()) or doc_id


def resolve_collisions(slugs_by_doc: dict[str, str]) -> dict[str, str]:
    """Disambiguate duplicate slugs by appending ``-2``, ``-3``, ….

    Sort key is ``doc_id`` so the assignment is deterministic regardless
    of the input dict's iteration order: the smallest ``doc_id`` keeps
    the bare slug, the next gets ``-2``, and so on.
    """
    counts: dict[str, int] = {}
    resolved: dict[str, str] = {}
    for doc_id in sorted(slugs_by_doc):
        slug = slugs_by_doc[doc_id]
        counts[slug] = counts.get(slug, 0) + 1
        resolved[doc_id] = slug if counts[slug] == 1 else f"{slug}-{counts[slug]}"
    return resolved


def _strip_brackets(text: str) -> str:
    return _BRACKET_CONTENT.sub("", text).strip()


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = _NON_SLUG_CHAR.sub("-", text)
    text = _HYPHEN_RUN.sub("-", text)
    return text.strip("-")
