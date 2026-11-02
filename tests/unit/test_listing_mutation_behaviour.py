"""Observable listing behavior for the reported discovery survivors."""

from hashlib import sha256

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.errors import ParseError, StortingetStatusError
from lovspor.stortinget_discovery import fetch_listing

URL = "https://data.stortinget.no/eksport/saker?sesjonid=2021-2022"


@pytest.mark.parametrize("header_name", ["content-type", "CONTENT-TYPE", "cOnTeNt-TyPe"])
def test_listing_accepts_xml_header_case_and_uses_get(
    httpx_mock: HTTPXMock, header_name: str
) -> None:
    body = b'<saker_oversikt xmlns="http://data.stortinget.no"/>'
    httpx_mock.add_response(
        url=URL,
        method="GET",
        headers={header_name: " Text/XML ; charset=utf-8"},
        content=body,
    )
    with httpx.Client() as client:
        snapshot = fetch_listing(client, "saker", "2021-2022")
    assert snapshot.content_type == "text/xml"
    assert snapshot.body == body
    assert snapshot.source_sha256 == sha256(body).hexdigest()
    assert snapshot.source_url == URL
    assert [request.method for request in httpx_mock.get_requests()] == ["GET"]


def test_listing_without_content_type_rejects_even_xml_body(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, content=b"<saker_oversikt/>")
    with (
        httpx.Client() as client,
        pytest.raises(ParseError, match="^Expected an XML listing from Stortinget$"),
    ):
        fetch_listing(client, "saker", "2021-2022")
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_listing_refuses_redirect_before_contacting_target(
    httpx_mock: HTTPXMock, status: int
) -> None:
    httpx_mock.add_response(
        url=URL, status_code=status, headers={"Location": "https://example.org/listing"}
    )
    with (
        httpx.Client(follow_redirects=True) as client,
        pytest.raises(StortingetStatusError) as error,
    ):
        fetch_listing(client, "saker", "2021-2022")
    assert error.value.status_code == status
    assert error.value.url == URL
    assert [str(request.url) for request in httpx_mock.get_requests()] == [URL]
