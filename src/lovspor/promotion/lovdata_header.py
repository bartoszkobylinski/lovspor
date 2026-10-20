"""Lovdata's metadata block in a text pasted from Lovdata without print chrome (#527).

A copy pasted out of a Lovdata page keeps the block of field lines Lovdata sets
above every regulation (``Dato: FOR-…``, ``Publisert``, ``Ikrafttredelse``,
``Gjelder for``, ``Hjemmel: LOV-…-§…``, ``Korttittel``…), and § 14 does not
cover that editorial markup (ADR-0016 Risks).

One field line is not evidence: a municipal preamble names its ``Hjemmel``, and
any text may cite a ``FOR-`` id. The block is Lovdata's when its ``Dato`` line
carries the regulation's own Lovdata id (``FOR-``/``LOV-`` yyyy-mm-dd-n), which
Lovdata assigns on publication, and at least ``_MIN_FIELDS`` distinct field
names start lines within ``_BLOCK_SPAN`` lines of it. A municipal draft laid
out on Lovdata's submission template leaves ``Dato`` blank (Orkland 5059) and
is not held here. The names are matched case-sensitively, as Lovdata sets them.

A DOCX exported from Lovdata sets each field name and its value as paragraphs
of their own (``Dato`` / ``FOR-…``). ``has_lovdata_header`` reads the observed
channel's hold as it always has; ``lovdata_dato``, which the received channel
reads (ADR-0016 A1-3), also takes the id from the line after a bare ``Dato``.

The print markers live here too, so the extractor's hold and the received
channel's removal (``lovdata_markup.py``) read one definition:
``LOVDATA_PRINT`` is the "Utskrift fra Lovdata" stamp, and
``LOVDATA_PRINT_CHROME`` a browser print's page chrome: a print date or a page
counter beside the page URL or the ``Lovdata - <title>`` page title (#519).
Requiring the chrome keeps a regulation that merely links Lovdata in its body
from being held: a bare URL, or ``<title> - Lovdata`` as a link's text, is not
one.

``header_validity`` reads what the block states about a repealed regulation
(#639): Lovdata's ``OPPHEVET`` stamp, a line of its own, and the end of the
``Ikrafttredelse`` range, ``15.02.2020 - 31.03.2020`` as Lovdata sets it with
an en dash (its value on the next line in a DOCX export). Only two real
``dd.mm.yyyy`` dates joined by a hyphen, an en dash or an em dash are a range;
anything else states no end.
"""

from __future__ import annotations

import re
from datetime import date
from typing import NamedTuple

from lovspor.promotion.dates import parse_stated_date
from lovspor.promotion.models import HeaderValidity, RegulationStatus

_MIN_FIELDS = 3
_BLOCK_SPAN = 10
_FIELD = re.compile(
    r"^[ \t]*(Dato|Departement|Publisert|Ikrafttredelse|Sist[ \t]+endret|Endrer"
    r"|Gjelder[ \t]+for|Hjemmel|Kunngjort|Korttittel|Journalnummer|Rettelse)\b"
    r"[ \t]*:?(.*)$"
)
_LOVDATA_ID = re.compile(r"[ \t]*(?:LOV|FOR)-\d{4}-\d{2}-\d{2}-\d+")
_BARE_ID = re.compile(r"[ \t]*(?:LOV|FOR)-\d{4}-\d{2}-\d{2}-\d+[ \t]*")

_OPPHEVET = re.compile(r"[ \t]*OPPHEVET[ \t]*")
_IKRAFT = re.compile(r"[ \t]*Ikrafttredelse\b[ \t]*:?[ \t]*(.*)")
_NUMERIC = r"\d{1,2}\.\d{1,2}\.\d{4}"
_RANGE = re.compile(rf"[ \t]*{_NUMERIC}[ \t]*[-\u2013\u2014][ \t]*({_NUMERIC})[ \t]*")

LOVDATA_PRINT = re.compile(r"utskrift\s+fra\s+lovdata", re.IGNORECASE)
_PRINT_CHROME = r"(?:\d{1,2}[./]\d{1,2}[./]\d{2,4}(?:,?\s*\d{1,2}:\d{2})?|side\s+\d+\s+av\s+\d+)"
_LOVDATA_PAGE = r"(?:https?://)?(?:www\.)?lovdata\.no/\S+"
LOVDATA_PRINT_CHROME = re.compile(
    rf"^[ \t]*{_PRINT_CHROME}[ \t]*(?:{_LOVDATA_PAGE}|lovdata\s+-\s)"
    rf"|(?:^|\s){_LOVDATA_PAGE}[ \t]+\d+/\d+[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


class _Field(NamedTuple):
    line: int
    name: tuple[str, ...]
    dated: bool
    bare: bool


def has_lovdata_header(text: str) -> bool:
    """Whether ``text`` holds Lovdata's metadata block (see the module docstring)."""
    fields = _field_lines(text)
    return any(_is_block(fields, anchor) for anchor in fields if anchor.dated)


def _field_lines(text: str) -> tuple[_Field, ...]:
    found: list[_Field] = []
    for number, line in enumerate(text.splitlines()):
        match = _FIELD.match(line)
        if match is not None:
            # The words, not a re-joined string: "Sist  endret" and "Sist endret"
            # are one field, and no separator literal is left to mean anything.
            name = tuple(match.group(1).split())
            dated = name == ("Dato",) and _LOVDATA_ID.match(match.group(2)) is not None
            bare = name == ("Dato",) and not match.group(2).strip()
            found.append(_Field(number, name, dated, bare))
    return tuple(found)


def _is_block(fields: tuple[_Field, ...], anchor: _Field) -> bool:
    near = {field.name for field in fields if abs(field.line - anchor.line) <= _BLOCK_SPAN}
    return len(near) >= _MIN_FIELDS


def lovdata_dato(lines: tuple[str, ...]) -> tuple[int, int] | None:
    """Where Lovdata's block in ``lines`` states the regulation's id, or ``None``.

    The index of the ``Dato`` line, and of the line carrying the id: the same
    line, or the next one after a bare ``Dato`` (a DOCX export).
    """
    fields = _field_lines("\n".join(lines))
    for anchor in fields:
        holder = _id_holder(lines, anchor)
        if holder is not None and _is_block(fields, anchor):
            return anchor.line, holder
    return None


def _id_holder(lines: tuple[str, ...], anchor: _Field) -> int | None:
    if anchor.dated:
        return anchor.line
    following = anchor.line + 1
    if anchor.bare and following < len(lines) and _BARE_ID.fullmatch(lines[following]):
        return following
    return None


def header_validity(block: tuple[str, ...]) -> HeaderValidity:
    """What Lovdata's block ``block`` states about the regulation's validity."""
    repealed = any(_OPPHEVET.fullmatch(line) for line in block)
    return HeaderValidity(
        status=RegulationStatus.REPEALED if repealed else None,
        ikraft_end=_ikraft_end(block),
    )


def _ikraft_end(block: tuple[str, ...]) -> date | None:
    for index, line in enumerate(block):
        label = _IKRAFT.fullmatch(line)
        if label is not None:
            value = label.group(1) or _following(block, index)
            stated = _RANGE.fullmatch(value)
            return None if stated is None else parse_stated_date(stated.group(1))
    return None


def _following(block: tuple[str, ...], index: int) -> str:
    return block[index + 1] if index + 1 < len(block) else ""
