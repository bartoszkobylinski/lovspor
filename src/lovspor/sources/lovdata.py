"""HTTP client for the Lovdata public-data API.

Endpoints used:
    GET /v1/publicData/list           — catalogue of available archives
    GET /v1/publicData/get/{filename} — download an archive (added later)

Data is licensed under NLOD 2.0, attributed to Lovdata. See
docs/legal-and-sources.md.
"""

import hashlib
import logging
import os
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from lovspor.errors import (
    ConfigError,
    ExtractionError,
    LovsporError,
    NetworkError,
    ParseError,
)
from lovspor.retry import retry_with_backoff

_LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.lovdata.no/v1/publicData"
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_USER_AGENT = "lovspor/0.1 (+https://github.com/bartoszkobylinski/lovspor)"

# 429 (Too Many Requests) is transient rate-limiting, not a client error to
# give up on — a scheduled sync that hits Lovdata's limit should back off and
# retry rather than fail the whole run. (Retry-After is not yet honored; the
# fixed exponential backoff applies. See docs/decisions.md if that changes.)
_RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
_HTTP_ERROR_THRESHOLD = 400
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 1.0
_DOWNLOAD_CHUNK_BYTES = 64 * 1024


class LovdataArchive(BaseModel):
    """An archive entry from the Lovdata public-data /list endpoint."""

    # extra="allow" (not "forbid"): an additive field Lovdata adds to /list must
    # not take the nightly sync offline until a code deploy. Unknown fields are
    # retained in model_extra so the engine can surface them as schema drift
    # (unknown_archive_fields) and notify rather than fail loudly.
    model_config = ConfigDict(
        frozen=True,
        extra="allow",
        populate_by_name=True,
    )

    filename: str
    description: str
    size_bytes: int = Field(alias="sizeBytes")
    last_modified: datetime = Field(alias="lastModified")

    @field_validator("filename")
    @classmethod
    def _reject_path_traversal(cls, v: str) -> str:
        """Reject filenames that could escape a download directory.

        Upstream metadata is not trusted for filesystem operations. A
        filename like ``"../escape.tar.bz2"`` or ``"/etc/passwd"`` must
        not slip into ``dest_dir / archive.filename``.
        """
        if not v:
            raise ValueError("filename must not be empty")
        if "\x00" in v:
            raise ValueError("filename must not contain null bytes")
        if "/" in v or "\\" in v:
            raise ValueError(
                f"filename must not contain path separators: {v!r}",
            )
        if v in {".", ".."}:
            raise ValueError(
                f"filename must not be a directory reference: {v!r}",
            )
        return v


class DownloadResult(BaseModel):
    """Outcome of a successful archive download.

    ``sha256`` is the raw hex digest (64 chars, no ``sha256:`` prefix) of the
    bytes written to disk. ``size_bytes`` is the actual on-disk size.
    """

    model_config = ConfigDict(frozen=True)

    filename: str
    path: Path
    size_bytes: int
    sha256: str


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


def unknown_archive_fields(archives: list[LovdataArchive]) -> list[str]:
    """Sorted, de-duplicated field names Lovdata returned that the schema does
    not model — i.e. additive upstream drift retained via ``extra="allow"``.

    Empty when every archive matches the known schema. The caller surfaces a
    non-empty result as a notification so the model can be updated deliberately.
    """
    fields: set[str] = set()
    for archive in archives:
        fields.update(archive.model_extra or {})
    return sorted(fields)


def _parse_archives(
    payload: list[object],
    url: str,
) -> list[LovdataArchive]:
    try:
        archives = [LovdataArchive.model_validate(item) for item in payload]
    except ValidationError as exc:
        raise ParseError(
            f"GET {url} schema validation failed: {exc}",
        ) from exc
    drift = unknown_archive_fields(archives)
    if drift:
        _LOGGER.warning(
            "Lovdata /list returned unknown field(s) %s; tolerated but "
            "LovdataArchive should be updated to model them",
            drift,
        )
    return archives


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

        Retries on transient HTTP 429 / 5xx and network errors.
        Raises:
            NetworkError: non-retryable HTTP status (4xx other than 429)
                or final attempt fails after retries.
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

    def download(
        self,
        archive: LovdataArchive,
        dest_dir: Path,
    ) -> DownloadResult:
        """Stream /get/{filename} to dest_dir/{filename}.

        Atomic: streams to ``{filename}.part``, renames on successful
        verify. Computes sha256 during streaming. Verifies actual
        byte count matches ``archive.size_bytes`` (a mismatch is
        treated as transient transport truncation and retried).

        Retries on transient 429 / 5xx, network errors, and size mismatch.

        Raises:
            NetworkError: non-retryable HTTP status or final retry
                failure.
        """
        return retry_with_backoff(
            lambda: self._download_once(archive, dest_dir),
            attempts=_RETRY_ATTEMPTS,
            base_delay_seconds=_RETRY_BASE_DELAY_SECONDS,
            retryable=(_RetryableNetworkError,),
        )

    def _download_once(
        self,
        archive: LovdataArchive,
        dest_dir: Path,
    ) -> DownloadResult:
        dest = dest_dir / archive.filename
        if dest.parent != dest_dir:
            raise ExtractionError(
                f"archive filename {archive.filename!r} resolves outside dest_dir {dest_dir}",
            )
        part = dest.with_suffix(dest.suffix + ".part")
        try:
            size, sha256 = self._stream_to_part(archive, part)
        except (LovsporError, OSError):
            part.unlink(missing_ok=True)
            raise
        if size != archive.size_bytes:
            part.unlink(missing_ok=True)
            url = f"{self._base_url}/get/{archive.filename}"
            raise _RetryableNetworkError(
                f"GET {url}: size mismatch; expected {archive.size_bytes}, got {size}",
            )
        part.replace(dest)
        return DownloadResult(
            filename=archive.filename,
            path=dest,
            size_bytes=size,
            sha256=sha256,
        )

    def _stream_to_part(
        self,
        archive: LovdataArchive,
        part: Path,
    ) -> tuple[int, str]:
        url = f"{self._base_url}/get/{archive.filename}"
        part.parent.mkdir(parents=True, exist_ok=True)
        hasher = hashlib.sha256()
        size = 0
        try:
            with self._client.stream("GET", url) as response:
                _raise_for_status(response, url)
                with part.open("wb") as fh:
                    for chunk in response.iter_bytes(_DOWNLOAD_CHUNK_BYTES):
                        fh.write(chunk)
                        hasher.update(chunk)
                        size += len(chunk)
        except httpx.RequestError as exc:
            raise _RetryableNetworkError(
                f"GET {url}: {exc.__class__.__name__}: {exc}",
            ) from exc
        return size, hasher.hexdigest()
