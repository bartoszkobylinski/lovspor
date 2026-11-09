"""``lovspor ops forarbeider-fetch`` is reachable and its pages feed the offline commands (#738)."""

import re
from datetime import UTC, datetime
from pathlib import Path

from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.regjeringen_fetch.commands import fetch_impl
from lovspor.regjeringen_fetch.fetch import FetchClock, FetchSettings
from tests.unit.cli_output import option_help

FIXTURE = Path(__file__).parents[1] / "fixtures" / "forarbeider" / "prop-l.html"
ROBOTS = "https://www.regjeringen.no/robots.txt"
URL = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=1"
OTHER = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=2"


def _instant() -> FetchSettings:
    moment = datetime(2100, 4, 1, 12, 0, tzinfo=UTC)
    return FetchSettings(
        clock=FetchClock(now=lambda: moment, monotonic=lambda: 0.0, sleep=lambda _: None)
    )


def _list(tmp_path: Path, *urls: str) -> Path:
    path = tmp_path / "urls.txt"
    path.write_text("\n".join(urls) + "\n", encoding="utf-8")
    return path


def test_the_command_is_registered_on_ops() -> None:
    result = CliRunner().invoke(app, ["ops", "forarbeider-fetch", "--help"])
    assert result.exit_code == 0
    assert option_help(app, ["ops", "forarbeider-fetch"], "--urls")
    assert option_help(app, ["ops", "forarbeider-fetch"], "--out")


def test_a_url_outside_the_grammar_exits_1_before_any_request(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    bad = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=1&x=1"
    argv = [
        "ops",
        "forarbeider-fetch",
        "--urls",
        str(_list(tmp_path, bad)),
        "--out",
        str(tmp_path / "s"),
    ]
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 1
    assert "refused: url_not_allowed" in result.output
    assert httpx_mock.get_requests() == []


def test_a_fetched_page_is_accepted_by_forarbeider_metadata(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    httpx_mock.add_response(url=ROBOTS, text="User-agent: *\n")
    httpx_mock.add_response(url=URL, content=FIXTURE.read_bytes())
    report, complete = fetch_impl(_list(tmp_path, URL), tmp_path / "staging", 10, _instant())
    assert complete
    match = re.search(r"retrieved_at (\S+) file (\S+)", report)
    assert match is not None
    when, page = match.groups()
    argv = [
        "ops",
        "forarbeider-metadata",
        "--html",
        page,
        "--source-url",
        URL,
        "--retrieved-at",
        when,
        "--output",
        str(tmp_path / "out" / "record.json"),
    ]
    (tmp_path / "out").mkdir(mode=0o700)
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 0, result.output
    assert "regjeringen-9000001" in result.output


def test_an_incomplete_run_reports_what_was_not_stored(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    httpx_mock.add_response(url=ROBOTS, text="User-agent: *\n")
    httpx_mock.add_response(url=URL, content=FIXTURE.read_bytes())
    httpx_mock.add_response(url=OTHER, status_code=404)
    report, complete = fetch_impl(_list(tmp_path, URL, OTHER), tmp_path / "staging", 10, _instant())
    assert not complete
    assert f"http_status {OTHER} status 404" in report
    assert report.splitlines()[-1].startswith("fetched 1 of 2;")
