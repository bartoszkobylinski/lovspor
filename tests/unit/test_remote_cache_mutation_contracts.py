"""Observable refusal, signing and logging contracts for the R2 cache."""

import base64
import hashlib
import logging
import os
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.embeddings.cache import with_embedding_cache
from lovspor.embeddings.cache_format import CachedVectors, encode_entry
from lovspor.embeddings.cache_remote import (
    ACCOUNT_VARIABLE,
    BUCKET_VARIABLE,
    KEY_ID_VARIABLE,
    REMOTE_VARIABLE,
    SECRET_VARIABLE,
    RemoteEntryStore,
    remote_key,
    with_remote,
)
from lovspor.embeddings.cache_store import LocalEntryStore, entry_location
from lovspor.embeddings.r2_client import R2Client, R2Credentials, _canonical_query, sign_request
from lovspor.errors import EmbeddingCacheError
from tests.unit import r2_fakes
from tests.unit.test_embedding_cache_admin import NOW, POLICY, _doc
from tests.unit.test_embedding_cache_remote_sync import _Provider, _settings


def test_remote_configuration_refusals(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(REMOTE_VARIABLE, "r2")
    monkeypatch.delenv("LOVSPOR_EMBEDDING_CACHE_DIR", raising=False)
    with pytest.raises(EmbeddingCacheError) as error:
        with_embedding_cache(_Provider(), _settings(tmp_path))
    assert str(error.value) == (
        "a remote embedding cache needs LOVSPOR_EMBEDDING_CACHE_DIR for its local copy; "
        "set it, or unset the remote cache variables"
    )
    names = (BUCKET_VARIABLE, ACCOUNT_VARIABLE, KEY_ID_VARIABLE, SECRET_VARIABLE)
    for name in names:
        monkeypatch.delenv(name, raising=False)
    local = LocalEntryStore.open(tmp_path / "cache", (), POLICY)
    with pytest.raises(EmbeddingCacheError) as error:
        with_remote(local)
    assert str(error.value) == f"the remote embedding cache needs {', '.join(names)}"
    monkeypatch.delenv(REMOTE_VARIABLE)
    assert with_remote(local) is local


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("account_id", "bad", "R2 account id must be 32 lowercase hex characters"),
        ("bucket", "!", "R2 bucket name is not a valid bucket name"),
        ("access_key_id", "", "R2 access key id and secret must both be set"),
        ("secret_access_key", "", "R2 access key id and secret must both be set"),
    ],
)
def test_credentials_refuse_invalid_values(field: str, value: str, message: str) -> None:
    values = {
        "account_id": r2_fakes.ACCOUNT_ID,
        "bucket": "cache",
        "access_key_id": "id",
        "secret_access_key": "secret",
    }
    values[field] = value
    with pytest.raises(EmbeddingCacheError) as error:
        R2Credentials(**values)
    assert str(error.value) == message


def test_credentials_repr_is_redacted() -> None:
    assert repr(r2_fakes.credentials()) == "R2Credentials(<redacted>)"


