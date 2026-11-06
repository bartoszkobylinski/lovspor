"""A release_candidate revision anywhere in the history needs authorization evidence.

Independent review of #799: `_candidates()` looked only at current revisions, so a
forged `release_candidate` revision followed by a `research_only` one read as a
research file and got a manifest with no approval evidence at all. A past
candidate claims an owner authorization just as much as a current one.
"""

import copy
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from lovspor.crosswalk.approval import CARRIED
from lovspor.crosswalk.build import BuildOptions, build
from lovspor.crosswalk.canonical import Record, parse_records, serialize_records
from lovspor.crosswalk.check import check, require_passed
from lovspor.crosswalk.commands import manifest_impl
from lovspor.crosswalk.errors import CrosswalkError
from lovspor.crosswalk.manifest import build_manifest
from lovspor.crosswalk.release import Approval, promote
from tests.unit.crosswalk_signing import Signing, approval_body, approval_dir, new_signer, trust
from tests.unit.crosswalk_support import PRODUCER, make_capture


@pytest.fixture
def capture_dir(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    return make_capture(tmp_path, httpx_mock)


@pytest.fixture
def built(capture_dir: Path) -> bytes:
    return build(capture_dir, BuildOptions(producer_commit=PRODUCER))


@pytest.fixture
def signer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Signing:
    made = new_signer(tmp_path / "keys")
    trust(monkeypatch, made)
    return made


@pytest.fixture
def candidate(built: bytes, capture_dir: Path) -> bytes:
    return promote(built, Approval.model_validate(approval_body(built)), capture_dir)[0]


def _attested(records: list[Record]) -> Record:
    return next(r for r in records if r["status"] == "attested")


def _forged_history(built: bytes) -> bytes:
    """Revision 1 hand-edited to release_candidate, then a research_only revision 2."""
    records = parse_records(built)
    first = _attested(records)
    first["release_state"] = "release_candidate"
    later = {**copy.deepcopy(first), "release_state": "research_only", "record_revision": 2}
    return serialize_records([*records, later])


def _withdrawal(record: Record) -> Record:
    """The next revision of ``record``, withdrawn by the producer (D6, append-only)."""
    return {
        **copy.deepcopy(record),
        "record_revision": record["record_revision"] + 1,
        "record_state": "withdrawn",
        "release_state": "research_only",
        "status": "unresolved",
        "edge_status": {**record["edge_status"], "decision_case": "unresolved"},
        "reason": "producer edge was wrong",
        "reason_code": "withdrawn_by_producer",
        "withdrawal": {"withdrawn_on": "2026-11-06", "reason": "producer edge was wrong"},
    }


def _withdrawn_after_research(built: bytes) -> bytes:
    """A legitimate append-only transition with no candidate anywhere: a withdrawal."""
    records = parse_records(built)
    return serialize_records([*records, _withdrawal(_attested(records))])


def test_the_forged_history_passes_check(built: bytes, capture_dir: Path) -> None:
    """The forgery is well-formed: only the manifest gate can refuse it."""
    require_passed(check(_forged_history(built), cache=capture_dir))


@pytest.mark.parametrize("with_capture", [True, False])
def test_a_forged_historical_candidate_gets_no_manifest(
    built: bytes, capture_dir: Path, with_capture: bool
) -> None:
    forged = _forged_history(built)
    with pytest.raises(CrosswalkError, match="release_candidate"):
        build_manifest(forged, "rc.jsonl", cache=capture_dir if with_capture else None)


def test_a_history_without_any_candidate_still_gets_a_research_manifest(
    built: bytes, capture_dir: Path
) -> None:
    data = _withdrawn_after_research(built)
    require_passed(check(data, cache=capture_dir))
    manifest = build_manifest(data, "rc.jsonl", cache=capture_dir)
    assert manifest["release_state"] == "research_only"
    assert "approval" not in manifest


def test_a_forged_historical_candidate_is_refused_even_beside_a_valid_proof(
    built: bytes, capture_dir: Path, signer: Signing, tmp_path: Path
) -> None:
    data = tmp_path / "rc.jsonl"
    data.write_bytes(_forged_history(built))
    proof = approval_dir(tmp_path / "proof", built, signer)
    output = tmp_path / "manifest.json"
    with pytest.raises(CrosswalkError, match="not the gate's output"):
        manifest_impl(data, output, capture_dir, proof)
    assert not output.exists()


def test_a_withdrawn_real_candidate_is_kept_and_needs_evidence(
    candidate: bytes, capture_dir: Path
) -> None:
    """A legitimate candidate later withdrawn: the history stays, the claim is not blessed."""
    records = parse_records(candidate)
    promoted = next(r for r in records if r["release_state"] == "release_candidate")
    data = serialize_records([*records, _withdrawal(promoted)])
    require_passed(check(data, cache=capture_dir))
    law = promoted["amending_lov_id"]
    with pytest.raises(CrosswalkError) as caught:
        build_manifest(data, "rc.jsonl", cache=capture_dir)
    assert f"{law}@{promoted['record_revision']}" in str(caught.value)
    assert promoted in parse_records(data)


@pytest.mark.parametrize("carried", ["current", "historical"])
def test_an_approved_input_carrying_a_candidate_proves_nothing(
    candidate: bytes, capture_dir: Path, signer: Signing, tmp_path: Path, carried: str
) -> None:
    """The gate leaves a carried candidate untouched, so replay alone would match."""
    records = parse_records(candidate)
    promoted = next(r for r in records if r["release_state"] == "release_candidate")
    extra = [] if carried == "current" else [_withdrawal(promoted)]
    approved = serialize_records([*records, *extra])
    data = tmp_path / "rc.jsonl"
    data.write_bytes(approved)
    proof = approval_dir(tmp_path / "proof", approved, signer)
    with pytest.raises(CrosswalkError) as caught:
        manifest_impl(data, tmp_path / "manifest.json", capture_dir, proof)
    assert str(caught.value) == CARRIED
