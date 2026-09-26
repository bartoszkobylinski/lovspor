"""String fields that mean "somebody actually filled this in" (issue #207).

``Field(min_length=1)`` counts characters, and a space is a character, so a
field declared mandatory admits ``" "``. Where the field carries attribution —
who reviewed a clearance, who authorised a removal — a blank one is worse than
a missing one: it reads as though somebody signed it.

Two shapes, because the right answer to surrounding whitespace depends on
where the value lives:

``NonBlankStr``
    Refuses a blank value and stores everything else exactly as given. For
    records read back from files that are fingerprinted or rewritten from what
    was read (the register, the observation and sweep logs): a read that
    edits a value would make the in-memory record disagree with the bytes it
    came from.

``TrimmedNonBlankStr``
    Takes surrounding whitespace off, then refuses an empty result. For values
    typed by an operator at the moment they are recorded, where a trailing
    space is the same name and a log holding two spellings of one person
    cannot be grouped by who decided what.
"""

from typing import Annotated

from pydantic import AfterValidator, Field


def _refuse_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


def _trim_and_refuse_blank(value: str) -> str:
    return _refuse_blank(value.strip())


NonBlankStr = Annotated[str, Field(min_length=1), AfterValidator(_refuse_blank)]
TrimmedNonBlankStr = Annotated[str, Field(min_length=1), AfterValidator(_trim_and_refuse_blank)]
