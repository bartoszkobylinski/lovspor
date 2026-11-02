"""Offline reading of a crosswalk capture: the manifest and its write-once cache (#610).

Moved from scripts/study_52.py so the study and the producer read one way.
Nothing here touches the network. Every cached object is read through its
manifest record and must still hash to the digest recorded at capture; a
mismatch is a refusal, never a silent re-read. A listing or case whose bytes do
not parse is reported as not read, so it can never turn into "no candidates" or
"not listed".
"""

import json
from collections.abc import Callable, Iterable
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from lovspor.crosswalk.errors import CrosswalkError
from lovspor.errors import LovsporError
from lovspor.legislative_chain import (
    CHAIN_PARSER_VERSION,
    ODELSTING_PARSER_VERSION,
    LovtidendMember,
    cited_odelsting_decisions,
    parse_law_publication,
    read_lovtidend_members,
)
from lovspor.stortinget_case_source import CaseSnapshot, LawDecision
from lovspor.stortinget_discovery import (
    ListingSnapshot,
    SakHint,
    candidate_saks,
    parse_case,
    parse_lovvedtak_listing,
    parse_sak_listing,
    previous_session,
)

MANIFEST = "capture-manifest.json"
LAW_PARSER_VERSIONS = {
    "lovtidend_identity": CHAIN_PARSER_VERSION,
    "lovtidend_besl_o": ODELSTING_PARSER_VERSION,
}
Manifest = dict[str, Any]
Entry = dict[str, Any]
_LISTING_PARSERS: dict[str, Callable[[ListingSnapshot], Any]] = {
    "saker": parse_sak_listing,
    "publikasjoner": parse_lovvedtak_listing,
}


def load_manifest(cache: Path) -> Manifest:
    try:
        loaded: Manifest = json.loads((cache / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CrosswalkError(f"capture manifest unreadable in {cache}") from exc
    return loaded


def write_manifest(cache: Path, manifest: Manifest) -> None:
    text = json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True)
    (cache / MANIFEST).write_text(text + "\n", encoding="utf-8")


def latest(manifest: Manifest, stage: str, kind: str, key: object) -> Entry | None:
    """The last capture of one object in one stage; earlier attempts stay in the manifest."""
    found = [
        r for r in manifest["stortinget"] if (r["stage"], r["kind"], r["key"]) == (stage, kind, key)
    ]
    return found[-1] if found else None


def _inside(cache: Path, relative: str) -> Path:
    path = (cache / relative).resolve()
    if not path.is_relative_to(cache.resolve()):
        raise CrosswalkError(f"manifest path leaves the cache: {relative}")
    return path


def cached_body(cache: Path, record: Entry | None) -> bytes | None:
    """The bytes a successful capture stored, verified against their recorded digest."""
    if record is None or record["error"] is not None:
        return None
    body = _inside(cache, record["path"]).read_bytes()
    if sha256(body).hexdigest() != record["sha256"]:
        raise CrosswalkError(f"cached bytes do not match the manifest: {record['path']}")
    return body


def listing_snapshot(
    cache: Path, manifest: Manifest, kind: str, session: str
) -> ListingSnapshot | None:
    record = latest(manifest, "discovery", kind, session)
    body = cached_body(cache, record)
    if body is None or record is None:
        return None
    observed = datetime.fromisoformat(record["retrieved_at"])
    return ListingSnapshot(
        kind,  # type: ignore[arg-type]
        session,
        record["source_url"],
        observed,
        record["content_type"],
        record["sha256"],
        body,
    )


def parsed_listing(cache: Path, manifest: Manifest, kind: str, session: str) -> Any:
    """A listing's parsed content, or None if it was not read or its bytes do not parse."""
    listing = listing_snapshot(cache, manifest, kind, session)
    if listing is None:
        return None
    try:
        return _LISTING_PARSERS[kind](listing)
    except LovsporError:
        return None


def listing_parse_error(cache: Path, manifest: Manifest, kind: str, session: str) -> str | None:
    """The parse failure of a listing that was read, kept as a source diagnostic."""
    listing = listing_snapshot(cache, manifest, kind, session)
    if listing is None:
        return None
    try:
        _LISTING_PARSERS[kind](listing)
    except LovsporError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def case_snapshot(
    cache: Path, manifest: Manifest, stage: str, sak_id: int
) -> tuple[CaseSnapshot | None, Entry | None]:
    record = latest(manifest, stage, "sak", sak_id)
    body = cached_body(cache, record)
    if body is None or record is None:
        return None, record
    observed = datetime.fromisoformat(record["retrieved_at"])
    snapshot = CaseSnapshot(
        sak_id, record["source_url"], observed, record["content_type"], record["sha256"], body
    )
    return snapshot, record


def parses(snapshot: CaseSnapshot) -> bool:
    """A candidate whose bytes do not parse was not read; it is never a silent no."""
    try:
        parse_case(snapshot)
    except LovsporError:
        return False
    return True


def hints(cache: Path, manifest: Manifest, decision: LawDecision) -> list[SakHint] | None:
    """Henvisning hints from both sessions; None if either listing was not read or parsed."""
    sessions = (decision.session_id, previous_session(decision.session_id))
    parsed = [parsed_listing(cache, manifest, "saker", session) for session in sessions]
    if any(found is None for found in parsed):
        return None
    return [hint for found in parsed for hint in found]


def discovery_candidates(
    cache: Path, manifest: Manifest, decisions: Iterable[LawDecision]
) -> list[int]:
    found: set[int] = set()
    for decision in decisions:
        found.update(candidate_saks(hints(cache, manifest, decision) or [], decision))
    return sorted(found)


# --- law edge (Lovdata) ------------------------------------------------------------


def read_archives(
    cache: Path, archives: list[Entry], law_ids: list[str]
) -> dict[str, list[tuple[Entry, LovtidendMember]]]:
    """Every member of every pinned archive that claims each act; none is chosen here."""
    found: dict[str, list[tuple[Entry, LovtidendMember]]] = {i: [] for i in law_ids}
    for record in archives:
        path = _inside(cache, record["path"])
        members = read_lovtidend_members(path, law_ids)
        for law_id, matches in members.items():
            found[law_id].extend((record, member) for member in matches)
    return found


def _match(archive: Entry, member: LovtidendMember) -> Entry:
    return {
        "archive": archive["filename"],
        "member": member.name,
        "sha256": member.sha256,
        "archive_source_url": archive.get("source_url"),
        "archive_sha256": archive.get("sha256"),
        "archive_retrieved_at": archive.get("retrieved_at"),
        "archive_last_modified": archive.get("last_modified"),
    }


def law_header(member: LovtidendMember) -> Entry:
    try:
        lovvedtak = parse_law_publication(member).lovvedtak
        besl_o = cited_odelsting_decisions(member)
    except LovsporError as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"lovvedtak": [asdict(d) for d in lovvedtak], "besl_o": [asdict(d) for d in besl_o]}


