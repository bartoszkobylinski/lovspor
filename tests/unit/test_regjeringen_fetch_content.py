"""Content boundaries of the explicit-list fetch contract (#738)."""

import gzip
import hashlib
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock, IteratorStream

from lovspor.regjeringen_fetch.fetch import FetchSettings, prepare_out, run_fetch
from lovspor.regjeringen_fetch.models import FetchOutcome
from tests.unit.test_regjeringen_fetch import CH1, CH2, ROBOTS, _Clock


@pytest.mark.parametrize("size", [1024, 1025])
def test_compressed_page_limit_applies_to_decoded_bytes(
    httpx_mock: HTTPXMock, tmp_path: Path, size: int
) -> None:
    """A small wire payload cannot evade the maximum stored body size.

    Since the identity-only rule (#738 review), a coded body is never inflated
    or stored at all, whatever it would decode to.
    """
    body = b"x" * size
    compressed = gzip.compress(body, mtime=0)
    assert len(compressed) < 1024
    httpx_mock.add_response(url=ROBOTS, content=b"User-agent: *\n")
    httpx_mock.add_response(
        url=CH1,
        headers={"Content-Encoding": "gzip", "Content-Length": str(len(compressed))},
        stream=IteratorStream([compressed[:10], compressed[10:]]),
    )
    out = prepare_out(tmp_path / "pages")
    with httpx.Client() as client:
        run = run_fetch(
            client,
            (CH1,),
            out,
            FetchSettings(max_bytes=1024, clock=_Clock().as_fetch_clock()),
        )
    record = run.records[0]
    assert (record.outcome, record.sha256, record.size) == (
        FetchOutcome.CONTENT_ENCODED,
        None,
        None,
    )
    assert list(out.glob("*.html")) == []


def test_page_links_never_expand_the_explicit_url_list(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    """The no-discovery contract includes same-host chapter and asset links."""
    body = (
        f'<html><body><a href="{CH2}">Next chapter</a>'
        '<a href="https://elsewhere.example/page">External</a>'
        '<img src="/contentassets/synthetic.png"></body></html>'
    ).encode()
    httpx_mock.add_response(url=ROBOTS, content=b"User-agent: *\n")
    httpx_mock.add_response(url=CH1, content=body)
    out = prepare_out(tmp_path / "pages")
    with httpx.Client() as client:
        run = run_fetch(
            client,
            (CH1,),
            out,
            FetchSettings(clock=_Clock().as_fetch_clock()),
        )
    assert [str(request.url) for request in httpx_mock.get_requests()] == [ROBOTS, CH1]
    assert [record.url for record in run.records] == [CH1]
    assert run.records[0].outcome is FetchOutcome.FETCHED
    digest = hashlib.sha256(body).hexdigest()
    assert (out / f"{digest}.html").read_bytes() == body
    assert len(list(out.glob("*.html"))) == 1
