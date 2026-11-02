"""Parsed identities: the comparison key across captures (proposal item g, ruling D8).

`body_sha256` says which bytes a run read; the parsed identity says what the
named parser extracted from them, and only that is compared between captures.
The field list is fixed per parser version; a change to it is a new parser
version. The digest is SHA-256 of the canonical JSON (sorted keys, no
whitespace, UTF-8).
"""

from collections.abc import Iterable
from hashlib import sha256
from typing import Any

from lovspor.crosswalk.canonical import canonical_json

Decisions = list[dict[str, Any]]


def identity_sha256(identity: dict[str, Any]) -> str:
    return sha256(canonical_json(identity).encode("utf-8")).hexdigest()


def law_identity(law_id: str, lovvedtak: Decisions) -> dict[str, Any]:
    """`lovtidend-identity-1`: the act and the Lovvedtak its header cites."""
    return {"law_id": law_id, "lovvedtak": lovvedtak}


def besl_o_identity(law_id: str, besl_o: Decisions) -> dict[str, Any]:
    """`besl-o-header-1`: the act and the Besl. O. its header cites."""
    return {"law_id": law_id, "besl_o": besl_o}


def case_identity(sak_id: int, sak_sesjon: str, lovvedtak: Decisions) -> dict[str, Any]:
    """`sak-xml-2`: the case, its session and its typed Lovvedtak references."""
    return {"sak_id": sak_id, "sak_sesjon": sak_sesjon, "lovvedtak": lovvedtak}


def publication_identity(session_id: str, ids: Iterable[str]) -> dict[str, Any]:
    """`lovvedtak-discovery-2`: the session and the sorted ids of its Lovvedtak list."""
    return {"session_id": session_id, "lovvedtak": sorted(ids)}
