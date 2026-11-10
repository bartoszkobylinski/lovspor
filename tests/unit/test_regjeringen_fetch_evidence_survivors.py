"""Observable policy evidence and verification diagnostics for listed survivors."""

from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.errors import RegjeringenFetchError
from lovspor.regjeringen_fetch.fetch import (
    FetchSettings,
    prepare_out,
    run_fetch,
    verified_source_bytes,
)
from tests.unit.test_regjeringen_fetch import CH1, PAGE, ROBOTS, _Clock
from tests.unit.test_regjeringen_fetch_policy_streams import _InterruptedPolicy
from tests.unit.test_regjeringen_fetch_terms import TERMS, _published


@pytest.mark.parametrize("status", [301, 404, 500])
def test_terms_preserve_status_and_redirect_evidence(
    httpx_mock: HTTPXMock, tmp_path: Path, status: int
) -> None:
    httpx_mock.add_response(url=ROBOTS, text="User-agent: *\n")
    httpx_mock.add_response(url=TERMS, status_code=status, headers={"Location": "/elsewhere/"})
    httpx_mock.add_response(url=CH1, content=PAGE)
    clock = _Clock()
    out = prepare_out(tmp_path / "pages")
    with httpx.Client(follow_redirects=True) as client:
        run = run_fetch(
            client, (CH1,), out, FetchSettings(clock=clock.as_fetch_clock(), terms_url=TERMS)
        )
    assert run.robots.retrieved_at == run.started_at
    assert run.terms is not None
    assert run.terms.status == status
    assert run.terms.hops[0].location == "/elsewhere/"
    assert [str(r.url) for r in httpx_mock.get_requests()] == [ROBOTS, TERMS, CH1]
    (sidecar,) = out.glob("fetch-run-*.json")
    assert type(run).model_validate_json(sidecar.read_bytes()) == run


def test_non_success_policy_body_is_never_consumed(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    httpx_mock.add_response(url=ROBOTS, status_code=404, stream=_InterruptedPolicy())
    httpx_mock.add_response(url=CH1, content=PAGE)
    with httpx.Client() as client:
        run = run_fetch(
            client,
            (CH1,),
            prepare_out(tmp_path / "pages"),
            FetchSettings(clock=_Clock().as_fetch_clock()),
        )
    assert (run.robots.state, run.robots.status) == ("absent", 404)
    assert run.records[0].sha256 is not None


@pytest.mark.parametrize("damage", ["unpublished", "missing", "readable", "mismatch"])
def test_evidence_verification_reports_exact_reason_and_source(
    httpx_mock: HTTPXMock, tmp_path: Path, damage: str
) -> None:
    out, evidence = _published(httpx_mock, tmp_path)
    name = f"terms-{evidence.sha256}.html"
    stored = out / name
    reason = {
        "unpublished": "evidence_not_published",
        "missing": "evidence_missing",
        "readable": "evidence_not_private",
        "mismatch": "evidence_mismatch",
    }[damage]
    if damage == "unpublished":
        evidence = evidence.model_copy(update={"state": "absent", "sha256": None, "size": None})
    elif damage == "missing":
        stored.unlink()
    elif damage == "readable":
        stored.chmod(0o640)
    else:
        stored.write_bytes(b"tampered")
    with pytest.raises(RegjeringenFetchError) as caught:
        verified_source_bytes(out, evidence)
    assert (caught.value.reason, caught.value.detail) == (
        reason,
        "absent" if damage == "unpublished" else name,
    )
