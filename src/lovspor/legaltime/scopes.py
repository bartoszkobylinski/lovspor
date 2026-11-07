"""What a partial commencement decision puts into force: the scope grammar.

Every scope is one sentence of a ``Delt`` block read literally: a part
(``Romertall I``, ``Lovens del I``), a part narrowed to listed bare sections,
the act's own sections (``Paragrafane 1 til 10``), or the remainder (``De andre
bestemmelsene …``). Any exception word, ledd qualifier or unknown shape makes
the sentence foreign, and a foreign sentence makes the whole block unreadable.
"""

import re
from datetime import date

from pydantic import model_validator

from lovspor.legaltime.document import Document, Frozen
from lovspor.legaltime.instructions import Operation
from lovspor.legaltime.text import parse_date, section_key, section_key_of

_STRAKS = re.compile(r"\b(?:straks|med\s+en\s+gang|omgående)\b", re.I)


_RESERVATION = re.compile(r"\b(?:unntatt|bortsett|likevel|med\s+unntak|unnateke|unnatatt)\b", re.I)


_SEC = r"§\s*\d+[A-Z]?(?:-\d+)?(?:\s*[a-hj-zæøå](?![a-zæøå]))?"


_DELT_SENTENCE = re.compile(
    rf"^(?:Endringsloven|Endringslova|Lovens|Loven|Lovas|Lova)\s+del\s+(?P<part>[IVXLC]+)"
    rf"(?:,\s*(?:ny\s+(?P<sec>{_SEC})\s+i\s+lov|(?:endringene|endringane)\s+i\s+lov)(?P<rest>[^,]*))?"
    r",?\s*trer\s+i\s+kraft\s+(?P<when>.+)$",
    re.I,
)


_SECTION_IN = re.compile(_SEC)


_ROMAN = r"[IVXLC]+"


_PART_LIST = (
    rf"(?P<parts>{_ROMAN}(?:\s*,\s*(?:del\s+)?{_ROMAN})*(?:\s+(?:og|til)\s+(?:del\s+)?{_ROMAN})?)"
)
_PARTS_IN_ORDER = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII")
_RANGE_ENDS = 2  # ``I til III`` names two ends of a run


_SECTION_LIST = rf"(?P<secs>\s+{_SEC}(?:\s*,\s*{_SEC})*(?:\s+og\s+{_SEC})?)?"


_PAREN = r"(?P<paren>\s*\([^()§]*\))?"  # an act name; a § inside the parenthesis is a scope


_PART_HEAD = r"(?:Romertall|(?:Lovens|Loven|Lova|Lovas|Endringsloven|Endringslova)\s+del)"


_PART_SCOPES = re.compile(
    rf"^{_PART_HEAD}\s+{_PART_LIST}{_PAREN}{_SECTION_LIST}\s+trer\s+i\s+kraft\s+(?:fra\s+|frå\s+)?"
    r"(?P<when>.+?)\.?$",
    re.I,
)


_LISTED_SCOPES = re.compile(
    r"^(?:Følgende|Følgjande)(?:\s+endringer|\s+endringar)?\s+"
    r"(?:trer\s+i\s+kraft|blir\s+sett\s+i\s+kraft|vert\s+sett\s+i\s+kraft)\s+(?P<when>[^:]+?):\s*"
    rf"(?:Endringsloven|Endringslova|Loven|Lova)\s+del\s+{_PART_LIST}{_PAREN}{_SECTION_LIST}\.?$",
    re.I,
)


_REMAINDER = re.compile(
    r"^(?:(?:De|Dei)\s+(?:andre|øvrige)\s+(?:bestemmelsene|føresegnene|endringene|endringane)"
    r"(?:\s+i\s+(?:endringsloven|endringslova|loven|lova))?"
    r"|(?:Loven|Lova|Endringsloven|Endringslova)\s+for\s+øvrig)\s+trer\s+i\s+kraft\s+"
    r"(?:fra\s+|frå\s+)?(?P<when>.+?)\.?$",
    re.I,
)


_OWN_SECTIONS = re.compile(
    r"^(?:Paragrafane|Paragrafene|Paragrafar|Paragrafer|Paragraf|§§?)\s+(?P<list>.+?)\s+"
    r"(?:gjeld|gjelder|trer\s+i\s+kraft|tek\s+til\s+å\s+gjelde|tar\s+til\s+å\s+gjelde)\s+"
    r"(?:frå|fra)?\s*(?P<when>\d.+)$",
    re.I,
)


_LISTING_TOKEN = re.compile(
    r"\s*(?:(?P<range>(?P<first>\d+)\s+til\s+(?P<last>\d+))"
    r"|(?P<one>\d+[A-Z]?(?:-\d+)?(?:\s*[a-hj-zæøå](?![a-zæøå]))?)"
    r"|(?P<sep>,|og|§§|§))(?=\s|,|$)"
)


class Scope(Frozen):
    """What one sentence of a partial decision puts into force, and when."""

    part: str | None = None
    section: str | None = None
    own_sections: frozenset[str] = frozenset()
    remainder: bool = False  # "De andre bestemmelsene …": whatever no other scope covers
    when: date
    quote: str  # the sentence the scope and date were read from

    @model_validator(mode="after")
    def _one_kind(self) -> "Scope":
        kinds = sum((bool(self.own_sections), self.part is not None, self.remainder))
        if kinds > 1 or (self.section is not None and self.part is None):
            raise ValueError("a scope names own_sections, a part or the remainder, never two")
        return self

    def covers(self, operation: Operation) -> bool:
        if self.remainder:
            return False  # decided by _scopes_for, against the decision's other scopes
        if self.own_sections:
            return operation.host_section in self.own_sections
        if self.part is None or operation.part.upper() != self.part:
            return False
        return self.section is None or operation.address.section == self.section


