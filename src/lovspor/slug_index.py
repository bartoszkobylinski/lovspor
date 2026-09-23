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
"""

from collections.abc import Mapping

from lovspor.errors import AmbiguousSlugError
from lovspor.storage.manifest import ManifestRecord

SlugEntry = tuple[str, ManifestRecord]


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
