"""Behavioral regression cases for the survey redirect mutation survivors."""

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.observatory.survey_probe import ProbeSettings, SiteProbe, _same_name_host

FRONT = "https://example.invalid/"


def test_front_page_uses_get(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=FRONT, method="GET", content=b"/api/presentation/")
    with httpx.Client() as client:
        result = SiteProbe(client, ProbeSettings(delay_seconds=0))._front_page(
            FRONT, "example.invalid", 100
        )
    assert result == (None, b"/api/presentation/")


@pytest.mark.parametrize("status", [301, 404, 503])
def test_non_ok_front_page_returns_no_content(httpx_mock: HTTPXMock, status: int) -> None:
    httpx_mock.add_response(
        url=FRONT,
        status_code=status,
        headers={"Location": "https://www.example.invalid/"},
        content=b"/api/presentation/",
    )
    with httpx.Client() as client:
        target, body = SiteProbe(client, ProbeSettings(delay_seconds=0))._front_page(
            FRONT, "example.invalid", 100
        )
    assert target == ("www.example.invalid" if status == 301 else None)
    assert body == b""


def test_front_page_transport_failure_returns_no_content(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ReadTimeout("interrupted"), url=FRONT)
    with httpx.Client() as client:
        result = SiteProbe(client, ProbeSettings(delay_seconds=0))._front_page(
            FRONT, "example.invalid", 100
        )
    assert result == (None, b"")


@pytest.mark.parametrize("header", ["location", "Location", "LOCATION"])
def test_redirect_header_case_does_not_change_target(header: str) -> None:
    response = httpx.Response(302, headers={header: "https://www.example.invalid/"})
    assert _same_name_host(response, "example.invalid") == "www.example.invalid"


@pytest.mark.parametrize("location", [None, "", "/relative", "XXXX"])
@pytest.mark.parametrize("domain", ["example.invalid", "", "XXXX"])
def test_hostless_redirect_has_no_same_name_target(location: str | None, domain: str) -> None:
    headers = {} if location is None else {"Location": location}
    assert _same_name_host(httpx.Response(302, headers=headers), domain) is None


def test_only_one_same_name_redirect_is_followed(httpx_mock: HTTPXMock) -> None:
    for host, target in [
        ("example.invalid", "www.example.invalid"),
        ("www.example.invalid", "www2.example.invalid"),
    ]:
        base = f"https://{host}/"
        httpx_mock.add_response(url=base + "robots.txt", text="User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=base + "sitemap.xml", status_code=404)
        httpx_mock.add_response(
            url=base, status_code=302, headers={"Location": f"https://{target}/"}
        )
    with httpx.Client() as client:
        shape = SiteProbe(client, ProbeSettings(delay_seconds=0)).read("example.invalid")
    assert shape.domain == "example.invalid"
    assert shape.entry == "no_machine_index"
    assert shape.front_page_markers == ()
    assert [str(request.url) for request in httpx_mock.get_requests()] == [
        "https://example.invalid/robots.txt",
        "https://example.invalid/sitemap.xml",
        FRONT,
        "https://www.example.invalid/robots.txt",
        "https://www.example.invalid/sitemap.xml",
        "https://www.example.invalid/",
    ]
