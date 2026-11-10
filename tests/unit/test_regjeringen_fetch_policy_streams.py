"""Policy evidence must be complete and within the documented body limits."""

import hashlib
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock, IteratorStream

from lovspor.errors import RegjeringenFetchError
from lovspor.regjeringen_fetch.fetch import FetchSettings, prepare_out, run_fetch
from lovspor.regjeringen_fetch.models import FetchOutcome, FetchRun
from tests.unit.test_regjeringen_fetch import CH1, PAGE, ROBOTS, _Clock
from tests.unit.test_regjeringen_fetch_terms import TERMS


class _InterruptedPolicy(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield b"User-agent: *\nDisallow: /no/\n"
        raise httpx.ReadTimeout("policy body interrupted")


@pytest.mark.parametrize("kind", ["robots", "terms"])
def test_interrupted_policy_body_never_becomes_evidence(
    httpx_mock: HTTPXMock, tmp_path: Path, kind: str
) -> None:
    if kind == "terms":
        httpx_mock.add_response(url=ROBOTS, text="User-agent: *\n")
    url = ROBOTS if kind == "robots" else TERMS
    httpx_mock.add_response(url=url, stream=_InterruptedPolicy())
    if kind == "terms":
        httpx_mock.add_response(url=CH1, content=PAGE)
    out = prepare_out(tmp_path / "pages")
    settings = FetchSettings(
        clock=_Clock().as_fetch_clock(), terms_url=TERMS if kind == "terms" else None
    )
    with httpx.Client() as client:
        if kind == "robots":
            with pytest.raises(RegjeringenFetchError) as caught:
                run_fetch(client, (CH1,), out, settings)
            assert (caught.value.reason, caught.value.detail) == (
                "robots_unreadable",
                "ReadTimeout",
            )
            assert list(out.iterdir()) == []
        else:
            run = run_fetch(client, (CH1,), out, settings)
            assert run.terms is not None
            assert (run.terms.state, run.terms.sha256, run.terms.size) == (
                "unreadable",
                None,
                None,
            )
            assert run.records[0].outcome is FetchOutcome.FETCHED
            assert list(out.glob("terms-*")) == []
            assert [path.read_bytes() for path in out.glob("*.html")] == [PAGE]
            (sidecar,) = out.glob("fetch-run-*.json")
            assert FetchRun.model_validate_json(sidecar.read_bytes()) == run
    expected = [ROBOTS] if kind == "robots" else [ROBOTS, TERMS, CH1]
    assert [str(request.url) for request in httpx_mock.get_requests()] == expected


@pytest.mark.parametrize("extra", [0, 1])
def test_robots_stream_limit_is_inclusive_and_independent_of_page_limit(
    httpx_mock: HTTPXMock, tmp_path: Path, extra: int
) -> None:
    prefix = b"User-agent: *\n#"
    body = prefix + b"x" * (512 * 1024 - len(prefix) + extra)
    httpx_mock.add_response(
        url=ROBOTS, stream=IteratorStream([body[:100], body[100:-1], body[-1:]])
    )
    if extra == 0:
        httpx_mock.add_response(url=CH1, content=PAGE)
    out = prepare_out(tmp_path / "pages")
    settings = FetchSettings(max_bytes=len(PAGE), clock=_Clock().as_fetch_clock())
    with httpx.Client() as client:
        if extra:
            with pytest.raises(RegjeringenFetchError) as caught:
                run_fetch(client, (CH1,), out, settings)
            assert (caught.value.reason, caught.value.detail) == (
                "robots_unreadable",
                "oversized",
            )
            assert list(out.iterdir()) == []
        else:
            run = run_fetch(client, (CH1,), out, settings)
            digest = hashlib.sha256(body).hexdigest()
            assert (run.robots.sha256, run.robots.size) == (digest, len(body))
            assert (out / f"robots-{digest}.txt").read_bytes() == body
            assert run.records[0].outcome is FetchOutcome.FETCHED
    expected = [ROBOTS] if extra else [ROBOTS, CH1]
    assert [str(request.url) for request in httpx_mock.get_requests()] == expected
