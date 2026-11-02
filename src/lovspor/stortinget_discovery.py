"""Label-free discovery of the Stortinget case behind one Lovvedtak (#610, ADR-0017).

The session's sak listing only proposes candidates: its free-text `henvisning`
is a search hint and never a link. A case is accepted only when its own typed
`publikasjon_referanse` names the decision, by link text and by `eksport_id`.
No title, date or fuzzy join takes part, and no manually located label is read.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

import httpx
from lxml import etree

from lovspor.errors import NetworkError, ParseError, StortingetStatusError
from lovspor.parsing.xml_normalizer import safe_parser
from lovspor.stortinget_case_source import CaseMetadata, CaseSnapshot, LawDecision, fetch_sak
from lovspor.stortinget_case_source import parse_sak as _parse_sak

DISCOVERY_PARSER_VERSION = "lovvedtak-discovery-2"
_NS = "http://data.stortinget.no"
_LINK = re.compile(r"^Lovvedtak\s+(\d{1,4})\s*\((\d{4})-(\d{4})\)$", re.I)
_ENDPOINTS = {
    "saker": "https://data.stortinget.no/eksport/saker",
    "publikasjoner": "https://data.stortinget.no/eksport/publikasjoner",
}
_LISTING_LIMIT = 20_000_000
_MAX_RANGE = 400
_OK = 200
_SESSION = re.compile(r"^(\d{4})-(\d{4})$")
_ITEM = r"\d{1,4}(?:\s*[-\u2013]\s*\d{1,4})?"
_HENVISNING = re.compile(
    rf"\bLovvedtak\s+({_ITEM}(?:\s*(?:,|\bog\b)\s*{_ITEM})*)"
    r"\s*\(\s*(\d{4})\s*[-\u2013]\s*(\d{4})\s*\)",
    re.I,
)
_RANGE = re.compile(r"(\d{1,4})(?:\s*[-\u2013]\s*(\d{1,4}))?")

ListingKind = Literal["saker", "publikasjoner"]
Outcome = Literal["discovered", "not_found", "ambiguous", "incomplete"]
Drift = Literal["identical", "drift_in_bytes_only", "identity_changed"]


@dataclass(frozen=True)
class ListingSnapshot:
    kind: ListingKind
    session_id: str
    source_url: str
    observed_at: datetime
    content_type: str
    source_sha256: str
    body: bytes


@dataclass(frozen=True)
class SakHint:
    sak_id: int
    henvisning: str


@dataclass(frozen=True)
class Discovery:
    """`sak_ids` are the typed confirmations; `candidates` only what the hint proposed."""

    decision: LawDecision
    outcome: Outcome
    sak_ids: tuple[int, ...]
    candidates: tuple[int, ...]
    failed: tuple[int, ...]


@dataclass(frozen=True)
class Observation:
    sha256: str
    identity: tuple[str, ...]


def _session_years(session_id: str) -> tuple[int, int]:
    match = _SESSION.fullmatch(session_id)
    if match is None or int(match[2]) != int(match[1]) + 1:
        raise ParseError(f"Not a Stortinget session: {session_id!r}")
    return int(match[1]), int(match[2])


def previous_session(session_id: str) -> str:
    first, _ = _session_years(session_id)
    return f"{first - 1:04d}-{first:04d}"


def export_id(decision: LawDecision) -> str:
    """The publication id of a Lovvedtak, as both the listing and the case carry it."""
    first, last = _session_years(decision.session_id)
    return f"vedtak-{first:04d}{last % 100:02d}-{decision.number:03d}"


def _expand(numbers: str) -> set[int]:
    found: set[int] = set()
    for low, high in _RANGE.findall(numbers):
        start, end = int(low), int(high or low)
        if 1 <= start <= end <= start + _MAX_RANGE:
            found.update(range(start, end + 1))
    return found


def henvisning_numbers(text: str, session_id: str) -> frozenset[int]:
    """Lovvedtak numbers a henvisning names for one session: singles, ranges and lists."""
    found: set[int] = set()
    for numbers, first, last in _HENVISNING.findall(text):
        if f"{first}-{last}" == session_id:
            found |= _expand(numbers)
    return frozenset(found)


def candidate_saks(hints: Iterable[SakHint], decision: LawDecision) -> tuple[int, ...]:
    named = {
        hint.sak_id
        for hint in hints
        if decision.number in henvisning_numbers(hint.henvisning, decision.session_id)
    }
    return tuple(sorted(named))


def _checked_listing(response: httpx.Response, endpoint: str) -> str:
    if response.status_code != _OK:
        raise StortingetStatusError(response.status_code, str(response.request.url))
    if not str(response.request.url).startswith(f"{endpoint}?"):
        raise ParseError("Unexpected Stortinget endpoint")
    header: str = response.headers.get("content-type", "")
    media_type = header.partition(";")[0].strip().lower()
    if media_type not in {"application/xml", "text/xml"}:
        raise ParseError("Expected an XML listing from Stortinget")
    return media_type


def _bounded(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    used = 0
    for chunk in response.iter_bytes():
        used += len(chunk)
        if used > _LISTING_LIMIT:
            raise ParseError("Stortinget listing exceeds byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _listing_params(kind: ListingKind, session_id: str) -> dict[str, str]:
    _session_years(session_id)
    if kind == "publikasjoner":
        return {"publikasjontype": "lovvedtak", "sesjonid": session_id}
    return {"sesjonid": session_id}


def fetch_listing(client: httpx.Client, kind: ListingKind, session_id: str) -> ListingSnapshot:
    """One session listing; never follows a redirect, never reads a refusal as empty."""
    endpoint, params = _ENDPOINTS[kind], _listing_params(kind, session_id)
    observed = datetime.now(UTC)
    try:
        with client.stream("GET", endpoint, params=params, follow_redirects=False) as response:
            content_type = _checked_listing(response, endpoint)
            body = _bounded(response)
    except httpx.TransportError as exc:
        raise NetworkError(f"Stortinget {kind} {session_id}: {type(exc).__name__}") from exc
    url = str(response.request.url)
    digest = sha256(body).hexdigest()
    return ListingSnapshot(kind, session_id, url, observed, content_type, digest, body)


def fetch_case(client: httpx.Client, sak_id: int) -> CaseSnapshot:
    """`fetch_sak` with every refusal mapped into the LovsporError family."""
    try:
        return fetch_sak(client, sak_id)
    except httpx.HTTPStatusError as exc:
        raise StortingetStatusError(exc.response.status_code, str(exc.request.url)) from exc
    except httpx.TransportError as exc:
        raise NetworkError(f"Stortinget sak {sak_id}: {type(exc).__name__}") from exc
    except ValueError as exc:
        raise ParseError(f"Stortinget sak {sak_id}: {exc}") from exc


def parse_case(snapshot: CaseSnapshot) -> CaseMetadata:
    try:
        return _parse_sak(snapshot)
    except ValueError as exc:
        raise ParseError(f"Stortinget sak {snapshot.sak_id}: {exc}") from exc


def _listing_root(body: bytes, root_name: str) -> etree._Element:
    if b"<!DOCTYPE" in body.upper():
        raise ParseError("Forbidden XML DTD")
    try:
        root = etree.fromstring(body, parser=safe_parser())
    except etree.XMLSyntaxError as exc:
        raise ParseError("Invalid Stortinget listing XML") from exc
    if root.getroottree().docinfo.internalDTD is not None:
        raise ParseError("Forbidden XML DTD")
    if root.tag != f"{{{_NS}}}{root_name}":
        raise ParseError("Unexpected Stortinget listing root")
    return root


def _text(element: etree._Element, name: str) -> str:
    return (element.findtext(f"{{{_NS}}}{name}") or "").strip()


def _hint(sak: etree._Element) -> SakHint:
    sak_id = _text(sak, "id")
    if not sak_id.isdigit() or not sak_id.isascii():
        raise ParseError(f"Invalid sak id in listing: {sak_id!r}")
    return SakHint(int(sak_id), _text(sak, "henvisning"))


def parse_sak_listing(snapshot: ListingSnapshot) -> tuple[SakHint, ...]:
    root = _listing_root(snapshot.body, "saker_oversikt")
    return tuple(_hint(sak) for sak in root.findall(f"{{{_NS}}}saker_liste/{{{_NS}}}sak"))


def parse_lovvedtak_listing(snapshot: ListingSnapshot) -> frozenset[str]:
    """The session's Lovvedtak publication ids: the decision exists independently of any case."""
    root = _listing_root(snapshot.body, "publikasjoner_oversikt")
    found = root.findall(f"{{{_NS}}}publikasjoner_liste/{{{_NS}}}publikasjon")
    return frozenset(_text(pub, "id") for pub in found if _text(pub, "type") == "lovvedtak")


