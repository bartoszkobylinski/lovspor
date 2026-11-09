"""What a partial commencement decision puts into force: the scope grammar.

Every scope is one sentence of a ``Delt`` block read literally: a part
(``Romertall I``, ``Lovens del I``), a part narrowed to listed bare sections,
the act's own sections (``Paragrafane 1 til 10``), or the remainder (``De andre
bestemmelsene …``). Any exception word, ledd qualifier or unknown shape makes
the sentence foreign, and a foreign sentence makes the whole block unreadable.
"""

import re
from datetime import date
from typing import Literal

from pydantic import model_validator

from lovspor.legaltime.document import Document, Frozen
from lovspor.legaltime.instructions import Operation
from lovspor.legaltime.text import act_refid, chapter_key, parse_date, section_key, section_key_of
from lovspor.legaltime.units import ChapterOf, Relation, Unit, parse_units, relation

_STRAKS = re.compile(r"\b(?:straks|med\s+en\s+gang|omgående)\b", re.I)


_RESERVATION = re.compile(r"\b(?:unntatt|bortsett|likevel|med\s+unntak|unnateke|unnatatt)\b", re.I)


_SEC = r"§\s*\d+(?:[A-Z]|\s[A-Z](?=-))?(?:-\d+)?(?:\s*[a-hj-zæøå](?![a-zæøå]))?"


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


_WHOLE_WITH_EXCEPTIONS = re.compile(
    r"^(?:Loven|Lova)\s+trer\s+i\s+kraft\s+(?P<when>.+?),?\s+med\s+unntak\s+av\s+(?P<units>.+?)\.?$",
    re.I,
)
_UNITS_OF_THE_ACT = re.compile(
    r"^(?:Loven|Lova)\s+(?P<units>(?:ny\s+|nytt\s+)?§.+?)\s+trer\s+i\s+kraft\s+(?P<when>.+?)\.?$",
    re.I,
)
_CHAPTER_OF_THE_ACT = re.compile(
    rf"^(?:Lovvedtaket|Lovens|Lovas|Loven|Lova)(?:\s+del\s+(?P<part>{_ROMAN}))?\s+"
    r"(?P<chapter>kapit(?:te)?l\s+\d+\s*[A-Za-z]?)\s+trer\s+i\s+kraft\s+(?P<when>.+?)\.?$",
    re.I,
)
_LISTED_ITEM = re.compile(
    r"^(?:Følgende|Følgjande)(?:\s+endringer|\s+endringar)?\s+"
    r"(?:trer\s+i\s+kraft|blir\s+sett\s+i\s+kraft|vert\s+sett\s+i\s+kraft)\s+(?P<when>.+?):?\s+"
    rf"(?:Endringsloven|Endringslova|Loven|Lova)\s+del\s+(?P<part>{_ROMAN})(?P<rest>.*?)\.?$",
    re.I,
)
_EXCEPTIONS = re.compile(r"^med\s+unntak\s+av\s+(?P<units>.+)$", re.I)
_UNIT_START = re.compile(
    r"^(?:ny\s+|nytt\s+|nye\s+)?(?:§|kapit(?:te)?l\b|overskriften\s+til\s+kapit)"
)


_LISTING_TOKEN = re.compile(
    r"\s*(?:(?P<range>(?P<first>\d+)\s+til\s+(?P<last>\d+))"
    r"|(?P<one>\d+(?:[A-Z]|\s[A-Z](?=-))?(?:-\d+)?(?:\s*[a-hj-zæøå](?![a-zæøå]))?)"
    r"|(?P<sep>,|og|§§|§))(?=\s|,|$)"
)


Reach = Literal[Relation, "excluded"]
SPANS = "operation spans a unit the decision excepts"
UNPLACED = "a unit the decision names cannot be placed against the operation"


