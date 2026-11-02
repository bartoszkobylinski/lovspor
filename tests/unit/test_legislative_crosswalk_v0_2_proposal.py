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
EDGES = ("law_identity", "law_decision", "decision_case")
SYNTHETIC_NOTICE = "https://www.regjeringen.no/synthetic-fixture-not-a-real-notice/"


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
        "lovdata_archives": [_archive_pin()],
    }


def _pilot_record() -> dict[str, object]:
    """The pilot chain; its v0 `initial_sanction` becomes `unclassified_sanction` (item f)."""
    v0 = copy.deepcopy(EVIDENCE["contract_v0_record"])
    digests = _parsed_identities()
    for item in v0["evidence"]:
        item["parsed_identity_sha256"] = digests[
            "case" if item["role"] == "decision_case" else "law"
        ]
    for event in v0["sanction_events"]:
        event["kind"] = "unclassified_sanction"
    return {
        "schema_version": "0.2.0-proposal",
        "release_state": "research_only",
        "amending_lov_id": v0["amending_lov_id"],
        "record_revision": 1,
        "record_state": "active",
        "observed_on": v0["observed_on"],
        "status": "attested",
        "edge_status": dict.fromkeys(EDGES, "attested"),
        "decision": v0["decision"],
        "sak_id": v0["sak_id"],
        "sanction_events": v0["sanction_events"],
        "evidence": v0["evidence"],
        "source_pins": _pins(v0),
    }


def _unresolved(record: dict[str, object], **edges: str) -> dict[str, object]:
    record.update(status="unresolved", reason="Not attested", reason_code="case_not_found")
    record["edge_status"] = {**record["edge_status"], **edges}
    return record


def _evidence(record: dict[str, object], role: str) -> dict[str, object]:
    return next(item for item in record["evidence"] if item["role"] == role)


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


@pytest.mark.parametrize("basis", ["stated_for_file", "stated_for_endpoint"])
def test_a_stated_licence_admits_a_release_candidate(basis: str) -> None:
    record = _pilot_record()
    record["source_pins"]["lovdata_archives"][0]["licence_basis"] = basis
    VALIDATOR.validate({**record, "release_state": "release_candidate"})


def test_attested_needs_pinned_archive_and_every_edge_attested() -> None:
    record = _pilot_record()
    record["source_pins"]["lovdata_archives"] = []
    _invalid(record)
    for edge in EDGES:
        record = _pilot_record()
        record["edge_status"][edge] = "ambiguous"
        _invalid(record)


def test_status_is_derived_from_the_edges() -> None:
    """All edges attested means the record is attested, never a weaker status."""
    record = _pilot_record()
    record.update(status="unresolved", reason="x", reason_code="case_not_found")
    _invalid(record)


@pytest.mark.parametrize(
    ("edge", "role"),
    [
        ("law_identity", "law_identity"),
        ("law_decision", "law_decision_pair"),
        ("decision_case", "decision_case"),
    ],
)
def test_an_attested_edge_needs_its_own_raw_evidence(edge: str, role: str) -> None:
    other = next(name for name in EDGES if name != edge)
    record = _unresolved(_pilot_record(), **{other: "unresolved"})
    VALIDATOR.validate(record)
    record["evidence"] = [item for item in record["evidence"] if item["role"] != role]
    _invalid(record)


def test_attested_edges_need_the_identities_they_link() -> None:
    record = _unresolved(_pilot_record(), law_identity="unresolved")
    _invalid({**record, "decision": None})
    _invalid({**record, "sak_id": None})
    _invalid({**record, "sanction_events": []})


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
        edge_status={
            "law_identity": "attested",
            "law_decision": "unsupported",
            "decision_case": "unsupported",
        },
        reason="Reader covers Lovvedtak only",
        reason_code="decision_kind_not_supported",
    )
    VALIDATOR.validate(record)
    _invalid({**record, "status": "unresolved"})
    _invalid({**record, "reason_code": "other"})


def _corrective(record: dict[str, object], source_url: str) -> dict[str, object]:
    record["sanction_events"] = [
        {
            "law_id": "LOV-2022-06-17-63",
            "kind": "corrective_resanction",
            "corrects_law_id": "LOV-2022-06-17-62",
            "source_url": SYNTHETIC_NOTICE,
        }
    ]
    record["evidence"].append(
        {
            "role": "correction",
            "source_url": source_url,
            "capture_level": "web_page_review",
            "observed_on": "2026-11-01",
        }
    )
    return record


def test_corrective_resanction_requires_correction_evidence() -> None:
    record = _corrective(_pilot_record(), SYNTHETIC_NOTICE)
    VALIDATOR.validate(record)
    record["evidence"].pop()
    _invalid(record)


