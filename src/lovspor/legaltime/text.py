"""Text grammar shared by the replay: ordinals, dates, act citations, sentences.

Every function here is deterministic and pure. Nothing guesses: an ordinal
word outside the table, a date outside the month table, or a citation without
a day-month-year is ``None`` to the caller, which then records a gap.
"""

import re
from datetime import date

ORDINALS: dict[str, int] = {
    "første": 1, "fyrste": 1, "annet": 2, "andre": 2, "tredje": 3, "fjerde": 4,
    "femte": 5, "sjette": 6, "sjuende": 7, "syvende": 7, "åttende": 8, "niende": 9,
    "tiende": 10, "ellevte": 11, "tolvte": 12, "trettende": 13, "fjortende": 14,
    "femtende": 15, "sekstende": 16, "syttende": 17, "attende": 18, "nittende": 19,
    "tjuende": 20,
}  # fmt: skip
ORDINAL_ALT = "|".join(sorted(ORDINALS, key=len, reverse=True))

MONTHS: dict[str, int] = {
    "januar": 1, "februar": 2, "mars": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11,
    "desember": 12,
}  # fmt: skip
_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
DATE = re.compile(rf"\b(\d{{1,2}})\.\s*({_MONTH_ALT})\s+(\d{{4}})\b", re.I)
ACT_CITATION = re.compile(
    rf"\blov\s+(\d{{1,2}})\.\s*({_MONTH_ALT})\s+(\d{{4}})(?:\s+nr\.\s*(\d+))?", re.I
)
_SECTION_LABEL = re.compile(r"§\s*(\d+(?:-\d+)?)\s*([a-zæøå])?\b")
_ABBREVIATIONS = frozenset(
    {
        "jf",
        "nr",
        "mv",
        "bl.a",
        "f.eks",
        "kap",
        "pkt",
        "mfl",
        "osv",
        "ca",
        "evt",
        "dvs",
        "m.m",
        "m.v",
    }
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-ZÆØÅ«§(\d])")
_WHITESPACE = re.compile(r"\s+")


def collapse(text: str) -> str:
    """Whitespace-collapse and strip; the only normalisation applied to text."""
    return _WHITESPACE.sub(" ", text).strip()


def ordinal(word: str) -> int | None:
    return ORDINALS.get(word.lower())


def parse_date(text: str) -> date | None:
    """The first ``D. måned YYYY`` in ``text``, or None when there is none."""
    match = DATE.search(text)
    if match is None:
        return None
    return date(int(match.group(3)), MONTHS[match.group(2).lower()], int(match.group(1)))


def act_refid(text: str) -> str | None:
    """``lov/YYYY-MM-DD[-N]`` for the first act citation in ``text``."""
    match = ACT_CITATION.search(text)
    if match is None:
        return None
    day, month, year, number = match.groups()
    refid = f"lov/{int(year):04d}-{MONTHS[month.lower()]:02d}-{int(day):02d}"
    return refid if number is None else f"{refid}-{int(number)}"


def section_key(label: str) -> str | None:
    """``"§ 88 a"`` → ``"§88a"``, the ``data-name`` form the documents use."""
    match = _SECTION_LABEL.search(label)
    if match is None:
        return None
    number, letter = match.groups()
    return f"§{number}{letter or ''}"


def section_sort_key(key: str) -> tuple[int, int, str]:
    """Order ``§88`` < ``§88a`` < ``§89``; chapter-numbered ``§8-7`` by both parts."""
    match = re.fullmatch(r"§(\d+)(?:-(\d+))?([a-zæøå]?)", key)
    if match is None:
        return (10**9, 0, key)
    first, second, letter = match.groups()
    return (int(first), int(second or 0), letter)


def _ends_with_abbreviation(sentence: str) -> bool:
    last = sentence.rstrip(".").rsplit(" ", 1)[-1].lower()
    return last in _ABBREVIATIONS


def split_sentences(text: str) -> list[str]:
    """Split a ledd into punktum; abbreviations and ordinal numbers never split."""
    parts: list[str] = []
    for piece in _SENTENCE_END.split(collapse(text)):
        if parts and _ends_with_abbreviation(parts[-1]):
            parts[-1] = f"{parts[-1]} {piece}"
        else:
            parts.append(piece)
    return parts
