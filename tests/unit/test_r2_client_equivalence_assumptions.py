"""Library behaviour the R2 client's registered equivalent mutants argue from (#802).

Each test pins one claim a ``mutation-equivalents/embeddings.r2_client/`` entry
makes about httpx or the standard library. The day a claim stops holding, the
waived mutant becomes a real defect and the test here goes red.
"""

import hashlib
from urllib.parse import quote

import httpx

_URL = "https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com/b/k.lsec"


def test_assumption_httpx_header_names_fold_case_on_set_and_lookup() -> None:
    """Waives the ``"x-amz-date"`` -> ``"X-AMZ-DATE"`` style mutants in
    ``sign_for_region`` and ``_canonical_request``, and the ``"if-none-match"`` /
    ``"content-md5"`` ones in ``put_if_absent``: httpx stores one value per
    case-folded name, lists names lower-cased, and reads any spelling."""
    request = httpx.Request("PUT", _URL, headers={"IF-NONE-MATCH": "*", "CONTENT-MD5": "d"})
    request.headers["X-AMZ-DATE"] = "first"
    request.headers["x-amz-date"] = "second"

    assert request.headers["x-amz-date"] == request.headers["X-AMZ-DATE"] == "second"
    assert request.headers["if-none-match"] == "*"
    assert request.headers["content-md5"] == "d"
    assert [n for n in request.headers if n == "x-amz-date"] == ["x-amz-date"]
    assert all(n == n.lower() for n in request.headers)


def test_assumption_httpx_builds_the_same_get_from_empty_and_absent_parts() -> None:
    """Waives ``_send("GET", key, {}, b"")`` -> ``None`` headers or ``None``
    content in ``R2Client.get``: httpx builds the identical request either way,
    with an empty body and no extra header."""
    client = httpx.Client()
    given = client.build_request("GET", _URL, headers={}, content=b"")
    absent = client.build_request("GET", _URL, headers=None, content=None)

    assert given.content == absent.content == b""
    assert list(given.headers.multi_items()) == list(absent.headers.multi_items())


def test_assumption_httpx_upper_cases_put_and_get() -> None:
    """Waives the ``"PUT"`` -> ``"put"`` and ``"GET"`` -> ``"get"`` mutants in
    ``put_if_absent`` and ``get``, through ``Client.build_request``."""
    client = httpx.Client()

    assert client.build_request("put", _URL, content=b"x").method == "PUT"
    assert client.build_request("get", _URL).method == "GET"


def test_assumption_md5_digest_ignores_usedforsecurity() -> None:
    """Waives the ``usedforsecurity=False`` mutants in ``put_if_absent``: the
    flag only lets a FIPS build refuse MD5; it never changes the digest."""
    data = b"lovspor-embedding-cache"
    expected = hashlib.md5(data, usedforsecurity=False).digest()

    assert hashlib.md5(data).digest() == expected  # noqa: S324 - the claim under test
    assert hashlib.md5(data, usedforsecurity=True).digest() == expected  # noqa: S324 - as above
    assert hashlib.md5(data, usedforsecurity=None).digest() == expected  # type: ignore[arg-type]  # noqa: S324


def test_assumption_quote_always_keeps_unreserved_characters() -> None:
    """Waives the ``safe=`` mutants in ``_canonical_request`` and
    ``_canonical_query``: ``quote`` never encodes ASCII letters, digits or
    ``-_.~``, so adding ``X`` to ``safe``, or dropping ``-_.~`` from it, changes
    nothing; only ``/`` depends on ``safe``, and its default is ``"/"``."""
    text = "a/B-9_.~ %æ&="

    assert quote(text, safe="/-_.~") == quote(text) == quote(text, safe="XX/-_.~XX")
    assert quote(text, safe="-_.~") == quote(text, safe="XX-_.~XX") == quote(text, safe="")
