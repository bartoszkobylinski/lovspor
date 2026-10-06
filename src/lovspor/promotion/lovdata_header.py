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
"""

from __future__ import annotations

import re
from typing import NamedTuple

_MIN_FIELDS = 3
_BLOCK_SPAN = 10
_FIELD = re.compile(
    r"^[ \t]*(Dato|Departement|Publisert|Ikrafttredelse|Sist[ \t]+endret|Endrer"
    r"|Gjelder[ \t]+for|Hjemmel|Kunngjort|Korttittel|Journalnummer|Rettelse)\b"
    r"[ \t]*:?(.*)$"
)
_LOVDATA_ID = re.compile(r"[ \t]*(?:LOV|FOR)-\d{4}-\d{2}-\d{2}-\d+")


class _Field(NamedTuple):
    line: int
    name: tuple[str, ...]
    dated: bool


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
            found.append(_Field(number, name, dated))
    return tuple(found)


def _is_block(fields: tuple[_Field, ...], anchor: _Field) -> bool:
    near = {field.name for field in fields if abs(field.line - anchor.line) <= _BLOCK_SPAN}
    return len(near) >= _MIN_FIELDS
