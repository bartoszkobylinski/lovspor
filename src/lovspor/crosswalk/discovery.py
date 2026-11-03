"""Label-free discovery and the chain verdict over a capture, offline (#610, #768).

Moved from scripts/study_52.py with its behaviour unchanged; the study imports it.
The #617 labels are never read here: candidates come only from the saker
listings' hints, and acceptance only from a case's typed reference.

Evidence policy (#768): the session's Lovvedtak publication list is a REQUIRED,
independent confirmation that the decision exists. A typed case reference alone
never discovers: with the list unread or unparseable the outcome is "incomplete",
and with the decision absent from a list that was read it is "unlisted", which
the chain reports as contradicted. Neither can become attested.
"""

from dataclasses import asdict
from pathlib import Path
from typing import Any

from lovspor.crosswalk.cache import (
    Entry,
    Manifest,
    case_snapshot,
    hints,
    latest,
    listing_parse_error,
    parsed_listing,
    parses,
)
from lovspor.crosswalk.canonical import canonical_json
from lovspor.stortinget_case_source import SAK_PARSER_VERSION, CaseSnapshot, LawDecision
from lovspor.stortinget_discovery import (
    DISCOVERY_PARSER_VERSION,
    Discovery,
    candidate_saks,
    discover,
    export_id,
    parse_case,
    previous_session,
)

_UNCONFIRMED = {
    None: ("incomplete", "the session's Lovvedtak publication list was not read or parsed"),
    False: ("unlisted", "the decision is absent from the session's Lovvedtak publication list"),
}
_SUMMARY_KEYS = ("outcome", "reason", "sak_ids", "candidates", "failed")


def publication_listed(cache: Path, manifest: Manifest, decision: LawDecision) -> bool | None:
    """True or False from the session's Lovvedtak list; None if it was not read or parsed."""
    ids = parsed_listing(cache, manifest, "publikasjoner", decision.session_id)
    return None if ids is None else export_id(decision) in ids


def confirmed(outcome: str, listed: bool | None) -> tuple[str, str | None]:
    """Gate a discovery on the publication list; other outcomes pass unchanged."""
    if outcome != "discovered" or listed is True:
        return outcome, None
    return _UNCONFIRMED[listed]


def listings_used(cache: Path, manifest: Manifest, decision: LawDecision) -> list[Entry]:
    session = decision.session_id
    keys = (("publikasjoner", session), ("saker", session), ("saker", previous_session(session)))
    fields = ("kind", "key", "source_url", "sha256", "retrieved_at", "http_status", "error")
    used = [(key, latest(manifest, "discovery", *key)) for key in keys]
    return [
        {
            **{f: record.get(f) for f in fields},
            "parse_error": listing_parse_error(cache, manifest, *key),
        }
        for key, record in used
        if record is not None
    ]


def case_summary(snapshot: CaseSnapshot) -> Entry:
    case = parse_case(snapshot)
    return {
        "sak_id": case.sak_id,
        "sak_sesjon": case.session_id,
        "typed_lovvedtak": [asdict(d) for d in case.lovvedtak],
        "source_url": snapshot.source_url,
        "sha256": snapshot.source_sha256,
        "retrieved_at": snapshot.observed_at.isoformat(),
        "parser_version": SAK_PARSER_VERSION,
    }


def _base(cache: Path, manifest: Manifest, decision: LawDecision) -> Entry:
    base: Entry = {"decision": asdict(decision), "export_id": export_id(decision)}
    base["publication_listed"] = publication_listed(cache, manifest, decision)
    base["listings"] = listings_used(cache, manifest, decision)
    base["parser_versions"] = {
        "stortinget_discovery": DISCOVERY_PARSER_VERSION,
        "stortinget_sak": SAK_PARSER_VERSION,
    }
    return base


def _discovered(
    cache: Path, manifest: Manifest, decision: LawDecision, candidates: tuple[int, ...]
) -> tuple[Discovery, list[CaseSnapshot]]:
    pairs = [case_snapshot(cache, manifest, "discovery", sak_id) for sak_id in candidates]
    cases = [snapshot for snapshot, _ in pairs if snapshot is not None and parses(snapshot)]
    failed = [sak_id for sak_id in candidates if sak_id not in {c.sak_id for c in cases}]
    return discover(decision, cases, failed), cases


def discovery_record(cache: Path, manifest: Manifest, decision: LawDecision) -> Entry:
    base = _base(cache, manifest, decision)
    found_hints = hints(cache, manifest, decision)
    if found_hints is None:
        unread = "a saker listing was not read or did not parse"
        return {**base, "outcome": "incomplete", "reason": unread}
    candidates = candidate_saks(found_hints, decision)
    found, cases = _discovered(cache, manifest, decision, candidates)
    outcome, reason = confirmed(found.outcome, base["publication_listed"])
    return {
        **base,
        "outcome": outcome,
        "reason": reason,
        "sak_ids": list(found.sak_ids),
        "candidates": list(found.candidates),
        "failed": list(found.failed),
        "cases": [case_summary(s) for s in cases if s.sak_id in found.sak_ids],
    }


def chain(law: Entry, found: Entry | None) -> Entry:
    """The label-free chain: the header's decision, then a typed, unique case."""
    edge = law["law_decision"]
    if edge["status"] != "attested":
        return {"status": edge["status"], "reason": edge.get("reason")}
    if found is None or found["outcome"] in {"not_found", "incomplete"}:
        outcome = "not_attempted" if found is None else found["outcome"]
        detail = "" if found is None or not found.get("reason") else f": {found['reason']}"
        return {"status": "unresolved", "reason": f"discovery {outcome}{detail}"}
    if found["outcome"] == "unlisted":
        return {"status": "contradicted", "reason": f"discovery unlisted: {found['reason']}"}
    if found["outcome"] == "ambiguous":
        return {"status": "ambiguous", "reason": f"typed in saker {found['sak_ids']}"}
    return {"status": "attested", "reason": None, "sak_id": found["sak_ids"][0]}


def other_decisions(law: Entry, found: Entry | None) -> list[Any]:
    if found is None or found["outcome"] != "discovered":
        return []
    own = law["law_decision"]["decision"]
    return [d for d in found["cases"][0]["typed_lovvedtak"] if d != own]


def _key(decision: object) -> str:
    return canonical_json(decision)


def attach_discovery(laws: list[Entry], decisions: list[Entry]) -> None:
    """Give each law its decision's discovery summary and chain verdict, in place."""
    by_decision = {_key(d["decision"]): d for d in decisions}
    for law in laws:
        decision = law["law_decision"].get("decision")
        found = by_decision.get(_key(decision)) if decision else None
        summary = {k: found.get(k) for k in _SUMMARY_KEYS} if found else None
        law["discovery"] = summary or {"outcome": "not_attempted"}
        law["chain"] = chain(law, found)
        law["chain"]["other_case_decisions"] = other_decisions(law, found)
