"""Tests for lovspor.sources.lovdata.

The list_response fixture is a real response captured from
https://api.lovdata.no/v1/publicData/list on 2026-04-22, licensed under
NLOD 2.0, attributed to Lovdata.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from pytest_httpx import HTTPXMock

from lovspor.errors import ConfigError, ExtractionError, NetworkError, ParseError
from lovspor.sources.lovdata import (
    _DOWNLOAD_CHUNK_BYTES,
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
    DownloadResult,
    LovdataArchive,
    LovdataClient,
    _parse_archives,
    _parse_json_array,
    _raise_for_status,
    _RetryableNetworkError,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_LIST_URL = f"{DEFAULT_BASE_URL}/list"
_TEST_FILENAME = "gjeldende-lover.tar.bz2"
_GET_URL = f"{DEFAULT_BASE_URL}/get/{_TEST_FILENAME}"


def _make_archive(size_bytes: int, filename: str = _TEST_FILENAME) -> LovdataArchive:
    return LovdataArchive.model_validate(
        {
            "filename": filename,
            "description": "test",
            "sizeBytes": str(size_bytes),
            "lastModified": "2026-04-22T01:31:00Z",
        },
    )


class _FakeStreamResponse:
    status_code = 200

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.requested_chunk_size: int | None = None

    def __enter__(self) -> "_FakeStreamResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def iter_bytes(self, chunk_size: int) -> list[bytes]:
        self.requested_chunk_size = chunk_size
        return self.chunks


class _FakeHttpClient:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def stream(self, method: str, url: str) -> _FakeStreamResponse:
        self.calls.append((method, url))
        return self.response

    def close(self) -> None:
        return None


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


def test_list_datasets_retries_on_429_then_succeeds(
    httpx_mock: HTTPXMock,
    list_payload: list[dict[str, Any]],
) -> None:
    """429 (rate limit) is transient: back off and retry, not fail the run."""
    httpx_mock.add_response(url=_LIST_URL, status_code=429)
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


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_list_datasets_retries_on_each_retryable_status(
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


def test_client_uses_default_timeout_and_user_agent() -> None:
    with LovdataClient() as client:
        assert client._client.timeout.connect == DEFAULT_TIMEOUT_SECONDS == 120.0
        assert (
            client._client.headers["User-Agent"]
            == DEFAULT_USER_AGENT
            == "lovspor/0.1 (+https://github.com/bartoszkobylinski/lovspor)"
        )


def test_list_datasets_sends_accept_application_json_header(
    httpx_mock: HTTPXMock,
    list_payload: list[dict[str, Any]],
) -> None:
    httpx_mock.add_response(url=_LIST_URL, json=list_payload)
    with LovdataClient() as client:
        client.list_datasets()
    request = httpx_mock.get_requests()[0]
    assert request.headers.get("Accept") == "application/json"


def test_list_datasets_uses_retry_policy_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_retry_with_backoff(
        fn: object,
        *,
        attempts: int,
        base_delay_seconds: float,
        retryable: tuple[type[BaseException], ...],
    ) -> list[LovdataArchive]:
        captured.update(
            {
                "fn": fn,
                "attempts": attempts,
                "base_delay_seconds": base_delay_seconds,
                "retryable": retryable,
            },
        )
        return []

    monkeypatch.setattr("lovspor.sources.lovdata.retry_with_backoff", fake_retry_with_backoff)

    with LovdataClient() as client:
        assert client.list_datasets() == []

    assert captured["attempts"] == 3
    assert captured["base_delay_seconds"] == 1.0
    assert captured["retryable"] == (_RetryableNetworkError,)


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
    with LovdataClient() as client, pytest.raises(ParseError, match="returned non-JSON body"):
        client.list_datasets()


def test_list_datasets_raises_parse_error_on_non_array(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(url=_LIST_URL, json={"not": "an array"})
    with (
        LovdataClient() as client,
        pytest.raises(
            ParseError,
            match="expected JSON array, got dict",
        ),
    ):
        client.list_datasets()


def test_parse_json_array_error_messages_are_specific() -> None:
    url = "https://example.test/list"
    with pytest.raises(ParseError) as exc_info:
        _parse_json_array(httpx.Response(200, content=b"not-json"), url)
    assert str(exc_info.value) == "GET https://example.test/list returned non-JSON body"

    with pytest.raises(ParseError) as exc_info:
        _parse_json_array(httpx.Response(200, json={"not": "array"}), url)
    assert str(exc_info.value) == ("GET https://example.test/list expected JSON array, got dict")


def test_list_datasets_raises_parse_error_on_invalid_schema(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url=_LIST_URL,
        json=[{"unknown_field": "value"}],
    )
    with LovdataClient() as client, pytest.raises(ParseError, match="schema validation failed"):
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


def test_lovdata_archive_accepts_field_names_when_constructing_in_python() -> None:
    archive = LovdataArchive(
        filename="x.tar.bz2",
        description="x",
        size_bytes=1,
        last_modified=datetime.fromisoformat("2026-04-22T01:31:00+00:00"),
    )

    assert archive.size_bytes == 1
    assert archive.last_modified.isoformat() == "2026-04-22T01:31:00+00:00"


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
    with pytest.raises(ConfigError) as exc_info:
        LovdataClient()
    assert str(exc_info.value) == (
        "LOVSPOR_HTTP_TIMEOUT_SECONDS must be a float, got: 'not-a-number'"
    )


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


def test_download_result_is_frozen() -> None:
    result = DownloadResult(
        filename="x.tar.bz2",
        path=Path("/tmp/x.tar.bz2"),  # noqa: S108
        size_bytes=100,
        sha256="a" * 64,
    )
    with pytest.raises(ValidationError):
        result.filename = "mutated"  # type: ignore[misc]


def test_download_result_carries_all_fields() -> None:
    result = DownloadResult(
        filename="gjeldende-lover.tar.bz2",
        path=Path("/tmp/gjeldende-lover.tar.bz2"),  # noqa: S108
        size_bytes=5844867,
        sha256="abcdef0123456789" * 4,
    )
    assert result.filename == "gjeldende-lover.tar.bz2"
    assert result.path == Path("/tmp/gjeldende-lover.tar.bz2")  # noqa: S108
    assert result.size_bytes == 5844867
    assert result.sha256 == "abcdef0123456789" * 4


def test_download_writes_file_and_returns_integrity_data(
    httpx_mock: HTTPXMock,
    tmp_path: Path,
) -> None:
    payload = b"hello lovdata" * 100
    httpx_mock.add_response(url=_GET_URL, content=payload)
    archive = _make_archive(size_bytes=len(payload))
    with LovdataClient() as client:
        result = client.download(archive, tmp_path)
    assert result.filename == _TEST_FILENAME
    assert result.path == tmp_path / _TEST_FILENAME
    assert result.size_bytes == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert result.path.read_bytes() == payload


def test_download_uses_retry_policy_parameters(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    archive = _make_archive(size_bytes=1)

    def fake_retry_with_backoff(
        fn: object,
        *,
        attempts: int,
        base_delay_seconds: float,
        retryable: tuple[type[BaseException], ...],
    ) -> DownloadResult:
        captured.update(
            {
                "fn": fn,
                "attempts": attempts,
                "base_delay_seconds": base_delay_seconds,
                "retryable": retryable,
            },
        )
        return DownloadResult(
            filename=archive.filename,
            path=tmp_path / archive.filename,
            size_bytes=1,
            sha256="a" * 64,
        )

    monkeypatch.setattr("lovspor.sources.lovdata.retry_with_backoff", fake_retry_with_backoff)

    with LovdataClient() as client:
        result = client.download(archive, tmp_path)

    assert result.filename == archive.filename
    assert captured["attempts"] == 3
    assert captured["base_delay_seconds"] == 1.0
    assert captured["retryable"] == (_RetryableNetworkError,)


def test_download_once_passes_exact_part_path_to_stream(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive = _make_archive(size_bytes=3)
    seen_parts: list[Path] = []

    def fake_stream_to_part(_archive: LovdataArchive, part: Path) -> tuple[int, str]:
        seen_parts.append(part)
        part.write_bytes(b"abc")
        return 3, hashlib.sha256(b"abc").hexdigest()

    with LovdataClient() as client:
        monkeypatch.setattr(client, "_stream_to_part", fake_stream_to_part)
        result = client._download_once(archive, tmp_path)

    assert seen_parts == [tmp_path / f"{_TEST_FILENAME}.part"]
    assert result.path == tmp_path / _TEST_FILENAME


def test_download_leaves_no_part_file_on_success(
    httpx_mock: HTTPXMock,
    tmp_path: Path,
) -> None:
    payload = b"content"
    httpx_mock.add_response(url=_GET_URL, content=payload)
    archive = _make_archive(size_bytes=len(payload))
    with LovdataClient() as client:
        client.download(archive, tmp_path)
    assert not (tmp_path / f"{_TEST_FILENAME}.part").exists()


def test_download_does_not_retry_on_4xx(
    httpx_mock: HTTPXMock,
    tmp_path: Path,
) -> None:
    httpx_mock.add_response(url=_GET_URL, status_code=404)
    archive = _make_archive(size_bytes=100)
    with LovdataClient() as client, pytest.raises(NetworkError):
        client.download(archive, tmp_path)
    assert not (tmp_path / _TEST_FILENAME).exists()
    assert not (tmp_path / f"{_TEST_FILENAME}.part").exists()


def test_download_retries_on_5xx_then_succeeds(
    httpx_mock: HTTPXMock,
    tmp_path: Path,
) -> None:
    payload = b"after retry"
    httpx_mock.add_response(url=_GET_URL, status_code=503)
    httpx_mock.add_response(url=_GET_URL, content=payload)
    archive = _make_archive(size_bytes=len(payload))
    with LovdataClient() as client:
        result = client.download(archive, tmp_path)
    assert result.path.read_bytes() == payload


def test_download_retries_on_429_then_succeeds(
    httpx_mock: HTTPXMock,
    tmp_path: Path,
) -> None:
    """The download call site retries a 429 rate limit, same as list."""
    payload = b"after backoff"
    httpx_mock.add_response(url=_GET_URL, status_code=429)
    httpx_mock.add_response(url=_GET_URL, content=payload)
    archive = _make_archive(size_bytes=len(payload))
    with LovdataClient() as client:
        result = client.download(archive, tmp_path)
    assert result.path.read_bytes() == payload


def test_download_retries_on_connect_error_then_succeeds(
    httpx_mock: HTTPXMock,
    tmp_path: Path,
) -> None:
    payload = b"recovered"
    httpx_mock.add_exception(httpx.ConnectError("reset"))
    httpx_mock.add_response(url=_GET_URL, content=payload)
    archive = _make_archive(size_bytes=len(payload))
    with LovdataClient() as client:
        result = client.download(archive, tmp_path)
    assert result.path.read_bytes() == payload


def test_download_rejects_size_mismatch_and_cleans_up(
    httpx_mock: HTTPXMock,
    tmp_path: Path,
) -> None:
    short_payload = b"too short"
    for _ in range(3):
        httpx_mock.add_response(url=_GET_URL, content=short_payload)
    archive = _make_archive(size_bytes=1000)
    with LovdataClient() as client, pytest.raises(NetworkError, match="size mismatch"):
        client.download(archive, tmp_path)
    assert not (tmp_path / _TEST_FILENAME).exists()
    assert not (tmp_path / f"{_TEST_FILENAME}.part").exists()


def test_download_size_mismatch_cleans_up_even_when_part_was_not_created(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive = _make_archive(size_bytes=1000)

    def fake_stream_to_part(_archive: LovdataArchive, _part: Path) -> tuple[int, str]:
        return 3, "a" * 64

    with LovdataClient() as client:
        monkeypatch.setattr(client, "_stream_to_part", fake_stream_to_part)
        with pytest.raises(_RetryableNetworkError) as exc_info:
            client._download_once(archive, tmp_path)
    assert str(exc_info.value) == (
        f"GET {DEFAULT_BASE_URL}/get/{_TEST_FILENAME}: size mismatch; expected 1000, got 3"
    )


def test_download_creates_missing_parent_directory(
    httpx_mock: HTTPXMock,
    tmp_path: Path,
) -> None:
    payload = b"x"
    httpx_mock.add_response(url=_GET_URL, content=payload)
    archive = _make_archive(size_bytes=1)
    nested = tmp_path / "cache" / "nested"
    with LovdataClient() as client:
        result = client.download(archive, nested)
    assert result.path == nested / _TEST_FILENAME
    assert result.path.exists()


def test_stream_to_part_uses_get_chunk_size_and_accumulates_size(tmp_path: Path) -> None:
    response = _FakeStreamResponse([b"abc", b"defgh"])
    fake_client = _FakeHttpClient(response)
    archive = _make_archive(size_bytes=8)

    with LovdataClient() as client:
        client._client = fake_client  # type: ignore[assignment]
        size, sha256 = client._stream_to_part(archive, tmp_path / "archive.part")

    assert fake_client.calls == [("GET", _GET_URL)]
    assert response.requested_chunk_size == _DOWNLOAD_CHUNK_BYTES == 64 * 1024
    assert size == 8
    assert sha256 == hashlib.sha256(b"abcdefgh").hexdigest()
    assert (tmp_path / "archive.part").read_bytes() == b"abcdefgh"


@pytest.mark.parametrize(
    "hostile",
    [
        "../escape.tar.bz2",
        "/etc/passwd",
        "sub/file.tar.bz2",
        "foo\\bar.tar.bz2",
        "..",
        ".",
        "",
        "with\x00null.tar.bz2",
        "..\\..\\Windows\\System32\\evil",
    ],
)
def test_lovdata_archive_rejects_path_traversal_filenames(hostile: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        LovdataArchive.model_validate(
            {
                "filename": hostile,
                "description": "attack",
                "sizeBytes": "1",
                "lastModified": "2026-04-22T01:31:00Z",
            },
        )
    message = str(exc_info.value)
    if hostile == "":
        assert "filename must not be empty" in message
    elif "\x00" in hostile:
        assert "filename must not contain null bytes" in message
    elif "/" in hostile or "\\" in hostile:
        assert "filename must not contain path separators" in message
    elif hostile in {".", ".."}:
        assert "filename must not be a directory reference" in message


def test_lovdata_archive_validation_messages_are_exact() -> None:
    cases = {
        "": "filename must not be empty",
        "with\x00null.tar.bz2": "filename must not contain null bytes",
        "sub/file.tar.bz2": "filename must not contain path separators: 'sub/file.tar.bz2'",
        ".": "filename must not be a directory reference: '.'",
    }
    for filename, expected in cases.items():
        with pytest.raises(ValidationError) as exc_info:
            LovdataArchive.model_validate(
                {
                    "filename": filename,
                    "description": "attack",
                    "sizeBytes": "1",
                    "lastModified": "2026-04-22T01:31:00Z",
                },
            )
        assert expected in str(exc_info.value)


def test_lovdata_archive_accepts_real_filenames() -> None:
    """Regression guard: the four real Lovdata filenames all pass validation."""
    real_filenames = [
        "gjeldende-lover.tar.bz2",
        "gjeldende-sentrale-forskrifter.tar.bz2",
        "lovtidend-avd1-2001-2025.tar.bz2",
        "lovtidend-avd1-2026.tar.bz2",
    ]
    for name in real_filenames:
        archive = LovdataArchive.model_validate(
            {
                "filename": name,
                "description": "ok",
                "sizeBytes": "1",
                "lastModified": "2026-04-22T01:31:00Z",
            },
        )
        assert archive.filename == name


def test_download_defense_in_depth_rejects_traversal_via_model_construct(
    tmp_path: Path,
) -> None:
    """Defense in depth: if schema validation is bypassed (e.g. via
    model_construct), the download() method itself must still refuse to
    write outside dest_dir."""
    archive = LovdataArchive.model_construct(
        filename="../escape.tar.bz2",
        description="bypassed",
        size_bytes=1,
        last_modified=datetime.fromisoformat("2026-04-22T01:31:00+00:00"),
    )
    with LovdataClient() as client, pytest.raises(ExtractionError) as exc_info:
        client.download(archive, tmp_path)
    assert str(exc_info.value) == (
        f"archive filename '../escape.tar.bz2' resolves outside dest_dir {tmp_path}"
    )
    assert not (tmp_path.parent / "escape.tar.bz2").exists()


def test_raise_for_status_messages_are_specific() -> None:
    retryable = httpx.Response(503)
    with pytest.raises(_RetryableNetworkError) as exc_info:
        _raise_for_status(retryable, "https://example.test")
    assert str(exc_info.value) == "GET https://example.test returned 503 (retryable)"

    non_retryable = httpx.Response(404)
    with pytest.raises(NetworkError) as exc_info:
        _raise_for_status(non_retryable, "https://example.test")
    assert str(exc_info.value) == "GET https://example.test returned 404"


def test_parse_archives_schema_error_message_is_specific() -> None:
    with pytest.raises(ParseError) as exc_info:
        _parse_archives([{"unknown": "field"}], "https://example.test/list")

    assert str(exc_info.value).startswith(
        "GET https://example.test/list schema validation failed: ",
    )


def test_fetch_list_request_error_message_is_specific(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def get(self, url: str) -> httpx.Response:
            raise httpx.ConnectError("boom")

        def close(self) -> None:
            return None

    with LovdataClient(base_url="https://example.test") as client:
        client._client = FakeClient()  # type: ignore[assignment]
        with pytest.raises(_RetryableNetworkError) as exc_info:
            client._fetch_list()

    assert str(exc_info.value) == "GET https://example.test/list: ConnectError: boom"


def test_stream_to_part_request_error_message_is_specific(tmp_path: Path) -> None:
    class FakeClient:
        def stream(self, method: str, url: str) -> object:
            assert method == "GET"
            raise httpx.ReadTimeout("too slow")

        def close(self) -> None:
            return None

    with LovdataClient(base_url="https://example.test") as client:
        client._client = FakeClient()  # type: ignore[assignment]
        with pytest.raises(_RetryableNetworkError) as exc_info:
            client._stream_to_part(_make_archive(size_bytes=1), tmp_path / "x.part")

    assert str(exc_info.value) == (
        f"GET https://example.test/get/{_TEST_FILENAME}: ReadTimeout: too slow"
    )
