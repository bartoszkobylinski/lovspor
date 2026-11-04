"""Fail-closed check and the explicit release-candidate gate (#610, P4)."""

import json
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.crosswalk import commands
from lovspor.crosswalk.build import BuildOptions, build
from lovspor.crosswalk.cache import load_manifest
from lovspor.crosswalk.canonical import parse_records, serialize_records
from lovspor.crosswalk.check import append_only_problems, check, current_records, require_passed
from lovspor.crosswalk.commands import (
    check_impl,
    read_approval,
    read_bounded,
    release_candidate_impl,
)
from lovspor.crosswalk.errors import CrosswalkContractError, CrosswalkError
from lovspor.crosswalk.release import (
    Approval,
    accepted_bases,
    cited_archives,
    ineligibility,
    promote,
)
from tests.unit.crosswalk_support import PRODUCER, attested, make_capture

OPTIONS = BuildOptions(producer_commit=PRODUCER)
ATTESTED = {"LOV-2022-06-17-63", "LOV-2025-04-25-13", "LOV-2025-06-20-67"}


@pytest.fixture
def capture_dir(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    return make_capture(tmp_path, httpx_mock)


@pytest.fixture
def built(capture_dir: Path) -> bytes:
    return build(capture_dir, OPTIONS)


def _approval(data: bytes, r2: str = "approved") -> Approval:
    return Approval.model_validate(
        {
            "input_sha256": sha256(data).hexdigest(),
            "approved_by": "synthetic test approval",
            "approved_on": "2026-11-03",
            "R1": "approved",
            "R2": r2,
        }
    )


# --- check ---------------------------------------------------------------------------


def test_a_fresh_build_passes_and_is_reproduced(built: bytes, capture_dir: Path) -> None:
    report = check(built, cache=capture_dir)
    assert report.passed, report.problems
    assert report.unverified == []
    assert len(report.records) == 8


def test_non_canonical_bytes_fail(built: bytes) -> None:
    spaced = built.replace(b'":', b'": ', 1)
    assert "file is not in canonical form" in check(spaced).problems


def test_malformed_file_fails_without_reading_further(built: bytes) -> None:
    report = check(built + b"{oops\n")
    assert not report.passed
    assert report.records == []


def test_contract_problems_fail(built: bytes) -> None:
    records = parse_records(built)
    records[0]["release_state"] = "released"
    assert not check(serialize_records(records)).passed


def test_previous_revisions_must_stay_byte_identical(built: bytes) -> None:
    records = parse_records(built)
    assert check(built, previous=built).passed
    changed = [dict(r) for r in records]
    changed[0] = {**changed[0], "reason": "rewritten after release"}
    problems = check(serialize_records(changed), previous=built).problems
    assert any("was rewritten" in p for p in problems)
    assert any(
        "was removed" in p for p in check(serialize_records(records[1:]), previous=built).problems
    )


def test_append_only_compares_canonical_lines() -> None:
    one = {"amending_lov_id": "LOV-1", "record_revision": 1, "evidence": [], "a": 1}
    assert append_only_problems([one, {**one, "record_revision": 2}], [one]) == []
    assert append_only_problems([{**one, "a": 2}], [one]) == [
        "LOV-1@1: revision of the previous release was rewritten"
    ]


def test_current_record_is_the_highest_revision() -> None:
    one = {"amending_lov_id": "LOV-1", "record_revision": 1}
    three = {"amending_lov_id": "LOV-1", "record_revision": 3}
    assert current_records([three, one]) == {"LOV-1": three}


def test_evidence_not_reproduced_from_the_capture_fails(built: bytes, capture_dir: Path) -> None:
    records = parse_records(built)
    record = next(r for r in records if r["amending_lov_id"] == "LOV-2022-06-17-63")
    case = next(item for item in record["evidence"] if item["role"] == "decision_case")
    case["parsed_identity_sha256"] = "f" * 64
    problems = check(serialize_records(records), cache=capture_dir).problems
    assert problems == [
        "LOV-2022-06-17-63: evidence identities are not reproduced from the capture"
    ]


def test_a_law_outside_the_capture_is_unverified_not_failed(
    built: bytes, capture_dir: Path
) -> None:
    manifest = load_manifest(capture_dir)
    manifest["scope"]["law_ids"] = sorted(set(manifest["scope"]["law_ids"]) - {"LOV-2010-01-01-1"})
    (capture_dir / "capture-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    report = check(built, cache=capture_dir)
    assert report.passed
    assert report.unverified == ["LOV-2010-01-01-1"]


def test_check_impl_and_cli(built: bytes, capture_dir: Path, tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    path.write_bytes(built)
    assert check_impl(path, None, capture_dir) == (
        "ok: 8 records, 0 not reproduced from a capture, 0 with raw bytes not in this capture"
    )
    assert check_impl(path, None, None) == "ok: 8 records"
    path.write_bytes(built.replace(b'":', b'": ', 1))
    with pytest.raises(CrosswalkContractError):
        check_impl(path, None, None)
    result = CliRunner().invoke(app, ["ops", "crosswalk", "check", "--input", str(path)])
    assert result.exit_code == 1
    assert "problem: file is not in canonical form" in result.output


def test_cli_check_refuses_a_missing_input_without_a_traceback(tmp_path: Path) -> None:
    # check_impl's CrosswalkError (not a CrosswalkContractError) must still reach the
    # command's second `except LovsporError` branch and exit cleanly, the same way
    # build_command and capture_command already do for an unreadable input.
    missing = tmp_path / "missing.jsonl"
    result = CliRunner().invoke(app, ["ops", "crosswalk", "check", "--input", str(missing)])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert f"refused: crosswalk file unreadable: {missing}" in result.output


# --- release-candidate gate --------------------------------------------------------


def test_only_attested_records_on_accepted_rights_are_promoted(
    built: bytes, capture_dir: Path
) -> None:
    promoted, counts = promote(built, _approval(built), capture_dir)
    records = parse_records(promoted)
    candidates = {
        r["amending_lov_id"] for r in records if r["release_state"] == "release_candidate"
    }
    assert candidates == ATTESTED
    assert counts == {"promoted": 3, "not an active attested record": 5}
    assert set(built.splitlines()) <= set(promoted.splitlines())
    assert all(
        r["record_revision"] == 2 for r in records if r["release_state"] == "release_candidate"
    )
    assert check(promoted, previous=built, cache=capture_dir).passed


def test_promotion_is_idempotent_by_appending_nothing_new(built: bytes, capture_dir: Path) -> None:
    once, _ = promote(built, _approval(built), capture_dir)
    twice, counts = promote(once, _approval(once), capture_dir)
    assert twice == once
    assert counts["promoted"] == 0


def test_r2_rejected_keeps_records_on_the_2026_archive(built: bytes) -> None:
    record = next(r for r in parse_records(built) if r["amending_lov_id"] == "LOV-2022-06-17-63")
    url = "https://api.lovdata.no/v1/publicData/get/lovtidend-avd1-2001-2025.tar.bz2"
    on_2026 = json.loads(json.dumps(record).replace("2001-2025", "2026"))
    on_2026["source_pins"]["lovdata_archives"] = record["source_pins"]["lovdata_archives"]
    assert cited_archives(record) == {url}
    rejected = _approval(built, "rejected")
    approved = _approval(built)
    assert ineligibility(record, frozenset({"stated_for_file"})) is None
    reason = "rests on an archive whose licence basis is not accepted"
    assert ineligibility(on_2026, frozenset({"stated_for_file"})) == reason
    assert rejected.r2 == "rejected" and approved.r2 == "approved"


def test_inferred_rights_and_corrections_are_never_promoted(built: bytes) -> None:
    record = next(r for r in parse_records(built) if r["amending_lov_id"] == "LOV-2022-06-17-63")
    pins = record["source_pins"]["lovdata_archives"]
    inferred = {
        **record,
        "source_pins": {
            **record["source_pins"],
            "lovdata_archives": [{**pin, "licence_basis": "inferred_same_dataset"} for pin in pins],
        },
    }
    every = frozenset({"stated_for_file", "stated_for_endpoint"})
    assert (
        ineligibility(inferred, every) == "rests on an archive whose licence basis is not accepted"
    )
    corrected = {
        **record,
        "evidence": [*record["evidence"], {"role": "correction", "upstream_id": "x"}],
    }
    assert ineligibility(corrected, every) == "carries a correction (R4 is blocked)"
    assert ineligibility({**record, "release_state": "release_candidate"}, every) == (
        "already a release candidate"
    )


def test_ineligibility_checks_status_and_record_state_independently(built: bytes) -> None:
    """release.py promises promotion only for a record that is 'attested, active':

    status and record_state are two separate fields, and either one alone
    failing must block promotion with the same diagnostic, not just the two
    combined (#610, P4, R1/R2 gate)."""
    record = next(r for r in parse_records(built) if r["amending_lov_id"] == "LOV-2022-06-17-63")
    every = frozenset({"stated_for_file", "stated_for_endpoint"})
    assert record["status"] == "attested"
    assert record["record_state"] == "active"
    assert ineligibility(record, every) is None
    assert ineligibility({**record, "record_state": "withdrawn"}, every) == (
        "not an active attested record"
    )
    assert ineligibility({**record, "status": "unresolved"}, every) == (
        "not an active attested record"
    )


def test_approval_must_name_these_bytes(built: bytes, capture_dir: Path) -> None:
    other = _approval(built + b" ")
    with pytest.raises(CrosswalkError, match="other bytes"):
        promote(built, other, capture_dir)


def test_a_failing_check_blocks_promotion(built: bytes, capture_dir: Path) -> None:
    spaced = built.replace(b'":', b'": ', 1)
    with pytest.raises(CrosswalkContractError):
        promote(spaced, _approval(spaced), capture_dir)


@pytest.mark.parametrize(
    "change",
    [{"R1": "rejected"}, {"R2": "maybe"}, {"input_sha256": "x"}, {"approved_by": ""}, {"extra": 1}],
)
def test_an_approval_file_is_strict(tmp_path: Path, change: dict) -> None:
    body = {
        "input_sha256": "0" * 64,
        "approved_by": "owner",
        "approved_on": "2026-11-03",
        "R1": "approved",
        "R2": "approved",
    }
    path = tmp_path / "approval.json"
    path.write_text(json.dumps({**body, **change}), encoding="utf-8")
    with pytest.raises(CrosswalkError, match="approval refused"):
        read_approval(path)
    path.write_text(json.dumps(body), encoding="utf-8")
    assert read_approval(path).r1 == "approved"


def test_release_candidate_impl_writes_a_new_file(
    built: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    data, approval, output = tmp_path / "in.jsonl", tmp_path / "ok.json", tmp_path / "rc.jsonl"
    data.write_bytes(built)
    approval.write_text(_approval(built).model_dump_json(by_alias=True), encoding="utf-8")
    line = release_candidate_impl(data, approval, capture_dir, output)
    assert line == "not an active attested record: 5; promoted: 3"
    assert output.read_bytes() == promote(built, _approval(built), capture_dir)[0]
    with pytest.raises(CrosswalkError, match="overwrite"):
        release_candidate_impl(data, approval, capture_dir, output)
    assert data.read_bytes() == built


@pytest.mark.parametrize("source", ["archive", "case", "publication"])
def test_release_refuses_tampered_capture_without_writing_output(
    built: bytes, capture_dir: Path, tmp_path: Path, source: str
) -> None:
    """Approval cannot bypass cache digest verification (cache module contract)."""
    manifest = load_manifest(capture_dir)
    if source == "archive":
        entry = manifest["lovdata"][0]
    else:
        kind, key = ("sak", 89016) if source == "case" else ("publikasjoner", "2021-2022")
        entry = next(
            e
            for e in manifest["stortinget"]
            if (e["stage"], e["kind"], e["key"]) == ("discovery", kind, key)
        )
    cached = capture_dir / entry["path"]
    # Keep XML parseable and the archive decompressible: appended bytes alone
    # must invalidate the pin, even if the parsed identity would stay the same.
    cached.write_bytes(cached.read_bytes() + b"\n")
    data, approval, output = tmp_path / "input", tmp_path / "approval", tmp_path / "output"
    data.write_bytes(built)
    approval_bytes = _approval(built).model_dump_json(by_alias=True).encode()
    approval.write_bytes(approval_bytes)

    with pytest.raises(CrosswalkError, match="do not match the manifest"):
        release_candidate_impl(data, approval, capture_dir, output)

    assert not output.exists()
    assert not output.with_name(f".{output.name}.partial").exists()
    assert data.read_bytes() == built
    assert approval.read_bytes() == approval_bytes


def test_cli_gate_refuses_without_an_approval(
    built: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    data = tmp_path / "in.jsonl"
    data.write_bytes(built)
    args = [
        "ops",
        "crosswalk",
        "release-candidate",
        "--input",
        str(data),
        "--cache",
        str(capture_dir),
    ]
    result = CliRunner().invoke(
        app, [*args, "--approval", str(tmp_path / "none.json"), "--output", str(tmp_path / "o")]
    )
    assert result.exit_code == 1
    assert "refused: approval unreadable" in result.output
    assert not (tmp_path / "o").exists()


def test_cli_gate_refuses_a_failing_check_without_writing_output(
    built: bytes, tmp_path: Path
) -> None:
    # release_candidate_command has one `except LovsporError` branch, not check_command's
    # two: a CrosswalkContractError from a failing check must still collapse to one
    # "refused: N crosswalk contract problem(s): ..." line, and never reach write_once.
    data = tmp_path / "in.jsonl"
    data.write_bytes(built.replace(b'":', b'": ', 1))
    approval = tmp_path / "approval.json"
    approval.write_text(
        _approval(data.read_bytes()).model_dump_json(by_alias=True), encoding="utf-8"
    )
    output = tmp_path / "rc.jsonl"
    args = [
        "ops",
        "crosswalk",
        "release-candidate",
        "--input",
        str(data),
        "--approval",
        str(approval),
        "--cache",
        str(tmp_path / "no-such-cache"),
        "--output",
        str(output),
    ]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "refused: 1 crosswalk contract problem(s): file is not in canonical form" in (
        result.output
    )
    assert not output.exists()


def test_attested_law_outside_capture_cannot_be_promoted(built: bytes, capture_dir: Path) -> None:
    """An otherwise eligible record still needs reproduction from this capture."""
    law_id = "LOV-2022-06-17-63"
    manifest = load_manifest(capture_dir)
    manifest["scope"]["law_ids"].remove(law_id)
    (capture_dir / "capture-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    report = check(built, cache=capture_dir)
    assert report.passed, report.problems
    assert report.unverified == [law_id]
    promoted, counts = promote(built, _approval(built), capture_dir)
    records = parse_records(promoted)
    assert counts == {
        "promoted": 2,
        "not reproduced from the capture": 1,
        "not an active attested record": 5,
    }
    assert [r for r in records if r["amending_lov_id"] == law_id] == [
        r for r in parse_records(built) if r["amending_lov_id"] == law_id
    ]
    assert set(built.splitlines()) <= set(promoted.splitlines())


def test_provenance_uses_current_revision_and_preserves_old_identities(
    built: bytes, capture_dir: Path
) -> None:
    """A corrected identity can supersede an older one without rewriting history (D6)."""
    records = parse_records(built)
    current = next(r for r in records if r["amending_lov_id"] == "LOV-2022-06-17-63")
    historical = json.loads(json.dumps(current))
    case = next(e for e in historical["evidence"] if e["role"] == "decision_case")
    case["parsed_identity_sha256"] = "f" * 64
    previous = serialize_records([historical])
    current["record_revision"] = 2
    data = serialize_records([*records, historical])

    report = check(data, previous=previous, cache=capture_dir)
    assert report.passed, report.problems
    assert report.unverified == []
    assert previous.splitlines()[0] in data.splitlines()


def test_withdrawn_current_record_is_unverified_even_when_reproduced(
    built: bytes, capture_dir: Path
) -> None:
    """`_judge` treats a withdrawn record as unverified before it ever compares evidence.

    Evidence, pins and sanction events are left exactly as the capture produced
    them, so a rebuild would otherwise match both identity and raw bytes. The
    withdrawal alone must still bar certification (#610, P4): a withdrawn record
    is never "passed" by provenance, only ever unverified.
    """
    records = parse_records(built)
    law_id = "LOV-2022-06-17-63"
    current = next(r for r in records if r["amending_lov_id"] == law_id)
    withdrawn = json.loads(json.dumps(current))
    withdrawn.update(
        record_revision=2,
        record_state="withdrawn",
        status="unresolved",
        edge_status={**current["edge_status"], "decision_case": "unresolved"},
        reason="producer edge was wrong",
        reason_code="withdrawn_by_producer",
        withdrawal={"withdrawn_on": "2026-11-03", "reason": "producer edge was wrong"},
    )
    data = serialize_records([*records, withdrawn])

    report = check(data, cache=capture_dir)

    assert report.passed, report.problems
    assert report.unverified == [law_id]
    assert report.drifted == []


def test_changed_parser_version_is_not_reproduced(built: bytes, capture_dir: Path) -> None:
    records = parse_records(built)
    record = next(r for r in records if r["amending_lov_id"] == "LOV-2022-06-17-63")
    case = next(e for e in record["evidence"] if e["role"] == "decision_case")
    case["parser_version"] = "sak-xml-999"
    data = serialize_records(records)
    assert check(data).passed  # Valid shape alone cannot establish provenance.
    assert check(data, cache=capture_dir).problems == [
        "LOV-2022-06-17-63: evidence identities are not reproduced from the capture"
    ]
    with pytest.raises(CrosswalkContractError, match="not reproduced"):
        promote(data, _approval(data), capture_dir)


def test_sanction_only_archive_must_have_accepted_rights(built: bytes) -> None:
    record = next(r for r in parse_records(built) if r["amending_lov_id"] == "LOV-2022-06-17-63")
    endpoint_pin = next(
        p
        for p in record["source_pins"]["lovdata_archives"]
        if p["licence_basis"] == "stated_for_endpoint"
    )
    record["sanction_events"].append(
        {
            "law_id": "LOV-2026-01-01-1",
            "kind": "unclassified_sanction",
            "source_url": endpoint_pin["source_url"],
        }
    )
    assert check(serialize_records([record])).passed
    assert all(e["source_url"] != endpoint_pin["source_url"] for e in record["evidence"])
    assert ineligibility(record, frozenset({"stated_for_file"})) == (
        "rests on an archive whose licence basis is not accepted"
    )
    assert ineligibility(record, frozenset({"stated_for_file", "stated_for_endpoint"})) is None


def test_equal_revisions_keep_first_record() -> None:
    first = {"amending_lov_id": "LOV-1", "record_revision": 1, "reason": "first"}
    second = {**first, "reason": "second"}
    assert current_records([first, second]) == {"LOV-1": first}


def test_removed_revision_diagnostic_names_law() -> None:
    record = {"amending_lov_id": "LOV-1", "record_revision": 2}
    assert append_only_problems([], [record]) == [
        "LOV-1@2: revision of the previous release was removed"
    ]


def test_previous_does_not_erase_canonical_failure(built: bytes) -> None:
    report = check(built.replace(b'":', b'": ', 1), previous=built)
    assert report.problems == ["file is not in canonical form"]


def test_capture_without_scope_rebuilds_input_laws(built: bytes, capture_dir: Path) -> None:
    manifest = load_manifest(capture_dir)
    del manifest["scope"]
    (capture_dir / "capture-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    report = check(built, cache=capture_dir)
    assert report.passed, report.problems
    assert report.unverified == []


def test_check_impl_checks_previous_and_capture(
    built: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    data, previous = tmp_path / "input", tmp_path / "previous"
    previous.write_bytes(built)
    data.write_bytes(serialize_records(parse_records(built)[1:]))
    with pytest.raises(CrosswalkContractError, match="was removed"):
        check_impl(data, previous, None)
    records = parse_records(built)
    record = next(r for r in records if r["amending_lov_id"] == "LOV-2022-06-17-63")
    record["evidence"][0]["parsed_identity_sha256"] = "f" * 64
    data.write_bytes(serialize_records(records))
    with pytest.raises(CrosswalkContractError, match="not reproduced"):
        check_impl(data, None, capture_dir)


@pytest.mark.parametrize("command", ["check", "release"])
def test_input_read_error_names_crosswalk_file(tmp_path: Path, command: str) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(CrosswalkError) as caught:
        if command == "check":
            check_impl(missing, None, None)
        else:
            release_candidate_impl(missing, missing, missing, tmp_path / "output")
    assert str(caught.value) == f"crosswalk file unreadable: {missing}"
    assert not (tmp_path / "output").exists()


def test_bounded_read_equal_limit_and_oversize(tmp_path: Path) -> None:
    path = tmp_path / "bytes"
    path.write_bytes(b"abcd")
    assert read_bounded(path, 4, "input") == b"abcd"
    path.write_bytes(b"abcde")
    with pytest.raises(CrosswalkError) as caught:
        read_bounded(path, 4, "input")
    assert str(caught.value) == f"input too large: {path}"


@pytest.mark.parametrize("limit", [0, 4])
def test_bounded_read_rejects_fifo_without_waiting_for_eof(tmp_path: Path, limit: int) -> None:
    regular = tmp_path / "regular"
    regular.write_bytes(b"abcd")
    assert read_bounded(regular, 4, "input") == b"abcd"
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    descriptor = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    try:
        os.write(descriptor, b"x" * (limit + 1))
        code = (
            "from pathlib import Path; from lovspor.crosswalk.commands import read_bounded; "
            "from lovspor.crosswalk.errors import CrosswalkError; "
            "import sys\n"
            f"try: read_bounded(Path(sys.argv[1]), {limit}, 'input')\n"
            "except CrosswalkError as e: print(e)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(fifo)],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        assert result.stdout == f"input too large: {fifo}\n"
    finally:
        os.close(descriptor)


def test_approval_lists_exact_invalid_fields(tmp_path: Path) -> None:
    path = tmp_path / "approval"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(CrosswalkError) as caught:
        read_approval(path)
    assert str(caught.value) == (
        "approval refused: invalid R1, R2, approved_by, approved_on, input_sha256"
    )


@pytest.mark.parametrize("r2", ["approved", "rejected"])
def test_approval_controls_accepted_licence_bases(r2: str) -> None:
    expected = {"stated_for_file"}
    if r2 == "approved":
        expected.add("stated_for_endpoint")
    assert accepted_bases(_approval(b"", r2)) == frozenset(expected)


def test_archive_citation_refuses_a_member_id_with_a_second_separator() -> None:
    # The schema allows exactly one "!" (archive!member); anything else is refused, never guessed.
    record = {"sanction_events": [], "evidence": [{"upstream_id": "archive.tar.bz2!dir/a!b.xml"}]}
    with pytest.raises(CrosswalkError, match="malformed archive member id"):
        cited_archives(record)
    record["evidence"] = [{"upstream_id": "archive.tar.bz2!dir/b.xml"}, {"upstream_id": "sak/1"}]
    assert cited_archives(record) == {"https://api.lovdata.no/v1/publicData/get/archive.tar.bz2"}


def test_approval_digest_mismatch_diagnostic_is_exact(tmp_path: Path) -> None:
    with pytest.raises(CrosswalkError) as caught:
        promote(b"", _approval(b"other"), tmp_path)
    assert str(caught.value) == "the approval names other bytes than this input"


def test_release_command_does_not_promote_unverified_law(
    built: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    manifest = load_manifest(capture_dir)
    law_id = "LOV-2022-06-17-63"
    manifest["scope"]["law_ids"].remove(law_id)
    (capture_dir / "capture-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    data, approval, output = tmp_path / "input", tmp_path / "approval", tmp_path / "output"
    data.write_bytes(built)
    approval.write_text(_approval(built).model_dump_json(by_alias=True), encoding="utf-8")
    line = release_candidate_impl(data, approval, capture_dir, output)
    assert line == (
        "not an active attested record: 5; not reproduced from the capture: 1; promoted: 2"
    )
    assert [r for r in parse_records(output.read_bytes()) if r["amending_lov_id"] == law_id] == [
        r for r in parse_records(built) if r["amending_lov_id"] == law_id
    ]


@pytest.mark.parametrize("scoped", [True, False])
@pytest.mark.parametrize("tampered", [True, False])
def test_capture_rebuild_verdict_depends_on_evidence_identity(
    built: bytes, capture_dir: Path, scoped: bool, tampered: bool
) -> None:
    """Both capture formats reproduce identities, independently of producer pins."""
    if not scoped:
        manifest = load_manifest(capture_dir)
        del manifest["scope"]
        (capture_dir / "capture-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    records = parse_records(built)
    for record in records:
        record["source_pins"]["producer_commit"] = "c" * 40
    law_id = "LOV-2022-06-17-63"
    if tampered:
        record = next(r for r in records if r["amending_lov_id"] == law_id)
        record["evidence"][0]["parsed_identity_sha256"] = "f" * 64
    report = check(serialize_records(records), cache=capture_dir)
    assert report.problems == (
        [f"{law_id}: evidence identities are not reproduced from the capture"] if tampered else []
    )
    assert report.passed is not tampered
    assert report.unverified == []


def test_approval_nested_values_report_the_scalar_field_names(tmp_path: Path) -> None:
    """Objects cannot turn scalar approval fields into nested validation paths."""
    path = tmp_path / "approval.json"
    path.write_text(
        json.dumps(
            {key: {"x": []} for key in ["input_sha256", "approved_by", "approved_on", "R1", "R2"]}
        ),
        encoding="utf-8",
    )
    with pytest.raises(CrosswalkError) as caught:
        read_approval(path)
    assert str(caught.value) == (
        "approval refused: invalid R1, R2, approved_by, approved_on, input_sha256"
    )


def test_approval_size_limit_is_inclusive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Sibling bounded reads (previous release, law list) each pin this boundary;
    # MAX_APPROVAL_BYTES is new in P4 and had no test holding it to the same contract.
    body = json.dumps(
        {
            "input_sha256": "0" * 64,
            "approved_by": "owner",
            "approved_on": "2026-11-03",
            "R1": "approved",
            "R2": "approved",
        }
    ).encode()
    path = tmp_path / "approval.json"
    path.write_bytes(body)
    monkeypatch.setattr(commands, "MAX_APPROVAL_BYTES", len(body))
    assert read_approval(path).r1 == "approved"
    monkeypatch.setattr(commands, "MAX_APPROVAL_BYTES", len(body) - 1)
    with pytest.raises(CrosswalkError, match="approval too large"):
        read_approval(path)


def test_crosswalk_file_size_limit_is_inclusive_for_check_and_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # check_impl and release_candidate_impl each bound the input file on MAX_PREVIOUS_BYTES
    # independently; a regression in either call site must fail here, not only in build's.
    path = tmp_path / "in.jsonl"
    path.write_bytes(b"{}\n")
    monkeypatch.setattr(commands, "MAX_PREVIOUS_BYTES", 3)
    with pytest.raises(CrosswalkError) as at_limit:
        check_impl(path, None, None)
    assert "too large" not in str(at_limit.value)
    with pytest.raises(CrosswalkError) as at_limit:
        release_candidate_impl(path, path, tmp_path, tmp_path / "out.jsonl")
    assert "too large" not in str(at_limit.value)

    monkeypatch.setattr(commands, "MAX_PREVIOUS_BYTES", 2)
    with pytest.raises(CrosswalkError, match="crosswalk file too large"):
        check_impl(path, None, None)
    with pytest.raises(CrosswalkError, match="crosswalk file too large"):
        release_candidate_impl(path, path, tmp_path, tmp_path / "out.jsonl")


def test_cache_provenance_is_skipped_when_history_already_failed(tmp_path: Path) -> None:
    # check()'s docstring orders its checks: canonical form, contract, history,
    # then provenance against the cache, each gating the next. A cache that
    # cannot even be opened must never be reached once history already failed:
    # load_manifest would raise CrosswalkError and check() would crash instead
    # of returning a report, which is exactly what a fail-closed check must not do.
    record = attested()
    previous = serialize_records([record])
    rewritten = serialize_records([{**record, "reason": "rewritten after release"}])
    missing_cache = tmp_path / "no-such-cache"

    report = check(rewritten, previous=previous, cache=missing_cache)

    assert any("was rewritten" in p for p in report.problems)
    assert report.unverified == []
    assert report.drifted == []


def test_require_passed_raises_exactly_the_reports_problems() -> None:
    report = check(serialize_records([attested()]))
    assert report.passed
    require_passed(report)  # Does not raise.

    report.problems = ["a problem"]
    with pytest.raises(CrosswalkContractError) as caught:
        require_passed(report)
    assert caught.value.problems == ("a problem",)
