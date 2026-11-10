"""What "the exact bytes as served" means when a server applies a content coding (#738).

Every request asks for ``Accept-Encoding: identity``. A body that arrives with
no content coding is stored and hashed byte for byte as it came off the wire
(after HTTP/1.1 chunked framing, which is not part of the content). A body
that still carries a content coding is never decoded and never stored: its
digest would depend on the server's compressor, and decoding it is where a
small response becomes a large one.
"""

import gzip
import hashlib
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock, IteratorStream

from lovspor.errors import RegjeringenFetchError
from lovspor.regjeringen_fetch.fetch import FetchSettings, prepare_out, run_fetch
from lovspor.regjeringen_fetch.models import FetchOutcome, FetchRun
from tests.unit.test_regjeringen_fetch import CH1, PAGE, ROBOTS, _Clock

TERMS = "https://www.regjeringen.no/no/om-nettstedet/vilkar/id0000001/"
ROBOTS_BODY = b"User-agent: *\nDisallow: /sok\n"


def _run(out: Path, max_bytes: int = 1024, terms: str | None = None) -> FetchRun:
    settings = FetchSettings(max_bytes=max_bytes, clock=_Clock().as_fetch_clock(), terms_url=terms)
    with httpx.Client() as client:
        return run_fetch(client, (CH1,), out, settings)


def test_every_request_asks_for_the_identity_coding(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    httpx_mock.add_response(url=ROBOTS, content=ROBOTS_BODY)
    httpx_mock.add_response(url=TERMS, content=b"<html>terms</html>")
    httpx_mock.add_response(url=CH1, content=PAGE)
    _run(prepare_out(tmp_path / "pages"), terms=TERMS)
    requests = httpx_mock.get_requests()
    assert len(requests) == 3
    assert {request.headers["Accept-Encoding"] for request in requests} == {"identity"}


@pytest.mark.parametrize("coding", [None, "identity"])
def test_an_uncoded_page_is_stored_exactly_as_it_came_off_the_wire(
    httpx_mock: HTTPXMock, tmp_path: Path, coding: str | None
) -> None:
    headers = {} if coding is None else {"Content-Encoding": coding}
    body = b"<!DOCTYPE html>\r\n<html><body>bl\xc3\xa5b\xc3\xa6r</body></html>\n"
    httpx_mock.add_response(url=ROBOTS, content=ROBOTS_BODY)
    httpx_mock.add_response(url=CH1, content=body, headers=headers)
    out = prepare_out(tmp_path / "pages")
    record = _run(out).records[0]
    assert record.outcome is FetchOutcome.FETCHED
    assert record.sha256 == hashlib.sha256(body).hexdigest()
    assert (out / f"{record.sha256}.html").read_bytes() == body


@pytest.mark.codex_proposal
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Proposal: content-coding tokens are case-insensitive (RFC 9110 §8.4.1), "
        "so a differently-cased 'identity' should be treated as uncoded too"
    ),
)
@pytest.mark.parametrize("coding", ["IDENTITY", "Identity"])
def test_an_uncoded_page_survives_a_differently_cased_identity_token(
    httpx_mock: HTTPXMock, tmp_path: Path, coding: str
) -> None:
    """``_coded`` compares the header value with ``!=``, so a conformant server that
    spells the content-coding token in another case is wrongly treated as coded.
    """
    body = b"<html>mixed-case identity token</html>"
    httpx_mock.add_response(url=ROBOTS, content=ROBOTS_BODY)
    httpx_mock.add_response(url=CH1, content=body, headers={"Content-Encoding": coding})
    out = prepare_out(tmp_path / "pages")
    record = _run(out).records[0]
    assert record.outcome is FetchOutcome.FETCHED
    assert record.sha256 == hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize("coding", ["gzip", "br", "deflate", "x-unknown"])
def test_a_coded_page_is_neither_decoded_nor_stored(
    httpx_mock: HTTPXMock, tmp_path: Path, coding: str
) -> None:
    httpx_mock.add_response(url=ROBOTS, content=ROBOTS_BODY)
    httpx_mock.add_response(
        url=CH1,
        stream=IteratorStream([gzip.compress(PAGE)]),
        headers={"Content-Encoding": coding},
    )
    out = prepare_out(tmp_path / "pages")
    record = _run(out).records[0]
    assert (record.outcome, record.status, record.sha256) == ("content_encoded", 200, None)
    assert list(out.glob("*.html")) == []


def test_a_compressed_bomb_is_refused_without_being_inflated(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    bomb = gzip.compress(b"\0" * (8 * 1024 * 1024))
    httpx_mock.add_response(url=ROBOTS, content=ROBOTS_BODY)
    httpx_mock.add_response(
        url=CH1, stream=IteratorStream([bomb]), headers={"Content-Encoding": "gzip"}
    )
    record = _run(prepare_out(tmp_path / "pages"), max_bytes=len(bomb) + 1).records[0]
    assert record.outcome is FetchOutcome.CONTENT_ENCODED


def test_the_size_cap_counts_the_bytes_that_arrived(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    httpx_mock.add_response(url=ROBOTS, content=ROBOTS_BODY)
    httpx_mock.add_response(url=CH1, content=b"x" * 1025)
    record = _run(prepare_out(tmp_path / "pages")).records[0]
    assert record.outcome is FetchOutcome.TOO_LARGE


def test_a_coded_robots_txt_stops_the_run(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    httpx_mock.add_response(
        url=ROBOTS,
        stream=IteratorStream([gzip.compress(ROBOTS_BODY)]),
        headers={"Content-Encoding": "gzip"},
    )
    out = prepare_out(tmp_path / "pages")
    with pytest.raises(RegjeringenFetchError) as caught:
        _run(out)
    assert (caught.value.reason, caught.value.detail) == ("robots_unreadable", "content_encoded")
    assert [str(request.url) for request in httpx_mock.get_requests()] == [ROBOTS]
    assert list(out.iterdir()) == []


def test_coded_terms_are_unreadable_and_not_stored(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    httpx_mock.add_response(url=ROBOTS, content=ROBOTS_BODY)
    httpx_mock.add_response(
        url=TERMS,
        stream=IteratorStream([gzip.compress(b"<html>t</html>")]),
        headers={"Content-Encoding": "gzip"},
    )
    httpx_mock.add_response(url=CH1, content=PAGE)
    out = prepare_out(tmp_path / "pages")
    run = _run(out, terms=TERMS)
    assert run.terms is not None
    assert (run.terms.state, run.terms.status, run.terms.sha256) == ("unreadable", 200, None)
    assert list(out.glob("terms-*")) == []


def test_published_robots_digest_is_over_the_uncoded_wire_bytes(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    body = b"User-agent: *\r\nCrawl-delay: 7\r\n"
    httpx_mock.add_response(url=ROBOTS, content=body, headers={"Content-Encoding": "identity"})
    httpx_mock.add_response(url=CH1, content=PAGE)
    out = prepare_out(tmp_path / "pages")
    run = _run(out)
    assert run.robots.sha256 == hashlib.sha256(body).hexdigest()
    assert run.delay_seconds == 7.0
