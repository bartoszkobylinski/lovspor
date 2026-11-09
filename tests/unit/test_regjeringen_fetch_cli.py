"""``lovspor ops forarbeider-fetch`` is reachable and its pages feed the offline commands (#738)."""

import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from click.testing import Result
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


def test_incomplete_command_exits_2_and_writes_the_run_record(
    httpx_mock: HTTPXMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("lovspor.regjeringen_fetch.commands.FetchSettings", _instant)
    httpx_mock.add_response(url=ROBOTS, text="User-agent: *\n")
    httpx_mock.add_response(url=URL, status_code=429)
    out = tmp_path / "staging"
    result = CliRunner().invoke(
        app,
        [
            "ops",
            "forarbeider-fetch",
            "--urls",
            str(_list(tmp_path, URL, OTHER)),
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 2, result.output
    assert f"stopped_by_server {URL} status 429" in result.output
    assert f"not_attempted {OTHER} status -" in result.output
    assert "fetched 0 of 2;" in result.output
    assert [str(request.url) for request in httpx_mock.get_requests()] == [ROBOTS, URL]
    assert list(out.glob("*.html")) == []
    (sidecar,) = out.glob("fetch-run-*.json")
    stored = json.loads(sidecar.read_text(encoding="utf-8"))
    assert [record["outcome"] for record in stored["records"]] == [
        "stopped_by_server",
        "not_attempted",
    ]


TERMS = "https://www.regjeringen.no/no/om-nettstedet/vilkar/id0000001/"


def _invoke(tmp_path: Path, *extra: str) -> Result:
    argv = ["ops", "forarbeider-fetch", "--urls", str(_list(tmp_path, URL)), "--out"]
    return CliRunner().invoke(app, [*argv, str(tmp_path / "staging"), *extra])


def test_terms_url_option_is_documented() -> None:
    assert "website terms" in option_help(app, ["ops", "forarbeider-fetch"], "--terms-url")


def test_the_command_stores_and_prints_the_current_source_proof(
    httpx_mock: HTTPXMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("lovspor.regjeringen_fetch.commands.FetchSettings", _instant)
    robots, terms = b"User-agent: *\n", b"<html>synthetic terms</html>"
    httpx_mock.add_response(url=ROBOTS, content=robots)
    httpx_mock.add_response(url=TERMS, content=terms)
    httpx_mock.add_response(url=URL, content=FIXTURE.read_bytes())
    result = _invoke(tmp_path, "--terms-url", TERMS)
    assert result.exit_code == 0, result.output
    found = dict(
        re.findall(
            r"^(robots_txt|website_terms) published .* file (\S+)$", result.output, re.MULTILINE
        )
    )
    assert Path(found["robots_txt"]).read_bytes() == robots
    assert Path(found["website_terms"]).read_bytes() == terms
    (sidecar,) = (tmp_path / "staging").glob("fetch-run-*.json")
    stored = json.loads(sidecar.read_text(encoding="utf-8"))
    assert stored["schema_version"] == 2
    assert stored["terms"]["requested_url"] == TERMS
    assert stored["robots"]["hops"] == [{"url": ROBOTS, "status": 200, "location": None}]


def test_a_terms_url_outside_the_grammar_exits_1_before_any_request(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    result = _invoke(tmp_path, "--terms-url", "https://example.org/terms")
    assert result.exit_code == 1
    assert "refused: terms_url_not_allowed" in result.output
    assert httpx_mock.get_requests() == []


def test_a_refused_robots_exits_1_with_nothing_fetched(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    httpx_mock.add_response(url=ROBOTS, status_code=429)
    result = _invoke(tmp_path)
    assert result.exit_code == 1
    assert "refused: robots_refused: 429" in result.output
    assert [str(request.url) for request in httpx_mock.get_requests()] == [ROBOTS]


def test_the_report_names_absent_and_unread_source_pages(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    terms = "https://www.regjeringen.no/no/vilkar/"
    httpx_mock.add_response(url=ROBOTS, status_code=404)
    httpx_mock.add_exception(httpx.ConnectError("down"), url=terms)
    httpx_mock.add_response(url=URL, content=FIXTURE.read_bytes())
    settings = replace(_instant(), terms_url=terms)
    report, _ = fetch_impl(_list(tmp_path, URL), tmp_path / "staging", 10, settings)
    lines = report.splitlines()
    assert lines[0] == f"robots_txt absent {ROBOTS} status 404 at 2100-04-01T12:00:00+00:00"
    assert lines[1] == f"website_terms unreadable {terms} status - at 2100-04-01T12:00:00+00:00"
    assert lines[-1].startswith("fetched 1 of 1; robots absent; terms unreadable; spacing 5s;")
