"""Source-pinned, experimental Stortinget sak XML acquisition (ADR-0017).

No Lovdata ingestion, no Politik dependency, no automatic legal-law linkage.
A captured XML blob is evidence of the *retrieved API body*, not of legal validity.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

import httpx
from lxml import etree

_NS = "http://data.stortinget.no"
_ENDPOINT = "https://data.stortinget.no/eksport/sak"
_LIMIT = 2_000_000
_REF = re.compile(r"^Lovvedtak\s+(\d{1,4})\s*\((\d{4})-(\d{4})\)$", re.I)
_SESSION = re.compile(r"^\d{4}-\d{4}$")
Status = Literal["attested", "ambiguous", "unresolved", "contradicted"]


@dataclass(frozen=True)
class LawDecision:
    session_id: str
    number: int


@dataclass(frozen=True)
class CaseSnapshot:
    sak_id: int
    source_url: str
    observed_at: datetime
    content_type: str
    source_sha256: str
    body: bytes


@dataclass(frozen=True)
class CaseMetadata:
    sak_id: int
    session_id: str
    lovvedtak: tuple[LawDecision, ...]
    source_sha256: str


def _read_limited(response: httpx.Response) -> bytes:
    """Bound the decoded HTTP response rather than trusting Content-Length."""
    chunks: list[bytes] = []
    used = 0
    for chunk in response.iter_bytes():
        used += len(chunk)
        if used > _LIMIT:
            raise ValueError("Stortinget sak response exceeds byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_response(response: httpx.Response) -> str:
    response.raise_for_status()
    url = str(response.request.url)
    if not url.startswith(f"{_ENDPOINT}?"):
        raise ValueError("Unexpected source endpoint")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
    if content_type not in {"application/xml", "text/xml"}:
        raise ValueError("Expected an XML response from Stortinget")
    return content_type


def fetch_sak(
    client: httpx.Client, sak_id: int, *, observed_at: datetime | None = None
) -> CaseSnapshot:
    """Retrieve exactly one official case; never follow a location redirect."""
    if type(sak_id) is not int or sak_id <= 0:
        raise ValueError("sak_id must be a positive integer")
    observed = observed_at or datetime.now(UTC)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("observed_at must be timezone aware")
    with client.stream("GET", _ENDPOINT, params={"sakid": str(sak_id)}, follow_redirects=False) as r:
        content_type = _validate_response(r)
        body = _read_limited(r)
        return CaseSnapshot(
            sak_id, str(r.request.url), observed, content_type, sha256(body).hexdigest(), body
        )


def _field(element: etree._Element, name: str) -> str:
    return (element.findtext(f"{{{_NS}}}{name}") or "").strip()


def _law_decision(ref: etree._Element, case_session: str) -> LawDecision | None:
    if _field(ref, "type") != "lovvedtak":
        return None
    match = _REF.fullmatch(_field(ref, "lenke_tekst"))
    if match is None:
        return None
    number, first, last = (int(group) for group in match.groups())
    if last != first + 1 or f"{first:04d}-{last:04d}" != case_session:
        return None
    return LawDecision(case_session, number)


def _decisions(root: etree._Element, session: str) -> tuple[LawDecision, ...]:
    path = f"{{{_NS}}}publikasjon_referanse_liste/{{{_NS}}}publikasjon_referanse"
    found = (_law_decision(ref, session) for ref in root.findall(path))
    return tuple(sorted(set(dec for dec in found if dec is not None), key=lambda d: d.number))


def parse_sak(snapshot: CaseSnapshot) -> CaseMetadata:
    """Read only direct, typed references, not prose in a title or henvisning."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    try:
        root = etree.fromstring(snapshot.body, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise ValueError("Invalid Stortinget case XML") from exc
    if root.tag != f"{{{_NS}}}detaljert_sak" or root.getroottree().docinfo.doctype:
        raise ValueError("Unexpected XML root or forbidden DTD")
    if _field(root, "id") != str(snapshot.sak_id):
        raise ValueError("Stortinget case id mismatch")
    session = _field(root, "sak_sesjon")
    if not _SESSION.fullmatch(session):
        raise ValueError("Invalid Stortinget session identity")
    return CaseMetadata(snapshot.sak_id, session, _decisions(root, session), snapshot.source_sha256)


def resolve_publication(case: CaseMetadata, expected: LawDecision) -> Status:
    """A typed reference proves the parliamentary decision, not the statute."""
    if len(case.lovvedtak) > 1:
        return "ambiguous"
    if not case.lovvedtak:
        return "unresolved"
    if case.lovvedtak[0] != expected:
        return "contradicted"
    return "attested"


def _store_once(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as output:
            output.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise ValueError("Archived Stortinget object hash/content mismatch") from None


def archive_sak(snapshot: CaseSnapshot, directory: Path) -> Path:
    """Keep immutable content and per-observation metadata outside Lovverk."""
    directory.mkdir(parents=True, exist_ok=True)
    name = f"sak-{snapshot.sak_id}-{snapshot.source_sha256}"
    blob = directory / f"{name}.xml"
    _store_once(blob, snapshot.body)
    identity = {"sak_id": snapshot.sak_id, "source_url": snapshot.source_url,
                "sha256": snapshot.source_sha256, "content_type": snapshot.content_type}
    _store_once(blob.with_suffix(".json"), json.dumps(identity, sort_keys=True).encode())
    observation_dir = directory / "observations"
    observation_dir.mkdir(exist_ok=True)
    timestamp = snapshot.observed_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    record = {"object": blob.name, "observed_at": snapshot.observed_at.isoformat()}
    _store_once(observation_dir / f"{name}-{timestamp}.json", json.dumps(record, sort_keys=True).encode())
    return blob