class Scope(Frozen):
    """What one sentence of a partial decision puts into force, and when.

    A scope is the whole act, a part of the amending law, the act's own sections, or
    the remainder; ``units`` narrow it to the units listed, ``excluded`` carve units out.
    """

    part: str | None = None
    section: str | None = None
    own_sections: frozenset[str] = frozenset()
    remainder: bool = False  # "De andre bestemmelsene …": whatever no other scope covers
    whole: bool = False  # "Loven trer i kraft D, med unntak av …": the act, less the exceptions
    units: tuple[Unit, ...] = ()  # narrowed to these units of the act
    excluded: tuple[Unit, ...] = ()  # these units are not reached by this scope
    act: str | None = None  # the act an item cites inline before its units; must be the target's
    when: date
    quote: str  # the sentence the scope and date were read from

    @model_validator(mode="after")
    def _one_kind(self) -> "Scope":
        bare_units = bool(self.units) and self.part is None
        kinds = sum((bool(self.own_sections), self.part is not None, self.remainder, self.whole))
        if kinds + bare_units > 1 or (self.section is not None and self.part is None):
            raise ValueError(
                "a scope names own_sections, a part, the act or the remainder, never two"
            )
        if self.units and self.excluded:
            raise ValueError("a scope narrows to units or excludes units, never both")
        return self

    @property
    def specific(self) -> bool:
        return self.section is not None or bool(self.own_sections) or bool(self.units)

    def _placed(self, operation: Operation) -> bool:
        if self.remainder:
            return False  # decided by _scopes_for, against the decision's other scopes
        if self.own_sections:
            return operation.host_section in self.own_sections
        if self.whole:
            return True
        if self.part is None or operation.part.upper() != self.part:
            return False
        return self.section is None or operation.address.section == self.section

    def reach(self, operation: Operation, chapter_of: ChapterOf | None = None) -> Reach:
        """How this scope stands to the operation, once exceptions and narrowing are read."""
        if not self._placed(operation):
            return "disjoint"
        if self.act is not None and operation.act != self.act:
            return "unknown"  # the item names another act's units: not placeable against this one
        if self.units:
            return _narrowed(self.units, operation, chapter_of)
        found = {relation(unit, operation, chapter_of) for unit in self.excluded}
        if "spans" in found or "unknown" in found:
            return "spans" if "spans" in found else "unknown"
        return "excluded" if "covers" in found else "covers"

    def covers(self, operation: Operation) -> bool:
        return self.reach(operation) == "covers"


def _narrowed(
    units: tuple[Unit, ...], operation: Operation, chapter_of: ChapterOf | None
) -> Relation:
    found = {relation(unit, operation, chapter_of) for unit in units}
    for answer in ("covers", "spans", "unknown"):
        if answer in found:
            return answer
    return "disjoint"


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
    listed = match.group("secs")
    sections = [section_key_of(m) for m in re.findall(_SEC, listed)] if listed else []
    if sections and len(parts) != 1:
        return None  # sections listed under several parts: which part owns which is unstated
    if not sections:
        return [Scope(part=part, when=when, quote=sentence) for part in parts]
    return [Scope(part=parts[0], section=sec, when=when, quote=sentence) for sec in sections]


def _strip_parenthetical(rest: str) -> str | None:
    """Drop one leading balanced ``(…)`` act name; None when the parentheses do not balance."""
    text = rest.strip()
    if not text.startswith("("):
        return text
    depth = 0
    for index, char in enumerate(text):
        depth += (char == "(") - (char == ")")
        if depth == 0:
            name = text[1:index]
            return None if "§" in name else text[index + 1 :].strip()  # ``(§ 3)`` is a scope
    return None


class _Item(Frozen):
    units: tuple[Unit, ...] = ()
    excluded: tuple[Unit, ...] = ()
    act: str | None = None  # the act cited inline before the units, as a refid


def _item_units(rest: str) -> _Item | None:
    """What an item names after ``del R``; None when its tail is outside the grammar."""
    tail = _strip_parenthetical(rest)
    if tail is None:
        return None
    if not tail:
        return _Item()
    exceptions = _EXCEPTIONS.match(tail)
    if exceptions is not None:
        excluded = parse_units(exceptions.group("units"))
        return _Item(excluded=excluded) if excluded is not None else None
    act = None
    if not _UNIT_START.match(tail):
        if not tail.lower().startswith("lov ") or "§" not in tail:
            return None  # neither a unit listing nor an inline act citation before one
        act = act_refid(tail[: tail.index("§")])  # ``lov 6. juni 2014 nr. 19 om … § 2 …``
        if act is None:
            return None  # an act named without a citable date: whose units these are is unstated
        tail = tail[tail.index("§") :]
    units = parse_units(tail)
    return _Item(units=units, act=act) if units is not None else None


def _listed_item(sentence: str, own: date | None) -> list[Scope] | None:
    match = _LISTED_ITEM.match(sentence)
    if match is None:
        return None
    when = _when(match.group("when"), own)
    read = _item_units(match.group("rest"))
    if when is None or read is None:
        return None  # not one part with units: the part-list grammar below may still read it
    part = match.group("part").upper()
    return [
        Scope(
            part=part,
            units=read.units,
            excluded=read.excluded,
            act=read.act,
            when=when,
            quote=sentence,
        )
    ]


