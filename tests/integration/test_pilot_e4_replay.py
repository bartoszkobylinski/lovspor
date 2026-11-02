"""Replay every E4 verdict from the real Lovdata archive, offline.

Raw Lovdata XML is never committed, so this runs only where
`scripts/pilot_e4.py` left the archive in the gitignored cache. A skip here means
the Lovdata half was not replayed on that machine; it is not a pass.
"""

import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import pytest

from lovspor.legislative_chain import (
    parse_law_publication,
    read_lovtidend_member,
    resolve_chain,
)
from lovspor.stortinget_case_source import CaseSnapshot, LawDecision, parse_sak

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = json.loads(
    (ROOT / "docs/research/data/pilot-e4-evidence-2026-11-01.json").read_text(encoding="utf-8")
)
ARCHIVE = ROOT / "data/cache/pilot-e4/lovdata/lovtidend-avd1-2001-2025.tar.bz2"
pytestmark = pytest.mark.skipif(not ARCHIVE.exists(), reason="no local Lovdata archive")


def _case(sak_id: int):
    record = next(r for r in EVIDENCE["stortinget"] if r["sak_id"] == sak_id)
    body = (ROOT / "tests/fixtures/stortinget" / f"sak-{sak_id}.xml").read_bytes()
    return parse_sak(
        CaseSnapshot(
            sak_id,
            record["source_url"],
            datetime.fromisoformat(record["retrieved_at"]),
            record["content_type"],
            sha256(body).hexdigest(),
            body,
        )
    )


def test_archive_is_the_captured_one() -> None:
    assert sha256(ARCHIVE.read_bytes()).hexdigest() == EVIDENCE["lovdata_archive"]["sha256"]


@pytest.mark.parametrize(
    "claim", EVIDENCE["claims"], ids=lambda c: f"{c['label']}-{c['law_id']}-{c['sak_id']}"
)
def test_verdict_replays_from_real_bytes(claim: dict) -> None:
    member = read_lovtidend_member(ARCHIVE, claim["law_id"])
    assert member is not None
    assert (member.name, member.sha256) == (
        claim["lovdata_member"],
        claim["lovdata_member_sha256"],
    )
    decision = LawDecision(**claim["decision"])
    verdict = resolve_chain(parse_law_publication(member), _case(claim["sak_id"]), decision)
    assert (verdict.status, verdict.law_edge, verdict.case_edge) == (
        claim["status"],
        claim["law_edge"],
        claim["case_edge"],
    )
