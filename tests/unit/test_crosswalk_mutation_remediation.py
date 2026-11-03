"""Observable crosswalk identities, diagnostics and incomplete-capture boundaries."""

import copy
import hashlib
import json
import subprocess
import time
from pathlib import Path

import pytest

from lovspor.crosswalk import build as builder
from lovspor.crosswalk import commands, discovery, identity, records
from lovspor.crosswalk.errors import CrosswalkError
from lovspor.stortinget_case_source import LawDecision

D = {"session_id": "2021-2022", "number": 85}
OTHER = {"session_id": "2021-2022", "number": 86}


def digest(value: object) -> str:
    data = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def law() -> dict:
    return {
        "amending_lov_id": "LOV-2022-06-17-63",
        "law_identity": {"status": "attested"},
        "law_decision": {"status": "attested", "decision": dict(D)},
        "header": {"lovvedtak": [dict(D)], "besl_o": [dict(OTHER)]},
        "lovdata_matches": [
            {
                "archive_source_url": "https://example.org/archive",
                "archive": "a.tar",
                "member": "law.xml",
                "sha256": "a" * 64,
                "archive_retrieved_at": "2026-11-01T00:00:00Z",
            }
        ],
        "chain": {"status": "attested", "sak_id": 123},
    }


def found() -> dict:
    return {
        "decision": dict(reversed(list(D.items()))),
        "outcome": "discovered",
        "sak_ids": [123],
        "cases": [
            {
                "sak_id": 123,
                "sak_sesjon": "2020-2021",
                "typed_lovvedtak": [D, OTHER],
                "source_url": "https://example.org/sak",
                "sha256": "b" * 64,
                "retrieved_at": "2026-11-02T00:00:00Z",
            }
        ],
        "listings": [
            {
                "kind": "publikasjoner",
                "key": "2021-2022",
                "error": None,
                "source_url": "https://example.org/list",
                "sha256": "c" * 64,
                "retrieved_at": "2026-11-02T00:00:00Z",
            }
        ],
    }


def context(publications: object = frozenset({"z", "a"})) -> records.Context:
    return records.Context(
        pins=[{"retrieved_at": "2026-11-01T00:00:00Z"}],
        commits=("a" * 40, "b" * 40),
        sanctioned={records.decision_key(D): [law()]},
        discoveries={records.decision_key(D): found()},
        publications={"2021-2022": publications},
    )


@pytest.mark.parametrize(
    "field,label,value",
    [
        ("law_ids", "law ids", ("LOV-2023-01-01-1",)),
        ("lovverk_commit", "Lovverk commit", "c" * 40),
    ],
)
def test_scope_conflict_diagnostic(field: str, label: str, value: object) -> None:
    manifest = {"scope": {"law_ids": [law()["amending_lov_id"]], "lovverk_commit": "a" * 40}}
    with pytest.raises(CrosswalkError) as error:
        builder._scope(manifest, builder.BuildOptions("b" * 40, **{field: value}))
    assert str(error.value) == f"the build's {label} differ from the capture's recorded scope"


def test_missing_scope_commit_is_rejected() -> None:
    with pytest.raises(CrosswalkError, match="40-hex commit id"):
        builder._scope({}, builder.BuildOptions("b" * 40, law_ids=(law()["amending_lov_id"],)))


def test_substance_accepts_absent_optional_evidence_metadata() -> None:
    assert builder.substance({"status": "unresolved"}) == '{"status":"unresolved"}'
    item = {"role": "law_identity", "source_url": "u"}
    assert json.loads(builder.substance({"evidence": [item]})) == {"evidence": [item]}


def test_equal_revision_keeps_first_seen_record() -> None:
    first = {"amending_lov_id": "LOV-1", "record_revision": 1, "status": "a"}
    second = {**first, "status": "b"}
    assert builder.append_revisions([first], [first, second]) == [first, second]


def test_discovery_records_name_the_parsers(tmp_path: Path) -> None:
    result = discovery.discovery_record(tmp_path, {"stortinget": []}, LawDecision("2021-2022", 85))
    assert result["parser_versions"] == {
        "stortinget_discovery": "lovvedtak-discovery-2",
        "stortinget_sak": "sak-xml-2",
    }


def test_absent_discovery_has_explicit_reason_and_summary() -> None:
    item = law()
    discovery.attach_discovery([item], [])
    assert item["discovery"] == {"outcome": "not_attempted"}
    assert item["chain"] == {
        "status": "unresolved",
        "reason": "discovery not_attempted",
        "other_case_decisions": [],
    }


def test_discovery_joins_unordered_decisions_and_keeps_other_references() -> None:
    item = law()
    discovery.attach_discovery([item], [found()])
    assert item["discovery"]["outcome"] == "discovered"
    assert item["chain"] == {
        "status": "attested",
        "reason": None,
        "sak_id": 123,
        "other_case_decisions": [OTHER],
    }


def test_missing_decision_does_not_join_a_null_discovery() -> None:
    item = law()
    item["law_decision"] = {"status": "unresolved", "kind": "none", "reason": "no citation"}
    discovery.attach_discovery([item], [{"decision": None, "outcome": "incomplete"}])
    assert item["discovery"] == {"outcome": "not_attempted"}