def _typed_references(snapshot: CaseSnapshot) -> frozenset[tuple[str, str]]:
    """(link text, export id) of each direct, typed lovvedtak reference, kept together."""
    parse_case(snapshot)
    root = etree.fromstring(snapshot.body, parser=safe_parser())
    path = f"{{{_NS}}}publikasjon_referanse_liste/{{{_NS}}}publikasjon_referanse"
    refs = (ref for ref in root.findall(path) if _text(ref, "type") == "lovvedtak")
    return frozenset((_text(ref, "lenke_tekst"), _text(ref, "eksport_id")) for ref in refs)


def typed_export_ids(snapshot: CaseSnapshot) -> frozenset[str]:
    """Export ids of the case's direct, typed lovvedtak references."""
    return frozenset(export for _, export in _typed_references(snapshot))


def _link_names(link: str, decision: LawDecision) -> bool:
    match = _LINK.fullmatch(link)
    if match is None:
        return False
    number, first, last = (int(group) for group in match.groups())
    return (f"{first:04d}-{last:04d}", number) == (decision.session_id, decision.number)


def confirms(snapshot: CaseSnapshot, decision: LawDecision) -> bool:
    """One typed reference must name this exact decision by link text AND export id."""
    if decision not in parse_case(snapshot).lovvedtak:
        return False
    wanted = export_id(decision)
    pairs = _typed_references(snapshot)
    return any(export == wanted and _link_names(link, decision) for link, export in pairs)


