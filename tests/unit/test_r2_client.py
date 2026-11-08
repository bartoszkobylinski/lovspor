"""Signed single requests to an R2 bucket (#802, design §8.1).

The signature is checked against two independent references:

* the published AWS Signature Version 4 example for S3 ``GET Object`` (bucket
  ``examplebucket``, 2013-05-24, region ``us-east-1``);
* a conditional ``PUT`` to an R2 path-style URL under region ``auto``. Its
  expected value was computed with ``botocore``'s ``S3SigV4Auth`` on
  2026-11-08 from the same inputs. botocore is not a dependency; the value is
  pinned here.
"""

import base64
import hashlib
import logging
from datetime import UTC, datetime

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.embeddings.r2_client import R2Client, R2Credentials, sign_for_region, sign_request
from lovspor.errors import EmbeddingCacheError
from tests.unit import r2_fakes

_AWS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
_AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
_ACCOUNT = "0123456789abcdef0123456789abcdef"


def _aws_credentials() -> R2Credentials:
    return R2Credentials(
        account_id=_ACCOUNT,
        bucket="bucket-x",
        access_key_id=_AWS_KEY_ID,
        secret_access_key=_AWS_SECRET,
    )


def test_signature_matches_the_published_aws_get_object_example() -> None:
    request = httpx.Request(
        "GET", "https://examplebucket.s3.amazonaws.com/test.txt", headers={"range": "bytes=0-9"}
    )
    sign_for_region(request, _aws_credentials(), datetime(2013, 5, 24, tzinfo=UTC), "us-east-1")
    assert request.headers["authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


def test_conditional_put_signature_matches_botocore_for_region_auto() -> None:
    body = b"hello entry"
    md5 = base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode()
    url = (
        f"https://{_ACCOUNT}.r2.cloudflarestorage.com/bucket-x/"
        "lovspor-embedding-cache/schema-1/a/documents/b.lsec"
    )
    request = httpx.Request(
        "PUT", url, headers={"if-none-match": "*", "content-md5": md5}, content=body
    )
    sign_request(request, _aws_credentials(), datetime(2026, 11, 8, 12, tzinfo=UTC))
    assert request.headers["authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20261108/auto/s3/aws4_request, "
        "SignedHeaders=content-md5;host;if-none-match;x-amz-content-sha256;x-amz-date, "
        "Signature=4cdc56f7820486f0712ba895846d91d64fed1d49582fde73057f7a265f88f247"
    )


def test_credentials_never_show_the_key_id_or_secret() -> None:
    shown = repr(r2_fakes.credentials()) + str(r2_fakes.credentials())
    assert r2_fakes.KEY_ID not in shown
    assert r2_fakes.SECRET not in shown


@pytest.mark.parametrize(
    ("account", "bucket", "key_id", "secret", "reason"),
    [
        ("not-hex", "bucket-x", "k", "s", "account id"),
        (_ACCOUNT.upper(), "bucket-x", "k", "s", "account id"),
        (_ACCOUNT, "Bucket_X", "k", "s", "bucket name"),
        (_ACCOUNT, "b.evil/x", "k", "s", "bucket name"),
        (_ACCOUNT, "bucket-x", "", "s", "must both be set"),
        (_ACCOUNT, "bucket-x", "k", "", "must both be set"),
    ],
)
def test_credentials_refuse_values_that_would_change_the_url(
    account: str, bucket: str, key_id: str, secret: str, reason: str
) -> None:
    with pytest.raises(EmbeddingCacheError, match=reason):
        R2Credentials(
            account_id=account, bucket=bucket, access_key_id=key_id, secret_access_key=secret
        )


def test_the_endpoint_is_the_accounts_r2_host() -> None:
    assert r2_fakes.credentials().endpoint == r2_fakes.ENDPOINT


def test_put_if_absent_is_conditional_signed_and_path_style(httpx_mock: HTTPXMock) -> None:
    bucket = r2_fakes.FakeBucket()
    httpx_mock.add_callback(bucket, is_reusable=True)
    client = R2Client(r2_fakes.credentials(), httpx.Client())
    first = client.put_if_absent("lovspor-embedding-cache/schema-1/x.lsec", b"payload")
    second = client.put_if_absent("lovspor-embedding-cache/schema-1/x.lsec", b"other")
    assert (first.status, second.status) == (200, 412)
    assert bucket.objects == {"lovspor-embedding-cache/schema-1/x.lsec": b"payload"}
    sent = httpx_mock.get_requests()[0]
    assert (
        sent.url == f"{r2_fakes.ENDPOINT}/{r2_fakes.BUCKET}/lovspor-embedding-cache/schema-1/x.lsec"
    )
    assert (
        sent.headers["content-md5"]
        == base64.b64encode(hashlib.md5(b"payload", usedforsecurity=False).digest()).decode()
    )
    assert r2_fakes.SECRET not in str(sent.headers)


def test_get_returns_status_and_body(httpx_mock: HTTPXMock) -> None:
    bucket = r2_fakes.FakeBucket(objects={"lovspor-embedding-cache/a.lsec": b"bytes"})
    httpx_mock.add_callback(bucket, is_reusable=True)
    client = R2Client(r2_fakes.credentials(), httpx.Client())
    assert client.get("lovspor-embedding-cache/a.lsec").body == b"bytes"
    assert client.get("lovspor-embedding-cache/b.lsec").status == 404


@pytest.mark.parametrize("key", ["../other-bucket/x", "a/../../b", "/abs", "UPPER", "a?x=1", ""])
def test_a_key_that_is_not_a_cache_key_is_refused_before_any_request(key: str) -> None:
    client = R2Client(r2_fakes.credentials(), httpx.Client())
    with pytest.raises(EmbeddingCacheError, match="not a cache key"):
        client.get(key)


def test_the_request_log_never_names_the_bucket_host(
    httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
) -> None:
    httpx_mock.add_callback(r2_fakes.FakeBucket(), is_reusable=True)
    caplog.set_level(logging.DEBUG)
    R2Client(r2_fakes.credentials(), httpx.Client()).get("lovspor-embedding-cache/a.lsec")
    assert r2_fakes.ACCOUNT_ID not in caplog.text
    assert r2_fakes.KEY_ID not in caplog.text
