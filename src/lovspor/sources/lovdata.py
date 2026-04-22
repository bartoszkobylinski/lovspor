"""HTTP client for the Lovdata public-data API.

Endpoints used:
    GET /v1/publicData/list           — catalogue of available archives
    GET /v1/publicData/get/{filename} — download an archive (added later)

Data is licensed under NLOD 2.0, attributed to Lovdata. See
docs/legal-and-sources.md.
"""

import os
from datetime import datetime
from types import TracebackType
from typing import Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lovspor.errors import ConfigError, NetworkError, ParseError
from lovspor.retry import retry_with_backoff

DEFAULT_BASE_URL = "https://api.lovdata.no/v1/publicData"
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_USER_AGENT = "lovspor/0.1 (+https://github.com/bartoszkobylinski/lovspor)"

_RETRYABLE_HTTP_STATUSES = frozenset({500, 502, 503, 504})
_HTTP_ERROR_THRESHOLD = 400
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 1.0


class LovdataArchive(BaseModel):
    """An archive entry from the Lovdata public-data /list endpoint."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
    )

    filename: str
    description: str
    size_bytes: int = Field(alias="sizeBytes")
    last_modified: datetime = Field(alias="lastModified")


class _RetryableNetworkError(NetworkError):
    """Internal: signals the retry helper that this failure is transient."""


def _raise_for_status(response: httpx.Response, url: str) -> None:
    if response.status_code in _RETRYABLE_HTTP_STATUSES:
        raise _RetryableNetworkError(
            f"GET {url} returned {response.status_code} (retryable)",
        )
    if response.status_code >= _HTTP_ERROR_THRESHOLD:
        raise NetworkError(
            f"GET {url} returned {response.status_code}",
        )


def _parse_json_array(response: httpx.Response, url: str) -> list[object]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ParseError(f"GET {url} returned non-JSON body") from exc
    if not isinstance(payload, list):
        raise ParseError(
            f"GET {url} expected JSON array, got {type(payload).__name__}",
        )
    return payload


def _resolve_timeout(explicit: float | None) -> float:
    if explicit is not None:
        return explicit
    raw = os.environ.get("LOVSPOR_HTTP_TIMEOUT_SECONDS")
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(
            f"LOVSPOR_HTTP_TIMEOUT_SECONDS must be a float, got: {raw!r}",
        ) from exc


def _parse_archives(
    payload: list[object],
    url: str,
) -> list[LovdataArchive]:
    try:
        return [LovdataArchive.model_validate(item) for item in payload]
    except ValidationError as exc:
        raise ParseError(
            f"GET {url} schema validation failed: {exc}",
        ) from exc


class LovdataClient:
    """Client for `https://api.lovdata.no/v1/publicData/`."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        user_agent: str | None = None,
    ) -> None:
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        timeout = _resolve_timeout(timeout_seconds)
        ua = user_agent or os.environ.get(
            "LOVSPOR_HTTP_USER_AGENT",
            DEFAULT_USER_AGENT,
        )
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": ua, "Accept": "application/json"},
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def list_datasets(self) -> list[LovdataArchive]:
        """Fetch the catalogue of available archives.

        Retries on transient HTTP 5xx and network errors.
        Raises:
            NetworkError: non-retryable HTTP status (4xx) or final
                attempt fails after retries.
            ParseError: response is not valid JSON, not an array, or
                does not match the LovdataArchive schema.
        """
        return retry_with_backoff(
            self._fetch_list,
            attempts=_RETRY_ATTEMPTS,
            base_delay_seconds=_RETRY_BASE_DELAY_SECONDS,
            retryable=(_RetryableNetworkError,),
        )

    def _fetch_list(self) -> list[LovdataArchive]:
        url = f"{self._base_url}/list"
        try:
            response = self._client.get(url)
        except httpx.RequestError as exc:
            raise _RetryableNetworkError(
                f"GET {url}: {exc.__class__.__name__}: {exc}",
            ) from exc
        _raise_for_status(response, url)
        payload = _parse_json_array(response, url)
        return _parse_archives(payload, url)
