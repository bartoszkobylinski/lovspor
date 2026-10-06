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


#: Characters that end the host part of ``https://{host}/``. A scheme or path
#: brings ``/``; a query, a fragment and user-info bring ``? # @``; a port
#: brings ``:``. No stored domain has ever carried a port (the live register
#: holds 263 bare hosts), so one is refused rather than kept as an option.
_NOT_IN_A_HOST = frozenset("/?#@:")


def require_bare_host(value: str) -> str:
    """``value`` when it names a host and nothing else, or ``ValueError``.

    Shared by the register's ``canonical_domain`` and the survey's hosts
    (issue #557): a domain is interpolated into ``https://{host}/``, and
    ``kommune.no@evil.example`` there sends the request to evil.example under
    kommune.no's name. Refused, never rewritten — the caller sees what it typed.
    """
    if _NOT_IN_A_HOST.intersection(value) or any(char.isspace() for char in value):
        raise ValueError(f"a host without scheme or path was expected, got: {value!r}")
    return value


NonBlankStr = Annotated[str, Field(min_length=1), AfterValidator(_refuse_blank)]
TrimmedNonBlankStr = Annotated[str, Field(min_length=1), AfterValidator(_trim_and_refuse_blank)]
#: A bare host, stored exactly as given: case and a trailing dot are spellings
#: of one host, and comparisons normalise them (``registry.normalised_domain``).
BareHostStr = Annotated[NonBlankStr, AfterValidator(require_bare_host)]
