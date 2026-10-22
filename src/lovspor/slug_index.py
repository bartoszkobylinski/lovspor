"""The ``slug -> (doc_id, record)`` index both corpus readers share.

The live reader (``CorpusReader._load_slug_index``) and a historical
snapshot (``CorpusSnapshot.slug_index``) build their index here so a
current answer and a historical one never disagree about which record a
slug names for any reason other than the state itself.

A slug that more than one current record claims is *ambiguous*: it is left
out of the unique map and kept, with every candidate, in ``ambiguous``.
Looking it up through :meth:`SlugIndex.resolve` raises
:class:`~lovspor.errors.AmbiguousSlugError` naming the candidates (issue
#243). The old first-wins rule served one record and hid the other with
no signal that a second one existed.

**Short names (issue #585).** A slug carries Lovdata's kortform and its
abbreviation (``folketrygdloven-ftrl``), but a person — and a model —
writes ``folketrygdloven § 8-18`` or ``ftrl. § 8-18``. :func:`slugs_named`
resolves an act's short name when it is exact: the kortform without the
abbreviation, or the abbreviation written with its dot. It is never a
prefix or a near miss (``folketrygd`` names nothing), and a name that
several slugs carry (``skatteloven-sktl`` beside ``skatteloven-2``) is
ambiguous: the callers refuse it with the candidates instead of guessing.
"""

import re
from collections.abc import Callable, Collection, Iterable, Mapping

from lovspor.errors import AmbiguousSlugError, CorpusNotFoundError
from lovspor.storage.manifest import ManifestRecord

SlugEntry = tuple[str, ManifestRecord]

_MIN_SHORT_NAME_CHARS = 4
_MIN_ABBREVIATION_CHARS = 3
# A slug-shaped token with the dot an abbreviation is written with (``ftrl.``).
_NAME_TOKEN = re.compile(r"[a-z0-9æøåäöü-]+\.?")


class SlugIndex(dict[str, SlugEntry]):
    """``slug -> (doc_id, record)`` for every uniquely claimed current slug.

    A plain dict for the consumers that iterate the namespace; the
    collisions live beside it in ``ambiguous``, in manifest order.
    """

    def __init__(self) -> None:
        super().__init__()
        self.ambiguous: dict[str, list[SlugEntry]] = {}

    def resolve(self, slug: str) -> SlugEntry | None:
        """The unique entry for ``slug``, ``None`` when no current record
        claims it, :class:`AmbiguousSlugError` when several do."""
        entry = self.get(slug)
        if entry is None and slug in self.ambiguous:
            raise AmbiguousSlugError(_ambiguity_message(slug, self.ambiguous[slug]))
        return entry


def build_slug_index(documents: Mapping[str, ManifestRecord]) -> SlugIndex:
    """Index the current, slugged records of one manifest."""
    index = SlugIndex()
    claims: dict[str, list[SlugEntry]] = {}
    for doc_id, record in documents.items():
        if record.status != "current" or record.slug is None:
            continue
        claims.setdefault(record.slug, []).append((doc_id, record))
    for slug, entries in claims.items():
        if len(entries) == 1:
            index[slug] = entries[0]
        else:
            index.ambiguous[slug] = entries
    return index


def _ambiguity_message(slug: str, entries: list[SlugEntry]) -> str:
    # No MCP tool takes a doc_id (checked 2026-09-23), so the only honest
    # pointer is the tool that shows both records side by side.
    candidates = ", ".join(f"{doc_id} ({record.doc_type})" for doc_id, record in entries)
    return (
        f"slug {slug!r} names {len(entries)} current documents: {candidates}; "
        f"no tool accepts a doc_id yet, so none of them can be fetched by slug — "
        f"search_laws lists them all with their doc_id and dataset"
    )


def slugs_named(slugs: Iterable[str], name: str) -> list[str]:
    """The slugs an exact short name identifies (#585), in namespace order.

    ``name`` is the slug itself, the kortform without Lovdata's abbreviation
    (``folketrygdloven`` names ``folketrygdloven-ftrl``), or the abbreviation
    written with its dot (``ftrl.``). Short tokens are words, not names.
    """
    key = name.lower().rstrip(".")
    abbreviated = name.endswith(".")
    if len(key) < (_MIN_ABBREVIATION_CHARS if abbreviated else _MIN_SHORT_NAME_CHARS):
        return []
    named = [slug for slug in slugs if slug == key or slug.rpartition("-")[0] == key]
    if named or not abbreviated:
        return named
    return [slug for slug in slugs if "-" in slug and slug.rpartition("-")[2] == key]


def short_name_entry(index: SlugIndex, name: str) -> SlugEntry | None:
    """The one entry an exact short name identifies; ``None`` when none does.

    Several is a refusal naming them all: a short name is resolved only when
    it cannot pick the wrong act.
    """
    named = slugs_named(index, name)
    if len(named) > 1:
        raise CorpusNotFoundError(
            f"short name {name!r} names {len(named)} laws: {', '.join(named)}; "
            f"cite the canonical slug"
        )
    return index[named[0]] if named else None


def cited_acts(
    slugs: Collection[str], citation_lower: str, token_in: Callable[[str, str], bool]
) -> tuple[list[str], str | None]:
    """The acts a citation names: its exact slug tokens, else its exact short names.

    ``token_in(slug, citation_lower)`` is the caller's slug-token test. The
    second element is the refusal when short names name more than one act;
    the first is then empty.
    """
    candidates = [slug for slug in slugs if token_in(slug, citation_lower)]
    if candidates:
        return candidates, None
    named = _short_names_in(slugs, citation_lower)
    acts = list(dict.fromkeys(slug for found in named.values() for slug in found))
    if len(acts) > 1:
        return [], _short_name_ambiguity(named)
    return acts, None


def _short_names_in(slugs: Collection[str], citation_lower: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for token in _NAME_TOKEN.findall(citation_lower):
        named = slugs_named(slugs, token)
        if named and token not in found:
            found[token] = named
    return found


def _short_name_ambiguity(named: dict[str, list[str]]) -> str:
    if len(named) == 1:
        ((token, slugs),) = named.items()
        return (
            f"ambiguous citation: {token!r} names {len(slugs)} acts: {', '.join(slugs)}; "
            f"cite the canonical slug"
        )
    parts = "; ".join(f"{token!r} names {', '.join(slugs)}" for token, slugs in named.items())
    return f"ambiguous citation: {parts}; cite the canonical slug"