def test_non_decision_key_is_empty() -> None:
    assert records.decision_key(None) == ""


@pytest.mark.parametrize(
    "edge,kind,text,code",
    [
        ("law_identity", None, "malformed document", "source_unreadable"),
        ("law_decision", "none", "header has no citation", "no_decision_citation_in_source"),
        ("decision_case", None, "no case was found", "case_not_found"),
    ],
)
def test_reason_preserves_source_explanation(edge: str, kind: str, text: str, code: str) -> None:
    item = law()
    edges = dict.fromkeys(("law_identity", "law_decision", "decision_case"), "attested")
    edges[edge] = "unresolved"
    target = "chain" if edge == "decision_case" else edge
    item[target].update(reason=text, kind=kind)
    item["discovery"] = {"outcome": "not_found"}
    assert records.reason(item, edges) == (text, code)


def test_decision_of_missing_or_multiple_besl_o_headers() -> None:
    assert (
        records.decision_of({"law_decision": {"status": "unsupported", "kind": "besl_o"}}) is None
    )
    item = law()
    item["law_decision"] = {"status": "unsupported", "kind": "none"}
    assert records.decision_of(item) is None
    item["law_decision"]["kind"] = "besl_o"
    item["header"]["besl_o"] = [D, OTHER]
    assert records.decision_of(item) is None


def test_law_evidence_digests_pin_the_exact_header_identity() -> None:
    item = law()
    expected = digest({"law_id": item["amending_lov_id"], "lovvedtak": [D]})
    evidence = records.law_evidence(item)
    assert [e["parsed_identity_sha256"] for e in evidence] == [expected, expected]
    item["law_decision"] = {"status": "unsupported", "kind": "besl_o"}
    evidence = records.law_evidence(item)
    assert evidence[1]["parsed_identity_sha256"] == digest(
        {
            "law_id": item["amending_lov_id"],
            "besl_o": [OTHER],
        }
    )


def test_publication_and_case_digests_pin_parsed_fields() -> None:
    publication = records.publication_evidence(found(), context())
    assert publication[0]["parsed_identity_sha256"] == digest(
        {
            "session_id": "2021-2022",
            "lovvedtak": ["a", "z"],
        }
    )
    case = records.case_evidence(found(), {"decision_case": "attested"})
    assert case[0]["parsed_identity_sha256"] == digest(
        {
            "sak_id": 123,
            "sak_sesjon": "2020-2021",
            "lovvedtak": [D, OTHER],
        }
    )


@pytest.mark.parametrize(
    "count,error,ids",
    [(0, None, frozenset()), (2, None, frozenset()), (1, "timeout", frozenset()), (1, None, None)],
)
def test_unusable_publication_list_is_not_evidence(count: int, error: str, ids: object) -> None:
    entry = found()
    listing = {**entry["listings"][0], "error": error}
    entry["listings"] = [copy.deepcopy(listing) for _ in range(count)]
    assert records.publication_evidence(entry, context(ids)) == []


def test_observed_on_uses_latest_evidence_and_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    time.tzset()
    try:
        record = {
            "evidence": [{"retrieved_at": "2026-11-03T00:30:00Z"}],
            "source_pins": {"lovdata_archives": [{"retrieved_at": "2026-11-01T00:00:00Z"}]},
        }
        assert records.observed_on(record) == "2026-11-03"
    finally:
        monkeypatch.undo()
        time.tzset()


def test_direct_record_starts_at_one_and_rejects_unattested_case_id() -> None:
    item = law()
    item["chain"].update(status="unresolved", reason="not found")
    item["discovery"] = {"outcome": "not_found"}
    result = records.build_record(item, context())
    assert result["record_revision"] == 1
    assert result["sak_id"] is None


def test_engine_commit_returns_text_and_exact_failure(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.org",
            "commit",
            "-qm",
            "initial",
            "--allow-empty",
        ],
        check=True,
    )
    expected = subprocess.check_output(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
    ).strip()
    assert commands.engine_commit(tmp_path) == expected
    with pytest.raises(CrosswalkError) as error:
        commands.engine_commit(tmp_path / "missing")
    assert str(error.value) == "producer commit unknown: pass --producer-commit"


def test_previous_release_reads_regular_file_bytes(tmp_path: Path) -> None:
    path = tmp_path / "previous"
    path.write_bytes(b'{"name":"\xc3\xb8"}\n')
    assert commands.read_previous(path) == path.read_bytes()


def test_identity_hash_uses_canonical_utf8_and_identity_fields() -> None:
    value = {"name": "blå", "number": 85}
    assert identity.identity_sha256(value) == digest(value)
    assert identity.law_identity("LOV-1", [D]) == {"law_id": "LOV-1", "lovvedtak": [D]}
    assert identity.besl_o_identity("LOV-1", [OTHER]) == {"law_id": "LOV-1", "besl_o": [OTHER]}
    assert identity.case_identity(123, "2020-2021", [D]) == {
        "sak_id": 123,
        "sak_sesjon": "2020-2021",
        "lovvedtak": [D],
    }
    assert identity.publication_identity("2021-2022", iter(["z", "a"])) == {
        "session_id": "2021-2022",
        "lovvedtak": ["a", "z"],
    }
