"""A fetch run record must tell a story the fetcher could have produced (#738 review).

``check_source_proof`` is integrity of the record's own semantics, not
authentication: anyone who can write the owner's private staging directory can
write a consistent record. What it refuses is a record no ``forarbeider-fetch``
run writes — a page under the wrong kind, a robots.txt from another URL, a
redirect chain that does not chain, a state its status contradicts.
"""

import json
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.errors import RegjeringenFetchError
from lovspor.regjeringen_fetch.fetch import (
    FetchSettings,
    check_source_proof,
    prepare_out,
    run_fetch,
)
from lovspor.regjeringen_fetch.models import FetchRun
from tests.unit.test_regjeringen_fetch import CH1, PAGE, ROBOTS, _Clock

TERMS = "https://www.regjeringen.no/no/om-nettstedet/vilkar/id0000001/"
MOVED = "https://www.regjeringen.no/robots-moved.txt"
Doc = dict[str, Any]


def _ordinary(httpx_mock: HTTPXMock, tmp_path: Path) -> Doc:
    """A real run: robots behind one same-host redirect, terms published."""
    httpx_mock.add_response(url=ROBOTS, status_code=301, headers={"Location": "/robots-moved.txt"})
    httpx_mock.add_response(url=MOVED, content=b"User-agent: *\n")
    httpx_mock.add_response(url=TERMS, content=b"<html>terms</html>")
    httpx_mock.add_response(url=CH1, content=PAGE)
    settings = FetchSettings(clock=_Clock().as_fetch_clock(), terms_url=TERMS)
    with httpx.Client() as client:
        run = run_fetch(client, (CH1,), prepare_out(tmp_path / "pages"), settings)
    loaded: Doc = json.loads(run.model_dump_json())
    return loaded


def _check(doc: Doc) -> None:
    check_source_proof(FetchRun.model_validate(doc))


