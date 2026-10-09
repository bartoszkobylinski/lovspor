"""The regulation's title, read across the lines its heading is broken over (issue #592).

The title line (``anchors.first_title``) is where the title starts, not
always all of it. A PDF heading set in capitals breaks where the line ends
(``FORSKRIFT OM`` / ``SKULEREGLAR`` / ``FOR GRUNNSKULEN I …``), and the reader
rejoins a wrap only when the continuation reads as one (lowercase, after a
comma), never an uppercase line. So the title takes the next line of the
identification block while

* it ends on a function word (``om``, ``for``, ``i``, ``ved``, ``av``,
  ``til``, ``og``) — no title ends there, so the line was cut; or
* it is set in capitals and the next line is too, opening on a function word.

A line that states the enactment or the hjemmel is never part of a title, and
a title stays within ``MAX_TITLE_CHARS``. A title that still ends on a
function word is cut off: it is returned as ``None`` and the extractor holds
the text (``title_truncated``), since a truncated title names the file, mints
the ``lk-`` id and is what the reviewer approves.
"""

from __future__ import annotations

import re

from lovspor.promotion.anchors import MAX_TITLE_CHARS

FUNCTION_WORDS = frozenset({"om", "for", "i", "ved", "av", "til", "og"})
_FIELD_LINE = re.compile(
    r"(?:vedtatt|vedteke|fastsatt|fastsett|(?:med\s+)?(?:hjemmel|heimel)|i\s+med(?:hold|hald))",
    re.IGNORECASE,
)


def read_title(block: tuple[str, ...]) -> str | None:
    """The title the block's first lines spell, or ``None`` when it ends cut off."""
    title = block[0]
    for line in block[1:]:
        if not _continues(title, line):
            break
        title = f"{title} {line}"
    return None if _dangles(title.split()[-1]) else title


def _continues(title: str, line: str) -> bool:
    if _FIELD_LINE.match(line) or len(title) + len(line) >= MAX_TITLE_CHARS:
        return False
    capitals = title.isupper() and line.isupper() and _dangles(line.split(maxsplit=1)[0])
    return capitals or _dangles(title.rsplit(maxsplit=1)[-1])


def _dangles(word: str) -> bool:
    return word.casefold() in FUNCTION_WORDS
