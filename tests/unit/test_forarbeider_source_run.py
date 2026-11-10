"""A fetch run as a batch reads it: the source proof, the pages it fetched, its digest (#738, A2b).

The batch planner needs more of a ``forarbeider-fetch`` run than a policy
does: which listed pages it actually stored, by URL and digest, so a clean
chapter's page can be proved to come from the run used as source proof. Only
``fetched`` records count; a page the run did not store proves nothing.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_source import read_source_proof, read_source_run
from lovspor.regjeringen_fetch.fetch import FetchClock, FetchSettings, prepare_out, run_fetch
from lovspor.regjeringen_fetch.models import FetchRun, evidence_file_name

ROBOTS = "https://www.regjeringen.no/robots.txt"
TERMS = "https://www.regjeringen.no/no/om-nettstedet/vilkar/id0000001/"
BASE = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/"
CH2, CH3, CH4 = (f"{BASE}?ch={n}" for n in (2, 3, 4))
RUN_AT = datetime(2100, 4, 5, 12, 0, tzinfo=UTC)


def _run(httpx_mock: HTTPXMock, out: Path) -> Path:
    httpx_mock.add_response(url=ROBOTS, content=b"User-agent: *\nDisallow: /sok\n")
    httpx_mock.add_response(url=TERMS, content=b"<html>synthetic terms</html>")
    httpx_mock.add_response(url=CH2, content=b"<html>two</html>")
    httpx_mock.add_response(url=CH3, status_code=404)
    httpx_mock.add_response(url=CH4, status_code=302, headers={"Location": BASE})
    clock = FetchClock(now=lambda: RUN_AT, monotonic=lambda: 0.0, sleep=lambda _: None)
    settings = FetchSettings(clock=clock, terms_url=TERMS)
    with httpx.Client() as client:
        run_fetch(client, (CH2, CH3, CH4), prepare_out(out), settings)
    (record,) = sorted(out.glob("fetch-run-*.json"))
    return record


def test_only_fetched_pages_are_listed_by_url_and_digest(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    run_file = _run(httpx_mock, tmp_path / "fetch")

    run = read_source_run(run_file)

    assert run.fetched == frozenset({(CH2, hashlib.sha256(b"<html>two</html>").hexdigest())})
    assert run.sha256 == hashlib.sha256(run_file.read_bytes()).hexdigest()


def test_fetched_requires_both_the_fetched_outcome_and_a_stored_digest(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    run_file = _run(httpx_mock, tmp_path / "fetch")
    payload = json.loads(run_file.read_text())
    for record in payload["records"]:
        if record["url"] == CH3:
            assert record["outcome"] != "fetched"
            assert record["sha256"] is None
            record["sha256"] = hashlib.sha256(b"not actually fetched").hexdigest()
    run_file.write_text(json.dumps(payload))

    run = read_source_run(run_file)

    assert run.fetched == frozenset({(CH2, hashlib.sha256(b"<html>two</html>").hexdigest())})


def test_the_source_part_is_exactly_the_policy_source_proof(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    run_file = _run(httpx_mock, tmp_path / "fetch")

    assert read_source_run(run_file).source == read_source_proof(run_file)
    assert read_source_run(run_file).source.run_started_at == RUN_AT


def test_a_stored_source_page_edited_after_the_run_is_corrupt(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    run_file = _run(httpx_mock, tmp_path / "fetch")
    run = FetchRun.model_validate_json(run_file.read_bytes())
    stored = run_file.parent / str(evidence_file_name(run.robots))
    stored.write_bytes(b"User-agent: *\nDisallow:\n")

    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_source_run(run_file)

    assert caught.value.reason is RefusalReason.RELEASE_RECORD_CORRUPT


def test_a_missing_or_malformed_run_record_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "fetch"
    out.mkdir(mode=0o700)
    malformed = out / "fetch-run-x.json"
    malformed.write_bytes(b'{"schema_version": 2}')
    malformed.chmod(0o600)

    with pytest.raises(ForarbeiderRefusedError) as missing:
        read_source_run(out / "absent.json")
    with pytest.raises(ForarbeiderRefusedError) as invalid:
        read_source_run(malformed)

    assert (missing.value.reason, missing.value.detail) == (
        RefusalReason.INPUT_UNREADABLE,
        "source_proof",
    )
    assert invalid.value.reason is RefusalReason.RELEASE_INVALID
