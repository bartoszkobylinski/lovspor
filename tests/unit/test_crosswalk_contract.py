"""The 1.0.0-rc crosswalk contract: schema and producer invariants (#610, proposal item l)."""

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from lovspor.crosswalk import contract
from lovspor.crosswalk.contract import (
    history_problems,
    record_problems,
    schema,
    schema_problems,
    validate_records,
)
from lovspor.crosswalk.errors import CrosswalkContractError
from tests.unit.crosswalk_support import (
    ARCHIVE_URL,
    archive_pin,
    attested,
    besl_o,
    evidence,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "docs/research/schemas"
V0 = json.loads((SCHEMAS / "legislative-crosswalk-v0.schema.json").read_text(encoding="utf-8"))
PILOT = json.loads(
    (ROOT / "docs/research/data/pilot-e4-evidence-2026-11-01.json").read_text(encoding="utf-8")
)


def _role(record: dict[str, Any], role: str) -> dict[str, Any]:
    return next(item for item in record["evidence"] if item["role"] == role)


def test_schema_is_valid_draft_2020_12_and_versioned_rc() -> None:
    Draft202012Validator.check_schema(schema())
    assert schema()["properties"]["schema_version"] == {"const": contract.SCHEMA_VERSION}
    assert contract.SCHEMA_VERSION == "1.0.0-rc"


@pytest.mark.parametrize("build", [attested, besl_o], ids=["attested", "besl_o"])
def test_reference_records_pass_schema_and_invariants(build: Any) -> None:
    assert record_problems(build()) == []


def test_no_released_state_exists() -> None:
    record = attested()
    record["release_state"] = "released"
    assert schema_problems(record)


def test_v0_and_rc_refuse_each_other() -> None:
    v0_record = PILOT["contract_v0_record"]
    assert list(Draft202012Validator(V0).iter_errors(v0_record)) == []
    assert list(Draft202012Validator(V0).iter_errors(attested()))
    assert schema_problems(v0_record)


@pytest.mark.parametrize(
    "role", ["law_identity", "law_decision_pair", "decision_case", "decision_publication"]
)
def test_attested_edges_need_their_own_raw_evidence(role: str) -> None:
    record = attested()
    record["evidence"] = [item for item in record["evidence"] if item["role"] != role]
    assert record_problems(record)


def test_publication_list_evidence_is_mandatory_for_an_attested_case_edge() -> None:
    record = attested()
    listed = _role(record, "decision_publication")
    record["evidence"] = [i for i in record["evidence"] if i is not listed]
    assert schema_problems(record)
    record["evidence"].append(listed)
    assert schema_problems(record) == []


def test_status_is_attested_exactly_when_every_edge_is() -> None:
    record = attested()
    record["edge_status"]["decision_case"] = "unresolved"
    assert schema_problems(record)
    record["status"] = "unresolved"
    record.update(reason="case not found", reason_code="case_not_found")
    assert schema_problems(record) == []


def test_web_page_review_evidence_is_refused() -> None:
    record = attested()
    _role(record, "decision_case")["capture_level"] = "web_page_review"
    assert schema_problems(record)


@pytest.mark.parametrize(
    "url",
    [
        "https://lovdata.no/dokument/LTI/lov/2022-06-17-63",
        "https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=89016",
        "http://data.stortinget.no/eksport/sak?sakid=89016",
    ],
)
def test_raw_case_evidence_only_from_the_export_api(url: str) -> None:
    record = attested()
    _role(record, "decision_case")["source_url"] = url
    assert record_problems(record)


def test_raw_law_evidence_only_from_publicdata() -> None:
    record = attested()
    _role(record, "law_identity")["source_url"] = (
        "https://lovdata.no/dokument/LTI/lov/2022-06-17-63"
    )
    assert schema_problems(record)


@pytest.mark.parametrize("stamp", ["not-a-timestamp", "2026-11-02T10:00:00+01:00", "2026-11-02"])
def test_retrieval_times_are_utc_timestamps(stamp: str) -> None:
    record = attested()
    _role(record, "decision_case")["retrieved_at"] = stamp
    assert schema_problems(record)


def test_no_initial_sanction_and_no_inferred_validity() -> None:
    record = attested()
    record["sanction_events"][0]["kind"] = "initial_sanction"
    assert schema_problems(record)
    for field in ("valid_at", "in_force_on", "authorship"):
        record = attested()
        record[field] = "2022-07-01"
        assert schema_problems(record)


def test_618_two_unclassified_events_for_one_decision_validate() -> None:
    record = attested()
    record["sanction_events"].append(
        {"law_id": "LOV-2022-06-17-64", "kind": "unclassified_sanction", "source_url": ARCHIVE_URL}
    )
    assert record_problems(record) == []


def test_618_correction_needs_correction_evidence_and_a_named_predecessor() -> None:
    record = attested()
    record["sanction_events"].append(
        {
            "law_id": "LOV-2022-06-17-64",
            "kind": "corrective_resanction",
            "source_url": ARCHIVE_URL,
            "corrects_law_id": "LOV-2022-06-17-63",
        }
    )
    assert schema_problems(record)
    member = "lovtidend-avd1-2001-2025.tar.bz2!lti/2022/nl-20220617-064.xml"
    record["evidence"].append(evidence("correction", ARCHIVE_URL, member, "lovtidend-identity-1"))
    assert record_problems(record) == []
    record["sanction_events"][1]["corrects_law_id"] = "LOV-2022-06-17-99"
    assert any("names no other event" in p for p in record_problems(record))


def test_correction_evidence_never_from_regjeringen_or_stortinget() -> None:
    record = attested()
    url = "https://www.regjeringen.no/no/aktuelt/x/id1/"
    record["evidence"].append(evidence("correction", url, "sak/89016", "sak-xml-2"))
    assert schema_problems(record)


def test_duplicate_sanction_events_are_refused() -> None:
    record = attested()
    record["sanction_events"].append(dict(record["sanction_events"][0]))
    assert schema_problems(record)
    record["sanction_events"][1]["kind"] = "corrective_resanction"
    record["sanction_events"][1]["corrects_law_id"] = "LOV-2022-06-17-63"
    record["evidence"].append(
        evidence(
            "correction",
            ARCHIVE_URL,
            "lovtidend-avd1-2001-2025.tar.bz2!lti/2022/nl-20220617-063.xml",
            "lovtidend-identity-1",
        )
    )
    assert any("share a law_id" in p for p in record_problems(record))
    assert "two sanction events share a law_id" in record_problems(record)


def test_amending_law_must_be_a_sanction_event_when_its_edge_is_attested() -> None:
    record = attested()
    record["sanction_events"][0]["law_id"] = "LOV-2022-06-17-64"
    assert record_problems(record) == ["amending law is not a sanction event"]


def test_case_evidence_must_name_the_record_sak() -> None:
    record = attested()
    record["sak_id"] = 89017
    assert any("is not sak 89017" in p for p in record_problems(record))


def test_case_upstream_id_must_match_its_url() -> None:
    record = attested()
    _role(record, "decision_case")["upstream_id"] = "sak/89017"
    assert any("is not sak 89016" in p for p in record_problems(record))


def test_publication_list_must_be_the_decision_session() -> None:
    record = attested()
    record["decision"]["session_id"] = "2022-2023"
    assert any("is not session 2022-2023" in p for p in record_problems(record))


def test_cited_archives_must_be_pinned() -> None:
    record = attested()
    record["source_pins"]["lovdata_archives"] = [archive_pin("lovtidend-avd1-2026.tar.bz2")]
    problems = record_problems(record)
    assert any("not pinned" in p for p in problems)
    assert any("sanction event" in p for p in problems)


def test_law_evidence_must_be_the_amending_act_document() -> None:
    record = attested()
    item = _role(record, "law_identity")
    item["upstream_id"] = "lovtidend-avd1-2001-2025.tar.bz2!lti/2022/nl-20220617-064.xml"
    assert any("is not the document of" in p for p in record_problems(record))


def test_one_edge_never_mixes_parser_versions() -> None:
    record = attested()
    extra = dict(_role(record, "decision_case"))
    extra["parser_version"] = "sak-xml-3"
    extra["body_sha256"] = "c" * 64
    record["evidence"].append(extra)
    assert "decision_case evidence mixes parser versions" in record_problems(record)


def test_observed_on_is_the_latest_capture_date_not_a_wall_clock() -> None:
    record = attested()
    record["observed_on"] = "2026-11-03"
    assert any("observed_on" in p for p in record_problems(record))


def test_besl_o_is_always_unsupported() -> None:
    record = besl_o()
    record["status"] = "unresolved"
    record["edge_status"]["law_decision"] = "unresolved"
    assert schema_problems(record)


def test_withdrawal_is_a_later_non_attested_revision() -> None:
    record = attested()
    record.update(
        record_revision=2,
        record_state="withdrawn",
        status="unresolved",
        edge_status={**record["edge_status"], "decision_case": "unresolved"},
        reason="producer edge was wrong",
        reason_code="withdrawn_by_producer",
        withdrawal={"withdrawn_on": "2026-11-03", "reason": "producer edge was wrong"},
    )
    assert schema_problems(record) == []
    record["record_revision"] = 1
    assert schema_problems(record)


def test_release_candidate_refuses_an_inferred_licence_basis() -> None:
    record = attested()
    record["release_state"] = "release_candidate"
    assert schema_problems(record) == []
    record["source_pins"]["lovdata_archives"] = [archive_pin(basis="inferred_same_dataset")]
    assert schema_problems(record)


def test_history_needs_contiguous_unique_revisions() -> None:
    first, second = attested(), attested()
    second["record_revision"] = 3
    assert history_problems([first, second])
    second["record_revision"] = 1
    assert history_problems([first, second])
    second["record_revision"] = 2
    assert history_problems([first, second]) == []


def test_validate_records_reports_every_problem() -> None:
    bad = attested()
    bad["sak_id"] = 89017
    other = besl_o()
    other["status"] = "attested"
    with pytest.raises(CrosswalkContractError) as caught:
        validate_records([bad, other])
    assert len(caught.value.problems) >= 2
    assert all(
        p.startswith(("LOV-2022-06-17-63@1", "LOV-2009-06-19-107@1")) for p in caught.value.problems
    )


def test_validate_records_checks_history_once_records_are_valid() -> None:
    with pytest.raises(CrosswalkContractError, match="without repeats"):
        validate_records([attested(), attested()])
    validate_records([attested(), besl_o()])


@pytest.mark.parametrize("capture", ["evidence", "archive_pin"])
def test_latest_capture_can_come_from_evidence_or_an_archive_pin(capture: str) -> None:
    record = attested()
    item = (
        _role(record, "decision_case")
        if capture == "evidence"
        else record["source_pins"]["lovdata_archives"][0]
    )
    item["retrieved_at"] = "2026-11-03T00:00:00Z"
    assert record_problems(record) == [
        "observed_on 2026-11-02 is not the latest capture date 2026-11-03"
    ]
    record["observed_on"] = "2026-11-03"
    assert record_problems(record) == []


@pytest.mark.parametrize("record", [None, [], {}, {"evidence": [None]}])
def test_schema_errors_are_returned_before_invariants_run(record: object) -> None:
    problems = schema_problems(record)
    assert problems
    assert record_problems(record) == problems


@pytest.mark.parametrize("role", ["decision_case", "decision_publication"])
def test_evidence_url_must_match_even_when_upstream_id_is_correct(role: str) -> None:
    record = attested()
    item = _role(record, role)
    item["source_url"] = (
        item["source_url"].replace("89016", "89017").replace("2021-2022", "2022-2023")
    )
    assert schema_problems(record) == []
    assert len(record_problems(record)) == 1
    assert role in record_problems(record)[0]


def test_history_is_grouped_per_law_and_independent_of_input_order() -> None:
    first, second, other = attested(), attested(), besl_o()
    second["record_revision"] = 2
    assert history_problems(iter([second, other, first])) == []
    other["record_revision"] = 2
    assert history_problems([second, other, first]) == [
        "LOV-2009-06-19-107: revisions [2] are not 1..1 without repeats"
    ]


def test_schema_diagnostics_name_nested_property_and_array_index() -> None:
    record = attested()
    record["evidence"][0]["body_sha256"] = 42
    assert "/evidence/0/body_sha256: 42 is not of type 'string'" in schema_problems(record)


@pytest.mark.parametrize("role", ["decision_case", "decision_publication"])
def test_blank_extra_query_parameter_is_not_ignored(role: str) -> None:
    record = attested()
    _role(record, role)["source_url"] += "&extra="
    assert schema_problems(record) == []
    assert len(record_problems(record)) == 1
    assert record_problems(record)[0].startswith(role)


def test_unpinned_evidence_is_reported_even_without_sanction_events() -> None:
    record = besl_o()
    record["source_pins"]["lovdata_archives"] = [archive_pin("lovtidend-avd1-2026.tar.bz2")]
    assert schema_problems(record) == []
    upstream = record["evidence"][0]["upstream_id"]
    assert record_problems(record) == [f"evidence {upstream} cites an archive that is not pinned"]


def test_pair_evidence_must_name_the_amending_act_independently_of_identity() -> None:
    record = attested()
    item = _role(record, "law_decision_pair")
    item["upstream_id"] = "lovtidend-avd1-2001-2025.tar.bz2!lti/2022/nl-20220617-064.xml"
    assert schema_problems(record) == []
    assert record_problems(record) == [
        f"evidence {item['upstream_id']} is not the document of LOV-2022-06-17-63"
    ]


def test_publication_evidence_without_a_decision_is_a_diagnostic() -> None:
    record = attested()
    record.update(
        decision=None,
        status="unresolved",
        edge_status=dict.fromkeys(record["edge_status"], "unresolved"),
        reason="no decision citation",
        reason_code="no_decision_citation_in_source",
    )
    assert schema_problems(record) == []
    assert record_problems(record) == [
        "decision_publication publikasjoner/2021-2022 is not session None"
    ]


def test_no_captures_does_not_require_a_latest_capture_date() -> None:
    record = attested()
    record.update(
        decision=None,
        status="unresolved",
        edge_status=dict.fromkeys(record["edge_status"], "unresolved"),
        reason="no decision citation",
        reason_code="no_decision_citation_in_source",
        evidence=[],
        sanction_events=[],
    )
    record["source_pins"]["lovdata_archives"] = []
    assert schema_problems(record) == []
    assert record_problems(record) == []


def test_observed_date_uses_utc_in_a_non_utc_process_timezone(monkeypatch) -> None:
    previous = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "EST5")
    time.tzset()
    try:
        record = attested()
        for item in record["evidence"] + record["source_pins"]["lovdata_archives"]:
            item["retrieved_at"] = "2026-11-02T00:30:00Z"
        assert record_problems(record) == []
    finally:
        if previous is None:
            monkeypatch.delenv("TZ")
        else:
            monkeypatch.setenv("TZ", previous)
        time.tzset()