@pytest.mark.parametrize(
    "source_url",
    ["https://www.stortinget.no/no/Saker-og-publikasjoner/", "https://lovdata.no/dokument/x"],
)
def test_correction_evidence_only_from_a_source_that_can_state_it(source_url: str) -> None:
    _invalid(_corrective(_pilot_record(), source_url))


def test_unclassified_sanction_cannot_claim_a_correction() -> None:
    record = _pilot_record()
    VALIDATOR.validate(record)
    record["sanction_events"][0]["corrects_law_id"] = "LOV-2022-06-17-62"
    _invalid(record)


def test_no_initial_sanction_role_is_asserted() -> None:
    record = _pilot_record()
    record["sanction_events"][0]["kind"] = "initial_sanction"
    _invalid(record)


def test_one_decision_holds_several_sanction_events_without_inference() -> None:
    """#618: Lovvedtak 41 (2024-2025) sanctioned as two laws; neither role is stated."""
    record = _pilot_record()
    source = EVIDENCE["lovdata_archive"]["source_url"]
    record["sanction_events"] = [
        {"law_id": law_id, "kind": "unclassified_sanction", "source_url": source}
        for law_id in ("LOV-2025-04-25-13", "LOV-2025-06-20-67")
    ]
    VALIDATOR.validate(record)
    record["sanction_events"][1]["corrects_law_id"] = "LOV-2025-04-25-13"
    _invalid(record)


@pytest.mark.parametrize("field", ["sanction_events", "evidence"])
def test_duplicate_items_are_refused(field: str) -> None:
    record = _pilot_record()
    record[field].append(copy.deepcopy(record[field][0]))
    _invalid(record)


def test_duplicate_archive_pin_is_refused() -> None:
    record = _pilot_record()
    archives = record["source_pins"]["lovdata_archives"]
    archives.append(copy.deepcopy(archives[0]))
    _invalid(record)


def test_withdrawal_is_a_later_non_attested_revision_with_a_reason() -> None:
    withdrawal = {"withdrawn_on": "2026-11-02", "reason": "Parser defect"}
    record = _unresolved(_pilot_record(), decision_case="unresolved")
    record.update(record_revision=2, record_state="withdrawn", reason_code="withdrawn_by_producer")
    _invalid(record)
    record["withdrawal"] = withdrawal
    VALIDATOR.validate(record)
    _invalid({**record, "record_revision": 1})
    _invalid({**record, "reason_code": "case_not_found"})
    _invalid({**_pilot_record(), "record_revision": 2, "record_state": "withdrawn"})


@pytest.mark.parametrize("state", ["superseded", "deleted"])
def test_supersession_is_derived_never_written_back(state: str) -> None:
    _invalid({**_pilot_record(), "record_state": state})
    _invalid({**_pilot_record(), "record_revision": 2, "supersedes_revision": 1})


def test_active_revision_has_no_withdrawal() -> None:
    withdrawal = {"withdrawn_on": "2026-11-02", "reason": "x"}
    _invalid({**_pilot_record(), "withdrawal": withdrawal})


@pytest.mark.parametrize("status", ["unresolved", "ambiguous", "contradicted", "unsupported"])
def test_partial_status_requires_reason_and_reason_code(status: str) -> None:
    record = _pilot_record()
    record["edge_status"]["decision_case"] = status
    record.update(status=status, reason="No citation")
    _invalid(record)
    record["reason_code"] = "no_decision_citation_in_source"
    VALIDATOR.validate(record)


@pytest.mark.parametrize("field", ["lovverk_commit", "producer_commit", "lovdata_archives"])
def test_source_pin_set_is_required(field: str) -> None:
    """Proposal section d requires corpus, producer and archive pins."""
    record = _pilot_record()
    del record["source_pins"][field]
    _invalid(record)


@pytest.mark.parametrize(
    "field",
    [
        "source_url",
        "sha256",
        "size_bytes",
        "last_modified",
        "retrieved_at",
        "licence",
        "licence_basis",
    ],
)
def test_archive_pin_requires_every_provenance_field(field: str) -> None:
    record = _pilot_record()
    del record["source_pins"]["lovdata_archives"][0][field]
    _invalid(record)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.lovdata.no/v1/publicData/get/lovtidend-avd1-EVIL",
        "https://api.lovdata.no/v1/publicData/get/gjeldende-lover.tar.bz2",
    ],
)
def test_archive_pin_names_a_lovtidend_avd1_archive_exactly(url: str) -> None:
    record = _pilot_record()
    record["source_pins"]["lovdata_archives"][0]["source_url"] = url
    _invalid(record)


@pytest.mark.parametrize("field", ["upstream_id", "retrieved_at", "parser_version"])
def test_raw_capture_requires_identity_time_and_parser(field: str) -> None:
    record = _pilot_record()
    del record["evidence"][0][field]
    _invalid(record)