@pytest.mark.parametrize(
    "operation,status",
    [("PUT", 403), ("GET", 403), ("PUT", 503), ("VANISH", 412), ("CONFLICT", 200)],
)
def test_remote_refusal_explains_recovery(
    operation: str, status: int, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    bucket = r2_fakes.FakeBucket()
    httpx_mock.add_callback(bucket, is_reusable=True)
    local = LocalEntryStore.open(tmp_path / "cache", (), POLICY)
    store = RemoteEntryStore(local, R2Client(r2_fakes.credentials()), sleep=lambda _: None)
    entry = _doc("a")
    if operation == "VANISH":
        bucket.fail("PUT", 412, 412)
        message = (
            f"remote embedding cache entry was held, then vanished, twice ({entry.key.label()}); "
            "nothing was stored locally and no further request was sent"
        )
    elif operation == "CONFLICT":
        local.put(entry)
        different = CachedVectors(entry.key, entry.vectors // 2, entry.scale, NOW)
        bucket.objects[remote_key(entry.key)] = encode_entry(different)
        message = (
            f"local and remote embedding cache values differ ({entry.key.label()}); "
            "both are kept: invalidate one copy to continue"
        )
    else:
        bucket.fail(operation, *([status] * (3 if status == 503 else 1)))
        message = (
            f"remote embedding cache unreachable after 3 attempts ({entry.key.label()}); "
            "the run stops before paying again"
            if status == 503
            else (
                f"remote embedding cache refused {operation} with status {status} "
                f"({entry.key.label()}); the run stops before paying again"
            )
        )
    with pytest.raises(EmbeddingCacheError) as error:
        if operation in {"GET", "CONFLICT"}:
            store.get(entry.key)
        else:
            store.put(entry)
    assert str(error.value) == message


def test_get_keeps_local_timestamp_when_values_agree(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    entry = _doc("a")
    earlier = CachedVectors(entry.key, entry.vectors, entry.scale, NOW - timedelta(hours=1))
    local = LocalEntryStore.open(tmp_path / "cache", (), POLICY)
    local.put(entry)
    httpx_mock.add_response(content=encode_entry(earlier))
    store = RemoteEntryStore(local, R2Client(r2_fakes.credentials()))
    parts, _ = entry_location(entry.key)
    directory = local.root.joinpath(*parts)
    os.utime(directory, ns=(1_000_000_000, 1_000_000_000))
    result = store.get(entry.key)
    assert directory.stat().st_mtime_ns == 1_000_000_000, "a cache hit does not publish again"
    assert result is not None
    assert result.created_at == entry.created_at


def test_get_has_no_body_and_invalid_keys_explain_refusal(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(content=b"value")
    client = R2Client(r2_fakes.credentials())
    assert client.get("valid/key").body == b"value"
    request = httpx_mock.get_request()
    assert request is not None
    assert request.content == b""
    with pytest.raises(EmbeddingCacheError) as error:
        client.get("../unsafe")
    assert str(error.value) == "refusing an R2 object key that is not a cache key"
    assert client.clock().utcoffset() == timedelta(0)
    assert client.clock().tzinfo is UTC


def test_signing_uses_utc_even_in_another_local_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    old = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "EST5")
    time.tzset()
    try:
        request = httpx.Request("GET", "https://example.com/a")
        sign_request(
            request,
            r2_fakes.credentials(),
            datetime(2026, 1, 2, 3, tzinfo=timezone(timedelta(hours=2))),
        )
        assert request.headers["x-amz-date"] == "20260102T010000Z"
    finally:
        if old is None:
            monkeypatch.delenv("TZ")
        else:
            monkeypatch.setenv("TZ", old)
        time.tzset()


def test_query_encoding_sorts_and_escapes_slashes() -> None:
    url = httpx.URL("https://example.com", params=[("z/", "v/"), ("a", "space +"), ("a", "~X")])
    assert _canonical_query(url) == "a=space%20%2B&a=~X&z%2F=v%2F"


def test_bucket_hosts_are_removed_from_httpx_logs(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("httpx")
    previous = logger.filters[:]
    logger.filters.clear()
    try:
        with caplog.at_level(logging.INFO, logger="httpx"):
            R2Client(r2_fakes.credentials(), endpoint="https://first.example")
            R2Client(r2_fakes.credentials(), endpoint="https://second.example")
            logger.info("GET https://first.example/private")
            logger.info("GET https://second.example/private")
            logger.info("GET https://public.example/public")
        assert "first.example" not in caplog.text
        assert "second.example" not in caplog.text
        assert "public.example" in caplog.text
    finally:
        logger.filters[:] = previous


def test_conditional_put_preserves_payload_and_digest(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200)
    client = R2Client(r2_fakes.credentials())
    assert client.put_if_absent("valid/key", b"payload").status == 200
    request = httpx_mock.get_request()
    assert request is not None
    assert request.method == "PUT"
    assert request.content == b"payload"
    assert request.headers["if-none-match"] == "*"
    assert (
        request.headers["content-md5"]
        == base64.b64encode(hashlib.md5(b"payload", usedforsecurity=False).digest()).decode()
    )
