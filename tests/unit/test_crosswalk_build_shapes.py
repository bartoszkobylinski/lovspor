"""Exact values the offline build derives (#610, P3).

Each test pins a whole value: identity dicts and their digests, every scenario
record's reason and code, the attested record field by field, the discovery
summary and the build's refusals. HTTP is mocked with pytest-httpx only, and
only to make the capture.
"""

import json
from hashlib import sha256
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from lovspor.crosswalk import capture as cap
from lovspor.crosswalk import commands, discovery, identity
from lovspor.crosswalk.build import BuildOptions, append_revisions, build, substance
from lovspor.crosswalk.cache import load_manifest
from lovspor.crosswalk.canonical import parse_records
from lovspor.crosswalk.errors import CrosswalkError
from lovspor.crosswalk.records import (
    Context,
    decision_key,
    edge_status,
    observed_on,
    overall_status,
    reason,
    sanction_events,
)
from lovspor.stortinget_case_source import LawDecision
from tests.unit.crosswalk_support import (
    COMMIT,
    OLD_ARCHIVE_MEMBERS,
    PRODUCER,
    SCENARIO_LAWS,
    document,
    mock_scenario,
    pubs,
    sak,
)

OPTIONS = BuildOptions(producer_commit=PRODUCER)
LAW = "LOV-2022-06-17-63"


