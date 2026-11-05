"""One act's chain, as read offline from a capture, as a 1.0.0-rc record (#610, P3).

Every value comes from the cached bytes through the parsers that read them, or
from the capture manifest; no label, no wall clock and no inference enters a
record. A record always leaves the build `research_only`: promotion is the
separate, explicit release-candidate gate's work, never the build's.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from lovspor.crosswalk.cache import Entry
from lovspor.crosswalk.contract import SCHEMA_VERSION
from lovspor.crosswalk.identity import (
    besl_o_identity,
    case_identity,
    identity_sha256,
    law_identity,
    publication_identity,
)
from lovspor.legislative_chain import CHAIN_PARSER_VERSION, ODELSTING_PARSER_VERSION
from lovspor.stortinget_case_source import SAK_PARSER_VERSION
from lovspor.stortinget_discovery import DISCOVERY_PARSER_VERSION

Record = dict[str, Any]
RAW = "raw_body_captured"
_WORST_FIRST = ("contradicted", "ambiguous", "unsupported", "unresolved")
_IDENTITY_CODES = {
    "no member in either archive": "law_not_in_pinned_archives",
    "several members": "multiple_candidates",
}
_DECISION_CODES = {
    "besl_o": "decision_kind_not_supported",
    "none": "no_decision_citation_in_source",
    "several": "multiple_candidates",
    "unreadable": "source_unreadable",
    "not_read": "law_not_in_pinned_archives",
}
_CASE_CODES = {
    "unlisted": "decision_not_in_publication_list",
    "ambiguous": "multiple_candidates",
    "not_found": "case_not_found",
    "incomplete": "source_unreadable",
    "not_attempted": "case_not_found",
}


@dataclass(frozen=True)
class Context:
    """What every record of one build shares, read once from the capture."""

    pins: list[Record]
    commits: tuple[str, str]
    sanctioned: dict[str, list[Entry]]
    discoveries: dict[str, Entry]
    publications: dict[str, frozenset[str] | None]


def decision_key(decision: object) -> str:
    return repr(sorted(decision.items())) if isinstance(decision, dict) else ""


def edge_status(law: Entry) -> dict[str, str]:
    law_decision = law["law_decision"]["status"]
    if law_decision == "attested":
        case = law["chain"]["status"]
    else:
        case = "unsupported" if law_decision == "unsupported" else "unresolved"
    return {
        "law_identity": law["law_identity"]["status"],
        "law_decision": law_decision,
        "decision_case": case,
    }


def overall_status(edges: dict[str, str]) -> str:
    """Attested only when every edge is; otherwise the worst edge, no edge repairing another."""
    found = set(edges.values())
    return next((status for status in _WORST_FIRST if status in found), "attested")


def reason(law: Entry, edges: dict[str, str]) -> tuple[str, str]:
    """The reason and code of the first edge, in chain order, that is not attested."""
    if edges["law_identity"] != "attested":
        text = str(law["law_identity"]["reason"])
        return text, _IDENTITY_CODES.get(text, "source_unreadable")
    if edges["law_decision"] != "attested":
        return str(law["law_decision"]["reason"]), _DECISION_CODES[law["law_decision"]["kind"]]
    outcome = law["discovery"]["outcome"]
    return str(law["chain"]["reason"]), _CASE_CODES[outcome]


def decision_of(law: Entry) -> Record | None:
    edge, header = law["law_decision"], law.get("header", {})
    if edge["status"] == "attested":
        return {"kind": "lovvedtak", **edge["decision"]}
    if edge.get("kind") == "besl_o" and len(header.get("besl_o", [])) == 1:
        return {"kind": "besl_o", **header["besl_o"][0]}
    return None


def _member_evidence(law: Entry, role: str, parser: str, identity: Record) -> Record:
    match = law["lovdata_matches"][0]
    return {
        "role": role,
        "source_url": match["archive_source_url"],
        "capture_level": RAW,
        "upstream_id": f"{match['archive']}!{match['member']}",
        "body_sha256": match["sha256"],
        "parsed_identity_sha256": identity_sha256(identity),
        "retrieved_at": match["archive_retrieved_at"],
        "parser_version": parser,
    }


def law_evidence(law: Entry) -> list[Record]:
    """The act's own Lovtidend document, once per edge it supports."""
    if law["law_identity"]["status"] != "attested":
        return []
    law_id, header = law["amending_lov_id"], law["header"]
    cited = law_identity(law_id, header["lovvedtak"])
    found = [_member_evidence(law, "law_identity", CHAIN_PARSER_VERSION, cited)]
    if law["law_decision"]["status"] == "attested":
        found.append(_member_evidence(law, "law_decision_pair", CHAIN_PARSER_VERSION, cited))
    elif law["law_decision"]["kind"] == "besl_o":
        odelsting = besl_o_identity(law_id, header["besl_o"])
        found.append(
            _member_evidence(law, "law_decision_pair", ODELSTING_PARSER_VERSION, odelsting)
        )
    return found


