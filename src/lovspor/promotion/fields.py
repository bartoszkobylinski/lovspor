"""Identification block, body and pre-filled fields from extracted lines (ADR-0016 S2).

The **identification block** runs from the title line to the line before
the first section (``§ 1``, ``Kapittel 1``); the **body** from there on (both
anchors are defined in ``anchors.py``, which the HTML reader shares). What
comes before the title — a page heading, a lead paragraph — is neither: it is
not the regulation, so it is not hashed and not rendered. A text with no
first section or no title before it is held, because the identity rules read
ids only from the block, and a block with no bound would let a citation in
the body pass for the document's own number.

Fields are pre-filled for a human to confirm, never promoted on trust (the
classification study measured 65-85 % accuracy per field, its §4). Each is
what the text states, verbatim or as a parsed date, or empty:

* ``title`` — the title line;
* ``hjemmel`` — ``Hjemmel: LOV-…`` header references and ``med hjemmel (i) …`` /
  ``i medhold av …`` phrases in the block, to the end of their sentence;
* ``vedtatt`` / ``vedtatt_av`` — the first ``vedtatt av <organ> <dato>``
  (or ``fastsatt``, ``vedteke``) in the block, else the body's
  ``Forskriften er vedtatt av <organ> den <dato>``. The organ stops at
  ``med hjemmel`` / ``i medhold av``, and a date right after a law's name is
  that statute's date, never the vedtaksdato;
* ``ikraft`` / ``ikraft_text`` — an ``Ikrafttredelse:`` header, else the first
  ``trer i kraft …`` clause anywhere: a date when one is stated, otherwise the
  phrase verbatim ("straks").
"""

from __future__ import annotations

import re
from datetime import date

from lovspor.promotion.anchors import first_section, first_title
from lovspor.promotion.dates import DATE, parse_stated_date
from lovspor.promotion.models import (
    ExtractedRegulation,
    ExtractionHoldReason,
    RegulationFields,
)

_HJEMMEL_HEADER = re.compile(r"(?:hjemmel|heimel)\s*:\s*((?:LOV|FOR)-.+)", re.I)
_LOVDATA_REFERENCE_SPLIT = re.compile(r",\s*(?=(?:LOV|FOR)-)")
# "med hjemmel lov 9. juni 2023" drops the "i"; without it, the phrase must
# name a law or a regulation, so "med hjemmel som nevnt" is not a hjemmel.
_LAW_NAME = r"\S*(?:lov|lova|loven|forskrift|forskrifta|forskriften)\b"
_HJEMMEL_PHRASE = re.compile(
    rf"(?:med\s+(?:hjemmel|heimel)\s+(?:i\s+|(?={_LAW_NAME}))"
    r"|i\s+med(?:hold|hald)\s+av\s+)(.+?)"
    r"(?:\.(?=\s+(?-i:[A-ZÆØÅ]))|\.?\s*$)",
    re.I,
)
# The organ never runs into a hjemmel phrase, and a date right after a law's
# name ("lov av 14. juni 2002") is that statute's date, not the adoption's.
_HJEMMEL_START = r"\bmed\s+(?:hjemmel|heimel)\b|\bmed(?:hold|hald)\s+av\b"
_ENACTMENT = (
    r"(?:fastsatt|fastsett|vedtatt|vedteke|vedteken)\s+(?:av|i)\s+"
    rf"(?P<organ>(?:(?!{_HJEMMEL_START})[^\d.]){{1,90}}?)(?:\s+(?:i\s+møte|den))?[\s,]*" + DATE
)
_ENACTED = re.compile(r"\b" + _ENACTMENT, re.I)
# In the body only the regulation's own adoption counts: "Forskriften er
# vedtatt av …", never a repealed one's "som blei vedteke i …".
_SELF_ENACTED = re.compile(
    r"\b(?:denne\s+)?forskrift(?:en|a)?\s+(?:er|ble|blei|vart|vert)\s+" + _ENACTMENT, re.I
)
_STATUTE_BEFORE_DATE = re.compile(rf"{_LAW_NAME}(?:\s+av)?\s*$", re.I)
_IKRAFT_HEADER = re.compile(r"(?:ikrafttredelse|ikraftsetjing|ikraftsetting)\s*:\s*(.+)", re.I)
_IKRAFT_CLAUSE = re.compile(r"\b(?:trer|trår)\s+i\s+kraft\s+(.{1,200})", re.I)
_DATE_FIRST = re.compile(
    r"(?:(?:fra\s+og\s+med|fra|frå|f\.o\.m\.|den|med\s+(?:virkning|verknad)\s+fr[aå])\s+)*" + DATE,
    re.I,
)
_SENTENCE_END = re.compile(r"\.(?=\s+[A-ZÆØÅ]|\s*$)")