def _act_scopes(sentence: str, own: date | None) -> list[Scope] | None:
    """Scopes naming the act itself: a date with exceptions, a unit, or a chapter under a part."""
    whole = _WHOLE_WITH_EXCEPTIONS.match(sentence)
    if whole is not None:
        when, excluded = _when(whole.group("when"), own), parse_units(whole.group("units"))
        if when is None or excluded is None:
            return []
        return [Scope(whole=True, excluded=excluded, when=when, quote=sentence)]
    chapter = _CHAPTER_OF_THE_ACT.match(sentence)
    if chapter is not None:
        when, part = _when(chapter.group("when"), own), chapter.group("part")
        unit = Unit(chapter=chapter_key(chapter.group("chapter")))
        if when is None:
            return []
        return [
            Scope(part=part.upper() if part else None, units=(unit,), when=when, quote=sentence)
        ]
    units_match = _UNITS_OF_THE_ACT.match(sentence)
    if units_match is not None:
        when, units = _when(units_match.group("when"), own), parse_units(units_match.group("units"))
        return [Scope(units=units, when=when, quote=sentence)] if when and units else []
    return None


def _scopes_of(sentence: str, own: date | None) -> list[Scope] | None:
    """Every scope one sentence of a partial decision states; None when outside the grammar."""
    for reader in (_listed_item, _act_scopes):
        found = reader(sentence, own)
        if found is not None:
            return found or None  # [] is a sentence of the shape whose units or date failed
    if _RESERVATION.search(sentence):
        return None  # an exception the grammar cannot scope: the whole decision is unreadable
    part_match = _DELT_SENTENCE.match(sentence)
    if part_match is not None:
        return _delt_scope(part_match, sentence, own)
    listed = _PART_SCOPES.match(sentence) or _LISTED_SCOPES.match(sentence)
    if listed is not None:
        return _part_scopes(listed, sentence, own)
    remainder = _REMAINDER.match(sentence)
    if remainder is not None:
        when = _when(remainder.group("when"), own)
        return [Scope(remainder=True, when=when, quote=sentence)] if when else None
    return _own_section_scopes(sentence)


def _delt_scope(match: re.Match[str], sentence: str, own: date | None) -> list[Scope] | None:
    when = _when(match.group("when"), own)
    if when is None or not _representable(match):
        return None
    part, section = match.group("part").upper(), _named_section(match)
    return [Scope(part=part, section=section, when=when, quote=sentence)]


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


_TRAILING_QUALIFIER = re.compile(r"\b(?:ledd|punktum|bokstav|nr\.)\b", re.I)


def _representable(match: re.Match[str]) -> bool:
    """The amended act's clause after ``endringene i lov …`` names nothing, or one bare section.

    ``§ 1 første ledd``, ``§ 1 første punktum`` and ``§§ 1 og 5`` are scopes the model cannot
    hold (a scope is a part or a whole section), so the sentence is foreign rather than widened.
    """
    rest = match.group("rest")
    if match.group("sec") or not rest or "§" not in rest:
        return True
    found = list(_SECTION_IN.finditer(rest))
    if len(found) != 1 or _TRAILING_QUALIFIER.search(rest) or "§§" in rest:
        return False
    return not rest[found[0].end() :].strip()


def _named_section(match: re.Match[str]) -> str | None:
    """The one section a ``Delt`` sentence names, from ``ny § X`` or the amended act's clause."""
    if match.group("sec"):
        return section_key_of(match.group("sec"))
    rest = match.group("rest")
    if rest is None:
        return None
    found = _SECTION_IN.findall(rest)
    return section_key(found[-1]) if len(found) == 1 else None


def _scopes_for(
    operation: Operation, scopes: list[Scope], chapter_of: ChapterOf | None = None
) -> list[Scope] | str:
    """The scopes that reach the operation; the specific ones when any does; else the remainder.

    A scope that spans the operation (the operation rewrites a unit the scope excepts) or
    cannot place it is a reason instead: the decision's answer for this operation is unknown.
    """
    reached = [(scope, scope.reach(operation, chapter_of)) for scope in scopes]
    if any(answer == "spans" for _, answer in reached):
        return SPANS
    if any(answer == "unknown" for _, answer in reached):
        return UNPLACED
    covering = [scope for scope, answer in reached if answer == "covers"]
    specific = [scope for scope in covering if scope.specific]
    if covering:
        return specific or covering
    return [scope for scope in scopes if scope.remainder]


def _named_in(law: Document, scope: Scope) -> bool:
    """Whether the law has the part or own sections the scope names; a remainder names none.

    A scope of the whole act names the law. A bare unit or chapter scope without a part is
    a statement about an enacted act's own structure, which only the baseline reading checks.
    """
    if scope.own_sections:
        return any(p.key in scope.own_sections for p in law.provisions)
    if scope.whole:
        return True
    return scope.part is not None and scope.part in {part.label.upper() for part in law.parts}
