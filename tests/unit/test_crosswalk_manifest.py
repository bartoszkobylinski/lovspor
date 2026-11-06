"""The tool-written crosswalk manifest (#610, P5)."""

import json
from hashlib import sha256
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.crosswalk import commands
from lovspor.crosswalk.approval import CandidateProof
from lovspor.crosswalk.build import BuildOptions, build
from lovspor.crosswalk.cache import MANIFEST
from lovspor.crosswalk.canonical import parse_records, serialize_records
from lovspor.crosswalk.commands import manifest_impl
from lovspor.crosswalk.errors import CrosswalkContractError, CrosswalkError
from lovspor.crosswalk.manifest import BLOCKERS, HISTORICAL, build_manifest, render_manifest
from lovspor.crosswalk.release import Approval, promote
from tests.unit.crosswalk_support import COMMIT, PRODUCER, attested, besl_o, digest, make_capture

OPTIONS = BuildOptions(producer_commit=PRODUCER)


@pytest.fixture
def capture_dir(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    return make_capture(tmp_path, httpx_mock)


@pytest.fixture
def built(capture_dir: Path) -> bytes:
    return build(capture_dir, OPTIONS)


def test_manifest_describes_the_file(built: bytes) -> None:
    manifest = build_manifest(built, "crosswalk.jsonl")
    assert manifest["manifest_version"] == 1
    assert manifest["schema_version"] == "1.0.0-rc"
    assert manifest["release_state"] == "research_only"
    assert manifest["files"] == [
        {"name": "crosswalk.jsonl", "sha256": sha256(built).hexdigest(), "size_bytes": len(built)}
    ]
    assert manifest["counts"] == {
        "revisions": 8,
        "acts": 8,
        "status": {
            "ambiguous": 1,
            "attested": 3,
            "contradicted": 1,
            "unresolved": 2,
            "unsupported": 1,
        },
        "release_state": {"research_only": 8},
        "record_state": {"active": 8},
    }
    assert manifest["producer_commits"] == [PRODUCER]
    assert manifest["lovverk_commits"] == [COMMIT]
    assert manifest["parser_versions"] == [
        "besl-o-header-1",
        "lovtidend-identity-1",
        "lovvedtak-discovery-2",
        "sak-xml-2",
    ]


def test_archive_pins_are_listed_once_with_their_basis(built: bytes) -> None:
    pins = build_manifest(built, "x")["lovdata_archives"]
    assert [(p["source_url"].rsplit("/", 1)[1], p["licence_basis"]) for p in pins] == [
        ("lovtidend-avd1-2001-2025.tar.bz2", "stated_for_file"),
        ("lovtidend-avd1-2026.tar.bz2", "stated_for_endpoint"),
    ]


def test_blockers_and_attribution_stay_until_resolved(built: bytes) -> None:
    manifest = build_manifest(built, "x")
    assert manifest["source_terms"] == [
        {
            "source": "Stiftelsen Lovdata, publicData",
            "terms_url": "https://api.lovdata.no/om-api-tjenesten/",
            "read_on": "2026-11-02",
            "licence": "NLOD-2.0",
        },
        {
            "source": "Stortinget, data.stortinget.no eksport",
            "terms_url": "https://data.stortinget.no/om-datatjenesten/bruksvilkar/",
            "read_on": "2026-11-02",
            "licence": "NLOD (current version; the terms name none)",
        },
    ]
    assert manifest["unresolved_blockers"] == BLOCKERS
    assert any(b.startswith("R3:") for b in BLOCKERS)
    assert any(b.startswith("R4:") for b in BLOCKERS)
    assert manifest["attribution"]["status"] == "provisional"
    assert "Stortinget" in manifest["attribution"]["nb"]
    assert "Stiftelsen Lovdata" in manifest["attribution"]["en"]
    assert "released" not in json.dumps(manifest)
    assert "no claim beyond them" in manifest["coverage"]


def test_manifest_is_deterministic(built: bytes) -> None:
    first = render_manifest(build_manifest(built, "x"))
    assert render_manifest(build_manifest(built, "x")) == first
    assert first.endswith(b"}\n")


def test_manifest_file_pin_counts_utf8_bytes_and_preserves_filename(tmp_path: Path) -> None:
    """P5 pins the exact checked file, including non-ASCII content and its basename."""
    record = besl_o()
    record["reason"] = "Kilden støtter ikke denne koblingen – må undersøkes"  # noqa: RUF001
    data = serialize_records([record])
    assert len(data) > len(data.decode("utf-8"))
    source = tmp_path / "krysskobling-æøå.jsonl"
    output = tmp_path / "manifest.json"
    source.write_bytes(data)

    manifest_impl(source, output)

    rendered = output.read_bytes()
    manifest = json.loads(rendered)
    assert manifest["files"] == [
        {"name": source.name, "sha256": sha256(data).hexdigest(), "size_bytes": len(data)}
    ]
    assert rendered == render_manifest(build_manifest(data, source.name))
    assert render_manifest(manifest) == rendered
    assert source.read_bytes() == data


def test_manifest_counts_current_revision_independently_for_each_law() -> None:
    """P5 counts current acts while retaining every historical revision."""
    first, revised, other = attested(), attested(), besl_o()
    revised.update(record_revision=2, sak_id=None, status="unresolved")
    revised.update(
        edge_status={**revised["edge_status"], "decision_case": "unresolved"},
        reason="case not read",
        reason_code="source_unreadable",
        evidence=[e for e in revised["evidence"] if e["role"] != "decision_case"],
    )
    manifest = build_manifest(serialize_records([revised, other, first]), "history.jsonl")
    assert manifest["counts"] == {
        "revisions": 3,
        "acts": 2,
        "status": {"unresolved": 1, "unsupported": 1},
        "release_state": {"research_only": 2},
        "record_state": {"active": 2},
    }
    assert manifest["release_state"] == "research_only"


def test_a_synthetic_release_candidate_without_a_capture_gets_no_manifest() -> None:
    first, promoted = attested(), attested()
    promoted.update(record_revision=2, release_state="release_candidate")
    with pytest.raises(CrosswalkError, match="needs verified source provenance"):
        build_manifest(serialize_records([first, promoted]), "history.jsonl")


@pytest.mark.parametrize("invalid", ["utf8", "noncanonical", "revision_gap", "duplicate_revision"])
def test_manifest_refuses_invalid_input_without_publishing(tmp_path: Path, invalid: str) -> None:
    """The manifest is derived only from a file that passes check (P5)."""
    first, later = attested(), attested()
    later["record_revision"] = 3
    data = {
        "utf8": b"\xff\n",
        "noncanonical": serialize_records([first]).replace(b'":', b'": ', 1),
        "revision_gap": serialize_records([first, later]),
        "duplicate_revision": serialize_records([first, first]),
    }[invalid]
    source, output = tmp_path / "input.jsonl", tmp_path / "manifest.json"
    source.write_bytes(data)
    with pytest.raises(CrosswalkContractError):
        manifest_impl(source, output)
    assert source.read_bytes() == data
    assert not output.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["input.jsonl"]


def test_manifest_retains_historical_provenance_when_an_archive_is_repinned() -> None:
    """Distinct pins and parser/commit versions describe all revisions, not only current."""
    first, second, third = attested(), attested(), attested()
    second["record_revision"] = 2
    third["record_revision"] = 3
    for record in (second, third):
        record["source_pins"]["producer_commit"] = "c" * 40
        record["source_pins"]["lovverk_commit"] = "d" * 40
        record["source_pins"]["lovdata_archives"][0]["sha256"] = digest("repinned archive")
        for item in record["evidence"]:
            item["parser_version"] = "replacement-parser-2"
    data = serialize_records([third, first, second])
    manifest = build_manifest(data, "crosswalk.jsonl")
    pins = manifest["lovdata_archives"]
    assert len(pins) == 2
    assert pins[0]["source_url"] == pins[1]["source_url"]
    assert {pin["sha256"] for pin in pins} == {
        first["source_pins"]["lovdata_archives"][0]["sha256"],
        digest("repinned archive"),
    }
    assert manifest["producer_commits"] == [PRODUCER, "c" * 40]
    assert manifest["lovverk_commits"] == [COMMIT, "d" * 40]
    assert manifest["parser_versions"] == [
        "lovtidend-identity-1",
        "lovvedtak-discovery-2",
        "replacement-parser-2",
        "sak-xml-2",
    ]
    assert manifest["counts"]["revisions"] == 3
    assert manifest["counts"]["acts"] == 1
    rendered = render_manifest(manifest)
    assert json.loads(rendered) == manifest
    assert render_manifest(build_manifest(data, "crosswalk.jsonl")) == rendered


def test_repinned_archive_order_uses_content_after_url() -> None:
    first, second = attested(), attested()
    second["record_revision"] = 2
    second["source_pins"]["lovdata_archives"][0]["sha256"] = "0" * 64
    manifest = build_manifest(serialize_records([first, second]), "crosswalk.jsonl")
    assert manifest["lovdata_archives"] == [
        second["source_pins"]["lovdata_archives"][0],
        first["source_pins"]["lovdata_archives"][0],
    ]


def test_manifest_missing_input_identifies_crosswalk_file(tmp_path: Path) -> None:
    data, output = tmp_path / "missing.jsonl", tmp_path / "manifest.json"
    with pytest.raises(CrosswalkError) as error:
        manifest_impl(data, output)
    assert str(error.value) == f"crosswalk file unreadable: {data}"
    assert not output.exists()


def test_manifest_cli_refuses_a_missing_input_file(tmp_path: Path) -> None:
    """Exercises ``manifest_command``'s generic ``except LovsporError`` branch through the
    CLI entrypoint, the same way P4's check command is held to (#610 P5)."""
    missing, output = tmp_path / "missing.jsonl", tmp_path / "manifest.json"
    args = ["ops", "crosswalk", "manifest", "--input", str(missing), "--output", str(output)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert f"refused: crosswalk file unreadable: {missing}" in result.output
    assert not output.exists()


def test_manifest_file_size_limit_is_inclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # check_impl and release_candidate_impl each bound the input file on MAX_PREVIOUS_BYTES
    # independently (test_crosswalk_check.py); manifest_impl is a third, separate call site
    # and a regression there must fail here, not only in the other two.
    path = tmp_path / "in.jsonl"
    path.write_bytes(b"{}\n")
    output = tmp_path / "manifest.json"
    monkeypatch.setattr(commands, "MAX_PREVIOUS_BYTES", 3)
    with pytest.raises(CrosswalkError) as at_limit:
        manifest_impl(path, output)
    assert "too large" not in str(at_limit.value)
    assert not output.exists()

    monkeypatch.setattr(commands, "MAX_PREVIOUS_BYTES", 2)
    with pytest.raises(CrosswalkError, match="crosswalk file too large"):
        manifest_impl(path, output)
    assert not output.exists()


def test_a_withdrawn_release_candidate_gets_no_manifest_without_evidence() -> None:
    """A superseded candidate still claims a past owner authorization (review of #799).

    It used to read as a research file and got a manifest with no approval
    evidence, which a forged revision 1 could exploit just as well. The history is
    kept as written; only the manifest is refused until that claim can be checked.
    """
    first, withdrawn = attested(), attested()
    first["release_state"] = "release_candidate"
    withdrawn.update(
        record_revision=2,
        record_state="withdrawn",
        status="unresolved",
        edge_status={**withdrawn["edge_status"], "decision_case": "unresolved"},
        reason="producer edge was wrong",
        reason_code="withdrawn_by_producer",
        withdrawal={"withdrawn_on": "2026-11-03", "reason": "producer edge was wrong"},
    )
    data = serialize_records([first, withdrawn])
    with pytest.raises(CrosswalkError) as caught:
        build_manifest(data, "x")
    assert str(caught.value) == HISTORICAL.format(f"{first['amending_lov_id']}@1")


def test_empty_checked_file_has_an_empty_manifest_scope() -> None:
    manifest = build_manifest(b"", "empty.jsonl")
    assert manifest["files"] == [
        {"name": "empty.jsonl", "sha256": sha256(b"").hexdigest(), "size_bytes": 0}
    ]
    assert manifest["counts"] == {
        "revisions": 0,
        "acts": 0,
        "status": {},
        "release_state": {},
        "record_state": {},
    }
    assert manifest["release_state"] == "research_only"
    for field in ("producer_commits", "lovverk_commits", "lovdata_archives", "parser_versions"):
        assert manifest[field] == []


def test_release_candidate_file_level_state(built: bytes, capture_dir: Path) -> None:
    approval = Approval.model_validate(
        {
            "input_sha256": sha256(built).hexdigest(),
            "approved_by": "synthetic test approval",
            "approved_on": "2026-11-03",
            "R1": "approved",
            "R2": "approved",
        }
    )
    promoted, _ = promote(built, approval, capture_dir)
    capture = sha256((capture_dir / MANIFEST).read_bytes()).hexdigest()
    # A stand-in for the replay, which test_crosswalk_approval_binding.py covers.
    proof = CandidateProof(sha256(promoted).hexdigest(), capture, {"proof": "stand-in"})
    manifest = build_manifest(promoted, "x", capture_dir, proof)
    assert manifest["release_state"] == "release_candidate"
    assert manifest["counts"]["release_state"] == {"release_candidate": 3, "research_only": 5}
    assert manifest["counts"]["revisions"] == 11


def test_a_file_that_fails_check_gets_no_manifest(built: bytes) -> None:
    records = parse_records(built)
    records[0]["status"] = "attested"
    with pytest.raises(CrosswalkContractError):
        build_manifest(serialize_records(records), "x")


def test_manifest_impl_writes_once_and_cli_refuses(built: bytes, tmp_path: Path) -> None:
    data, output = tmp_path / "crosswalk.jsonl", tmp_path / "manifest.json"
    data.write_bytes(built)
    assert manifest_impl(data, output).startswith("wrote the manifest of crosswalk.jsonl")
    assert json.loads(output.read_text())["files"][0]["name"] == "crosswalk.jsonl"
    with pytest.raises(CrosswalkError, match="overwrite"):
        manifest_impl(data, output)
    data.write_bytes(built.replace(b'":', b'": ', 1))
    args = ["ops", "crosswalk", "manifest", "--input", str(data)]
    result = CliRunner().invoke(app, [*args, "--output", str(tmp_path / "m2.json")])
    assert result.exit_code == 1
    assert "problem: file is not in canonical form" in result.output


def test_manifest_impl_message_names_research_only_state(built: bytes, tmp_path: Path) -> None:
    """research_only is named exactly in manifest_impl's message, the same way the
    release_candidate branch is pinned exactly in
    test_manifest_command_refuses_a_candidate_without_capture_and_writes_nothing."""
    data, output = tmp_path / "crosswalk.jsonl", tmp_path / "manifest.json"
    data.write_bytes(built)
    assert manifest_impl(data, output) == (
        f"wrote the manifest of crosswalk.jsonl (research_only) to {output}"
    )


def test_cli_writes_a_research_only_manifest(built: bytes, tmp_path: Path) -> None:
    """The CLI's default path (no --cache) is never exercised end to end elsewhere: every
    other CLI invocation in this module either refuses or supplies --cache."""
    data, output = tmp_path / "crosswalk.jsonl", tmp_path / "manifest.json"
    data.write_bytes(built)
    args = ["ops", "crosswalk", "manifest", "--input", str(data), "--output", str(output)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0
    assert f"wrote the manifest of crosswalk.jsonl (research_only) to {output}" in result.output
    written = json.loads(output.read_text())
    assert written["release_state"] == "research_only"
    assert written["provenance"] == {
        "verified": False,
        "fully_reproduced": False,
        "note": (
            "research_only: contract, invariants and canonical form checked; the evidence was "
            "not reproduced from a capture"
        ),
    }
