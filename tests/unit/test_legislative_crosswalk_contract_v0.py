"""Research-only ADR-0017 crosswalk proposal; no source attestation or runtime consumer."""

import csv
import io
import json
from hashlib import sha1
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "docs/research/schemas/legislative-crosswalk-v0.schema.json"
LEDGER_PATH = ROOT / "tests/fixtures/legislative-crosswalk-52-web-reviewed.csv"
SOURCE_BLOB_SHA = "956bbcba172bbb5a8d670b332c05437ab3e6cd2a"
LOVVERK_COMMIT = "2ab982d253c9470ddb885172b36c10687539c910"
RESEARCH_COMMIT = "e63713017dc4f1a42663731d1d805b37f18f9660"
CORRECTED_LAW = "LOV-2025-06-20-67"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def _ledger() -> list[dict[str, str]]:
    payload = LEDGER_PATH.read_bytes()
    header = b"blob " + str(len(payload)).encode("ascii") + b"\0"
    assert sha1(header + payload, usedforsecurity=False).hexdigest() == SOURCE_BLOB_SHA
    return list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))


def _evidence(row: dict[str, str], role: str, url: str) -> dict[str, str]:
    return {
        "role": role,
        "source_url": url,
        "capture_level": "web_page_review",
        "observed_on": row["verified_at_utc_date"],
    }


def _sanctions(row: dict[str, str]) -> list[dict[str, str]]:
    law_id = row["amending_lov_id"]
    if law_id != CORRECTED_LAW:
        return [{"law_id": law_id, "kind": "initial_sanction",
                 "source_url": row["verified_pair_source_url"]}]
    return [
        {"law_id": "LOV-2025-04-25-13", "kind": "initial_sanction",
         "source_url": "https://lovdata.no/lov/2025-04-25-13"},
        {"law_id": CORRECTED_LAW, "kind": "corrective_resanction",
         "corrects_law_id": "LOV-2025-04-25-13",
         "source_url": "https://lovdata.no/dokument/LTI/lov/2025-06-20-67"},
    ]


def _candidate(row: dict[str, str]) -> dict[str, object]:
    return {
        "schema_version": "0.1.0-research", "release_state": "research_only",
        "origin_lovverk_commit": LOVVERK_COMMIT,
        "origin_research_commit": RESEARCH_COMMIT,
        "observed_on": row["verified_at_utc_date"],
        "status": "reviewed_web_only",
        "amending_lov_id": row["amending_lov_id"],
        "decision": {
            "kind": "besl_o" if row["proceeding_kind"] == "Besl.O" else "lovvedtak",
            "session_id": row["session_id"], "number": int(row["decision_number"]),
        },
        "sak_id": int(row["sak_id"]),
        "sanction_events": _sanctions(row),
        "evidence": [
            _evidence(row, "law_decision_pair", row["verified_pair_source_url"]),
            _evidence(row, "decision_case", row["official_storting_case_url"]),
        ],
    }


def _record() -> dict[str, object]:
    return _candidate(_ledger()[0])


def _raw(role: str) -> dict[str, str]:
    return {
        "role": role, "source_url": "https://data.stortinget.no/eksport/sak?sakid=43206",
        "capture_level": "raw_body_captured", "observed_on": "2026-10-14",
        "upstream_id": "synthetic-fixture-only", "retrieved_at": "2026-10-14T12:00:00Z",
        "body_sha256": "a" * 64, "parser_version": "fixture-v0",
    }


def test_schema_is_valid_draft202012() -> None:
    Draft202012Validator.check_schema(SCHEMA)


def test_pinned_52_web_reviews_validate_without_promoting_them() -> None:
    rows = _ledger()
    assert len(rows) == 52
    assert len({row["amending_lov_id"] for row in rows}) == 52
    for row in rows:
        record = _candidate(row)
        VALIDATOR.validate(record)
        assert record["status"] == "reviewed_web_only"
        assert record["release_state"] == "research_only"
        assert all(item["capture_level"] == "web_page_review"
                   for item in record["evidence"])


