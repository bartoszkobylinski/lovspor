"""Which act a rendered ``§`` link names, read from the link target (#1034).

The renderer keeps Lovdata's references as Markdown links:
``[§ 5-2](lov/1969-06-13-26/§5-2)``. The target names the act exactly, while
the prose around it usually names the act by date and number (``lov 13. juni
1969 nr. 26``) or by title, never by our slug. Resolving such a reference from
the slug tokens near it attributes it to the act being read, and a real
citation of another act is then reported as invalid.
"""

import re
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple

_ACT_LINK = re.compile(
    r"\[[^\]\n]*\]\((?P<ref>(?P<kind>lov|forskrift)/(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"
    r"(?:-(?P<nr>\d+))?)(?:/§(?P<section>[^/)\s]+))?(?:/[^)\s]*)?\)"
)
_PREFIX = {"lov": ("nl", 3), "forskrift": ("sf", 4)}


class KnownActs(set[str]):
    """The current slugs of one corpus state, plus ``doc_id -> slug`` for them.

    A set, so every consumer of the plain slug set keeps working; the doc_id
    map rides beside it the way ``SlugIndex.ambiguous`` rides beside its dict.
    """

    def __init__(self, slugs: Iterable[str], by_doc_id: Mapping[str, str]) -> None:
        super().__init__(slugs)
        self.by_doc_id = dict(by_doc_id)


def known_acts(index: Mapping[str, tuple[str, Any]], *extra: str) -> KnownActs:
    """``KnownActs`` from a ``slug -> (doc_id, record)`` index, plus ``extra`` slugs."""
    by_doc_id = {doc_id: slug for slug, (doc_id, _record) in index.items()}
    return KnownActs([*index, *extra], by_doc_id)


def doc_id_of(kind: str, ref: re.Match[str]) -> str:
    """``lov/1969-06-13-26`` -> ``nl-19690613-026``; an unnumbered act is ``-0``."""
    prefix, width = _PREFIX[kind]
    return f"{prefix}-{ref['y']}{ref['m']}{ref['d']}-{(ref['nr'] or '0').zfill(width)}"


class LinkedAct(NamedTuple):
    """The act a reference names, the section to check, and why it cannot be checked.

    ``slug`` is ``None`` when this state holds no current document for a link.
    """

    slug: str | None
    problem: str | None
    section: str


class ActLinks:
    """The act links of one section body, by character span."""

    def __init__(self, body: str, known: set[str]) -> None:
        """A plain slug set carries no doc_id map, so no link can be resolved: none is read."""
        by_doc_id = known.by_doc_id if isinstance(known, KnownActs) else None
        self._links = [
            (match.span(), match.start("ref") - 1, match)
            for match in (_ACT_LINK.finditer(body) if by_doc_id is not None else ())
        ]
        self._by_doc_id = by_doc_id or {}

    def in_target(self, position: int) -> bool:
        """True inside a link's ``(...)`` target: a URL, not a reference in the text."""
        return any(target <= position < end for (_start, end), target, _link in self._links)

    def act_at(self, position: int, section_id: str) -> LinkedAct | None:
        """The act named by the link whose text holds ``position``, if any."""
        for (start, _end), target, link in self._links:
            if start <= position < target:
                return _linked(link, section_id, self._by_doc_id)
        return None


def _linked(link: re.Match[str], section_id: str, by_doc_id: Mapping[str, str]) -> LinkedAct:
    slug = by_doc_id.get(doc_id_of(link["kind"], link))
    if slug is None:
        problem = f"the link names {link['ref']}, which is not a current document in this corpus"
        return LinkedAct(None, f"{problem}, so its sections cannot be checked here", section_id)
    target = (link["section"] or section_id).lower()
    if target == section_id.lower() or _lettered(target, section_id.lower()):
        return LinkedAct(slug, None, target)
    problem = f"the link points at § {link['section']}, which this parser reads as § {section_id}"
    return LinkedAct(slug, f"{problem}, so it is not checked", section_id)


def _lettered(target: str, parsed: str) -> bool:
    """``§ 13 a`` parses as ``13``; its link names ``13a``, the id the index holds."""
    rest = target.removeprefix(parsed)
    return rest != target and rest[:1].isalpha()