def discover(
    decision: LawDecision, cases: Sequence[CaseSnapshot], failed: Iterable[int] = ()
) -> Discovery:
    """Exactly one typed confirmation discovers; a candidate not read leaves it incomplete."""
    unread = tuple(sorted(set(failed)))
    confirmed = tuple(sorted({case.sak_id for case in cases if confirms(case, decision)}))
    candidates = tuple(sorted({case.sak_id for case in cases} | set(unread)))
    outcome: Outcome = "discovered"
    if len(confirmed) > 1:
        outcome = "ambiguous"
    elif unread:
        outcome = "incomplete"
    elif not confirmed:
        outcome = "not_found"
    return Discovery(decision, outcome, confirmed, candidates, unread)


def case_identity(case: CaseMetadata) -> tuple[str, ...]:
    """What a capture says, free of the server timestamp its bytes carry."""
    decisions = (f"lovvedtak:{d.session_id}:{d.number}" for d in case.lovvedtak)
    return (f"sak:{case.sak_id}", f"sesjon:{case.session_id}", *decisions)


def classify_drift(before: Observation, after: Observation) -> Drift:
    """Compare captures by parsed identity; a digest alone changes with every response time."""
    if before.identity != after.identity:
        return "identity_changed"
    return "identical" if before.sha256 == after.sha256 else "drift_in_bytes_only"
