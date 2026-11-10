"""robots.txt answers that must never read as "no rules" (#738 review, Amendment 1 point 8).

A redirect body is not a robots file, a 403/429 is the server saying stop,
and the bytes a policy is compared against are the bytes that were served.
"""

import hashlib
import stat
from datetime import timedelta
from itertools import pairwise
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.errors import RegjeringenFetchError
from lovspor.regjeringen_fetch.fetch import FetchSettings, prepare_out, run_fetch
from lovspor.regjeringen_fetch.models import FetchOutcome, FetchRun
from tests.unit.test_regjeringen_fetch import CH1, PAGE, ROBOTS, _Clock

MOVED = "https://www.regjeringen.no/robots-moved.txt"


def test_robots_evidence_dates_the_final_request_not_the_response(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    clock = _Clock()
    started_at = clock.now()

    def redirect(request: httpx.Request) -> httpx.Response:
        clock.sleep(2.0)
        return httpx.Response(302, headers={"Location": MOVED})

    def rules(request: httpx.Request) -> httpx.Response:
        clock.sleep(3.0)
        return httpx.Response(200, text="User-agent: *\nDisallow: /no/\n")

    httpx_mock.add_callback(redirect, url=ROBOTS)
    httpx_mock.add_callback(rules, url=MOVED)
    out = prepare_out(tmp_path / "pages")
    run = _run(out, clock)

    assert run.started_at == started_at
    assert run.robots.retrieved_at == started_at + timedelta(seconds=5)
    assert clock.now() == started_at + timedelta(seconds=8)
    assert run.records[0].outcome is FetchOutcome.ROBOTS_DISALLOWED
    (record_file,) = out.glob("fetch-run-*.json")
    saved = FetchRun.model_validate_json(record_file.read_bytes())
    assert saved.robots.retrieved_at == run.robots.retrieved_at


def _run(out: Path, clock: _Clock) -> FetchRun:
    with httpx.Client() as client:
        return run_fetch(client, (CH1,), out, FetchSettings(clock=clock.as_fetch_clock()))


def _refused(out: Path) -> RegjeringenFetchError:
    with pytest.raises(RegjeringenFetchError) as caught:
        _run(out, _Clock())
    return caught.value


def _requested(httpx_mock: HTTPXMock) -> list[str]:
    return [str(request.url) for request in httpx_mock.get_requests()]


@pytest.mark.parametrize("status", [403, 429])
def test_a_refused_robots_stops_the_run_before_any_document(
    httpx_mock: HTTPXMock, tmp_path: Path, status: int
) -> None:
    httpx_mock.add_response(url=ROBOTS, status_code=status)
    out = prepare_out(tmp_path / "pages")
    error = _refused(out)
    assert (error.reason, error.detail) == ("robots_refused", str(status))
    assert _requested(httpx_mock) == [ROBOTS]
    assert list(out.iterdir()) == []


def test_a_redirect_body_is_never_read_as_robots(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    httpx_mock.add_response(
        url=ROBOTS,
        status_code=301,
        headers={"Location": "https://example.org/robots.txt"},
        text="<html>Moved</html>",
    )
    error = _refused(prepare_out(tmp_path / "pages"))
    assert (error.reason, error.detail) == ("robots_unreadable", "redirect_off_host")
    assert _requested(httpx_mock) == [ROBOTS]


@pytest.mark.parametrize(
    "location",
    [
        "http://www.regjeringen.no/robots.txt",
        "https://regjeringen.no/robots.txt",
        "https://www.regjeringen.no.example.org/robots.txt",
        "https://user@www.regjeringen.no/robots.txt",
        "https://www.regjeringen.no:8443/robots.txt",
    ],
)
def test_a_robots_redirect_off_the_https_host_is_never_requested(
    httpx_mock: HTTPXMock, tmp_path: Path, location: str
) -> None:
    httpx_mock.add_response(url=ROBOTS, status_code=302, headers={"Location": location})
    error = _refused(prepare_out(tmp_path / "pages"))
    assert (error.reason, error.detail) == ("robots_unreadable", "redirect_off_host")
    assert _requested(httpx_mock) == [ROBOTS]


def test_a_robots_redirect_without_location_is_unreadable(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    httpx_mock.add_response(url=ROBOTS, status_code=301, text="User-agent: *\n")
    error = _refused(prepare_out(tmp_path / "pages"))
    assert (error.reason, error.detail) == ("robots_unreadable", "301")
    assert _requested(httpx_mock) == [ROBOTS]


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_same_host_robots_redirect_is_followed_spaced_and_recorded(
    httpx_mock: HTTPXMock, tmp_path: Path, status: int
) -> None:
    httpx_mock.add_response(
        url=ROBOTS, status_code=status, headers={"Location": "/robots-moved.txt"}
    )
    httpx_mock.add_response(url=MOVED, text="User-agent: *\nDisallow: /no/\n")
    clock = _Clock()
    run = _run(prepare_out(tmp_path / "pages"), clock)
    assert run.records[0].outcome is FetchOutcome.ROBOTS_DISALLOWED
    assert _requested(httpx_mock) == [ROBOTS, MOVED]
    assert clock.slept == [5.0]
    assert [(hop.url, hop.status) for hop in run.robots.hops] == [(ROBOTS, status), (MOVED, 200)]
    assert run.robots.hops[0].location == "/robots-moved.txt"


def test_more_than_five_robots_redirects_are_unreadable(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    hops = [ROBOTS] + [f"https://www.regjeringen.no/r{n}.txt" for n in range(1, 7)]
    for here, there in pairwise(hops):
        httpx_mock.add_response(url=here, status_code=307, headers={"Location": there})
    error = _refused(prepare_out(tmp_path / "pages"))
    assert (error.reason, error.detail) == ("robots_unreadable", "too_many_redirects")
    assert _requested(httpx_mock) == hops[:6]


def test_five_robots_redirects_are_still_followed(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    hops = [ROBOTS] + [f"https://www.regjeringen.no/r{n}.txt" for n in range(1, 6)]
    for here, there in pairwise(hops):
        httpx_mock.add_response(url=here, status_code=308, headers={"Location": there})
    httpx_mock.add_response(url=hops[-1], text="User-agent: *\n")
    httpx_mock.add_response(url=CH1, content=PAGE)
    run = _run(prepare_out(tmp_path / "pages"), _Clock())
    assert len(run.robots.hops) == 6
    assert run.records[0].outcome is FetchOutcome.FETCHED


@pytest.mark.parametrize("status", [400, 404, 410, 499])
def test_an_absent_robots_publishes_no_rules_and_stores_no_evidence(
    httpx_mock: HTTPXMock, tmp_path: Path, status: int
) -> None:
    httpx_mock.add_response(url=ROBOTS, status_code=status, text="<html>not here</html>")
    httpx_mock.add_response(url=CH1, content=PAGE)
    out = prepare_out(tmp_path / "pages")
    run = _run(out, _Clock())
    assert run.records[0].outcome is FetchOutcome.FETCHED
    assert (run.robots.state, run.robots.status) == ("absent", status)
    assert (run.robots.sha256, run.robots.size) == (None, None)
    assert list(out.glob("robots-*")) == []


def test_published_robots_bytes_are_stored_owner_only_under_their_digest(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    body = b"User-agent: *\r\nDisallow: /sok\r\n"
    httpx_mock.add_response(url=ROBOTS, content=body)
    httpx_mock.add_response(url=CH1, content=PAGE)
    out = prepare_out(tmp_path / "pages")
    run = _run(out, _Clock())
    digest = hashlib.sha256(body).hexdigest()
    assert (run.robots.state, run.robots.sha256, run.robots.size) == (
        "published",
        digest,
        len(body),
    )
    stored = out / f"robots-{digest}.txt"
    assert stored.read_bytes() == body
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "answer", ["oversized", "undecodable", "server", "no_content", "not_modified"]
)
def test_a_robots_body_that_cannot_be_read_stops_the_run(
    httpx_mock: HTTPXMock, tmp_path: Path, answer: str
) -> None:
    replies = {
        "oversized": ({"content": b"#" * (512 * 1024 + 1)}, "oversized"),
        "undecodable": ({"content": b"User-agent: *\n\xff\xfe"}, "undecodable"),
        "server": ({"status_code": 500}, "500"),
        "no_content": ({"status_code": 204}, "204"),
        "not_modified": ({"status_code": 304, "headers": {"Location": "/x"}}, "304"),
    }
    reply, detail = replies[answer]
    httpx_mock.add_response(url=ROBOTS, **reply)  # type: ignore[arg-type]
    out = prepare_out(tmp_path / "pages")
    error = _refused(out)
    assert (error.reason, error.detail) == ("robots_unreadable", detail)
    assert list(out.iterdir()) == []
