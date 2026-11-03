"""Offline, deterministic crosswalk build (#610, P3).

One synthetic capture holds every outcome the build must keep: an attested
chain, a #618 pair of acts sanctioned for one decision, a case opened in the
session before its decision, a case carrying two decisions, Besl. O., an act
whose header cites nothing, a decision absent from its publication list, two
cases claiming one decision, and an act in no pinned archive. HTTP is mocked
with pytest-httpx only, and only to make the capture; the build runs with the
network cut.
"""

import json
import shutil
import socket
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.crosswalk import capture as cap
from lovspor.crosswalk import commands
from lovspor.crosswalk.build import BuildOptions, append_revisions, archive_pins, build, substance
from lovspor.crosswalk.cache import MANIFEST, load_manifest, write_manifest
from lovspor.crosswalk.canonical import is_canonical, parse_records, serialize_records
from lovspor.crosswalk.commands import build_impl, engine_commit, read_previous, write_once
from lovspor.crosswalk.contract import record_problems, validate_records
from lovspor.crosswalk.errors import CrosswalkContractError, CrosswalkError
from lovspor.crosswalk.rights import licence_basis
from tests.unit.crosswalk_support import (
    COMMIT,
    EXPECTED,
    PRODUCER,
    SCENARIO_LAWS,
    mock_scenario,
)

