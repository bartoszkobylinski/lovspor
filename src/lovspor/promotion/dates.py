"""Dates as a Norwegian regulation's text states them (ADR-0016 Decision 2).

Three written forms are read — ``12. desember 2019``, ``12.12.2019`` and
``2019-12-12`` — and nothing else. A form that is not a real calendar date
reads as ``None``: a valid-time fact is the text's or it is absent, never
repaired. A two-digit year is not read; which century it means is a guess.
"""

from __future__ import annotations

import re
from datetime import date

MONTHS = (
    "januar",
    "februar",
    "mars",
    "april",
    "mai",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "desember",
)
_MONTH_NUMBER = {name: number for number, name in enumerate(MONTHS, start=1)}

#: One stated date in any of the three forms; group 1 is the whole date.
DATE = r"(\d{1,2}\.\s*[^\W\d_]+\s+\d{4}|\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})"

_TEXTUAL_DATE = re.compile(r"(\d{1,2})\.\s*([^\W\d_]+)\s+(\d{4})")
_NUMERIC_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

# A draft's blank for a date still to be decided: "X.X.2016", "xx.xx.2016",
# "dd.mm.åååå", "__.__.2016". A real decision date is never written this way.
_PLACEHOLDER_DATE = re.compile(
    r"(?<![\w.])(?:[xX]{1,2}|dd|_{1,2})\.\s?(?:[xX]{1,2}|mm|_{1,2})\.\s?"
    r"(?:\d{4}|[xX]{4}|åååå|yyyy)(?!\w)"
)
# The same blank written with spaces, "xx xx xxxx". Two x's each for day and
# month: a lone "X" is a roman numeral ("kapittel X").
_SPACED_PLACEHOLDER_DATE = re.compile(
    r"(?<![\w.])[xX]{2}\s{1,3}[xX]{2}\s{1,3}(?:\d{4}|[xX]{4})(?!\w)"
)
# "(dato)" left blank in the enactment clause: "Fastsett av kommunestyret
# (dato)". Bound to the clause, since a form in an annex may ask for "(dato)".
_BLANK_ENACTMENT_DATE = re.compile(
    r"\b(?:fastsatt|fastsett|vedtatt|vedteke|vedteken|vedtekne)\b[^.]{0,120}?\(\s*dato\s*\)",
    re.IGNORECASE,
)
_PLACEHOLDERS = (_PLACEHOLDER_DATE, _SPACED_PLACEHOLDER_DATE, _BLANK_ENACTMENT_DATE)


def parse_stated_date(text: str) -> date | None:
    """A real calendar date in one of the three written forms, else ``None``."""
    try:
        return _date_parts(text.strip())
    except ValueError:
        return None


def has_placeholder_date(text: str) -> bool:
    """True when the text carries a draft's blank date, such as ``X.X.2016``."""
    return any(pattern.search(text) is not None for pattern in _PLACEHOLDERS)


def _date_parts(text: str) -> date | None:
    textual = _TEXTUAL_DATE.fullmatch(text)
    if textual:
        month = _MONTH_NUMBER.get(textual.group(2).casefold())
        if month is None:
            return None
        return date(int(textual.group(3)), month, int(textual.group(1)))
    numeric = _NUMERIC_DATE.fullmatch(text)
    if numeric:
        return date(int(numeric.group(3)), int(numeric.group(2)), int(numeric.group(1)))
    iso = _ISO_DATE.fullmatch(text)
    if iso:
        return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    return None
