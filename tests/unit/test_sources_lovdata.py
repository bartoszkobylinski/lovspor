"""Tests for lovspor.sources.lovdata.

The list_response fixture is a real response captured from
https://api.lovdata.no/v1/publicData/list on 2026-04-22, licensed under
NLOD 2.0, attributed to Lovdata.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from pytest_httpx import HTTPXMock

from lovspor.errors import ConfigError, NetworkError, ParseError
from lovspor.sources.lovdata import (
    DEFAULT_BASE_URL,
    LovdataArchive,
    LovdataClient,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_LIST_URL = f"{DEFAULT_BASE_URL}/list"


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lovspor.retry.time.sleep", lambda _seconds: None)


@pytest.fixture
def list_payload() -> list[dict[str, Any]]:
    return json.loads((_FIXTURES / "lovdata_list_response.json").read_text())


def test_list_datasets_parses_real_response(
    httpx_mock: HTTPXMock,
    list_payload: list[dict[str, Any]],
) -> None:
    httpx_mock.add_response(url=_LIST_URL, json=list_payload)
    with LovdataClient() as client:
        archives = client.list_datasets()
    assert len(archives) == 4
    filenames = {a.filename for a in archives}
    assert "gjeldende-lover.tar.bz2" in filenames
    assert "gjeldende-sentrale-forskrifter.tar.bz2" in filenames


def test_list_datasets_coerces_size_string_to_int(
    httpx_mock: HTTPXMock,
    list_payload: list[dict[str, Any]],
) -> None:
    httpx_mock.add_response(url=_LIST_URL, json=list_payload)
    with LovdataClient() as client:
        archives = client.list_datasets()
    for archive in archives:
        assert isinstance(archive.size_bytes, int)
        assert archive.size_bytes > 0


def test_list_datasets_parses_iso_datetime_with_tz(
    httpx_mock: HTTPXMock,
    list_payload: list[dict[str, Any]],
) -> None:
    httpx_mock.add_response(url=_LIST_URL, json=list_payload)
    with LovdataClient() as client:
        archives = client.list_datasets()
    for archive in archives:
        assert isinstance(archive.last_modified, datetime)
        assert archive.last_modified.tzinfo is not None


def test_list_datasets_retries_on_5xx_then_succeeds(
    httpx_mock: HTTPXMock,
    list_payload: list[dict[str, Any]],
) -> None:
    httpx_mock.add_response(url=_LIST_URL, status_code=503)
    httpx_mock.add_response(url=_LIST_URL, status_code=502)
    httpx_mock.add_response(url=_LIST_URL, json=list_payload)
    with LovdataClient() as client:
        archives = client.list_datasets()
    assert len(archives) == 4


def test_list_datasets_retries_on_connect_error_then_succeeds(
    httpx_mock: HTTPXMock,
    list_payload: list[dict[str, Any]],
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))
    httpx_mock.add_response(url=_LIST_URL, json=list_payload)
    with LovdataClient() as client:
        archives = client.list_datasets()
    assert len(archives) == 4


def test_list_datasets_raises_after_all_request_errors(
    httpx_mock: HTTPXMock,
) -> None:
    for _ in range(3):
        httpx_mock.add_exception(httpx.ReadTimeout("read timed out"))
    with LovdataClient() as client, pytest.raises(NetworkError):
        client.list_datasets()


def test_list_datasets_does_not_retry_on_4xx(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(url=_LIST_URL, status_code=404)
    with LovdataClient() as client, pytest.raises(NetworkError):
        client.list_datasets()


def test_list_datasets_treats_400_as_non_retryable(
    httpx_mock: HTTPXMock,
) -> None:
    """Pin _HTTP_ERROR_THRESHOLD against mutation to 401."""
    httpx_mock.add_response(url=_LIST_URL, status_code=400)
    with LovdataClient() as client, pytest.raises(NetworkError):
        client.list_datasets()


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_list_datasets_retries_on_each_retryable_5xx(
    httpx_mock: HTTPXMock,
    list_payload: list[dict[str, Any]],
    status: int,
) -> None:
    """Pin every element of _RETRYABLE_HTTP_STATUSES individually."""
    httpx_mock.add_response(url=_LIST_URL, status_code=status)
    httpx_mock.add_response(url=_LIST_URL, json=list_payload)
    with LovdataClient() as client:
        archives = client.list_datasets()
    assert len(archives) == 4


def test_list_datasets_calls_official_lovdata_endpoint(
    httpx_mock: HTTPXMock,
    list_payload: list[dict[str, Any]],
) -> None:
    """Literal URL pins DEFAULT_BASE_URL against constant mutation."""
    httpx_mock.add_response(
        url="https://api.lovdata.no/v1/publicData/list",
        json=list_payload,
    )
    with LovdataClient() as client:
        archives = client.list_datasets()
    assert len(archives) == 4


def test_list_datasets_sends_accept_application_json_header(
    httpx_mock: HTTPXMock,
    list_payload: list[dict[str, Any]],
) -> None:
    httpx_mock.add_response(url=_LIST_URL, json=list_payload)
    with LovdataClient() as client:
        client.list_datasets()
    request = httpx_mock.get_requests()[0]
    assert request.headers.get("Accept") == "application/json"


def test_list_datasets_raises_network_error_after_exhausting_retries(
    httpx_mock: HTTPXMock,
) -> None:
    for _ in range(3):
        httpx_mock.add_response(url=_LIST_URL, status_code=503)
    with LovdataClient() as client, pytest.raises(NetworkError):
        client.list_datasets()


def test_list_datasets_raises_parse_error_on_non_json(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(url=_LIST_URL, content=b"<html>nope</html>")
    with LovdataClient() as client, pytest.raises(ParseError):
        client.list_datasets()


def test_list_datasets_raises_parse_error_on_non_array(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(url=_LIST_URL, json={"not": "an array"})
    with LovdataClient() as client, pytest.raises(ParseError):
        client.list_datasets()


def test_list_datasets_raises_parse_error_on_invalid_schema(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url=_LIST_URL,
        json=[{"unknown_field": "value"}],
    )
    with LovdataClient() as client, pytest.raises(ParseError):
        client.list_datasets()


def test_list_datasets_raises_parse_error_on_extra_fields(
    httpx_mock: HTTPXMock,
) -> None:
    payload = [
        {
            "filename": "x.tar.bz2",
            "description": "x",
            "sizeBytes": "1",
            "lastModified": "2026-04-22T01:31:00Z",
            "rogue_field": True,
        },
    ]
    httpx_mock.add_response(url=_LIST_URL, json=payload)
    with LovdataClient() as client, pytest.raises(ParseError):
        client.list_datasets()


def test_lovdata_archive_is_frozen() -> None:
    archive = LovdataArchive.model_validate(
        {
            "filename": "x.tar.bz2",
            "description": "x",
            "sizeBytes": "1",
            "lastModified": "2026-04-22T01:31:00Z",
        },
    )
    with pytest.raises(ValidationError):
        archive.filename = "mutated.tar.bz2"  # type: ignore[misc]


def test_client_strips_trailing_slash_from_base_url(
    httpx_mock: HTTPXMock,
    list_payload: list[dict[str, Any]],
) -> None:
    httpx_mock.add_response(url=_LIST_URL, json=list_payload)
    with LovdataClient(base_url=f"{DEFAULT_BASE_URL}/") as client:
        archives = client.list_datasets()
    assert len(archives) == 4


def test_env_var_overrides_default_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOVSPOR_HTTP_TIMEOUT_SECONDS", "42.0")
    with LovdataClient() as client:
        assert client._client.timeout.connect == 42.0


def test_constructor_arg_takes_priority_over_env_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOVSPOR_HTTP_TIMEOUT_SECONDS", "42.0")
    with LovdataClient(timeout_seconds=99.0) as client:
        assert client._client.timeout.connect == 99.0


def test_env_var_overrides_default_user_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom = "lovspor-test/1.0 (+example)"
    monkeypatch.setenv("LOVSPOR_HTTP_USER_AGENT", custom)
    with LovdataClient() as client:
        assert client._client.headers["User-Agent"] == custom


def test_constructor_arg_takes_priority_over_env_user_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOVSPOR_HTTP_USER_AGENT", "from-env")
    with LovdataClient(user_agent="from-arg") as client:
        assert client._client.headers["User-Agent"] == "from-arg"


def test_explicit_close_releases_underlying_client() -> None:
    client = LovdataClient()
    underlying = client._client
    client.close()
    assert underlying.is_closed


def test_context_manager_closes_on_exit() -> None:
    with LovdataClient() as client:
        underlying = client._client
    assert underlying.is_closed


def test_malformed_timeout_env_var_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOVSPOR_HTTP_TIMEOUT_SECONDS", "not-a-number")
    with pytest.raises(ConfigError, match="must be a float"):
        LovdataClient()


def test_explicit_zero_timeout_does_not_fall_through_to_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: timeout_seconds=0.0 must not be treated as falsy.

    Old implementation used ``timeout_seconds or float(env)`` which
    silently replaced 0.0 with the env value. Current implementation
    uses ``is not None`` in _resolve_timeout and returns 0.0 explicitly.
    """
    monkeypatch.setenv("LOVSPOR_HTTP_TIMEOUT_SECONDS", "42.0")
    with LovdataClient(timeout_seconds=0.0) as client:
        assert client._client.timeout.connect == 0.0