OPTIONS = BuildOptions(producer_commit=PRODUCER)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def capture_dir(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    mock_scenario(httpx_mock)
    clock = _Clock()
    pacer = cap.Pacer(tmp_path / "progress.log", clock=clock, sleep=clock.sleep)
    cache = tmp_path / "capture"
    cap.capture(cache, SCENARIO_LAWS, COMMIT, cap.Sources(pacer=pacer))
    return cache


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the build opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def _by_law(data: bytes) -> dict[str, dict]:
    return {record["amending_lov_id"]: record for record in parse_records(data)}


def test_every_act_in_scope_is_kept_with_its_outcome(capture_dir: Path, offline: None) -> None:
    records = _by_law(build(capture_dir, OPTIONS))
    assert sorted(records) == SCENARIO_LAWS
    found = {law: (r["status"], r.get("reason_code")) for law, r in records.items()}
    assert found == EXPECTED
    assert all(r["release_state"] == "research_only" for r in records.values())
    assert all(r["record_revision"] == 1 for r in records.values())


def test_output_is_canonical_valid_and_byte_identical(capture_dir: Path, offline: None) -> None:
    data = build(capture_dir, OPTIONS)
    assert is_canonical(data)
    validate_records(parse_records(data))
    assert build(capture_dir, OPTIONS) == data


def test_a_copied_capture_builds_the_same_bytes(
    capture_dir: Path, tmp_path: Path, offline: None
) -> None:
    copy = tmp_path / "elsewhere"
    shutil.copytree(capture_dir, copy)
    assert build(copy, OPTIONS) == build(capture_dir, OPTIONS)


def test_618_both_acts_are_sanction_events_of_one_decision(capture_dir: Path) -> None:
    records = _by_law(build(capture_dir, OPTIONS))
    for law in ("LOV-2025-04-25-13", "LOV-2025-06-20-67"):
        record = records[law]
        assert [e["law_id"] for e in record["sanction_events"]] == [
            "LOV-2025-04-25-13",
            "LOV-2025-06-20-67",
        ]
        assert {e["kind"] for e in record["sanction_events"]} == {"unclassified_sanction"}
        assert record["decision"] == {"kind": "lovvedtak", "session_id": "2024-2025", "number": 41}
        assert record["sak_id"] == 101001
        assert not any(item["role"] == "correction" for item in record["evidence"])


def test_cross_session_multi_decision_case_attests_only_its_own_decision(
    capture_dir: Path,
) -> None:
    record = _by_law(build(capture_dir, OPTIONS))["LOV-2025-04-25-13"]
    case = next(item for item in record["evidence"] if item["role"] == "decision_case")
    assert case["upstream_id"] == "sak/101001"
    assert case["source_url"] == "https://data.stortinget.no/eksport/sak?sakid=101001"
    roles = sorted(item["role"] for item in record["evidence"])
    assert roles == ["decision_case", "decision_publication", "law_decision_pair", "law_identity"]


def test_attested_chain_carries_all_four_pieces_of_raw_evidence(capture_dir: Path) -> None:
    record = _by_law(build(capture_dir, OPTIONS))["LOV-2022-06-17-63"]
    assert record["edge_status"] == dict.fromkeys(
        ("law_identity", "law_decision", "decision_case"), "attested"
    )
    parsers = {item["role"]: item["parser_version"] for item in record["evidence"]}
    assert parsers == {
        "law_identity": "lovtidend-identity-1",
        "law_decision_pair": "lovtidend-identity-1",
        "decision_publication": "lovvedtak-discovery-2",
        "decision_case": "sak-xml-2",
    }
    assert record["source_pins"]["lovverk_commit"] == COMMIT
    assert record["source_pins"]["producer_commit"] == PRODUCER


def test_besl_o_is_unsupported_with_its_header_evidence(capture_dir: Path) -> None:
    record = _by_law(build(capture_dir, OPTIONS))["LOV-2009-06-19-107"]
    assert record["decision"] == {"kind": "besl_o", "session_id": "2008-2009", "number": 98}
    assert record["edge_status"]["law_identity"] == "attested"
    assert record["edge_status"]["decision_case"] == "unsupported"
    pair = next(i for i in record["evidence"] if i["role"] == "law_decision_pair")
    assert pair["parser_version"] == "besl-o-header-1"
    assert record["sak_id"] is None
    assert record["sanction_events"] == []


def test_non_attested_records_never_carry_case_evidence(capture_dir: Path) -> None:
    for record in parse_records(build(capture_dir, OPTIONS)):
        roles = {item["role"] for item in record["evidence"]}
        if record["edge_status"]["decision_case"] != "attested":
            assert "decision_case" not in roles
            assert record["sak_id"] is None


def test_unlisted_decision_keeps_the_list_that_contradicts_it(capture_dir: Path) -> None:
    record = _by_law(build(capture_dir, OPTIONS))["LOV-2023-01-01-5"]
    assert record["edge_status"]["decision_case"] == "contradicted"
    listing = next(i for i in record["evidence"] if i["role"] == "decision_publication")
    assert listing["upstream_id"] == "publikasjoner/2022-2023"


def test_archive_pins_carry_the_recorded_licence_basis(capture_dir: Path) -> None:
    pins = archive_pins(load_manifest(capture_dir))
    assert [(p["source_url"].rsplit("/", 1)[1], p["licence_basis"]) for p in pins] == [
        ("lovtidend-avd1-2001-2025.tar.bz2", "stated_for_file"),
        ("lovtidend-avd1-2026.tar.bz2", "stated_for_endpoint"),
    ]
    assert licence_basis("lovtidend-avd1-2027.tar.bz2") == "inferred_same_dataset"


def test_verification_captures_never_stand_in_for_discovery(capture_dir: Path) -> None:
    before = build(capture_dir, OPTIONS)
    manifest = load_manifest(capture_dir)
    saks = [e for e in manifest["stortinget"] if e["kind"] == "sak"]
    manifest["stortinget"] += [{**e, "stage": "verification"} for e in saks]
    write_manifest(capture_dir, manifest)
    assert build(capture_dir, OPTIONS) == before
    manifest["stortinget"] = [
        e for e in manifest["stortinget"] if not (e["stage"] == "discovery" and e["key"] == 90003)
    ]
    write_manifest(capture_dir, manifest)
    record = _by_law(build(capture_dir, OPTIONS))["LOV-2023-01-01-6"]
    assert (record["status"], record["reason_code"]) == ("unresolved", "source_unreadable")


def test_tampered_cache_is_refused(capture_dir: Path) -> None:
    manifest = load_manifest(capture_dir)
    entry = next(e for e in manifest["stortinget"] if e["kind"] == "sak")
    (capture_dir / entry["path"]).write_bytes(b"<tampered/>")
    with pytest.raises(CrosswalkError, match="do not match the manifest"):
        build(capture_dir, OPTIONS)


def test_tampered_archive_is_refused(capture_dir: Path) -> None:
    manifest = load_manifest(capture_dir)
    archive = capture_dir / manifest["lovdata"][0]["path"]
    archive.write_bytes(b"not a tar")
    with pytest.raises(CrosswalkError, match="do not match the manifest"):
        build(capture_dir, OPTIONS)


def test_scope_must_agree_with_the_capture(capture_dir: Path) -> None:
    with pytest.raises(CrosswalkError, match="law ids"):
        build(capture_dir, BuildOptions(producer_commit=PRODUCER, law_ids=("LOV-2022-06-17-63",)))
    with pytest.raises(CrosswalkError, match="Lovverk commit"):
        build(capture_dir, BuildOptions(producer_commit=PRODUCER, lovverk_commit="c" * 40))
    same = BuildOptions(
        producer_commit=PRODUCER, law_ids=tuple(SCENARIO_LAWS), lovverk_commit=COMMIT
    )
    assert build(capture_dir, same) == build(capture_dir, OPTIONS)


def test_a_capture_without_scope_needs_it_given(capture_dir: Path) -> None:
    manifest = load_manifest(capture_dir)
    del manifest["scope"]
    write_manifest(capture_dir, manifest)
    with pytest.raises(CrosswalkError, match="law ids"):
        build(capture_dir, OPTIONS)
    given = BuildOptions(
        producer_commit=PRODUCER, law_ids=tuple(SCENARIO_LAWS), lovverk_commit=COMMIT
    )
    assert len(parse_records(build(capture_dir, given))) == len(SCENARIO_LAWS)


# --- append-only history -------------------------------------------------------------


def test_replay_over_the_previous_release_appends_nothing(capture_dir: Path) -> None:
    first = build(capture_dir, OPTIONS)
    again = BuildOptions(producer_commit="c" * 40, previous=first)
    assert build(capture_dir, again) == first


def test_build_appends_changed_discovery_and_replay_adds_nothing(capture_dir: Path) -> None:
    first = build(capture_dir, OPTIONS)
    manifest = load_manifest(capture_dir)
    # The latest discovery attempt fails; an older success must not mask it.
    case = next(e for e in manifest["stortinget"] if e["kind"] == "sak" and e["key"] == 90003)
    manifest["stortinget"].append({**case, "error": "network timeout", "http_status": None})
    write_manifest(capture_dir, manifest)

    data = build(capture_dir, BuildOptions(PRODUCER, previous=first))
    history = parse_records(data)
    assert set(first.splitlines()) <= set(data.splitlines())
    assert len(history) == len(parse_records(first)) + 1
    changed = next(r for r in history if r["record_revision"] == 2)
    assert changed["amending_lov_id"] == "LOV-2023-01-01-6"
    assert (changed["status"], changed["reason_code"]) == ("unresolved", "source_unreadable")
    assert changed["sak_id"] is None
    assert "decision_case" not in {e["role"] for e in changed["evidence"]}
    assert build(capture_dir, BuildOptions(PRODUCER, previous=data)) == data


def test_new_law_gets_revision_one_without_rewriting_previous_history(capture_dir: Path) -> None:
    first = build(capture_dir, OPTIONS)
    previous = parse_records(first)
    law = "LOV-2022-06-17-63"
    kept = serialize_records(r for r in previous if r["amending_lov_id"] != law)
    assert build(capture_dir, BuildOptions(PRODUCER, previous=kept)) == first


def test_a_changed_record_is_appended_and_the_old_bytes_stay(capture_dir: Path) -> None:
    first = build(capture_dir, OPTIONS)
    previous = parse_records(first)
    fresh = [dict(r) for r in previous]
    changed = next(r for r in fresh if r["amending_lov_id"] == "LOV-2022-06-17-63")
    changed["edge_status"] = {**changed["edge_status"], "decision_case": "unresolved"}
    changed.update(status="unresolved", reason="case gone", reason_code="case_not_found")
    changed["evidence"] = [e for e in changed["evidence"] if e["role"] != "decision_case"]
    changed["sak_id"] = None
    history = append_revisions(fresh, previous)
    validate_records(history)
    data = serialize_records(history)
    assert set(first.splitlines()) <= set(data.splitlines())
    revisions = [
        r["record_revision"] for r in history if r["amending_lov_id"] == "LOV-2022-06-17-63"
    ]
    assert revisions == [1, 2]


def test_bytes_only_drift_is_no_change() -> None:
    record = {
        "amending_lov_id": "LOV-1",
        "record_revision": 1,
        "evidence": [
            {
                "role": "decision_case",
                "source_url": "u",
                "body_sha256": "a",
                "retrieved_at": "t1",
                "parsed_identity_sha256": "x",
            }
        ],
        "observed_on": "d1",
        "source_pins": {"producer_commit": "p1"},
    }
    drifted = json.loads(json.dumps(record))
    drifted["evidence"][0].update(body_sha256="b", retrieved_at="t2")
    drifted.update(observed_on="d2", source_pins={"producer_commit": "p2"}, record_revision=7)
    assert substance(record) == substance(drifted)
    drifted["evidence"][0]["parsed_identity_sha256"] = "y"
    assert substance(record) != substance(drifted)


def test_a_law_absent_from_the_new_capture_keeps_its_history() -> None:
    old = {"amending_lov_id": "LOV-1", "record_revision": 1, "evidence": []}
    assert append_revisions([], [old]) == [old]


def test_narrowed_capture_keeps_history_and_restored_scope_adds_nothing(
    capture_dir: Path, offline: None
) -> None:
    # build.py's append-only contract applies even when the capture scope changes.
    first = build(capture_dir, OPTIONS)
    manifest = load_manifest(capture_dir)
    original_scope = list(manifest["scope"]["law_ids"])
    manifest["scope"]["law_ids"] = ["LOV-2022-06-17-63"]
    write_manifest(capture_dir, manifest)

    narrowed = build(capture_dir, BuildOptions(PRODUCER, previous=first))
    assert narrowed == first

    manifest["scope"]["law_ids"] = original_scope
    write_manifest(capture_dir, manifest)
    assert build(capture_dir, BuildOptions(PRODUCER, previous=narrowed)) == first


@pytest.mark.parametrize("kind", ["archive", "case"])
def test_corrupt_capture_is_refused_before_output_is_published(
    capture_dir: Path, tmp_path: Path, offline: None, kind: str
) -> None:
    manifest = load_manifest(capture_dir)
    entry = (
        manifest["lovdata"][0]
        if kind == "archive"
        else next(e for e in manifest["stortinget"] if e["kind"] == "sak")
    )
    cached = capture_dir / entry["path"]
    cached.write_bytes(b"corrupted capture bytes")
    output = tmp_path / "release.jsonl"

    with pytest.raises(CrosswalkError, match="do not match"):
        build_impl(capture_dir, output, OPTIONS)

    assert not output.exists()
    assert not output.with_name(f".{output.name}.partial").exists()
    assert cached.read_bytes() == b"corrupted capture bytes"
    assert load_manifest(capture_dir) == manifest


def test_appending_continues_from_the_highest_revision() -> None:
    one = {"amending_lov_id": "LOV-1", "record_revision": 1, "evidence": [], "status": "a"}
    two = {**one, "record_revision": 2, "status": "b"}
    fresh = {**one, "status": "c"}
    assert append_revisions([fresh], [two, one])[-1]["record_revision"] == 3
    assert append_revisions([{**one, "status": "b"}], [two, one]) == [two, one]


def test_an_invalid_previous_release_is_refused(capture_dir: Path) -> None:
    first = parse_records(build(capture_dir, OPTIONS))
    first[0]["status"] = "attested"
    first[0]["edge_status"] = {"law_identity": "attested"}
    with pytest.raises(CrosswalkContractError):
        build(capture_dir, BuildOptions(PRODUCER, previous=serialize_records(first)))


def test_records_pass_the_contract_one_by_one(capture_dir: Path) -> None:
    for record in parse_records(build(capture_dir, OPTIONS)):
        assert record_problems(record) == []


# --- command ---------------------------------------------------------------------------


def test_build_impl_writes_once(capture_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "out.jsonl"
    assert build_impl(capture_dir, output, OPTIONS) == f"wrote 8 records to {output}"
    assert output.read_bytes() == build(capture_dir, OPTIONS)
    with pytest.raises(CrosswalkError, match="overwrite"):
        build_impl(capture_dir, output, OPTIONS)
    assert not list(tmp_path.glob(".*.partial"))


def test_write_once_reports_an_unwritable_target(tmp_path: Path) -> None:
    with pytest.raises(CrosswalkError, match="could not write"):
        write_once(tmp_path / "absent-dir" / "out.jsonl", b"x")


def test_previous_release_is_read_bounded(tmp_path: Path) -> None:
    assert read_previous(None) is None
    path = tmp_path / "prev.jsonl"
    path.write_bytes(b"{}\n")
    assert read_previous(path) == b"{}\n"
    with pytest.raises(CrosswalkError, match="unreadable"):
        read_previous(tmp_path / "absent")


def test_previous_release_size_limit_is_inclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commands, "MAX_PREVIOUS_BYTES", 3)
    path = tmp_path / "prev.jsonl"
    path.write_bytes(b"{}\n")
    assert read_previous(path) == b"{}\n"
    path.write_bytes(b"{}\n\n")
    with pytest.raises(CrosswalkError, match="previous release too large"):
        read_previous(path)


def test_write_once_cleans_staging_when_publication_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out.jsonl"

    def refuse_link(source: Path, destination: Path) -> None:
        assert source.read_bytes() == b"complete\n"
        assert destination == output
        raise OSError("link refused")

    monkeypatch.setattr(commands.os, "link", refuse_link)
    with pytest.raises(CrosswalkError, match="could not write.*link refused"):
        write_once(output, b"complete\n")
    assert not output.exists()
    assert not output.with_name(".out.jsonl.partial").exists()


def test_engine_commit_is_head_or_a_refusal(tmp_path: Path) -> None:
    assert len(engine_commit()) == 40
    with pytest.raises(CrosswalkError, match="producer commit unknown"):
        engine_commit(tmp_path)


def test_cli_build_refuses_a_missing_capture(tmp_path: Path) -> None:
    runner = CliRunner()
    args = ["ops", "crosswalk", "build", "--cache", str(tmp_path), "--output", str(tmp_path / "o")]
    result = runner.invoke(app, [*args, "--producer-commit", PRODUCER])
    assert result.exit_code == 1
    assert "refused:" in result.output
    assert not (tmp_path / MANIFEST).exists()


def test_build_is_unchanged_by_archive_and_scope_order(capture_dir: Path, offline: None) -> None:
    expected = build(capture_dir, OPTIONS)
    manifest = load_manifest(capture_dir)
    manifest["lovdata"].reverse()
    manifest["scope"]["law_ids"].reverse()
    write_manifest(capture_dir, manifest)

    assert build(capture_dir, OPTIONS) == expected


def test_cli_build_publishes_and_replays_previous_bytes(
    capture_dir: Path, tmp_path: Path, offline: None
) -> None:
    runner = CliRunner()
    output = tmp_path / "first.jsonl"
    replay = tmp_path / "replay.jsonl"
    args = ["ops", "crosswalk", "build", "--cache", str(capture_dir)]
    result = runner.invoke(app, [*args, "--output", str(output), "--producer-commit", PRODUCER])
    assert result.exit_code == 0, result.output
    assert f"wrote 8 records to {output}" in result.output
    assert output.read_bytes() == build(capture_dir, OPTIONS)

    result = runner.invoke(
        app,
        [
            *args,
            "--output",
            str(replay),
            "--previous",
            str(output),
            "--producer-commit",
            "c" * 40,
        ],
    )
    assert result.exit_code == 0, result.output
    assert f"wrote 8 records to {replay}" in result.output
    assert replay.read_bytes() == output.read_bytes()
    assert not list(tmp_path.glob(".*.partial"))


@pytest.mark.parametrize("revisions", [(2,), (1, 1), (1, 3)], ids=["start", "repeat", "gap"])
def test_cli_build_refuses_invalid_previous_history_without_publication(
    capture_dir: Path, tmp_path: Path, revisions: tuple[int, ...]
) -> None:
    record = _by_law(build(capture_dir, OPTIONS))["LOV-2022-06-17-63"]
    previous = tmp_path / "previous.jsonl"
    data = serialize_records({**record, "record_revision": revision} for revision in revisions)
    previous.write_bytes(data)
    output = tmp_path / "out.jsonl"

    result = CliRunner().invoke(
        app,
        [
            "ops",
            "crosswalk",
            "build",
            "--cache",
            str(capture_dir),
            "--output",
            str(output),
            "--previous",
            str(previous),
            "--producer-commit",
            PRODUCER,
        ],
    )

    assert result.exit_code == 1, result.output
    assert "without repeats" in result.output
    assert previous.read_bytes() == data
    assert not output.exists()
    assert not list(tmp_path.glob(".*.partial"))