ReadRegulation = tuple[ExtractedRegulation, RegulationFields]


def read_regulation(lines: tuple[str, ...]) -> ReadRegulation | ExtractionHoldReason:
    """Split ``lines`` and pre-fill the fields; a hold reason when it cannot be split."""
    body_start = first_section(lines)
    if body_start is None:
        return ExtractionHoldReason.NO_BODY
    title_index = first_title(lines[:body_start])
    if title_index is None:
        return ExtractionHoldReason.NO_TITLE
    block, body = lines[title_index:body_start], lines[body_start:]
    fields = _fields(block, body)
    regulation = ExtractedRegulation(
        title=fields.title,
        identification_block="\n".join(block),
        body="\n".join(body),
        vedtaksdato=fields.vedtatt,
    )
    return regulation, fields


def _fields(block: tuple[str, ...], body: tuple[str, ...]) -> RegulationFields:
    vedtatt, vedtatt_av = _enactment(" ".join(block), " ".join(body))
    ikraft, ikraft_text = _in_force(block, " ".join((*block, *body)))
    return RegulationFields(
        title=block[0],
        hjemmel=_hjemmel(block),
        vedtatt=vedtatt,
        vedtatt_av=vedtatt_av,
        ikraft=ikraft,
        ikraft_text=ikraft_text,
    )


def _hjemmel(block: tuple[str, ...]) -> tuple[str, ...]:
    found: dict[str, None] = {}
    for line in block:
        header = _HJEMMEL_HEADER.match(line)
        if header:
            for reference in _LOVDATA_REFERENCE_SPLIT.split(header.group(1)):
                found.setdefault(reference.strip().rstrip("."))
    for phrase in _HJEMMEL_PHRASE.finditer(" ".join(block)):
        found.setdefault(phrase.group(1).strip())
    return tuple(found)


def _enactment(block: str, body: str) -> tuple[date | None, str | None]:
    """The block's enactment clause, else the body's statement of the regulation's own."""
    match = _first_enactment(_ENACTED, block) or _first_enactment(_SELF_ENACTED, body)
    if match is None:
        return None, None
    return parse_stated_date(match.groups()[-1]), match.group("organ").strip()


def _first_enactment(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    return next(
        (m for m in pattern.finditer(text) if not _STATUTE_BEFORE_DATE.search(m.group("organ"))),
        None,
    )


def _in_force(block: tuple[str, ...], text: str) -> tuple[date | None, str | None]:
    for line in block:
        header = _IKRAFT_HEADER.match(line)
        if header:
            return _date_or_phrase(header.group(1))
    clause = _IKRAFT_CLAUSE.search(text)
    if clause is None:
        return None, None
    return _date_or_phrase(clause.group(1))


def _date_or_phrase(rest: str) -> tuple[date | None, str | None]:
    """A stated date, or the clause verbatim to its sentence end when it states none."""
    stated = _DATE_FIRST.match(rest)
    parsed = parse_stated_date(stated.groups()[-1]) if stated else None
    if parsed is not None:
        return parsed, None
    return None, _SENTENCE_END.split(rest, maxsplit=1)[0].strip() or None