@pytest.mark.parametrize("role", ["law_identity", "law_decision_pair"])
def test_multiple_archive_separators_are_rejected_before_invariants(role: str) -> None:
    record = attested()
    item = _role(record, role)
    item["upstream_id"] = item["upstream_id"].replace("!", "!extra!")
    problems = schema_problems(record)
    assert problems
    assert record_problems(record) == problems


def test_archive_member_splits_only_a_lovdata_member_id() -> None:
    assert contract.archive_member("lovtidend-avd1-2026.tar.bz2!lti/2026/nl-20260101-001.xml") == (
        "lovtidend-avd1-2026.tar.bz2",
        "lti/2026/nl-20260101-001.xml",
    )
    for upstream in ("sak/89016", "publikasjoner/2021-2022", "a!b!c", "!m", "a!"):
        assert contract.archive_member(upstream) is None


@pytest.mark.parametrize("role", ["law_identity", "law_decision_pair", "correction"])
def test_archive_separator_cannot_be_hidden_in_a_member_or_archive(role: str) -> None:
    record = attested()
    if role == "correction":
        item = {**_role(record, "law_identity"), "role": role}
        record["evidence"].append(item)
    else:
        item = _role(record, role)
    assert record_problems(record) == []
    archive, member = item["upstream_id"].split("!")
    for upstream in (f"{archive}!extra!{member}", f"{archive}!{member}!extra.xml"):
        item["upstream_id"] = upstream
        problems = schema_problems(record)
        assert problems
        assert any("upstream_id" in problem for problem in problems)
        assert record_problems(record) == problems
        with pytest.raises(CrosswalkContractError) as caught:
            validate_records([record])
        assert caught.value.problems == tuple(
            f"LOV-2022-06-17-63@1: {problem}" for problem in problems
        )