def test_legacy_odelsting_is_a_separate_typed_decision() -> None:
    rows = [row for row in _ledger() if row["proceeding_kind"] == "Besl.O"]
    assert len(rows) == 1
    record = _candidate(rows[0])
    assert record["amending_lov_id"] == "LOV-2009-06-19-107"
    assert record["decision"] == {"kind": "besl_o", "session_id": "2008-2009", "number": 87}


def test_corrective_resanction_preserves_both_legal_instruments() -> None:
    row = next(row for row in _ledger() if row["amending_lov_id"] == CORRECTED_LAW)
    record = _candidate(row)
    events = record["sanction_events"]
    assert [event["law_id"] for event in events] == [
        "LOV-2025-04-25-13", "LOV-2025-06-20-67",
    ]
    assert events[1]["kind"] == "corrective_resanction"
    assert events[1]["corrects_law_id"] == events[0]["law_id"]
    VALIDATOR.validate(record)


def test_corrective_sanction_cannot_drop_correction_link() -> None:
    record = _candidate(next(row for row in _ledger()
                             if row["amending_lov_id"] == CORRECTED_LAW))
    del record["sanction_events"][1]["corrects_law_id"]
    with pytest.raises(ValidationError):
        VALIDATOR.validate(record)


def test_initial_sanction_cannot_claim_correction() -> None:
    record = _record()
    record["sanction_events"][0]["corrects_law_id"] = "LOV-2025-04-25-13"
    with pytest.raises(ValidationError):
        VALIDATOR.validate(record)


def test_web_review_is_not_attested_api_evidence() -> None:
    record = _record()
    record["status"] = "attested"
    with pytest.raises(ValidationError):
        VALIDATOR.validate(record)


def test_attested_shape_needs_three_distinct_raw_evidence_roles() -> None:
    record = _record()
    record["status"] = "attested"
    for role in ("law_identity", "law_decision_pair", "decision_case"):
        record["evidence"].append(_raw(role))
    VALIDATOR.validate(record)
    for role in ("law_identity", "law_decision_pair", "decision_case"):
        reduced = {**record, "evidence": [
            item for item in record["evidence"]
            if item["role"] != role or item["capture_level"] != "raw_body_captured"
        ]}
        with pytest.raises(ValidationError):
            VALIDATOR.validate(reduced)


def test_raw_capture_must_include_retrieval_and_digest() -> None:
    record = _record()
    record["evidence"].append(_raw("law_identity"))
    del record["evidence"][-1]["body_sha256"]
    with pytest.raises(ValidationError):
        VALIDATOR.validate(record)


def test_web_review_cannot_fake_raw_digest() -> None:
    record = _record()
    record["evidence"][0]["body_sha256"] = "a" * 64
    with pytest.raises(ValidationError):
        VALIDATOR.validate(record)


def test_partial_unresolved_record_is_not_joined_by_guess() -> None:
    record = _record()
    record.update(status="unresolved", reason="No official decision identity",
                  decision=None, sak_id=None, evidence=[], sanction_events=[])
    VALIDATOR.validate(record)
    del record["reason"]
    with pytest.raises(ValidationError):
        VALIDATOR.validate(record)


def test_no_votes_authorship_or_validity_dates_in_law_crosswalk() -> None:
    record = _record()
    for forbidden in ("vote_ids", "authored_by", "valid_at"):
        with pytest.raises(ValidationError):
            VALIDATOR.validate({**record, forbidden: "invented"})


def test_official_source_url_does_not_accept_lookalike_host() -> None:
    record = _record()
    record["evidence"][0]["source_url"] = "https://www.stortinget.no.evil.invalid/x"
    with pytest.raises(ValidationError):
        VALIDATOR.validate(record)


def test_draft_must_not_be_marked_as_production_export() -> None:
    record = _record()
    record["release_state"] = "production"
    with pytest.raises(ValidationError):
        VALIDATOR.validate(record)
