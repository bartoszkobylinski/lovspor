"""The website-terms page and the stored source evidence (#738, Amendment 1 point 8)."""

import hashlib
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.errors import RegjeringenFetchError
from lovspor.regjeringen_fetch.fetch import (
    FetchSettings,
    check_terms_url,
    prepare_out,
    run_fetch,
    verified_source_bytes,
)
from lovspor.regjeringen_fetch.models import FetchOutcome, FetchRun, SourceEvidence
from tests.unit.test_regjeringen_fetch import CH1, PAGE, ROBOTS, _Clock

TERMS = "https://www.regjeringen.no/no/om-nettstedet/vilkar/id0000001/"
TERMS_BODY = b"<!DOCTYPE html><html><body>synthetic terms</body></html>"


def _run(out: Path, clock: _Clock, terms_url: str | None = TERMS) -> FetchRun:
    settings = FetchSettings(clock=clock.as_fetch_clock(), terms_url=terms_url)
    with httpx.Client() as client:
        return run_fetch(client, (CH1,), out, settings)


def _requested(httpx_mock: HTTPXMock) -> list[str]:
    return [str(request.url) for request in httpx_mock.get_requests()]


def test_published_terms_are_stored_after_robots_and_spaced(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    httpx_mock.add_response(url=ROBOTS, text="User-agent: *\n")
    httpx_mock.add_response(url=TERMS, content=TERMS_BODY)
    httpx_mock.add_response(url=CH1, content=PAGE)
    out = prepare_out(tmp_path / "pages")
    clock = _Clock()
    run = _run(out, clock)
    terms = run.terms
    assert terms is not None
    digest = hashlib.sha256(TERMS_BODY).hexdigest()
    assert (terms.state, terms.status, terms.sha256) == ("published", 200, digest)
    assert [(hop.url, hop.status) for hop in terms.hops] == [(TERMS, 200)]
    assert verified_source_bytes(out, terms) == TERMS_BODY
    assert (out / f"terms-{digest}.html").stat().st_mode & 0o777 == 0o600
    assert _requested(httpx_mock) == [ROBOTS, TERMS, CH1]
    assert clock.slept == [5.0, 5.0]
    assert terms.retrieved_at == clock.now().__class__.fromisoformat("2026-11-09T12:00:05+00:00")


@pytest.mark.parametrize("status", [403, 429])
def test_refused_terms_stop_the_run_before_any_document(
    httpx_mock: HTTPXMock, tmp_path: Path, status: int
) -> None:
    httpx_mock.add_response(url=ROBOTS, text="User-agent: *\n")
    httpx_mock.add_response(url=TERMS, status_code=status)
    run = _run(prepare_out(tmp_path / "pages"), _Clock())
    assert run.terms is not None
    assert (run.terms.state, run.terms.status) == ("refused", status)
    assert run.records[0].outcome is FetchOutcome.NOT_ATTEMPTED
    assert _requested(httpx_mock) == [ROBOTS, TERMS]


@pytest.mark.parametrize(
    ("reply", "state"),
    [
        ({"status_code": 404}, "absent"),
        ({"status_code": 400}, "absent"),
        ({"status_code": 499}, "absent"),
        ({"status_code": 500}, "unreadable"),
        ({"status_code": 301, "headers": {"Location": "/elsewhere/"}}, "unreadable"),
        ({"status_code": 204}, "unreadable"),
        ({"content": b"x" * 1025}, "unreadable"),
    ],
)
def test_unpublished_terms_store_nothing_and_the_run_goes_on(
    httpx_mock: HTTPXMock, tmp_path: Path, reply: dict[str, object], state: str
) -> None:
    httpx_mock.add_response(url=ROBOTS, text="User-agent: *\n")
    httpx_mock.add_response(url=TERMS, **reply)  # type: ignore[arg-type]
    httpx_mock.add_response(url=CH1, content=PAGE)
    out = prepare_out(tmp_path / "pages")
    settings = FetchSettings(max_bytes=1024, clock=_Clock().as_fetch_clock(), terms_url=TERMS)
    with httpx.Client() as client:
        run = run_fetch(client, (CH1,), out, settings)
    assert run.terms is not None
    assert (run.terms.state, run.terms.sha256) == (state, None)
    assert list(out.glob("terms-*")) == []
    assert _requested(httpx_mock) == [ROBOTS, TERMS, CH1]
    with pytest.raises(RegjeringenFetchError, match="evidence_not_published"):
        verified_source_bytes(out, run.terms)


def test_terms_robots_disallows_are_never_requested(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    httpx_mock.add_response(url=ROBOTS, text="User-agent: *\nDisallow: /no/om-nettstedet/\n")
    httpx_mock.add_response(url=CH1, content=PAGE)
    run = _run(prepare_out(tmp_path / "pages"), _Clock())
    assert run.terms is not None
    assert (run.terms.state, run.terms.status, run.terms.hops) == ("unreadable", None, ())
    assert _requested(httpx_mock) == [ROBOTS, CH1]


def test_a_terms_transport_failure_is_unreadable(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    httpx_mock.add_response(url=ROBOTS, text="User-agent: *\n")
    httpx_mock.add_exception(httpx.ConnectError("down"), url=TERMS)
    httpx_mock.add_response(url=CH1, content=PAGE)
    run = _run(prepare_out(tmp_path / "pages"), _Clock())
    assert run.terms is not None
    assert (run.terms.state, run.terms.status, run.terms.hops) == ("unreadable", None, ())


def test_no_terms_url_reads_no_terms(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    httpx_mock.add_response(url=ROBOTS, text="User-agent: *\n")
    httpx_mock.add_response(url=CH1, content=PAGE)
    run = _run(prepare_out(tmp_path / "pages"), _Clock(), terms_url=None)
    assert run.terms is None
    assert _requested(httpx_mock) == [ROBOTS, CH1]


@pytest.mark.parametrize(
    "url",
    [
        "http://www.regjeringen.no/no/vilkar/",
        "https://regjeringen.no/no/vilkar/",
        "https://www.regjeringen.no.example.org/no/vilkar/",
        "https://www.regjeringen.no:443/no/vilkar/",
        "https://www.regjeringen.no/no/vilkar/?ch=1",
        "https://www.regjeringen.no/no/vilkar/?",
        "https://www.regjeringen.no/no/vilkar/#top",
        "https://www.regjeringen.no/no/../vilkar/",
        "https://www.regjeringen.no/no/./vilkar/",
        "https://www.regjeringen.no/no//vilkar/",
        "https://www.regjeringen.no/no/vilk%C3%A5r/",
        "https://www.regjeringen.no",
    ],
)
def test_a_terms_url_outside_the_grammar_is_refused_before_any_request(
    httpx_mock: HTTPXMock, tmp_path: Path, url: str
) -> None:
    with pytest.raises(RegjeringenFetchError) as caught:
        _run(prepare_out(tmp_path / "pages"), _Clock(), terms_url=url)
    assert (caught.value.reason, caught.value.detail) == ("terms_url_not_allowed", url)
    assert httpx_mock.get_requests() == []


@pytest.mark.parametrize(
    "url", [TERMS, "https://www.regjeringen.no/", "https://www.regjeringen.no/a.b~c_d-e"]
)
def test_a_terms_url_inside_the_grammar_is_accepted(url: str) -> None:
    assert check_terms_url(url) == url


def _published(httpx_mock: HTTPXMock, tmp_path: Path) -> tuple[Path, SourceEvidence]:
    httpx_mock.add_response(url=ROBOTS, text="User-agent: *\n")
    httpx_mock.add_response(url=TERMS, content=TERMS_BODY)
    httpx_mock.add_response(url=CH1, content=PAGE)
    out = prepare_out(tmp_path / "pages")
    terms = _run(out, _Clock()).terms
    assert terms is not None
    return out, terms


def test_stored_evidence_that_changed_is_refused(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    out, terms = _published(httpx_mock, tmp_path)
    stored = out / f"terms-{terms.sha256}.html"
    stored.write_bytes(TERMS_BODY.replace(b"synthetic", b"Synthetic"))
    with pytest.raises(RegjeringenFetchError, match="evidence_mismatch"):
        verified_source_bytes(out, terms)


def test_stored_evidence_with_the_wrong_size_is_refused(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    out, terms = _published(httpx_mock, tmp_path)
    lying = terms.model_copy(update={"size": len(TERMS_BODY) + 1})
    with pytest.raises(RegjeringenFetchError, match="evidence_mismatch"):
        verified_source_bytes(out, lying)


def test_missing_stored_evidence_is_refused(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    out, terms = _published(httpx_mock, tmp_path)
    (out / f"terms-{terms.sha256}.html").unlink()
    with pytest.raises(RegjeringenFetchError, match="evidence_missing"):
        verified_source_bytes(out, terms)


@pytest.mark.parametrize("swap", ["readable", "symlink"])
def test_stored_evidence_that_is_not_private_and_regular_is_refused(
    httpx_mock: HTTPXMock, tmp_path: Path, swap: str
) -> None:
    out, terms = _published(httpx_mock, tmp_path)
    stored = out / f"terms-{terms.sha256}.html"
    if swap == "readable":
        stored.chmod(0o640)
    else:
        target = tmp_path / "elsewhere.html"
        target.write_bytes(TERMS_BODY)
        target.chmod(0o600)
        stored.unlink()
        stored.symlink_to(target)
    with pytest.raises(RegjeringenFetchError, match="evidence_not_private"):
        verified_source_bytes(out, terms)


def test_a_second_run_refuses_a_tampered_stored_copy(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    out, terms = _published(httpx_mock, tmp_path)
    (out / f"terms-{terms.sha256}.html").write_bytes(b"tampered")
    httpx_mock.add_response(url=ROBOTS, text="User-agent: *\n")
    httpx_mock.add_response(url=TERMS, content=TERMS_BODY)
    with pytest.raises(RegjeringenFetchError, match="evidence_mismatch"):
        _run(out, _Clock())


def test_a_second_run_reuses_an_intact_stored_copy(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    out, terms = _published(httpx_mock, tmp_path)
    httpx_mock.add_response(url=ROBOTS, text="User-agent: *\n")
    httpx_mock.add_response(url=TERMS, content=TERMS_BODY)
    httpx_mock.add_response(url=CH1, content=PAGE)
    clock = _Clock()
    clock.ticks = 3600.0
    again = _run(out, clock).terms
    assert again is not None
    assert again.sha256 == terms.sha256
    assert verified_source_bytes(out, again) == TERMS_BODY