def law_edge(header: Entry) -> Entry:
    """What the Lovtidend header alone says about the decision behind the act."""
    lovvedtak, besl_o = header.get("lovvedtak", []), header.get("besl_o", [])
    if "error" in header:
        return {"status": "unresolved", "kind": "unreadable", "reason": header["error"]}
    if len(lovvedtak) > 1:
        return {"status": "ambiguous", "kind": "several", "reason": "header cites several"}
    if lovvedtak:
        return {"status": "attested", "kind": "lovvedtak", "decision": lovvedtak[0]}
    if besl_o:
        reason = "header cites Besl. O.; the reader types Lovvedtak only"
        return {"status": "unsupported", "kind": "besl_o", "reason": reason}
    return {"status": "unresolved", "kind": "none", "reason": "header cites no decision"}


def law_record(law_id: str, matches: list[tuple[Entry, LovtidendMember]]) -> Entry:
    """One act's identity and law -> decision edge, from exactly one archive member."""
    record: Entry = {"amending_lov_id": law_id, "law_year": int(law_id[4:8])}
    record["lovdata_matches"] = [_match(archive, member) for archive, member in matches]
    record["parser_versions"] = dict(LAW_PARSER_VERSIONS)
    if len(matches) != 1:
        status = "unresolved" if not matches else "ambiguous"
        reason = "no member in either archive" if not matches else "several members"
        record["law_identity"] = {"status": status, "reason": reason}
        record["law_decision"] = {"status": status, "kind": "not_read", "reason": reason}
        return record
    header = law_header(matches[0][1])
    identity = "unresolved" if "error" in header else "attested"
    record["law_identity"] = {"status": identity, "reason": header.get("error")}
    record["header"] = header
    record["law_decision"] = law_edge(header)
    return record


def law_records(cache: Path, archives: list[Entry], law_ids: Iterable[str]) -> list[Entry]:
    ordered = sorted(set(law_ids))
    found = read_archives(cache, archives, ordered)
    return [law_record(law_id, found[law_id]) for law_id in ordered]


def cited_decisions(laws: Iterable[Entry]) -> list[LawDecision]:
    """Every Lovvedtak a header cites, even where the law edge is ambiguous."""
    cited = {
        LawDecision(**decision)
        for law in laws
        for decision in law.get("header", {}).get("lovvedtak", [])
    }
    return sorted(cited, key=lambda d: (d.session_id, d.number))


def listing_keys(decisions: Iterable[LawDecision]) -> list[tuple[str, str]]:
    """The decision session's Lovvedtak list, and the saker of it and of the session before."""
    keys = set()
    for decision in decisions:
        keys.add(("publikasjoner", decision.session_id))
        keys.add(("saker", decision.session_id))
        keys.add(("saker", previous_session(decision.session_id)))
    return sorted(keys)
