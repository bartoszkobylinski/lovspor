"""Observable contracts missed by the #738 mutation run."""

import hashlib
import locale
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.errors import RegjeringenFetchError
from lovspor.regjeringen_fetch.commands import fetch_impl
from lovspor.regjeringen_fetch.fetch import FetchSettings, prepare_out, read_url_list, run_fetch
from lovspor.regjeringen_fetch.models import FetchOutcome
from tests.unit.test_regjeringen_fetch import CH1, PAGE, ROBOTS, _Clock


def test_url_list_diagnostics_identify_the_input(tmp_path: Path) -> None:
    path = tmp_path / "urls.txt"
    for content, reason, detail in [
        ("https://evil.example/", "url_not_allowed", "https://evil.example/"),
        ("# empty\n", "no_urls", str(path)),
    ]:
        path.write_text(content, encoding="utf-8")
        with pytest.raises(RegjeringenFetchError) as caught:
            read_url_list(path, 10)
        assert (caught.value.reason, caught.value.detail) == (reason, detail)


def test_utf8_comments_do_not_depend_on_the_locale(tmp_path: Path) -> None:
    path = tmp_path / "urls.txt"
    path.write_text(f"# blåbær\n{CH1}\n", encoding="utf-8")
    previous = locale.setlocale(locale.LC_CTYPE)
    try:
        locale.setlocale(locale.LC_CTYPE, "C")
        assert read_url_list(path, 10) == (CH1,)
    finally:
        locale.setlocale(locale.LC_CTYPE, previous)


@pytest.mark.parametrize("kind", ["relative", "git", "public"])
def test_output_refusal_identifies_reason_and_path(tmp_path: Path, kind: str) -> None:
    if kind == "relative":
        out, reason = Path("staging"), "out_not_absolute"
    elif kind == "git":
        (tmp_path / ".git").mkdir()
        out, reason = tmp_path / "pages", "out_inside_git"
    else:
        out, reason = tmp_path / "pages", "out_not_private"
        out.mkdir()
        out.chmod(0o755)
    with pytest.raises(RegjeringenFetchError) as caught:
        prepare_out(out)
    assert (caught.value.reason, caught.value.detail) == (reason, str(out))


def test_output_creates_missing_ancestors(tmp_path: Path) -> None:
    out = tmp_path / "missing" / "nested" / "pages"
    assert prepare_out(out) == out.resolve()
    assert out.is_dir()
    assert out.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("failure", ["timeout", "status"])
def test_robots_failure_diagnostic(httpx_mock: HTTPXMock, tmp_path: Path, failure: str) -> None:
    if failure == "timeout":
        httpx_mock.add_exception(httpx.ReadTimeout("slow"), url=ROBOTS)
        detail = "ReadTimeout"
    else:
        httpx_mock.add_response(url=ROBOTS, status_code=503)
        detail = "503"
    out = prepare_out(tmp_path / "pages")
    with httpx.Client() as client, pytest.raises(RegjeringenFetchError) as caught:
        run_fetch(client, (CH1,), out, FetchSettings(clock=_Clock().as_fetch_clock()))
    assert (caught.value.reason, caught.value.detail) == ("robots_unreadable", detail)
    assert list(out.iterdir()) == []


def test_small_floor_and_run_record_bytes(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    httpx_mock.add_response(url=ROBOTS, text="User-agent: *\n")
    httpx_mock.add_response(url=CH1, method="GET", content=PAGE)
    clock = _Clock()
    out = prepare_out(tmp_path / "pages")
    with httpx.Client() as client:
        run = run_fetch(
            client,
            (CH1,),
            out,
            FetchSettings(floor_seconds=0.25, clock=clock.as_fetch_clock()),
        )
    assert all(
        request.headers["User-Agent"].startswith("lovspor-forarbeider/")
        for request in httpx_mock.get_requests()
    )
    assert clock.slept == [0.25]
    assert run.delay_seconds == 0.25
    assert run.records[0].outcome is FetchOutcome.FETCHED
    assert (out / "fetch-run-20261109T120000+0000.json").read_bytes() == (
        run.model_dump_json(indent=2).encode("utf-8")
    )


def test_explicit_timeouts_and_redirect_policy_override_client_defaults(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    timeouts = dict.fromkeys(("connect", "read", "write", "pool"), 1.25)
    httpx_mock.add_response(
        url=ROBOTS,
        method="GET",
        match_extensions={"timeout": timeouts},
        text="User-agent: *\n",
    )
    httpx_mock.add_response(
        url=CH1,
        method="GET",
        match_extensions={"timeout": timeouts},
        status_code=302,
        headers={"Location": "https://evil.example/"},
    )
    out = prepare_out(tmp_path / "pages")
    with httpx.Client(timeout=99, follow_redirects=True) as client:
        run = run_fetch(
            client,
            (CH1,),
            out,
            FetchSettings(timeout_seconds=1.25, clock=_Clock().as_fetch_clock()),
        )
    assert run.records[0].outcome is FetchOutcome.REDIRECT_NOT_FOLLOWED
    assert run.records[0].location == "https://evil.example/"
    assert [str(request.url) for request in httpx_mock.get_requests()] == [ROBOTS, CH1]


def test_report_uses_twelve_robot_digest_characters(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    robots = b"User-agent: *\n"
    httpx_mock.add_response(url=ROBOTS, content=robots)
    httpx_mock.add_response(url=CH1, content=PAGE)
    urls = tmp_path / "urls.txt"
    urls.write_text(CH1, encoding="utf-8")
    out = tmp_path / "pages"
    report, complete = fetch_impl(urls, out, 10, FetchSettings(clock=_Clock().as_fetch_clock()))
    assert complete
    assert report.splitlines()[-1] == (
        f"fetched 1 of 1; robots {hashlib.sha256(robots).hexdigest()[:12]}; "
        f"spacing 5s; run {out / 'fetch-run-20261109T120000+0000.json'}"
    )