@pytest.fixture
def capture_dir(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    mock_scenario(httpx_mock)
    pacer = cap.Pacer(tmp_path / "log", gap=0.0, clock=lambda: 0.0, sleep=lambda _s: None)
    cache = tmp_path / "capture"
    cap.capture(cache, SCENARIO_LAWS, COMMIT, cap.Sources(pacer=pacer))
    return cache


@pytest.fixture
def records(capture_dir: Path) -> dict[str, dict]:
    return {r["amending_lov_id"]: r for r in parse_records(build(capture_dir, OPTIONS))}


def _digest(text: str) -> str:
    return sha256(text.encode()).hexdigest()


# --- identities ----------------------------------------------------------------------


def test_identity_dicts_and_digests_are_exact() -> None:
    cited = [{"session_id": "2021-2022", "number": 85}]
    law = identity.law_identity(LAW, cited)
    assert law == {"law_id": LAW, "lovvedtak": cited}
    assert identity.identity_sha256(law) == _digest(
        '{"law_id":"LOV-2022-06-17-63","lovvedtak":[{"number":85,"session_id":"2021-2022"}]}'
    )
    odelsting = identity.besl_o_identity("LOV-2009-06-19-107", cited)
    assert odelsting == {"law_id": "LOV-2009-06-19-107", "besl_o": cited}
    case = identity.case_identity(89016, "2021-2022", cited)
    assert case == {"sak_id": 89016, "sak_sesjon": "2021-2022", "lovvedtak": cited}
    listing = identity.publication_identity("2021-2022", {"vedtak-b", "vedtak-a"})
    assert listing == {"session_id": "2021-2022", "lovvedtak": ["vedtak-a", "vedtak-b"]}
    assert identity.identity_sha256(listing) == _digest(
        '{"lovvedtak":["vedtak-a","vedtak-b"],"session_id":"2021-2022"}'
    )
    assert identity.identity_sha256({"a": "ø"}) == _digest('{"a":"ø"}')


# --- records -------------------------------------------------------------------------


def test_attested_record_is_exact(records: dict[str, dict], capture_dir: Path) -> None:
    manifest = load_manifest(capture_dir)
    old = next(a for a in manifest["lovdata"] if a["filename"].endswith("2001-2025.tar.bz2"))
    listing = next(
        e
        for e in manifest["stortinget"]
        if e["kind"] == "publikasjoner" and e["key"] == "2021-2022"
    )
    case = next(e for e in manifest["stortinget"] if e["kind"] == "sak" and e["key"] == 89016)
    member, misc = OLD_ARCHIVE_MEMBERS[LAW]
    body = document(LAW, misc)
    cited = [{"session_id": "2021-2022", "number": 85}]
    law_digest = identity.identity_sha256(identity.law_identity(LAW, cited))
    archive_url = old["source_url"]

    def law_evidence(role: str) -> dict:
        return {
            "role": role,
            "source_url": archive_url,
            "capture_level": "raw_body_captured",
            "upstream_id": f"lovtidend-avd1-2001-2025.tar.bz2!{member}",
            "body_sha256": sha256(body).hexdigest(),
            "parsed_identity_sha256": law_digest,
            "retrieved_at": old["retrieved_at"],
            "parser_version": "lovtidend-identity-1",
        }

    record = records[LAW]
    assert {
        k: v for k, v in record.items() if k not in ("evidence", "source_pins", "observed_on")
    } == {
        "schema_version": "1.0.0-rc",
        "release_state": "research_only",
        "amending_lov_id": LAW,
        "record_revision": 1,
        "record_state": "active",
        "status": "attested",
        "edge_status": {
            "law_identity": "attested",
            "law_decision": "attested",
            "decision_case": "attested",
        },
        "decision": {"kind": "lovvedtak", "session_id": "2021-2022", "number": 85},
        "sak_id": 89016,
        "sanction_events": [
            {"law_id": LAW, "kind": "unclassified_sanction", "source_url": archive_url}
        ],
    }
    assert record["evidence"] == [
        {
            "role": "decision_case",
            "source_url": case["source_url"],
            "capture_level": "raw_body_captured",
            "upstream_id": "sak/89016",
            "body_sha256": sha256(sak(89016, "2021-2022", ("2021-2022", 85))).hexdigest(),
            "parsed_identity_sha256": identity.identity_sha256(
                identity.case_identity(89016, "2021-2022", cited)
            ),
            "retrieved_at": case["retrieved_at"],
            "parser_version": "sak-xml-2",
        },
        {
            "role": "decision_publication",
            "source_url": listing["source_url"],
            "capture_level": "raw_body_captured",
            "upstream_id": "publikasjoner/2021-2022",
            "body_sha256": sha256(pubs("vedtak-202122-085")).hexdigest(),
            "parsed_identity_sha256": identity.identity_sha256(
                identity.publication_identity("2021-2022", {"vedtak-202122-085"})
            ),
            "retrieved_at": listing["retrieved_at"],
            "parser_version": "lovvedtak-discovery-2",
        },
        law_evidence("law_decision_pair"),
        law_evidence("law_identity"),
    ]
    assert record["source_pins"]["lovverk_commit"] == COMMIT
    assert record["source_pins"]["producer_commit"] == PRODUCER
    assert record["observed_on"] == observed_on(record)


@pytest.mark.parametrize(
    ("law", "text"),
    [
        ("LOV-2009-06-19-107", "header cites Besl. O.; the reader types Lovvedtak only"),
        ("LOV-2010-01-01-1", "header cites no decision"),
        (
            "LOV-2023-01-01-5",
            "discovery unlisted: the decision is absent from the session's Lovvedtak "
            "publication list",
        ),
        ("LOV-2023-01-01-6", "typed in saker [90002, 90003]"),
        ("LOV-2026-01-01-1", "no member in either archive"),
    ],
)
def test_non_attested_reasons_are_exact(records: dict[str, dict], law: str, text: str) -> None:
    assert records[law]["reason"] == text


def test_reason_codes_for_unread_sources() -> None:
    edges = {
        "law_identity": "unresolved",
        "law_decision": "unresolved",
        "decision_case": "unresolved",
    }
    law = {"law_identity": {"reason": "ParseError: x"}}
    assert reason(law, edges) == ("ParseError: x", "source_unreadable")
    law = {"law_identity": {"reason": "several members"}}
    assert reason(law, edges) == ("several members", "multiple_candidates")
    edges = {**edges, "law_identity": "attested"}
    law = {"law_decision": {"reason": "r", "kind": "unreadable"}}
    assert reason(law, edges) == ("r", "source_unreadable")
    edges = {**edges, "law_decision": "attested"}
    for outcome, code in (
        ("not_found", "case_not_found"),
        ("incomplete", "source_unreadable"),
        ("not_attempted", "case_not_found"),
    ):
        law = {"discovery": {"outcome": outcome}, "chain": {"reason": "c"}}
        assert reason(law, edges) == ("c", code)


def test_observed_on_is_the_latest_utc_date() -> None:
    record = {
        "evidence": [{"retrieved_at": "2026-11-02T23:30:00-02:00"}],
        "source_pins": {"lovdata_archives": [{"retrieved_at": "2026-11-02T10:00:00+00:00"}]},
    }
    assert observed_on(record) == "2026-11-03"


def test_overall_status_picks_the_worst_edge_in_fixed_order() -> None:
    """Contract in build.py's module docstring: 'the worst edge, no edge repairing another'."""
    assert overall_status({"a": "attested", "b": "attested"}) == "attested"
    assert overall_status({}) == "attested"
    assert overall_status({"a": "attested", "b": "unresolved"}) == "unresolved"
    assert overall_status({"a": "unsupported", "b": "unresolved"}) == "unsupported"
    assert overall_status({"a": "ambiguous", "b": "unsupported"}) == "ambiguous"
    assert overall_status({"a": "contradicted", "b": "ambiguous"}) == "contradicted"


def test_edge_status_derives_decision_case_from_law_decision_when_not_attested() -> None:
    """When the law_decision edge is not attested, decision_case follows it, not the chain."""
    unsupported = {
        "law_identity": {"status": "attested"},
        "law_decision": {"status": "unsupported"},
        "chain": {"status": "ambiguous"},
    }
    assert edge_status(unsupported)["decision_case"] == "unsupported"
    unresolved = {
        "law_identity": {"status": "attested"},
        "law_decision": {"status": "unresolved"},
        "chain": {"status": "attested"},
    }
    assert edge_status(unresolved)["decision_case"] == "unresolved"
    attested = {
        "law_identity": {"status": "unresolved"},
        "law_decision": {"status": "attested"},
        "chain": {"status": "contradicted"},
    }
    assert edge_status(attested) == {
        "law_identity": "unresolved",
        "law_decision": "attested",
        "decision_case": "contradicted",
    }


def test_sanction_events_only_appear_for_an_attested_decision() -> None:
    """Every other act the decision sanctions, never only the one being built (#618)."""
    decision = {"session_id": "2021-2022", "number": 85}
    sanctioned_laws = [
        {"amending_lov_id": "LOV-A", "lovdata_matches": [{"archive_source_url": "u-a"}]},
        {"amending_lov_id": "LOV-B", "lovdata_matches": [{"archive_source_url": "u-b"}]},
    ]
    ctx = Context(
        pins=[],
        commits=("c" * 40, "p" * 40),
        sanctioned={decision_key(decision): sanctioned_laws},
        discoveries={},
        publications={},
    )
    attested = {"law_decision": {"status": "attested", "decision": decision}}
    assert sanction_events(attested, ctx) == [
        {"law_id": "LOV-A", "kind": "unclassified_sanction", "source_url": "u-a"},
        {"law_id": "LOV-B", "kind": "unclassified_sanction", "source_url": "u-b"},
    ]
    unresolved = {"law_decision": {"status": "unresolved", "decision": decision}}
    assert sanction_events(unresolved, ctx) == []


# --- discovery -----------------------------------------------------------------------


def test_discovery_summary_and_chain_are_exact() -> None:
    attested = {
        "law_decision": {"status": "attested", "decision": {"session_id": "s", "number": 1}}
    }
    assert discovery.chain(attested, None) == {
        "status": "unresolved",
        "reason": "discovery not_attempted",
    }
    found = {
        "decision": {"session_id": "s", "number": 1},
        "outcome": "discovered",
        "reason": None,
        "sak_ids": [7],
        "candidates": [7, 8],
        "failed": [8],
        "cases": [
            {
                "typed_lovvedtak": [
                    {"session_id": "s", "number": 1},
                    {"session_id": "s", "number": 2},
                ]
            }
        ],
    }
    laws = [dict(attested), {"law_decision": {"status": "unresolved"}}]
    discovery.attach_discovery(laws, [found])
    assert laws[0]["discovery"] == {
        "outcome": "discovered",
        "reason": None,
        "sak_ids": [7],
        "candidates": [7, 8],
        "failed": [8],
    }
    assert laws[0]["chain"] == {
        "status": "attested",
        "reason": None,
        "sak_id": 7,
        "other_case_decisions": [{"session_id": "s", "number": 2}],
    }
    assert laws[1]["discovery"] == {"outcome": "not_attempted"}
    assert laws[1]["chain"]["other_case_decisions"] == []
    assert discovery.other_decisions(attested, {**found, "outcome": "ambiguous"}) == []


def test_discovery_record_names_its_parsers(capture_dir: Path) -> None:
    found = discovery.discovery_record(
        capture_dir, load_manifest(capture_dir), LawDecision("2021-2022", 85)
    )
    assert found["parser_versions"] == {
        "stortinget_discovery": "lovvedtak-discovery-2",
        "stortinget_sak": "sak-xml-2",
    }
    assert (found["outcome"], found["sak_ids"], found["export_id"]) == (
        "discovered",
        [89016],
        "vedtak-202122-085",
    )


# --- build refusals and history ------------------------------------------------------


def test_scope_disagreement_messages_are_exact(capture_dir: Path) -> None:
    with pytest.raises(CrosswalkError) as caught:
        build(capture_dir, BuildOptions(PRODUCER, law_ids=(LAW,)))
    assert str(caught.value) == "the build's law ids differ from the capture's recorded scope"
    with pytest.raises(CrosswalkError) as caught:
        build(capture_dir, BuildOptions(PRODUCER, lovverk_commit="c" * 40))
    assert (
        str(caught.value) == "the build's Lovverk commit differ from the capture's recorded scope"
    )


def test_substance_drops_exactly_the_volatile_fields() -> None:
    record = {
        "amending_lov_id": "L",
        "record_revision": 4,
        "observed_on": "d",
        "source_pins": {"p": 1},
        "status": "s",
        "evidence": [
            {
                "role": "r",
                "source_url": "u",
                "body_sha256": "b",
                "retrieved_at": "t",
                "parser_version": "v",
            }
        ],
    }
    assert json.loads(substance(record)) == {
        "amending_lov_id": "L",
        "status": "s",
        "evidence": [{"role": "r", "source_url": "u", "parser_version": "v"}],
    }
    assert record["evidence"][0]["body_sha256"] == "b"


def test_appending_uses_the_highest_previous_revision() -> None:
    two = {"amending_lov_id": "L", "record_revision": 2, "evidence": [], "s": "b"}
    one = {"amending_lov_id": "L", "record_revision": 1, "evidence": [], "s": "a"}
    assert append_revisions([{**one, "s": "a"}], [two, one])[-1] == {
        **one,
        "s": "a",
        "record_revision": 3,
    }
    assert append_revisions([{**one, "s": "b"}], [one, two]) == [one, two]


def test_engine_commit_is_forty_hex_text(tmp_path: Path) -> None:
    head = commands.engine_commit()
    assert isinstance(head, str) and len(head) == 40 and head == head.strip()
    assert int(head, 16) >= 0
    with pytest.raises(CrosswalkError) as caught:
        commands.engine_commit(tmp_path)
    assert str(caught.value) == "producer commit unknown: pass --producer-commit"


def test_previous_release_bounds_are_exact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "prev.jsonl"
    path.write_bytes(b"x" * 11)
    monkeypatch.setattr(commands, "MAX_PREVIOUS_BYTES", 10)
    with pytest.raises(CrosswalkError) as caught:
        commands.read_previous(path)
    assert str(caught.value) == f"previous release too large: {path}"
    monkeypatch.setattr(commands, "MAX_PREVIOUS_BYTES", 11)
    assert commands.read_previous(path) == b"x" * 11
