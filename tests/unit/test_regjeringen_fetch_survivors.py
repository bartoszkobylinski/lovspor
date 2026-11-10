"""Behavioral probes for the nine fetch survivors in the mutation artifact."""

from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.regjeringen_fetch.fetch import FetchSettings, prepare_out, read_url_list, run_fetch
from lovspor.regjeringen_fetch.models import FetchOutcome
from tests.unit.test_regjeringen_fetch import CH1, CH2, LANDING, ROBOTS, _Clock


@pytest.mark.parametrize("location_header", ["Location", "location", "LOCATION"])
def test_list_fetch_preserves_redirect_and_refusal_contracts(
    httpx_mock: HTTPXMock, tmp_path: Path, location_header: str
) -> None:
    urls = tmp_path / "urls.txt"
    urls.write_bytes(f"# Blåbær\r\n{CH1}\r\n{CH2}\r\n{LANDING}\r\n".encode())
    listed = read_url_list(urls, 10)
    assert listed == (CH1, CH2, LANDING)
    httpx_mock.add_response(url=ROBOTS, text="User-agent: *\n")
    httpx_mock.add_response(
        url=CH1, status_code=302, headers={location_header: "https://example.org/elsewhere"}
    )
    httpx_mock.add_response(url=CH2, status_code=429)
    clock = _Clock()
    out = prepare_out(tmp_path / "pages")
    with httpx.Client(follow_redirects=True) as client:
        run = run_fetch(client, listed, out, FetchSettings(clock=clock.as_fetch_clock()))
    assert [record.outcome for record in run.records] == [
        FetchOutcome.REDIRECT_NOT_FOLLOWED,
        FetchOutcome.STOPPED_BY_SERVER,
        FetchOutcome.NOT_ATTEMPTED,
    ]
    assert run.records[0].location == "https://example.org/elsewhere"
    requests = httpx_mock.get_requests()
    assert [str(request.url) for request in requests] == [ROBOTS, CH1, CH2]
    assert {request.method for request in requests} == {"GET"}
    assert {request.headers["user-agent"] for request in requests} == {run.user_agent}
    assert clock.slept == [5.0, 5.0]
    assert list(out.glob("*.html")) == []
    (record_file,) = out.glob("fetch-run-*.json")
    assert type(run).model_validate_json(record_file.read_bytes()) == run
