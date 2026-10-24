"""Where a regulation starts in extracted lines: its title line and first section (ADR-0016 S2).

One definition, read by two layers: ``fields.py`` splits the lines at these
anchors, and ``source_text.py`` uses them to tell which ``<article>`` of a
page holds the regulation (issue #576). Two copies of the rules could drift
apart, and then the reader would narrow to an article the splitter reads
differently. This module imports nothing from the package, so both can
depend on it without either importing the other.

The **first section** is the first line opening ``§ 1`` or ``Kapittel 1``
that is not a table-of-contents entry (``§1 Heimel ........ 3``: text, a
dot-leader run, an optional page number), which names the section without
opening it (#597); the **title line** is the first line before it that names
a ``forskrift`` (``Forskrift om …``, ``Lokal forskrift for …``) and is short
enough to be a title.
"""

from __future__ import annotations

import re
from string import digits

MAX_TITLE_CHARS = 250
_TITLE = re.compile(
    r"(?:[^\W\d_]+\s+){0,2}[^\W\d_]*forskrift(?:er)?\s+(?:om|for|til|ved|av|\d)", re.I
)
_FIRST_SECTION = re.compile(r"(?:§\s*1(?!\d)|kap(?:ittel|\.)\s*(?:1|I)(?![\w]))", re.I)
# Four dots: "..." closes an elided sentence, a leader is longer. An ellipsis
# character stands for three.
_LEADER_DOTS = 4
_LEADER_CHARS = ". \u2026"


def is_contents_entry(line: str) -> bool:
    """Whether ``line`` is a table-of-contents entry: text, a dot leader, maybe a page number."""
    without_page = line.rstrip(digits).rstrip()
    text = without_page.rstrip(_LEADER_CHARS)
    leader = without_page[len(text) :]
    dots = leader.count(".") + 3 * leader.count("\u2026")
    return bool(text) and dots >= _LEADER_DOTS


def first_section(lines: tuple[str, ...]) -> int | None:
    """The index of the first section line, or ``None``."""
    return next(
        (
            i
            for i, line in enumerate(lines)
            if _FIRST_SECTION.match(line) and not is_contents_entry(line)
        ),
        None,
    )


def first_title(lines: tuple[str, ...]) -> int | None:
    """The index of the first title line, or ``None``."""
    return next(
        (i for i, line in enumerate(lines) if len(line) <= MAX_TITLE_CHARS and _TITLE.match(line)),
        None,
    )


def anchor_lines(lines: tuple[str, ...]) -> tuple[str, str] | None:
    """The title line and first-section line, or ``None`` when the lines cannot be split."""
    body_start = first_section(lines)
    if body_start is None:
        return None
    title_index = first_title(lines[:body_start])
    if title_index is None:
        return None
    return lines[title_index], lines[body_start]
