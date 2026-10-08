"""``vedtatt`` and ``ikraft`` with their verbatim evidence (ADR-0016 Decision 2, slice S10).

This module reads *every* statement the text makes, keeps each one verbatim,
and says whether they agree. It is the one reading of these dates: the
evidence sidecar records it whole, and the front matter (``fields.py``) shows
its ``value`` and ``text``, so a held date is ``null`` in both (#581). The
patterns live here; ``fields.py`` reuses the enactment ones for the organ and
for the identity's vedtaksdato.

* ``stated`` — one date, however often stated; ``value`` is it;
* ``text`` — no date, one non-date phrase ("straks"); ``text`` is it;
* ``held`` — statements that disagree (two dates, a date and a phrase, two
  phrases) or a draft's blank date: ``value`` stays ``null`` and
  ``hold_reason`` says why, so no reader takes one of them for the answer;
* ``absent`` — the text states nothing.

Nothing is derived from an observation time, and nothing is repaired: a
statement whose date is not a real calendar date reads as a phrase.

``evidence`` is a substring of the extracted text with its line breaks read
as spaces, exactly the text the patterns ran over.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

from lovspor.promotion.dates import DATE, has_placeholder_date, parse_stated_date
from lovspor.promotion.models import ExtractedRegulation

_LAW_NAME = r"\S*(?:lov|lova|loven|forskrift|forskrifta|forskriften)\b"
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
_DATE_FIRST = re.compile(
    r"(?:(?:fra\s+og\s+med|fra|frå|f\.o\.m\.|den|med\s+(?:virkning|verknad)\s+fr[aå])\s+)*" + DATE,
    re.I,
)
_SENTENCE_END = re.compile(r"\.(?=\s+[A-ZÆØÅ]|\s*$)")
_IKRAFT_CUE = re.compile(r"\b(?:trer|trår)\s+i\s+kraft\s+(?=.)", re.IGNORECASE)
_ENACTMENT_WORD = re.compile(r"\b(?:vedtatt|vedteke[n]?|vedtekne)\b", re.IGNORECASE)
_SELF_ENACTMENT = re.compile(r"\bforskrift(?:en|a)?\b.*" + _ENACTMENT_WORD.pattern, re.IGNORECASE)
_CLAUSE_CHARS = 200

HoldReason = Literal["conflicting_statements", "placeholder_date"]


class DateStatement(BaseModel):
    """One statement as the text makes it: its date or phrase, and the words."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: date | None
    text: str | None
    evidence: str


class StatedDate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["stated", "text", "held", "absent"]
    value: date | None = None
    text: str | None = None
    hold_reason: HoldReason | None = None
    statements: tuple[DateStatement, ...] = ()


def stated_vedtatt(regulation: ExtractedRegulation) -> StatedDate:
    """Every enactment clause of the block, and the body's own "Forskriften er vedtatt …"."""
    block = regulation.identification_block.replace("\n", " ")
    body = regulation.body.replace("\n", " ")
    matches = [*_ENACTED.finditer(block), *_SELF_ENACTED.finditer(body)]
    statements = tuple(
        DateStatement(date=parse_stated_date(m.groups()[-1]), text=None, evidence=m.group(0))
        for m in matches
        if not _STATUTE_BEFORE_DATE.search(m.group("organ"))
    )
    return _verdict((*statements, *_blank_enactments(block, body)))


def _blank_enactments(block: str, body: str) -> tuple[DateStatement, ...]:
    """Enactment sentences whose date is a draft blank, which the date patterns never match."""
    sentences = [
        *(s for s in _SENTENCE_END.split(block) if _ENACTMENT_WORD.search(s)),
        *(s for s in _SENTENCE_END.split(body) if _SELF_ENACTMENT.search(s)),
    ]
    return tuple(
        DateStatement(date=None, text=None, evidence=s.strip())
        for s in sentences
        if has_placeholder_date(s)
    )


def stated_ikraft(regulation: ExtractedRegulation) -> StatedDate:
    """Every ``Ikrafttredelse:`` header line of the block and every ``trer i kraft`` clause."""
    headers = [
        _statement(line, header.start(1))
        for line in regulation.identification_block.split("\n")
        if (header := _IKRAFT_HEADER.match(line))
    ]
    text = regulation.full_text.replace("\n", " ")
    clauses = [_statement(text, cue.end(), cue.start()) for cue in _IKRAFT_CUE.finditer(text)]
    return _verdict((*headers, *clauses))


def _statement(text: str, rest_at: int, starts_at: int = 0) -> DateStatement:
    """The date the clause from ``rest_at`` opens with, else its phrase to the sentence end."""
    rest = text[rest_at : rest_at + _CLAUSE_CHARS]
    stated = _DATE_FIRST.match(rest)
    parsed = parse_stated_date(stated.groups()[-1]) if stated else None
    if stated is not None and parsed is not None:
        return DateStatement(
            date=parsed, text=None, evidence=text[starts_at : rest_at + stated.end()]
        )
    sentence_end = _SENTENCE_END.search(rest)
    phrase = rest[: sentence_end.start()] if sentence_end else rest
    evidence = text[starts_at : rest_at + len(phrase)].strip()
    return DateStatement(date=None, text=phrase.strip() or None, evidence=evidence)


def _verdict(statements: tuple[DateStatement, ...]) -> StatedDate:
    if not statements:
        return StatedDate(status="absent")
    if any(has_placeholder_date(s.evidence) for s in statements):
        return StatedDate(status="held", hold_reason="placeholder_date", statements=statements)
    dates = {s.date for s in statements if s.date is not None}
    phrases = {s.text for s in statements if s.date is None and s.text}
    if len(dates) + len(phrases) > 1:
        return StatedDate(
            status="held", hold_reason="conflicting_statements", statements=statements
        )
    if dates:
        return StatedDate(status="stated", value=dates.pop(), statements=statements)
    if phrases:
        return StatedDate(status="text", text=phrases.pop(), statements=statements)
    return StatedDate(status="absent", statements=statements)
