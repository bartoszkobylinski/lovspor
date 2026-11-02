"""PROPOSED v0.2 crosswalk record draft (#610); owner approval required, no runtime use."""

import copy
import json
from hashlib import sha256
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "docs/research/schemas"
PROPOSAL = json.loads((SCHEMAS / "legislative-crosswalk-v0.2-proposal.schema.json").read_text())
V0 = json.loads((SCHEMAS / "legislative-crosswalk-v0.schema.json").read_text())
EVIDENCE = json.loads((ROOT / "docs/research/data/pilot-e4-evidence-2026-11-01.json").read_text())
VALIDATOR = Draft202012Validator(PROPOSAL, format_checker=FormatChecker())
V0_VALIDATOR = Draft202012Validator(V0, format_checker=FormatChecker())


def _identity_digest(identity: object) -> str:
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _parsed_identities() -> dict[str, str]:
    claim = next(item for item in EVIDENCE["claims"] if item["label"] == "primary")
    case = next(item for item in EVIDENCE["stortinget"] if item["sak_id"] == claim["sak_id"])
    law = {"law_id": claim["law_id"], "lovvedtak": claim["lovdata_cited_decisions"]}
    sak = {"sak_id": case["sak_id"], "sak_sesjon": case["sak_sesjon"]}
    sak["lovvedtak"] = case["typed_lovvedtak"]
    return {"law": _identity_digest(law), "case": _identity_digest(sak)}


def _archive_pin() -> dict[str, object]:
    archive = EVIDENCE["lovdata_archive"]
    return {
        "filename": archive["source_url"].rsplit("/", 1)[1],
        "source_url": archive["source_url"],
        "sha256": archive["sha256"],
        "size_bytes": archive["size_bytes"],
        "last_modified": archive["last_modified"],
        "retrieved_at": archive["retrieved_at"],
        "licence": "NLOD-2.0",
        "licence_basis": "stated_for_file",
    }


def _pins(v0: dict[str, object]) -> dict[str, object]:
    return {
        "lovverk_commit": v0["origin_lovverk_commit"],
        "producer_commit": v0["origin_research_commit"],
        "parsers": {"lovtidend": "lovtidend-identity-1", "stortinget_sak": "sak-xml-2"},
        "lovdata_archives": [_archive_pin()],
    }


def _pilot_record() -> dict[str, object]:
    v0 = copy.deepcopy(EVIDENCE["contract_v0_record"])
    digests = _parsed_identities()
    for item in v0["evidence"]:
        item["parsed_identity_sha256"] = digests[
            "case" if item["role"] == "decision_case" else "law"
        ]
    return {
        "schema_version": "0.2.0-proposal",
        "release_state": "research_only",
        "amending_lov_id": v0["amending_lov_id"],
        "record_revision": 1,
        "record_state": "active",
        "observed_on": v0["observed_on"],
        "status": "attested",
        "edge_status": {"law_decision": "attested", "decision_case": "attested"},
        "decision": v0["decision"],
        "sak_id": v0["sak_id"],
        "sanction_events": v0["sanction_events"],
        "evidence": v0["evidence"],
        "source_pins": _pins(v0),
    }


