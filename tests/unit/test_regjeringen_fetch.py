"""Explicit-list fetch of regjeringen.no Prop. L / LS pages (#738, contract Amendment 1)."""

import hashlib
import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.errors import RegjeringenFetchError
from lovspor.regjeringen_fetch.fetch import (
    FetchClock,
    FetchSettings,
    prepare_out,
    read_url_list,
    run_fetch,
)
from lovspor.regjeringen_fetch.models import FetchOutcome

ROBOTS = "https://www.regjeringen.no/robots.txt"
CH1 = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=1"
CH2 = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=2"
LANDING = "https://www.regjeringen.no/no/dokumenter/prop.-902-ls-20992100/id9000002/"
PAGE = b"<!DOCTYPE html><html><body>synthetic</body></html>"


class _Clock:
    """Time as an input: ``sleep`` advances ``monotonic`` and is recorded."""

    def __init__(self) -> None:
        self.ticks = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.ticks

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.ticks += seconds

    def now(self) -> datetime:
        return datetime(2026, 11, 9, 12, 0, tzinfo=UTC) + timedelta(seconds=self.ticks)

    def as_fetch_clock(self) -> FetchClock:
        return FetchClock(now=self.now, monotonic=self.monotonic, sleep=self.sleep)


def _settings(clock: _Clock, max_bytes: int = 1024) -> FetchSettings:
    return FetchSettings(max_bytes=max_bytes, clock=clock.as_fetch_clock())


def _out(tmp_path: Path) -> Path:
    return prepare_out(tmp_path / "staging")


def _robots(httpx_mock: HTTPXMock, text: str = "User-agent: *\nDisallow: /*/?q\n") -> None:
    httpx_mock.add_response(url=ROBOTS, text=text)


def _run(client_urls: list[str], out: Path, clock: _Clock, max_bytes: int = 1024):  # type: ignore[no-untyped-def]
    with httpx.Client() as client:
        return run_fetch(client, tuple(client_urls), out, _settings(clock, max_bytes))


