"""Lovdata ids of local regulations, read from a captured page's bytes (issue #509).

The kommuner mostly link to Lovdata rather than host a regulation's text, so
the ids they link are the catalogue of their local law that the archive can
build without fetching lovdata.no — which this project never does. The link
forms are the ones the 2026-10-03 classification scan found in the archive:

- ``lovdata.no/dokument/LF/forskrift/<id>`` — the compiled local regulation;
- ``lovdata.no/dokument/LTII/forskrift/<id>`` and ``lovdata.no/LTII/forskrift/<id>``
  — Norsk Lovtidend avd. II, where a local regulation and every amendment to it
  is announced;
- ``lovdata.no/forskrift/<id>`` — the short form. It does not say whether the
  regulation is local or central, so it is kept as kind ``forskrift`` and left
  to the reader to resolve; it is never promoted to ``LF`` here;
- ``lovdata.no/pro/#document/LF/forskrift/<id>`` — the subscription viewer.

Separators are matched as ``/``, ``\\/`` (a link inside JSON) and ``%2F`` (a
link inside a redirect or a "safe links" wrapper), because each occurs in
captured pages. The scan runs on bytes, never decoded text: a page that is not
valid UTF-8 still carries ASCII links, and decoding first would either lose
them or substitute characters into evidence.

Pure: a function of the bytes alone, with a fixed output order.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LovdataKind = Literal["LF", "LTII", "forskrift"]

#: A Lovdata document id: the date it was given, then its number in that year.
LF_ID_PATTERN = r"^\d{4}-\d{2}-\d{2}-\d+$"

_SEP = rb"(?:/|\\/|%2F)"
# The host must not be the tail of a longer name (``notlovdata.no``), except
# after ``%2F``: an encoded "//" ends in a letter and still begins a host.
_LINK = re.compile(
    rb"(?:(?<=%2F)|(?<=%2f)|(?<![A-Za-z0-9-]))lovdata\.no" + _SEP + rb"(?:"
    rb"(?:dokument" + _SEP + rb"|pro" + _SEP + rb"(?:#|%23)document" + _SEP + rb")?"
    rb"(?P<kind>LF|LTII)" + _SEP + rb"forskrift" + _SEP + rb"|forskrift" + _SEP + rb")"
    rb"(?P<id>\d{4}-\d{2}-\d{2}-\d+)",
    re.IGNORECASE,
)


class LovdataRef(BaseModel):
    """One linked regulation: which Lovdata collection the link named, and the id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: LovdataKind
    lf_id: str = Field(pattern=LF_ID_PATTERN)


def _ref(match: re.Match[bytes]) -> LovdataRef:
    kind = match.group("kind")
    named: LovdataKind = "forskrift" if kind is None else "LF" if kind.upper() == b"LF" else "LTII"
    return LovdataRef(kind=named, lf_id=match.group("id").decode("ascii"))


def extract_lovdata_refs(payload: bytes) -> tuple[LovdataRef, ...]:
    """Every distinct local-regulation link in ``payload``, sorted by kind then id.

    The literal host check comes first because the full pattern runs at about
    20 MB/s and a rebuild reads tens of gigabytes. Fewer than one page in ten
    names the host, and a plain substring test over the lowered bytes runs at
    more than 1 GB/s. Every form the pattern accepts contains ``lovdata.no``.
    """
    if b"lovdata.no" not in payload.lower():
        return ()
    found = {_ref(match) for match in _LINK.finditer(payload)}
    return tuple(sorted(found, key=lambda ref: (ref.kind, ref.lf_id)))
