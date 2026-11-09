"""A mocked R2 bucket for offline tests of the remote embedding cache (#802, M4).

``FakeBucket`` holds objects in memory and answers the two calls the adapter
makes the way R2 documents them:

* ``PUT`` with ``If-None-Match: *`` creates an absent key (``200``) or
  answers ``412``;
* ``GET`` answers ``200`` with the bytes, or ``404``.

Every request must be signed (an ``AWS4-HMAC-SHA256`` ``Authorization`` header
for the test key id, and an ``x-amz-content-sha256`` that matches the body).
A test scripts faults per method with :meth:`FakeBucket.fail`.

The same bucket serves ``pytest-httpx`` callbacks in process and, through
:func:`serve`, a loopback HTTP server for tests whose writers are separate
processes. Nothing here reaches Cloudflare.
"""

import hashlib
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from lovspor.embeddings.r2_client import R2Credentials

ACCOUNT_ID = "0123456789abcdef0123456789abcdef"
BUCKET = "lovspor-cache-test"
KEY_ID = "test-key-id-AKIA0000000000000000"
SECRET = "test-secret-0000000000000000000000000000000000000000"
ENDPOINT = f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com"

# A fault is a status code to answer, or one of these actions.
TIMEOUT = "timeout"  # the request never answers, nothing is stored
COMMIT_THEN_TIMEOUT = "commit-then-timeout"  # stored, but the answer is lost
DROP = "connection-drop"


def credentials() -> R2Credentials:
    return R2Credentials(
        account_id=ACCOUNT_ID, bucket=BUCKET, access_key_id=KEY_ID, secret_access_key=SECRET
    )


@dataclass
class FakeBucket:
    objects: dict[str, bytes] = field(default_factory=dict)
    faults: dict[str, list[int | str]] = field(default_factory=lambda: {"PUT": [], "GET": []})
    calls: list[tuple[str, str]] = field(default_factory=list)
    before_put: Callable[[str], None] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def fail(self, method: str, *faults: int | str) -> None:
        """Answer the next ``len(faults)`` ``method`` requests with these faults, in order."""
        self.faults[method].extend(faults)

    def puts(self) -> int:
        return sum(1 for method, _key in self.calls if method == "PUT")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        status, body = self.answer(
            request.method, request.url.path, request.headers, request.read()
        )
        return httpx.Response(status, content=body)

    def answer(self, method: str, path: str, headers: object, body: bytes) -> tuple[int, bytes]:
        key = _key_of(path)
        _check_signed(headers, body)
        with self.lock:
            self.calls.append((method, key))
            fault = self.faults[method].pop(0) if self.faults[method] else None
        if isinstance(fault, int):
            return fault, b""
        if fault in (TIMEOUT, DROP):
            raise _transport_error(fault)
        return self._apply(method, key, headers, body, fault)

    def _apply(
        self, method: str, key: str, headers: object, body: bytes, fault: str | None
    ) -> tuple[int, bytes]:
        if method == "GET":
            data = self.objects.get(key)
            return (404, b"") if data is None else (200, data)
        if self.before_put is not None:
            self.before_put(key)
        assert _header(headers, "if-none-match") == "*", "every PUT is conditional"
        with self.lock:
            if key in self.objects:
                return 412, b""
            self.objects[key] = body
        if fault == COMMIT_THEN_TIMEOUT:
            raise httpx.ReadTimeout("timed out")
        return 200, b""


def _key_of(path: str) -> str:
    prefix = f"/{BUCKET}/"
    assert path.startswith(prefix), "requests are path-style, to the one bucket"
    return path[len(prefix) :]


def _header(headers: object, name: str) -> str | None:
    getter = getattr(headers, "get", None)
    return None if getter is None else getter(name)


def _check_signed(headers: object, body: bytes) -> None:
    authorization = _header(headers, "authorization") or ""
    assert authorization.startswith(f"AWS4-HMAC-SHA256 Credential={KEY_ID}/"), "unsigned request"
    assert "/auto/s3/aws4_request" in authorization, "signed for region auto"
    assert _header(headers, "x-amz-content-sha256") == hashlib.sha256(body).hexdigest()


def _transport_error(fault: str) -> httpx.TransportError:
    if fault == TIMEOUT:
        return httpx.ReadTimeout("timed out")
    return httpx.RemoteProtocolError("server disconnected")


class _Handler(BaseHTTPRequestHandler):
    bucket: FakeBucket

    def do_GET(self) -> None:
        self._answer(b"")

    def do_PUT(self) -> None:
        self._answer(self.rfile.read(int(self.headers.get("content-length", "0"))))

    def _answer(self, body: bytes) -> None:
        try:
            status, data = self.bucket.answer(self.command, self.path, self.headers, body)
        except httpx.TransportError:
            self.close_connection = True
            return
        self.send_response(status)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def serve(bucket: FakeBucket) -> Iterator[str]:
    """Serve ``bucket`` on a loopback port; yields the endpoint URL."""
    handler = type("Handler", (_Handler,), {"bucket": bucket})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