def test_a_fetched_page_is_stored_owner_only_under_its_digest(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    _robots(httpx_mock)
    httpx_mock.add_response(url=CH1, content=PAGE)
    out = _out(tmp_path)
    run = _run([CH1], out, _Clock())
    record = run.records[0]
    digest = hashlib.sha256(PAGE).hexdigest()
    assert record.outcome is FetchOutcome.FETCHED
    assert record.sha256 == digest
    assert record.size == len(PAGE)
    saved = out / f"{digest}.html"
    assert saved.read_bytes() == PAGE
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600


def test_every_request_names_the_client(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    _robots(httpx_mock)
    httpx_mock.add_response(url=CH1, content=PAGE)
    _run([CH1], _out(tmp_path), _Clock())
    agents = {request.headers["User-Agent"] for request in httpx_mock.get_requests()}
    assert len(agents) == 1
    (agent,) = agents
    assert agent.startswith("lovspor-forarbeider/")
    assert "kontakt@lovspor.no" in agent


def test_requests_are_spaced_by_the_floor_when_robots_sets_no_delay(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    _robots(httpx_mock)
    httpx_mock.add_response(url=CH1, content=PAGE)
    httpx_mock.add_response(url=CH2, content=PAGE + b" ")
    clock = _Clock()
    run = _run([CH1, CH2], _out(tmp_path), clock)
    assert clock.slept == [5.0, 5.0]
    assert run.delay_seconds == 5.0


def test_a_longer_crawl_delay_in_robots_wins_over_the_floor(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    _robots(httpx_mock, "User-agent: *\nCrawl-delay: 12\n")
    httpx_mock.add_response(url=CH1, content=PAGE)
    clock = _Clock()
    run = _run([CH1], _out(tmp_path), clock)
    assert clock.slept == [12.0]
    assert run.delay_seconds == 12.0


def test_a_disallowed_url_is_recorded_and_never_requested(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    _robots(httpx_mock, "User-agent: *\nDisallow: /no/dokumenter/prop.-901-l-20992100/\n")
    run = _run([CH1], _out(tmp_path), _Clock())
    assert run.records[0].outcome is FetchOutcome.ROBOTS_DISALLOWED
    assert [str(request.url) for request in httpx_mock.get_requests()] == [ROBOTS]


def test_unreadable_robots_refuses_the_whole_run(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    httpx_mock.add_response(url=ROBOTS, status_code=503)
    with pytest.raises(RegjeringenFetchError, match="robots_unreadable"):
        _run([CH1], _out(tmp_path), _Clock())
    assert [str(request.url) for request in httpx_mock.get_requests()] == [ROBOTS]


def test_robots_digest_is_recorded_for_the_standing_policy(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    text = "User-agent: *\nDisallow: /*/?q\n"
    _robots(httpx_mock, text)
    httpx_mock.add_response(url=CH1, content=PAGE)
    run = _run([CH1], _out(tmp_path), _Clock())
    assert run.robots_sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert run.robots_status == 200


def test_a_redirect_is_recorded_and_not_followed(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    _robots(httpx_mock)
    httpx_mock.add_response(
        url=CH1, status_code=301, headers={"Location": "https://evil.example/x"}
    )
    run = _run([CH1], _out(tmp_path), _Clock())
    record = run.records[0]
    assert record.outcome is FetchOutcome.REDIRECT_NOT_FOLLOWED
    assert record.location == "https://evil.example/x"
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.parametrize("status", [403, 429])
def test_a_refusal_stops_the_run_and_later_urls_are_not_attempted(
    httpx_mock: HTTPXMock, tmp_path: Path, status: int
) -> None:
    _robots(httpx_mock)
    httpx_mock.add_response(url=CH1, status_code=status)
    run = _run([CH1, CH2], _out(tmp_path), _Clock())
    assert [record.outcome for record in run.records] == [
        FetchOutcome.STOPPED_BY_SERVER,
        FetchOutcome.NOT_ATTEMPTED,
    ]
    assert run.records[0].status == status
    assert CH2 not in [str(request.url) for request in httpx_mock.get_requests()]


def test_another_status_is_recorded_and_the_run_goes_on(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    _robots(httpx_mock)
    httpx_mock.add_response(url=CH1, status_code=404)
    httpx_mock.add_response(url=CH2, content=PAGE)
    run = _run([CH1, CH2], _out(tmp_path), _Clock())
    assert [record.outcome for record in run.records] == [
        FetchOutcome.HTTP_STATUS,
        FetchOutcome.FETCHED,
    ]


def test_an_oversized_body_is_never_stored(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    _robots(httpx_mock)
    httpx_mock.add_response(url=CH1, content=b"x" * 2048)
    out = _out(tmp_path)
    run = _run([CH1], out, _Clock(), max_bytes=1024)
    assert run.records[0].outcome is FetchOutcome.TOO_LARGE
    assert run.records[0].sha256 is None
    assert list(out.glob("*.html")) == []


def test_a_transport_error_is_recorded(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    _robots(httpx_mock)
    httpx_mock.add_exception(httpx.ConnectTimeout("slow"), url=CH1)
    run = _run([CH1], _out(tmp_path), _Clock())
    assert run.records[0].outcome is FetchOutcome.TRANSPORT_ERROR


def test_retrieved_at_is_the_machine_clock_at_the_request(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    _robots(httpx_mock)
    httpx_mock.add_response(url=CH1, content=PAGE)
    clock = _Clock()
    run = _run([CH1], _out(tmp_path), clock)
    assert run.records[0].retrieved_at == datetime(2026, 11, 9, 12, 0, 5, tzinfo=UTC)
    assert run.records[0].retrieved_at.utcoffset() is not None


def test_the_run_record_is_written_owner_only_beside_the_pages(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    _robots(httpx_mock)
    httpx_mock.add_response(url=CH1, content=PAGE)
    out = _out(tmp_path)
    run = _run([CH1], out, _Clock())
    (sidecar,) = out.glob("fetch-run-*.json")
    assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
    stored = json.loads(sidecar.read_text(encoding="utf-8"))
    assert stored["record_type"] == "regjeringen_fetch_run"
    assert stored["records"][0]["url"] == CH1
    assert stored["records"][0]["sha256"] == run.records[0].sha256


def test_the_same_page_twice_is_stored_once(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    _robots(httpx_mock)
    httpx_mock.add_response(url=CH1, content=PAGE)
    httpx_mock.add_response(url=CH2, content=PAGE)
    out = _out(tmp_path)
    run = _run([CH1, CH2], out, _Clock())
    assert {record.sha256 for record in run.records} == {hashlib.sha256(PAGE).hexdigest()}
    assert len(list(out.glob("*.html"))) == 1


# --- the URL list ---------------------------------------------------------------------------


def _list(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "urls.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_the_list_keeps_order_skips_blanks_and_comments_and_drops_duplicates(
    tmp_path: Path,
) -> None:
    path = _list(tmp_path, "# Prop. 901 L", CH2, "", CH1, CH2, LANDING)
    assert read_url_list(path, 10) == (CH2, CH1, LANDING)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=1",
        "https://regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=1",
        "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=1&utm=x",
        "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=0",
        "https://www.regjeringen.no/no/dokumenter/nou-2025-1/id9000001/?ch=1",
        "https://www.regjeringen.no/no/dokumenter/prop.-901-s-20992100/id9000001/?ch=1",
        "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001",
        "https://www.regjeringen.no/contentassets/x.pdf",
        "https://evil.example/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=1",
    ],
)
def test_a_url_outside_the_grammar_refuses_the_list(tmp_path: Path, url: str) -> None:
    with pytest.raises(RegjeringenFetchError, match="url_not_allowed"):
        read_url_list(_list(tmp_path, CH1, url), 10)


def test_a_list_over_the_cap_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RegjeringenFetchError, match="too_many_urls"):
        read_url_list(_list(tmp_path, CH1, CH2, LANDING), 2)


def test_an_empty_list_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RegjeringenFetchError, match="no_urls"):
        read_url_list(_list(tmp_path, "# nothing"), 10)


# --- the output directory -------------------------------------------------------------------


def test_a_missing_output_directory_is_created_owner_only(tmp_path: Path) -> None:
    out = prepare_out(tmp_path / "new")
    assert stat.S_IMODE(out.stat().st_mode) == 0o700


def test_an_output_directory_others_can_read_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "open"
    out.mkdir(mode=0o755)
    out.chmod(0o755)
    with pytest.raises(RegjeringenFetchError, match="out_not_private"):
        prepare_out(out)


def test_a_relative_output_directory_is_refused() -> None:
    with pytest.raises(RegjeringenFetchError, match="out_not_absolute"):
        prepare_out(Path("staging"))


def test_an_output_directory_inside_a_git_checkout_is_refused(tmp_path: Path) -> None:
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(RegjeringenFetchError, match="out_inside_git"):
        prepare_out(tmp_path / "repo" / "pages")
