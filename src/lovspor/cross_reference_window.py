"""The text around a ``§`` reference that may name the act it cites.

Moved out of ``lovspor.mcp`` with #1034, unchanged.
"""

import re

CONTEXT_CHARS = 80
"""Characters of context on each side of a ``§`` match to scan for
slug tokens during cross-reference resolution. 80 chars typically
captures a cue word (``i``, ``jf.``, ``iht.``) plus the slug
token; longer windows risk picking up an unrelated slug from the
previous sentence and false-positively classifying a same-act
ref as cross-act."""


def window_of(
    body_lower: str, matches: list[re.Match[str]], idx: int, owner_starts: dict[int, int]
) -> str:
    """The text around ``matches[idx]`` that may name its act.

    Bounded by the surrounding ``§`` matches so adjacent refs do not share context.
    Without this bound, ``"jf. § 5-13. Likevel kan det iht. § 9-3 i annen-lov"`` would
    resolve § 5-13 to ``annen-lov`` (which sits in § 9-3's clause) and falsely classify a
    same-act ref as cross-act. ``owner_starts`` further trims the AFTER-window when the
    next ``§`` has a slug owner that precedes it (slug-before-§, e.g. ``"annen-lov §
    9-3"``); without that trim, the next ``§``'s slug owner would leak back into it.
    """
    match = matches[idx]
    prev_end = matches[idx - 1].end() if idx > 0 else 0
    next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(body_lower)
    if idx in owner_starts:
        next_start = min(next_start, owner_starts[idx])
    start = max(prev_end, match.start() - CONTEXT_CHARS)
    return body_lower[start : min(next_start, match.end() + CONTEXT_CHARS)]
