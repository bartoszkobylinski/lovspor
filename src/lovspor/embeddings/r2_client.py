"""One HTTP request at a time against a Cloudflare R2 bucket (#802, design §8.1).

This module speaks the S3 API that R2 implements and nothing more: a
conditional ``PUT`` (``If-None-Match: *``) and a ``GET``, each signed with AWS
Signature Version 4 under region ``auto``. Each call returns the status code
and body of a single attempt. Retries, the meaning of ``412`` and every
decision about the cache's value live in
:mod:`lovspor.embeddings.cache_remote`.

Credentials never leave this module in readable form:

* :class:`R2Credentials` redacts itself in ``repr``;
* errors name the operation and status only, never the endpoint, the key id or
  a signature;
* the ``httpx`` request log line, which carries the full URL, is dropped for
  the bucket's host.

The signature travels in the ``Authorization`` header, never in the URL.
"""

import base64
import hashlib
import hmac
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from lovspor.errors import EmbeddingCacheError

_REGION = "auto"
_SERVICE = "s3"
_ALGORITHM = "AWS4-HMAC-SHA256"
_ACCOUNT_PATTERN = re.compile(r"^[0-9a-f]{32}$")
# R2 bucket names: lowercase letters, digits and hyphens, 3-63 characters.
_BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9./-]{0,1023}$")
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


@dataclass(frozen=True)
class R2Credentials:
    """The bucket and the one bucket-scoped token a sync may use."""

    account_id: str
    bucket: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if not _ACCOUNT_PATTERN.fullmatch(self.account_id):
            raise EmbeddingCacheError("R2 account id must be 32 lowercase hex characters")
        if not _BUCKET_PATTERN.fullmatch(self.bucket):
            raise EmbeddingCacheError("R2 bucket name is not a valid bucket name")
        if not self.access_key_id or not self.secret_access_key:
            raise EmbeddingCacheError("R2 access key id and secret must both be set")

    def __repr__(self) -> str:
        return "R2Credentials(<redacted>)"

    @property
    def endpoint(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


@dataclass(frozen=True)
class Reply:
    """Status and body of one attempt."""

    status: int
    body: bytes


class R2Client:
    """Signed single requests to one bucket. Transport errors propagate as ``httpx`` ones."""

    def __init__(
        self,
        credentials: R2Credentials,
        http: httpx.Client | None = None,
        endpoint: str | None = None,
    ) -> None:
        self._credentials = credentials
        self._base = httpx.URL(endpoint or credentials.endpoint)
        self._http = http or httpx.Client(timeout=_TIMEOUT)
        self.clock: Callable[[], datetime] = lambda: datetime.now(UTC)
        _quiet_request_log(self._base.host)

    @property
    def http(self) -> httpx.Client:
        return self._http

    def put_if_absent(self, key: str, data: bytes) -> Reply:
        """One ``PUT`` that creates ``key`` only if no object holds it yet."""
        digest = base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()
        headers = {"if-none-match": "*", "content-md5": digest}
        return self._send("PUT", key, headers, data)

    def get(self, key: str) -> Reply:
        """One ``GET`` of ``key``."""
        return self._send("GET", key, {}, b"")

    def _send(self, method: str, key: str, headers: dict[str, str], data: bytes) -> Reply:
        request = self._http.build_request(method, self._url(key), headers=headers, content=data)
        sign_request(request, self._credentials, self.clock())
        response = self._http.send(request)
        return Reply(status=response.status_code, body=response.content)

    def _url(self, key: str) -> httpx.URL:
        if not _KEY_PATTERN.fullmatch(key) or ".." in key:
            raise EmbeddingCacheError("refusing an R2 object key that is not a cache key")
        return self._base.copy_with(path=f"/{self._credentials.bucket}/{key}")


def sign_request(request: httpx.Request, credentials: R2Credentials, now: datetime) -> None:
    """Add AWS Signature Version 4 headers to ``request``, signed for region ``auto``."""
    sign_for_region(request, credentials, now, _REGION)


def sign_for_region(
    request: httpx.Request, credentials: R2Credentials, now: datetime, region: str
) -> None:
    """Signature Version 4 for ``region``: R2 uses ``auto``; tests use a published vector."""
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    request.headers["x-amz-date"] = stamp
    request.headers["x-amz-content-sha256"] = hashlib.sha256(request.content).hexdigest()
    names = sorted({"host", *(n.lower() for n in request.headers if _signed(n))})
    scope = f"{stamp[:8]}/{region}/{_SERVICE}/aws4_request"
    canonical = _canonical_request(request, names)
    to_sign = "\n".join([_ALGORITHM, stamp, scope, _sha256_hex(canonical)])
    signature = hmac.new(
        _signing_key(credentials.secret_access_key, scope), to_sign.encode(), hashlib.sha256
    ).hexdigest()
    request.headers["authorization"] = (
        f"{_ALGORITHM} Credential={credentials.access_key_id}/{scope}, "
        f"SignedHeaders={';'.join(names)}, Signature={signature}"
    )


def _signed(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith("x-amz-") or lowered in {"content-md5", "if-none-match", "range"}


def _canonical_request(request: httpx.Request, names: list[str]) -> str:
    values = {"host": request.url.netloc.decode()}
    values.update({n: request.headers[n].strip() for n in names if n != "host"})
    return "\n".join(
        [
            request.method,
            quote(request.url.path, safe="/-_.~"),
            _canonical_query(request.url),
            *(f"{name}:{values[name]}" for name in names),
            "",
            ";".join(names),
            request.headers["x-amz-content-sha256"],
        ]
    )


def _canonical_query(url: httpx.URL) -> str:
    pairs = sorted(url.params.multi_items())
    return "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in pairs)


def _signing_key(secret: str, scope: str) -> bytes:
    key = f"AWS4{secret}".encode()
    for part in scope.split("/"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return key


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class _DropHost(logging.Filter):
    """Drops log records that mention the bucket's host (``httpx`` logs every URL)."""

    def __init__(self, host: str) -> None:
        super().__init__()
        self.host = host

    def filter(self, record: logging.LogRecord) -> bool:
        return self.host not in record.getMessage()


def _quiet_request_log(host: str) -> None:
    logger = logging.getLogger("httpx")
    if not any(isinstance(f, _DropHost) and f.host == host for f in logger.filters):
        logger.addFilter(_DropHost(host))