def test_an_ordinary_run_is_consistent(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    _check(_ordinary(httpx_mock, tmp_path))


@pytest.mark.parametrize(
    ("terms", "state"),
    [
        ({"status_code": 404}, "absent"),
        ({"status_code": 429}, "refused"),
        ({"status_code": 500}, "unreadable"),
        ({"status_code": 302, "headers": {"Location": "/x/"}}, "unreadable"),
        ({"content": b"x" * 2048}, "unreadable"),
    ],
)
def test_every_terms_answer_the_fetcher_writes_is_consistent(
    httpx_mock: HTTPXMock, tmp_path: Path, terms: Doc, state: str
) -> None:
    httpx_mock.add_response(url=ROBOTS, status_code=404)
    httpx_mock.add_response(url=TERMS, **terms)
    if state != "refused":
        httpx_mock.add_response(url=CH1, content=PAGE)
    settings = FetchSettings(max_bytes=1024, clock=_Clock().as_fetch_clock(), terms_url=TERMS)
    with httpx.Client() as client:
        run = run_fetch(client, (CH1,), prepare_out(tmp_path / "pages"), settings)
    assert run.terms is not None
    assert run.terms.state == state
    check_source_proof(run)


def test_disallowed_or_unreachable_terms_without_hops_are_consistent(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    httpx_mock.add_response(url=ROBOTS, text="User-agent: *\nDisallow: /no/om-nettstedet/\n")
    httpx_mock.add_response(url=CH1, content=PAGE)
    settings = FetchSettings(clock=_Clock().as_fetch_clock(), terms_url=TERMS)
    with httpx.Client() as client:
        run = run_fetch(client, (CH1,), prepare_out(tmp_path / "pages"), settings)
    check_source_proof(run)


def _robots(doc: Doc) -> Doc:
    robots: Doc = doc["robots"]
    return robots


def _terms(doc: Doc) -> Doc:
    terms: Doc = doc["terms"]
    return terms


FORGERIES: dict[str, Callable[[Doc], None]] = {
    "robots_as_terms": lambda d: _robots(d).update(kind="website_terms"),
    "terms_as_robots": lambda d: _terms(d).update(kind="robots_txt"),
    "robots_from_elsewhere": lambda d: _robots(d).update(
        requested_url="https://www.regjeringen.no/other.txt"
    ),
    "robots_first_hop_elsewhere": lambda d: _robots(d)["hops"][0].update(
        url="https://www.regjeringen.no/other.txt"
    ),
    "robots_without_hops": lambda d: _robots(d).update(hops=[]),
    "robots_chain_broken": lambda d: _robots(d)["hops"][1].update(
        url="https://www.regjeringen.no/elsewhere.txt"
    ),
    "robots_chain_off_host": lambda d: (
        _robots(d)["hops"][0].update(location="https://example.org/robots.txt"),
        _robots(d)["hops"][1].update(url="https://example.org/robots.txt"),
    ),
    "robots_hop_not_a_redirect": lambda d: _robots(d)["hops"][0].update(status=200),
    "robots_redirect_without_location": lambda d: _robots(d)["hops"][0].update(location=None),
    "robots_last_hop_disagrees": lambda d: _robots(d)["hops"][1].update(status=404),
    "robots_too_many_hops": lambda d: _robots(d).update(
        hops=[
            {
                "url": ROBOTS if n == 0 else f"https://www.regjeringen.no/r{n}",
                "status": 301,
                "location": f"/r{n + 1}",
            }
            for n in range(6)
        ]
        + [{"url": "https://www.regjeringen.no/r6", "status": 200, "location": None}]
    ),
    "robots_refused_but_recorded": lambda d: _robots(d).update(
        state="refused",
        status=429,
        sha256=None,
        size=None,
        hops=[{"url": ROBOTS, "status": 429, "location": None}],
    ),
    "robots_published_with_404": lambda d: (
        _robots(d).update(status=404),
        _robots(d)["hops"][1].update(status=404),
    ),
    "robots_absent_with_403": lambda d: _robots(d).update(
        state="absent",
        status=403,
        sha256=None,
        size=None,
        hops=[{"url": ROBOTS, "status": 403, "location": None}],
    ),
    "robots_absent_with_200": lambda d: _robots(d).update(
        state="absent",
        status=200,
        sha256=None,
        size=None,
        hops=[{"url": ROBOTS, "status": 200, "location": None}],
    ),
    "terms_outside_grammar": lambda d: (
        _terms(d).update(requested_url="https://example.org/terms"),
        _terms(d)["hops"][0].update(url="https://example.org/terms"),
    ),
    "terms_hop_elsewhere": lambda d: _terms(d)["hops"][0].update(
        url="https://www.regjeringen.no/no/other/"
    ),
    "terms_two_hops": lambda d: _terms(d).update(hops=[*_terms(d)["hops"], *_terms(d)["hops"]]),
    "terms_published_without_hops": lambda d: _terms(d).update(hops=[]),
    "terms_hop_status_disagrees": lambda d: _terms(d)["hops"][0].update(status=203),
    "terms_published_with_500": lambda d: (
        _terms(d).update(status=500),
        _terms(d)["hops"][0].update(status=500),
    ),
    "terms_refused_with_404": lambda d: _terms(d).update(
        state="refused",
        status=404,
        sha256=None,
        size=None,
        hops=[{"url": TERMS, "status": 404, "location": None}],
    ),
    "terms_unreadable_with_404": lambda d: _terms(d).update(
        state="unreadable",
        status=404,
        sha256=None,
        size=None,
        hops=[{"url": TERMS, "status": 404, "location": None}],
    ),
    "terms_unreadable_with_403": lambda d: _terms(d).update(
        state="unreadable",
        status=403,
        sha256=None,
        size=None,
        hops=[{"url": TERMS, "status": 403, "location": None}],
    ),
    "terms_absent_without_hops": lambda d: _terms(d).update(
        state="absent", status=None, sha256=None, size=None, hops=[]
    ),
    "terms_before_robots": lambda d: _terms(d).update(retrieved_at="2026-11-09T11:00:00Z"),
    "robots_before_start": lambda d: _robots(d).update(retrieved_at="2026-11-09T11:00:00Z"),
    "another_client": lambda d: d.update(user_agent="someone-else/1"),
}


DETAILS = {
    "robots_as_terms": "robots_identity",
    "terms_as_robots": "terms_identity",
    "robots_from_elsewhere": "robots_identity",
    "robots_first_hop_elsewhere": "robots_hops",
    "robots_without_hops": "robots_hops",
    "robots_chain_broken": "robots_hops",
    "robots_chain_off_host": "robots_hops",
    "robots_hop_not_a_redirect": "robots_hops",
    "robots_redirect_without_location": "robots_hops",
    "robots_last_hop_disagrees": "robots_hops",
    "robots_too_many_hops": "robots_hops",
    "robots_refused_but_recorded": "robots_identity",
    "robots_published_with_404": "robots_state",
    "robots_absent_with_403": "robots_state",
    "robots_absent_with_200": "robots_state",
    "terms_outside_grammar": "terms_identity",
    "terms_hop_elsewhere": "terms_hops",
    "terms_two_hops": "terms_hops",
    "terms_published_without_hops": "terms_hops",
    "terms_hop_status_disagrees": "terms_hops",
    "terms_published_with_500": "terms_state",
    "terms_refused_with_404": "terms_state",
    "terms_unreadable_with_404": "terms_state",
    "terms_unreadable_with_403": "terms_state",
    "terms_absent_without_hops": "terms_hops",
    "terms_before_robots": "terms_before_robots",
    "robots_before_start": "robots_before_start",
    "another_client": "user_agent",
}


@pytest.mark.parametrize("forgery", sorted(FORGERIES))
def test_a_record_no_fetch_run_writes_is_refused(
    httpx_mock: HTTPXMock, tmp_path: Path, forgery: str
) -> None:
    doc = _ordinary(httpx_mock, tmp_path)
    FORGERIES[forgery](doc)
    with pytest.raises(RegjeringenFetchError) as caught:
        _check(doc)
    assert (caught.value.reason, caught.value.detail) == (
        "source_proof_inconsistent",
        DETAILS[forgery],
    )


def test_every_forgery_names_its_fault() -> None:
    assert sorted(DETAILS) == sorted(FORGERIES)


def test_five_followed_robots_redirects_are_consistent(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    hops = [ROBOTS] + [f"https://www.regjeringen.no/r{n}.txt" for n in range(1, 6)]
    for here, there in pairwise(hops):
        httpx_mock.add_response(url=here, status_code=302, headers={"Location": there})
    httpx_mock.add_response(url=hops[-1], text="User-agent: *\n")
    httpx_mock.add_response(url=CH1, content=PAGE)
    with httpx.Client() as client:
        run = run_fetch(
            client,
            (CH1,),
            prepare_out(tmp_path / "pages"),
            FetchSettings(clock=_Clock().as_fetch_clock()),
        )
    assert len(run.robots.hops) == 6
    check_source_proof(run)
