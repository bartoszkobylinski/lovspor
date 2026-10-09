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

* ``title`` — the title line, with the lines a broken heading continues on
  (``title.py``); a title cut off on a function word is held as
  ``title_truncated``;
* ``hjemmel`` — ``Hjemmel: LOV-…`` header references and ``med hjemmel (i) …`` /
  ``i medhold av …`` phrases in the block, to the end of their sentence;
* ``vedtatt_av`` — the organ of the first ``vedtatt av <organ> <dato>``
  (or ``fastsatt``, ``vedteke``) in the block, else of the body's
  ``Forskriften er vedtatt av <organ> den <dato>``. The organ stops at
  ``med hjemmel`` / ``i medhold av``, and a date right after a law's name is
  that statute's date, never the vedtaksdato;
* ``vedtatt`` / ``ikraft`` / ``ikraft_text`` — the date (or, for ``ikraft``,
  the non-date phrase such as "straks") that **every** statement of the text
  agrees on, as ``stated_dates.py`` resolves it for the evidence sidecar
  (#581). When the statements disagree the date is held there, and is
  ``None`` here: the front matter never shows a date the evidence does not
  stand behind.

``ExtractedRegulation.vedtaksdato`` is the date of the *first* enactment
clause, not the resolved one: it seeds the ``lk-`` id (``identity.py``), and an
id must not move when a later statement of the same text is read differently.
"""

from __future__ import annotations

import re
from datetime import date

from lovspor.promotion.anchors import first_section, first_title
from lovspor.promotion.dates import parse_stated_date
from lovspor.promotion.models import (
    ExtractedRegulation,
    ExtractionHoldReason,
    RegulationFields,
)
from lovspor.promotion.stated_dates import (
    _ENACTED,
    _LAW_NAME,
    _SELF_ENACTED,
    _STATUTE_BEFORE_DATE,
    stated_ikraft,
    stated_vedtatt,
)
from lovspor.promotion.title import read_title

_HJEMMEL_HEADER = re.compile(r"(?:hjemmel|heimel)\s*:\s*((?:LOV|FOR)-.+)", re.I)
_LOVDATA_REFERENCE_SPLIT = re.compile(r",\s*(?=(?:LOV|FOR)-)")
# "med hjemmel lov 9. juni 2023" drops the "i"; without it, the phrase must
# name a law or a regulation, so "med hjemmel som nevnt" is not a hjemmel.
_HJEMMEL_PHRASE = re.compile(
    rf"(?:med\s+(?:hjemmel|heimel)\s+(?:i\s+|(?={_LAW_NAME}))"
    r"|i\s+med(?:hold|hald)\s+av\s+)(.+?)"
    r"(?:\.(?=\s+(?-i:[A-ZÆØÅ]))|\.?\s*$)",
    re.I,
)

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
    title = read_title(block)
    if title is None:
        return ExtractionHoldReason.TITLE_TRUNCATED
    vedtaksdato, vedtatt_av = _enactment(" ".join(block), " ".join(body))
    regulation = ExtractedRegulation(
        title=title,
        identification_block="\n".join(block),
        body="\n".join(body),
        vedtaksdato=vedtaksdato,
    )
    return regulation, _fields(regulation, block, vedtatt_av)


def _fields(
    regulation: ExtractedRegulation, block: tuple[str, ...], vedtatt_av: str | None
) -> RegulationFields:
    vedtatt, ikraft = stated_vedtatt(regulation), stated_ikraft(regulation)
    return RegulationFields(
        title=regulation.title,
        hjemmel=_hjemmel(block),
        vedtatt=vedtatt.value,
        vedtatt_av=vedtatt_av,
        ikraft=ikraft.value,
        ikraft_text=ikraft.text,
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
