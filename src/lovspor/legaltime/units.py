"""The legal units a commencement instrument names, and which operations they reach.

A unit is what a ``Delt`` decision or an exception clause points at: a
chapter, a section, a ledd, a punktum or an item inside it, or a heading.
Listings (``§ 12-11 annet ledd, § 13-5 og § 13-6 første ledd``) are read
token by token; one token outside the grammar refuses the whole listing,
because an exception the model cannot scope would otherwise date the very
provision it excepts (ADR-0020, Decision 6).

Reaching is literal: a unit ``covers`` an operation addressed inside it,
``spans`` one addressed to a larger unit that contains it, is ``disjoint``
from one beside it, and is ``unknown`` where the two addresses cannot be
compared without reading the act's text (a chapter against a section the
operation does not place in a chapter; a punktum named without its ledd
against one named with it).
"""

import re
from collections.abc import Mapping
from typing import Literal

from lovspor.legaltime.document import Frozen
from lovspor.legaltime.instructions import Operation
from lovspor.legaltime.text import ORDINAL_ALT, chapter_key, ordinal, section_key_of

Relation = Literal["covers", "spans", "disjoint", "unknown"]

_SECNUM = r"\d+[A-Z]?(?:-\d+)?(?:\s*[a-hj-zæøå](?![a-zæøå]))?"
_UNIT = re.compile(
    rf"^(?:(?P<new>ny|nytt|nye)\s+)?"
    rf"(?:(?P<chapter_heading>overskriften\s+til\s+)?kapit(?:te)?l\s+(?P<chapter>\d+\s*[A-Za-z]?)"
    rf"|§\s*(?P<sec>{_SECNUM})"
    rf"(?:\s+(?P<new_ledd>nytt\s+|ny\s+)?(?P<ledd>{ORDINAL_ALT})\s+ledd)?"
    rf"(?:\s+(?P<new_punktum>nytt\s+)?(?P<punktum>{ORDINAL_ALT})\s+punktum)?"
    rf"(?:\s+bokstav\s+(?P<bokstav>[a-zæøå])|\s+nr\.\s+(?P<nr>\d+))?"
    r"(?:\s+(?P<heading>paragrafoverskriften|overskriften))?)$",
    re.I,
)
_SPLIT = re.compile(r"\s*,\s*|\s+og\s+")


class Unit(Frozen):
    """One named legal unit; a chapter or a section, narrowed by the fields that are set."""

    chapter: str | None = None  # ``kap16``, the chapter section's data-name
    section: str | None = None  # ``§15-7``
    ledd: int | None = None
    punktum: int | None = None
    bokstav: str | None = None  # a letter, or an item number (``nr. 3``) as text
    heading: bool = False  # the section's or chapter's heading alone
    new: bool = False  # named as new text (``ny § 2-6``); recorded, never used to match

    def describe(self) -> str:
        head = self.section or self.chapter or "?"
        parts = [head]
        if self.ledd is not None:
            parts.append(f"ledd {self.ledd}")
        if self.punktum is not None:
            parts.append(f"punktum {self.punktum}")
        if self.bokstav is not None:
            parts.append(f"bokstav {self.bokstav}")
        if self.heading:
            parts.append("heading")
        return " ".join(parts)


def _unit(token: str) -> Unit | None:
    match = _UNIT.match(token)
    if match is None:
        return None
    if match.group("chapter"):
        return Unit(
            chapter=chapter_key(f"kapittel {match.group('chapter')}"),
            heading=match.group("chapter_heading") is not None,
            new=match.group("new") is not None,
        )
    return Unit(
        section=section_key_of(f"§ {match.group('sec')}"),
        ledd=ordinal(match.group("ledd")) if match.group("ledd") else None,
        punktum=ordinal(match.group("punktum")) if match.group("punktum") else None,
        bokstav=match.group("bokstav") or match.group("nr"),
        heading=match.group("heading") is not None,
        new=any(match.group(k) for k in ("new", "new_ledd", "new_punktum")),
    )


def _tokens(listing: str) -> list[str]:
    """Split a listing; after ``§§`` the bare numbers that follow carry the ``§`` with them."""
    tokens: list[str] = []
    carry = False
    for raw in _SPLIT.split(listing.strip()):
        token = raw
        if token.startswith("§§"):
            token, carry = "§ " + token[2:].lstrip(), True
        elif carry and token[:1].isdigit():
            token = "§ " + token
        tokens.append(token)
    return tokens


def parse_units(listing: str) -> tuple[Unit, ...] | None:
    """Every unit a listing names, or None when any token is outside the grammar."""
    units: list[Unit] = []
    for token in _tokens(listing):
        unit = _unit(token)
        if unit is None:
            return None
        units.append(unit)
    return tuple(units) if units else None


ChapterOf = Mapping[str, str]  # section key → chapter data-name, read from the act's structure


def _chapter_relation(unit: Unit, operation: Operation, chapter_of: ChapterOf) -> Relation:
    if unit.heading:
        return "disjoint"  # no operation kind addresses a chapter heading
    placed = operation.new_provision
    if operation.kind == "insert_section" and placed is not None and placed.chapter is not None:
        return "covers" if placed.chapter == unit.chapter else "disjoint"
    chapter = chapter_of.get(operation.address.section or "")
    if chapter is None:
        return "unknown"  # the operation's target sits in a chapter the act's text does not name
    return "covers" if chapter == unit.chapter else "disjoint"


def _heading_relation(unit: Unit, operation: Operation) -> Relation:
    if operation.kind == "replace_heading":
        return "covers"
    return "spans" if operation.kind in {"replace_section", "repeal_section"} else "disjoint"


def _level(unit_value: int | None, op_value: int | None, op_end: int | None) -> Relation | None:
    """Compare one address level; None means equal, go on to the finer level."""
    if unit_value is None:
        return None
    if op_value is None:
        return "spans"
    if op_end is not None and op_end != op_value:
        return "spans" if op_value <= unit_value <= op_end else "disjoint"
    return None if op_value == unit_value else "disjoint"


def relation(unit: Unit, operation: Operation, chapter_of: ChapterOf | None = None) -> Relation:
    """How the unit stands to the operation's address: covers, spans, disjoint or unknown.

    ``chapter_of`` places an existing section in its chapter, from the act's own structure.
    """
    if unit.chapter is not None:
        return _chapter_relation(unit, operation, chapter_of or {})
    address = operation.address
    if unit.section != address.section:
        return "disjoint"
    if unit.heading:
        return _heading_relation(unit, operation)
    if operation.kind == "replace_heading":
        return "covers" if unit.ledd is None and unit.punktum is None else "disjoint"
    levels = (
        _level(unit.ledd, address.ledd, address.ledd_end),
        _level(unit.punktum, address.punktum, address.punktum_end),
        None
        if unit.bokstav is None
        else _level(1, 1 if address.bokstav == unit.bokstav else 2, None),
    )
    skips_ledd = unit.ledd is None and (unit.punktum is not None or unit.bokstav is not None)
    if skips_ledd and address.ledd is not None:
        return "unknown"  # the unit skips the ledd the operation names
    return next((found for found in levels if found is not None), "covers")
