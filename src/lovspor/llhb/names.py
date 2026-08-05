"""Act-name index: deterministic mapping from prose act names to slugs.

Model answers cite acts by name («arbeidsmiljøloven»), not by corpus
slug. The index is built from the pinned corpus manifest only — three
key sources per document, all derived mechanically:

1. the slug itself (``skatteloven-sktl``),
2. the full title, normalized,
3. single-token law-name parentheticals inside the title — Norwegian
   official titles routinely end with the short name in parentheses
   («Lov om skatt … (skatteloven)») and that short name is what prose
   uses.

Lookup and scanning are exact after normalization (NFKC + casefold +
whitespace collapse). No fuzzy matching: an act name the index does not
contain is an unknown act, which the scorer reports as unresolved
residue rather than guessing.
"""

import re
import unicodedata
from collections.abc import Iterable
from typing import Self

from pydantic import BaseModel

from lovspor.storage.manifest import Manifest

_LAW_NAME_SUFFIXES = ("loven", "lova", "forskriften", "forskrifta")
_PARENTHETICAL = re.compile(r"\(([^()]+)\)")


class ActNameEntry(BaseModel, frozen=True):
    """One document a normalized name key can refer to."""

    slug: str
    status: str  # "current" | "removed"


class ActMention(BaseModel, frozen=True):
    """A recognized act-name occurrence in answer text."""

    start: int
    end: int
    text: str
    key: str  # normalized index key that matched


def normalize_name(name: str) -> str:
    """NFKC + casefold + single-space collapse — the index's key form."""
    folded = unicodedata.normalize("NFKC", name).casefold()
    return " ".join(folded.split())


def _title_keys(title: str) -> list[str]:
    """Mechanical name keys of one title: itself + law-name parentheticals."""
    keys = [normalize_name(title)]
    for group in _PARENTHETICAL.findall(title):
        candidate = normalize_name(group)
        if " " not in candidate and candidate.endswith(_LAW_NAME_SUFFIXES):
            keys.append(candidate)
    return keys


class ActNameIndex:
    """Immutable name→documents index with a longest-match text scanner."""

    def __init__(self, entries: dict[str, list[ActNameEntry]]) -> None:
        self._entries = entries
        self._scanner = _compile_scanner(entries)

    @classmethod
    def from_manifest(cls, manifest: Manifest) -> Self:
        entries: dict[str, list[ActNameEntry]] = {}
        for record in manifest.documents.values():
            if record.slug is None:
                continue
            entry = ActNameEntry(slug=record.slug, status=record.status)
            keys = [normalize_name(record.slug)]
            if record.title:
                keys.extend(_title_keys(record.title))
            for key in dict.fromkeys(keys):
                bucket = entries.setdefault(key, [])
                if entry not in bucket:
                    bucket.append(entry)
        return cls(entries)

    @classmethod
    def from_pairs(cls, pairs: Iterable[tuple[str, str]]) -> Self:
        """Test/tooling convenience: explicit (name, slug) pairs, all current."""
        entries: dict[str, list[ActNameEntry]] = {}
        for name, slug in pairs:
            entry = ActNameEntry(slug=slug, status="current")
            bucket = entries.setdefault(normalize_name(name), [])
            if entry not in bucket:
                bucket.append(entry)
        return cls(entries)

    def lookup(self, name: str) -> list[ActNameEntry]:
        """All documents the normalized ``name`` can refer to (empty = unknown)."""
        return list(self._entries.get(normalize_name(name), ()))

    def scan(self, text: str) -> list[ActMention]:
        """Every act-name occurrence in ``text``, leftmost-longest, no overlap."""
        if self._scanner is None:
            return []
        mentions: list[ActMention] = []
        for match in self._scanner.finditer(text):
            mentions.append(
                ActMention(
                    start=match.start(),
                    end=match.end(),
                    text=match.group(0),
                    key=normalize_name(match.group(0)),
                ),
            )
        return mentions


def _compile_scanner(entries: dict[str, list[ActNameEntry]]) -> re.Pattern[str] | None:
    """Alternation over all keys, longest first, hyphen-aware word bounds.

    ``(?<![\\w-]) … (?![\\w-])`` keeps ``skatteloven`` from matching inside
    the longer token ``skatteloven-sktl``; longest-first ordering makes the
    regex engine prefer the longer key at the same start position.
    """
    if not entries:
        return None
    keys = sorted(entries, key=len, reverse=True)
    alternation = "|".join(re.escape(key) for key in keys)
    return re.compile(rf"(?<![\w-])(?:{alternation})(?![\w-])", re.IGNORECASE)