@pytest.mark.parametrize(
    "value", ["not-a-timestamp", "2026-11-01T19:38:16+02:00", "2026-11-01", "2026-11-01 17:38:16Z"]
)
def test_timestamps_are_utc_and_checked_without_optional_format_packages(value: str) -> None:
    record = _pilot_record()
    record["evidence"][0]["retrieved_at"] = value
    _invalid(record)
    record = _pilot_record()
    record["source_pins"]["lovdata_archives"][0]["last_modified"] = value
    _invalid(record)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("upstream_id", "x"),
        ("parser_version", "Lovtidend identity"),
        ("source_url", "https://lovdata.no/dokument/LTI/lov/2022-06-17-63"),
    ],
)
def test_raw_law_evidence_is_typed_and_from_publicdata_only(field: str, value: str) -> None:
    record = _pilot_record()
    _evidence(record, "law_decision_pair")[field] = value
    _invalid(record)


def test_raw_case_evidence_comes_from_the_eksport_api() -> None:
    record = _pilot_record()
    _evidence(record, "decision_case")["source_url"] = "https://www.stortinget.no/no/sak/89016"
    _invalid(record)


def test_publication_list_is_a_typed_evidence_role() -> None:
    record = _pilot_record()
    case = _evidence(record, "decision_case")
    listing = {
        **case,
        "role": "decision_publication",
        "source_url": "https://data.stortinget.no/eksport/publikasjoner?publikasjontype=lovvedtak&sesjonid=2021-2022",
        "upstream_id": "publikasjoner/2021-2022",
    }
    record["evidence"].append(listing)
    VALIDATOR.validate(record)
    listing["source_url"] = case["source_url"]
    _invalid(record)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("amending_lov_id",), "LOV-2022-6-17-63"),
        (("decision", "session_id"), "2021/2022"),
        (("decision", "kind"), "besl_l"),
        (("status",), "reviewed_web_only"),
        (("edge_status", "law_decision"), "verified"),
        (("source_pins", "lovverk_commit"), "A" * 40),
        (("reason_code",), "other"),
    ],
)
def test_identities_and_vocabularies_are_closed(path: tuple[str, ...], value: str) -> None:
    record = _pilot_record()
    target = record
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    _invalid(record)


@pytest.mark.parametrize("where", ["record", "sanction_event"])
def test_no_valid_at_or_in_force_date_can_be_carried(where: str) -> None:
    """ADR-0017: a vote or royal assent never establishes valid_at."""
    record = _pilot_record()
    target = record if where == "record" else record["sanction_events"][0]
    target["valid_at" if where == "record" else "in_force_on"] = "2022-07-01"
    _invalid(record)


def test_attested_requires_raw_capture_for_each_evidence_role() -> None:
    for role in ("law_identity", "law_decision_pair", "decision_case"):
        record = _pilot_record()
        evidence = _evidence(record, role)
        evidence["capture_level"] = "web_page_review"
        for field in (
            "upstream_id",
            "body_sha256",
            "parsed_identity_sha256",
            "retrieved_at",
            "parser_version",
        ):
            del evidence[field]
        if role == "decision_case":
            evidence["source_url"] = "https://www.stortinget.no/no/sak/89016"
        assert VALIDATOR.evolve(schema=PROPOSAL["$defs"]["evidence"]).is_valid(evidence)
        _invalid(record)


def test_no_citation_can_be_recorded_without_inventing_a_decision_or_case() -> None:
    """Section h retains acts with no header citation as unresolved."""
    record = _pilot_record()
    record.update(
        status="unresolved",
        reason="No decision citation in the source header",
        reason_code="no_decision_citation_in_source",
        decision=None,
        sak_id=None,
        sanction_events=[],
        edge_status={
            "law_identity": "attested",
            "law_decision": "unresolved",
            "decision_case": "unresolved",
        },
        evidence=[item for item in record["evidence"] if item["role"] == "law_identity"],
    )
    VALIDATOR.validate(record)


@pytest.mark.parametrize("inferred_index", [0, 1])
def test_release_candidate_checks_licence_of_every_archive(inferred_index: int) -> None:
    record = _pilot_record()
    second = copy.deepcopy(record["source_pins"]["lovdata_archives"][0])
    second.update(
        source_url="https://api.lovdata.no/v1/publicData/get/lovtidend-avd1-2026.tar.bz2",
        licence_basis="stated_for_endpoint",
    )
    record["source_pins"]["lovdata_archives"].append(second)
    record["release_state"] = "release_candidate"
    VALIDATOR.validate(record)
    record["source_pins"]["lovdata_archives"][inferred_index]["licence_basis"] = (
        "inferred_same_dataset"
    )
    _invalid(record)
    record["release_state"] = "research_only"
    VALIDATOR.validate(record)