def _listing_evidence(listing: Entry, identity: Record) -> Record:
    return {
        "role": "decision_publication",
        "source_url": listing["source_url"],
        "capture_level": RAW,
        "upstream_id": f"publikasjoner/{listing['key']}",
        "body_sha256": listing["sha256"],
        "parsed_identity_sha256": identity_sha256(identity),
        "retrieved_at": listing["retrieved_at"],
        "parser_version": DISCOVERY_PARSER_VERSION,
    }


def publication_evidence(found: Entry | None, ctx: Context) -> list[Record]:
    """The session's Lovvedtak list, when it was read and parsed."""
    if found is None:
        return []
    session = found["decision"]["session_id"]
    ids = ctx.publications.get(session)
    listings = [x for x in found["listings"] if x["kind"] == "publikasjoner"]
    if ids is None or len(listings) != 1 or listings[0]["error"] is not None:
        return []
    return [_listing_evidence(listings[0], publication_identity(session, ids))]


def case_evidence(found: Entry | None, edges: dict[str, str]) -> list[Record]:
    """The one confirmed case, only when the decision -> case edge is attested."""
    if found is None or edges["decision_case"] != "attested":
        return []
    case = found["cases"][0]
    identity = case_identity(case["sak_id"], case["sak_sesjon"], case["typed_lovvedtak"])
    return [
        {
            "role": "decision_case",
            "source_url": case["source_url"],
            "capture_level": RAW,
            "upstream_id": f"sak/{case['sak_id']}",
            "body_sha256": case["sha256"],
            "parsed_identity_sha256": identity_sha256(identity),
            "retrieved_at": case["retrieved_at"],
            "parser_version": SAK_PARSER_VERSION,
        }
    ]


def sanction_events(law: Entry, ctx: Context) -> list[Record]:
    """Every act in scope whose header cites this decision: never one law per decision (#618)."""
    if law["law_decision"]["status"] != "attested":
        return []
    sanctioned = ctx.sanctioned[decision_key(law["law_decision"]["decision"])]
    return [
        {
            "law_id": other["amending_lov_id"],
            "kind": "unclassified_sanction",
            "source_url": other["lovdata_matches"][0]["archive_source_url"],
        }
        for other in sanctioned
    ]


def _utc_date(stamp: str) -> str:
    return datetime.fromisoformat(stamp).astimezone(UTC).date().isoformat()


def observed_on(record: Record) -> str:
    stamps = [item["retrieved_at"] for item in record["evidence"]]
    stamps += [pin["retrieved_at"] for pin in record["source_pins"]["lovdata_archives"]]
    return max(_utc_date(stamp) for stamp in stamps)


def _skeleton(law: Entry, ctx: Context, edges: dict[str, str]) -> Record:
    lovverk_commit, producer_commit = ctx.commits
    return {
        "schema_version": SCHEMA_VERSION,
        "release_state": "research_only",
        "amending_lov_id": law["amending_lov_id"],
        "record_revision": 1,
        "record_state": "active",
        "status": overall_status(edges),
        "edge_status": edges,
        "decision": decision_of(law),
        "sak_id": law["chain"].get("sak_id") if edges["decision_case"] == "attested" else None,
        "source_pins": {
            "lovverk_commit": lovverk_commit,
            "producer_commit": producer_commit,
            "lovdata_archives": ctx.pins,
        },
    }


def build_record(law: Entry, ctx: Context) -> Record:
    """Revision 1 of the act's record; the build renumbers it against the previous release."""
    edges = edge_status(law)
    record = _skeleton(law, ctx, edges)
    found = ctx.discoveries.get(decision_key(law["law_decision"].get("decision")))
    record["sanction_events"] = sanction_events(law, ctx)
    evidence = law_evidence(law) + publication_evidence(found, ctx) + case_evidence(found, edges)
    record["evidence"] = evidence
    if record["status"] != "attested":
        record["reason"], record["reason_code"] = reason(law, edges)
    record["observed_on"] = observed_on(record)
    return record