def _own_sections(listing: str) -> frozenset[str] | None:
    """``1 til 10 og §§ 12 til 25`` → the act's own section keys; None on any other token."""
    keys: set[str] = set()
    position = 0
    for match in _LISTING_TOKEN.finditer(listing):
        if match.start() != position:
            return None  # a word the listing grammar does not know (``unntatt``, a letter range)
        position = match.end()
        if match.group("range"):
            first, last = int(match.group("first")), int(match.group("last"))
            if last < first:
                return None
            keys.update(f"§{n}" for n in range(first, last + 1))
        elif match.group("one"):
            keys.add(f"§{match.group('one').replace(' ', '')}")
    return frozenset(keys) if position == len(listing) and keys else None


def _when(text: str, own: date | None) -> date | None:
    """The date a scope sentence states; ``straks`` is the resolution's own day, never both."""
    stated = parse_date(text)
    if _STRAKS.search(text):
        return None if stated is not None else own
    return stated


def _roman(numeral: str) -> int:
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total = 0
    for index, char in enumerate(numeral):
        value = values[char]
        bigger_follows = index + 1 < len(numeral) and values[numeral[index + 1]] > value
        total += -value if bigger_follows else value
    return total


def _part_labels(listing: str) -> list[str]:
    """``I, II og IV`` → the parts named; ``I til III`` → every part of the run."""
    found = [p.upper() for p in re.findall(_ROMAN, listing)]
    if " til " in listing and len(found) == _RANGE_ENDS:
        first, last = _roman(found[0]), _roman(found[1])
        return [p for p in _PARTS_IN_ORDER if first <= _roman(p) <= last]
    return found


def _part_scopes(match: re.Match[str], sentence: str, own: date | None) -> list[Scope] | None:
    """Scopes for ``Romertall I, II og III``, optionally narrowed to listed bare sections."""
    when = _when(match.group("when"), own)
    if when is None:
        return None
    parts = _part_labels(match.group("parts"))
    sections = [section_key_of(m) for m in re.findall(_SEC, match.group("secs") or "")]
    if sections and len(parts) != 1:
        return None  # sections listed under several parts: which part owns which is unstated
    if not sections:
        return [Scope(part=part, when=when, quote=sentence) for part in parts]
    return [Scope(part=parts[0], section=sec, when=when, quote=sentence) for sec in sections]


def _scopes_of(sentence: str, own: date | None) -> list[Scope] | None:
    """Every scope one sentence of a partial decision states; None when outside the grammar."""
    if _RESERVATION.search(sentence):
        return None  # an exception the grammar cannot scope: the whole decision is unreadable
    part_match = _DELT_SENTENCE.match(sentence)
    if part_match is not None:
        when = _when(part_match.group("when"), own)
        if when is None:
            return None
        part, section = part_match.group("part").upper(), _named_section(part_match)
        return [Scope(part=part, section=section, when=when, quote=sentence)]
    listed = _PART_SCOPES.match(sentence) or _LISTED_SCOPES.match(sentence)
    if listed is not None:
        return _part_scopes(listed, sentence, own)
    remainder = _REMAINDER.match(sentence)
    if remainder is not None:
        when = _when(remainder.group("when"), own)
        return [Scope(remainder=True, when=when, quote=sentence)] if when else None
    return _own_section_scopes(sentence)


def _own_section_scopes(sentence: str) -> list[Scope] | None:
    own = _OWN_SECTIONS.match(sentence)
    if own is None:
        return None
    when = parse_date(own.group("when"))
    sections = _own_sections(own.group("list"))
    if when is None or sections is None:
        return None
    return [Scope(own_sections=sections, when=when, quote=sentence)]


def _partial_scopes(sentences: list[str], own: date | None = None) -> list[Scope] | None:
    """The scopes of a ``Delt`` block; None when a sentence is foreign or only a remainder."""
    scopes: list[Scope] = []
    for sentence in sentences:
        found = _scopes_of(sentence, own)
        if found is None:
            return None
        scopes.extend(found)
    if any(scope.remainder for scope in scopes) and all(scope.remainder for scope in scopes):
        return None  # a remainder of nothing
    return scopes


def _named_section(match: re.Match[str]) -> str | None:
    """The one section a ``Delt`` sentence names, from ``ny § X`` or the amended act's clause."""
    if match.group("sec"):
        return section_key_of(match.group("sec"))
    rest = match.group("rest")
    if rest is None:
        return None
    found = _SECTION_IN.findall(rest)
    return section_key(found[-1]) if len(found) == 1 else None


def _scopes_for(operation: Operation, scopes: list[Scope]) -> list[Scope]:
    """The scopes that reach the operation; the specific ones when any does; else the remainder."""
    covering = [scope for scope in scopes if scope.covers(operation)]
    specific = [s for s in covering if s.section is not None or s.own_sections]
    if covering:
        return specific or covering
    return [scope for scope in scopes if scope.remainder]


def _named_in(law: Document, scope: Scope) -> bool:
    """Whether the law has the part or own sections the scope names; a remainder names none."""
    if scope.remainder:
        return False
    if scope.own_sections:
        return any(p.key in scope.own_sections for p in law.provisions)
    return scope.part in {part.label.upper() for part in law.parts}