def _invalid(record: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        VALIDATOR.validate(record)


def test_proposal_schema_is_valid_draft202012() -> None:
    Draft202012Validator.check_schema(PROPOSAL)


def test_v0_schema_stays_the_research_contract_politik_reads() -> None:
    assert V0["properties"]["schema_version"] == {"const": "0.1.0-research"}
    assert V0["properties"]["release_state"] == {"const": "research_only"}


def test_pilot_e4_chain_validates_as_proposed_v0_2_record() -> None:
    record = _pilot_record()
    VALIDATOR.validate(record)
    V0_VALIDATOR.validate(EVIDENCE["contract_v0_record"])


def test_v0_2_record_is_not_silently_readable_as_v0() -> None:
    with pytest.raises(ValidationError):
        V0_VALIDATOR.validate(_pilot_record())


@pytest.mark.parametrize("state", ["released", "production"])
def test_no_release_state_beyond_candidate_exists(state: str) -> None:
    _invalid({**_pilot_record(), "release_state": state})


def test_inferred_archive_licence_blocks_a_release_candidate() -> None:
    record = _pilot_record()
    record["source_pins"]["lovdata_archives"][0]["licence_basis"] = "inferred_same_dataset"
    VALIDATOR.validate(record)
    _invalid({**record, "release_state": "release_candidate"})


def test_attested_needs_pinned_archive_and_both_edges_attested() -> None:
    record = _pilot_record()
    record["source_pins"]["lovdata_archives"] = []
    _invalid(record)
    for edge in ("law_decision", "decision_case"):
        record = _pilot_record()
        record["edge_status"][edge] = "ambiguous"
        _invalid(record)


@pytest.mark.parametrize("field", ["body_sha256", "parsed_identity_sha256"])
def test_raw_capture_carries_both_provenance_digest_and_parsed_identity(field: str) -> None:
    record = _pilot_record()
    del record["evidence"][0][field]
    _invalid(record)


def test_odelsting_decision_is_explicitly_unsupported() -> None:
    record = _pilot_record()
    record.update(
        decision={"kind": "besl_o", "session_id": "2008-2009", "number": 87},
        status="unsupported",
        edge_status={"law_decision": "unsupported", "decision_case": "unsupported"},
        reason="Reader covers Lovvedtak only",
        reason_code="decision_kind_not_supported",
    )
    VALIDATOR.validate(record)
    _invalid({**record, "status": "unresolved"})
    _invalid({**record, "reason_code": "other"})


def test_corrective_resanction_requires_correction_evidence() -> None:
    record = _pilot_record()
    record["sanction_events"] = [
        {
            "law_id": "LOV-2022-06-17-63",
            "kind": "corrective_resanction",
            "corrects_law_id": "LOV-2022-06-17-62",
            "source_url": "https://www.regjeringen.no/synthetic-fixture-not-a-real-notice/",
        }
    ]
    _invalid(record)
    record["evidence"].append(
        {
            "role": "correction",
            "source_url": "https://www.regjeringen.no/synthetic-fixture-not-a-real-notice/",
            "capture_level": "web_page_review",
            "observed_on": "2026-11-01",
        }
    )
    VALIDATOR.validate(record)


def test_unclassified_sanction_cannot_claim_a_correction() -> None:
    record = _pilot_record()
    record["sanction_events"][0]["kind"] = "unclassified_sanction"
    VALIDATOR.validate(record)
    record["sanction_events"][0]["corrects_law_id"] = "LOV-2022-06-17-62"
    _invalid(record)


def test_withdrawal_is_a_new_revision_with_a_reason() -> None:
    record = {**_pilot_record(), "record_revision": 2, "record_state": "withdrawn"}
    _invalid(record)
    record["supersedes_revision"] = 1
    _invalid(record)
    record["withdrawal"] = {"withdrawn_on": "2026-11-02", "reason": "Parser defect"}
    VALIDATOR.validate(record)


def test_first_revision_supersedes_nothing_and_active_has_no_withdrawal() -> None:
    _invalid({**_pilot_record(), "supersedes_revision": 1})
    withdrawal = {"withdrawn_on": "2026-11-02", "reason": "x"}
    _invalid({**_pilot_record(), "withdrawal": withdrawal})


@pytest.mark.parametrize("status", ["unresolved", "ambiguous", "contradicted", "unsupported"])
def test_partial_status_requires_reason_and_reason_code(status: str) -> None:
    record = {**_pilot_record(), "status": status, "reason": "No citation"}
    _invalid(record)
    record["reason_code"] = "no_decision_citation_in_source"
    VALIDATOR.validate(record)
