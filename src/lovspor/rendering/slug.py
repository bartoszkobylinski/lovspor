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
"""

import re

_BRACKET_CONTENT = re.compile(r"[\[(].*?[\])]")
_NON_SLUG_CHAR = re.compile(r"[^a-z0-9æøåäöü]+")
_HYPHEN_RUN = re.compile(r"-+")

_MAX_SLUG_BYTES = 200
"""UTF-8 byte cap on slugs.

POSIX NAME_MAX is 255. We reserve 55 bytes of headroom for the ``.md``
extension (3 bytes), a collision suffix like ``-99`` (3 bytes), and
comfortable margin for any future filename-suffix conventions.
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
