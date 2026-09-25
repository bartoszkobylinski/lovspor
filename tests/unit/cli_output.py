"""Read what a command said, not how the terminal happened to lay it out.

Rich renders help and usage errors inside a panel sized to the console, and
folds a token that does not fit *mid-word*: at width 20 ``release_content_id``
arrives as ``release_content_i`` and ``d`` on two lines, each framed by box
drawing. Stripping ANSI and rejoining lines on a space still leaves the token
split (#295). What survives every width is the sequence of printed characters,
so the comparison drops the escapes, the frame and all whitespace on both
sides and compares what remains.
"""

import re

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_BOX_DRAWING = re.compile(r"[\u2500-\u257f]")


def unwrapped(rendered: str) -> str:
    """Rendered output reduced to its printed characters, in order."""
    text = _BOX_DRAWING.sub("", _ANSI.sub("", rendered))
    return "".join(text.split())


def said(phrase: str, rendered: str) -> bool:
    """Whether ``phrase`` was printed, however rich wrapped or framed it."""
    return unwrapped(phrase) in unwrapped(rendered)
