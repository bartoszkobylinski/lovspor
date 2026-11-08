"""Behavioral boundaries for the remote-cache mutation survivors."""

from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.embeddings.cache_remote import (
    ACCOUNT_VARIABLE,
    BUCKET_VARIABLE,
    KEY_ID_VARIABLE,
    REMOTE_VARIABLE,
    SECRET_VARIABLE,
    _credentials,
    with_remote,
)
from lovspor.embeddings.cache_store import LocalEntryStore
from lovspor.embeddings.r2_client import R2Client, _canonical_query, _canonical_request
from lovspor.errors import EmbeddingCacheError
from tests.unit import r2_fakes
from tests.unit.test_embedding_cache_admin import POLICY


def test_absent_backend_preserves_local_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(REMOTE_VARIABLE, raising=False)
    local = LocalEntryStore(tmp_path, POLICY)
    assert with_remote(local) is local


@pytest.mark.parametrize(
    "absent", [BUCKET_VARIABLE, ACCOUNT_VARIABLE, KEY_ID_VARIABLE, SECRET_VARIABLE]
)
def test_missing_credential_is_named_without_leaking_values(
    monkeypatch: pytest.MonkeyPatch, absent: str
) -> None:
    for name, value in (
        (BUCKET_VARIABLE, r2_fakes.BUCKET),
        (ACCOUNT_VARIABLE, r2_fakes.ACCOUNT_ID),
        (KEY_ID_VARIABLE, r2_fakes.KEY_ID),
        (SECRET_VARIABLE, r2_fakes.SECRET),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(absent)
    with pytest.raises(EmbeddingCacheError) as caught:
        _credentials()
    assert str(caught.value) == f"the remote embedding cache needs {absent}"


def test_signed_round_trip_preserves_payload_and_conditional_write(httpx_mock: HTTPXMock) -> None:
    bucket = r2_fakes.FakeBucket()
    httpx_mock.add_callback(bucket, is_reusable=True)
    with httpx.Client() as http:
        client = R2Client(r2_fakes.credentials(), http)
        assert client.put_if_absent("cache/x.lsec", b"payload").status == 200
        assert client.put_if_absent("cache/x.lsec", b"replacement").status == 412
        reply = client.get("cache/x.lsec")
    assert (reply.status, reply.body) == (200, b"payload")
    requests = httpx_mock.get_requests()
    assert requests[0].headers["content-md5"] == "Mhw89IbtUJFk7eweGYH+yA=="
    assert requests[2].content == b""


def test_canonical_host_comes_from_url_even_with_explicit_host_header() -> None:
    # _canonical_request explicitly initializes host from URL and excludes it
    # from the header update: the URL authority must survive that update.
    request = httpx.Request(
        "GET",
        "https://bucket.example:8443/aX-_.~/b",
        headers={"host": "proxy.example", "x-amz-content-sha256": "payload-hash"},
    )
    assert _canonical_request(request, ["host", "x-amz-content-sha256"]) == (
        "GET\n/aX-_.~/b\n\nhost:bucket.example:8443\n"
        "x-amz-content-sha256:payload-hash\n\nhost;x-amz-content-sha256\npayload-hash"
    )


def test_query_escapes_reserved_characters_but_preserves_unreserved_x() -> None:
    url = httpx.URL("https://bucket.example/", params=[("X/-_.~", "X/ +"), ("a", "z")])
    assert _canonical_query(url) == "X%2F-_.~=X%2F%20%2B&a=z"
