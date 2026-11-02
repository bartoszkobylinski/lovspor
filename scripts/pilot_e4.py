"""Capture the E4 evidence for ADR-0017 (#610) from the live official sources.

Fetches the cases from data.stortinget.no and the Lovtidend archive from Lovdata
publicData into a gitignored cache, then writes the evidence file. The offline
replay is tests/integration/test_pilot_e4_replay.py.

Lovdata bytes stay in the cache: raw Lovdata XML is never committed.
"""

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from lovspor.legislative_chain import (
    CHAIN_PARSER_VERSION,
    parse_law_publication,
    read_lovtidend_member,
    resolve_chain,
)
from lovspor.sources.lovdata import LovdataClient
from lovspor.stortinget_case_source import (
    SAK_PARSER_VERSION,
    CaseMetadata,
    CaseSnapshot,
    LawDecision,
    archive_sak,
    fetch_sak,
    parse_sak,
)

ARCHIVE = "lovtidend-avd1-2001-2025.tar.bz2"
ARCHIVE_URL = f"https://api.lovdata.no/v1/publicData/get/{ARCHIVE}"
SAK_REQUEST_GAP_SECONDS = 0.7  # Stortinget's terms allow 100 calls a minute.
# (label, law, session, decision, sak): the expected link is the claim under test.
CLAIMS = (
    ("primary", "LOV-2022-06-17-63", "2021-2022", 85, 89016),
    ("one_case_two_acts", "LOV-2015-12-18-122", "2015-2016", 13, 63056),
    ("one_case_two_acts", "LOV-2015-12-18-123", "2015-2016", 14, 63056),
    ("one_decision_two_acts", "LOV-2025-04-25-13", "2024-2025", 41, 101001),
    ("one_decision_two_acts", "LOV-2025-06-20-67", "2024-2025", 41, 101001),
    ("no_lovvedtak_kind", "LOV-2009-06-19-107", "2008-2009", 87, 43206),
    ("wrong_case", "LOV-2022-06-17-63", "2021-2022", 85, 101001),
    ("absent_from_multi_decision_case", "LOV-2022-06-17-63", "2021-2022", 85, 63056),
)


def _claim_record(claim: tuple[Any, ...], archive: Path, case: CaseMetadata) -> dict[str, Any]:
    label, law_id, session, number, sak_id = claim
    member = read_lovtidend_member(archive, law_id)
    if member is None:
        return {"label": label, "law_id": law_id, "sak_id": sak_id, "status": "unresolved"}
    publication = parse_law_publication(member)
    verdict = resolve_chain(publication, case, LawDecision(session, number))
    return {
        "label": label,
        **json.loads(json.dumps(asdict(verdict))),
        "lovdata_member": member.name,
        "lovdata_member_sha256": member.sha256,
        "lovdata_cited_decisions": [asdict(decision) for decision in publication.lovvedtak],
        "stortinget_sak_sha256": case.source_sha256,
    }


def _capture_cases(directory: Path) -> tuple[dict[int, CaseMetadata], list[dict[str, Any]]]:
    cases: dict[int, CaseMetadata] = {}
    records: list[dict[str, Any]] = []
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        for sak_id in sorted({claim[4] for claim in CLAIMS}):
            snapshot = fetch_sak(client, sak_id)
            archive_sak(snapshot, directory / "stortinget")
            cases[sak_id] = parse_sak(snapshot)
            records.append(_sak_record(snapshot, cases[sak_id]))
            time.sleep(SAK_REQUEST_GAP_SECONDS)
    return cases, records


def _sak_record(snapshot: CaseSnapshot, case: CaseMetadata) -> dict[str, Any]:
    return {
        "sak_id": snapshot.sak_id,
        "source_url": snapshot.source_url,
        "retrieved_at": snapshot.observed_at.isoformat(),
        "content_type": snapshot.content_type,
        "sha256": snapshot.source_sha256,
        "size_bytes": len(snapshot.body),
        "sak_sesjon": case.session_id,
        "typed_lovvedtak": [asdict(decision) for decision in case.lovvedtak],
        "parser_version": SAK_PARSER_VERSION,
    }


def _capture_archive(directory: Path) -> tuple[Path, dict[str, Any]]:
    with LovdataClient() as client:
        catalogue = {entry.filename: entry for entry in client.list_datasets()}
        retrieved_at = datetime.now(UTC).isoformat()
        result = client.download(catalogue[ARCHIVE], directory / "lovdata")
    return result.path, {
        "source_url": ARCHIVE_URL,
        "retrieved_at": retrieved_at,
        "last_modified": catalogue[ARCHIVE].last_modified.isoformat(),
        "sha256": result.sha256,
        "size_bytes": result.size_bytes,
        "parser_version": CHAIN_PARSER_VERSION,
    }


def capture(directory: Path) -> dict[str, Any]:
    cases, sak_records = _capture_cases(directory)
    archive, archive_record = _capture_archive(directory)
    claims = [_claim_record(claim, archive, cases[claim[4]]) for claim in CLAIMS]
    return {"stortinget": sak_records, "lovdata_archive": archive_record, "claims": claims}


def _raw_evidence(role: str, url: str, upstream: str, digest: str, at: str) -> dict[str, str]:
    return {
        "role": role,
        "source_url": url,
        "capture_level": "raw_body_captured",
        "observed_on": at[:10],
        "upstream_id": upstream,
        "body_sha256": digest,
        "retrieved_at": at,
        "parser_version": SAK_PARSER_VERSION if "stortinget" in url else CHAIN_PARSER_VERSION,
    }


def contract_record(evidence: dict[str, Any], lovverk: str, research: str) -> dict[str, Any]:
    """Contract v0 record for the primary claim; release_state stays research_only."""
    claim = next(c for c in evidence["claims"] if c["label"] == "primary")
    sak = next(r for r in evidence["stortinget"] if r["sak_id"] == claim["sak_id"])
    archive = evidence["lovdata_archive"]
    upstream = f"{ARCHIVE}!{claim['lovdata_member']}"
    law = (archive["source_url"], upstream, claim["lovdata_member_sha256"], archive["retrieved_at"])
    return {
        "schema_version": "0.1.0-research",
        "release_state": "research_only",
        "origin_lovverk_commit": lovverk,
        "origin_research_commit": research,
        "observed_on": sak["retrieved_at"][:10],
        "status": claim["status"],
        "amending_lov_id": claim["law_id"],
        "decision": {"kind": "lovvedtak", **claim["decision"]},
        "sak_id": claim["sak_id"],
        "sanction_events": [
            {
                "law_id": claim["law_id"],
                "kind": "initial_sanction",
                "source_url": archive["source_url"],
            }
        ],
        "evidence": [
            _raw_evidence("law_identity", *law),
            _raw_evidence("law_decision_pair", *law),
            _raw_evidence(
                "decision_case",
                sak["source_url"],
                f"sak/{sak['sak_id']}",
                sak["sha256"],
                sak["retrieved_at"],
            ),
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path("data/cache/pilot-e4"))
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--lovverk-commit", required=True)
    parser.add_argument("--research-commit", required=True)
    args = parser.parse_args()
    evidence = capture(args.cache)
    evidence["contract_v0_record"] = contract_record(
        evidence, args.lovverk_commit, args.research_commit
    )
    text = json.dumps(evidence, indent=2, ensure_ascii=False, sort_keys=True)
    args.evidence.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
