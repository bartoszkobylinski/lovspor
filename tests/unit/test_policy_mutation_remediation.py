"""Observable policy and source-evidence contracts from the mutation artifact."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.errors import RegjeringenFetchError
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_commands import (
    _preview,
    _require_no_overlap,
    _source_line,
    policy_revoke_impl,
    policy_show_impl,
    register,
)
from lovspor.forarbeider.policy_source import CurrentSource, read_source_proof
from lovspor.forarbeider.release_commands import ReleaseScope, open_release_workspace
from lovspor.forarbeider.release_models import record_text
from lovspor.forarbeider.release_store import write_record
from lovspor.regjeringen_fetch.fetch import (
    FetchSettings,
    prepare_out,
    run_fetch,
    verified_source_bytes,
)
from lovspor.regjeringen_fetch.models import SourceEvidence, evidence_file_name
from tests.unit.forarbeider_policy_support import DECIDED, OWNER, standing_policy
from tests.unit.forarbeider_release_support import private
from tests.unit.test_forarbeider_policy_source import (
    AT,
    ROBOTS_BODY,
    RUN_AT,
    TERMS,
    TERMS_BODY,
    _published,
)
from tests.unit.test_regjeringen_fetch import CH1, ROBOTS, _Clock


def test_preview_exact_lines_and_digest_width() -> None:
    policy = standing_policy()
    assert _preview(policy, "a" * 64).splitlines() == [
        "policy aaaaaaaaaaaa source regjeringen.no types prop-l,prop-ls",
        "valid 2100-04-05T00:00:00+00:00/2100-07-01T00:00:00+00:00",
        f"terms_url {policy.terms_url}",
        *[f"evidence {item.kind.value} {item.sha256[:12]}" for item in policy.evidence],
    ]


def test_register_ignores_hidden_files(tmp_path: Path) -> None:
    private(tmp_path / "policies" / ".pending", b"incomplete")
    assert register(tmp_path) == ()


def test_register_refusal_names_policies(tmp_path: Path) -> None:
    private(tmp_path / "policies", b"not a directory")
    with pytest.raises(ForarbeiderRefusedError) as caught:
        register(tmp_path)
    assert caught.value.detail == "policies"


def test_overlap_after_a_disjoint_record_is_still_refused(tmp_path: Path) -> None:
    policy = standing_policy()
    # Find a real disjoint record whose digest sorts before the overlapping one.
    overlapping = write_record(tmp_path, "policies", policy)
    for day in range(1, 29):
        other = standing_policy(
            valid_from=f"2100-08-{day:02}T00:00:00Z", valid_until="2100-09-01T00:00:00Z"
        )

        digest = hashlib.sha256(record_text(other).encode()).hexdigest()
        if digest < overlapping:
            write_record(tmp_path, "policies", other)
            break
    else:
        pytest.fail("no disjoint record sorted first")
    with pytest.raises(ForarbeiderRefusedError) as caught:
        _require_no_overlap(tmp_path, policy, "f" * 64)
    assert (caught.value.reason, caught.value.detail) == (RefusalReason.POLICY_OVERLAP, overlapping)


def test_revocation_at_decision_time_is_allowed(tmp_path: Path) -> None:
    scope = ReleaseScope(workspace=tmp_path / "workspace", ledger=tmp_path / "ledger")
    workspace = open_release_workspace(scope)
    sha = write_record(workspace, "policies", standing_policy())
    assert policy_revoke_impl(scope, sha, f"{OWNER}@{DECIDED}") == f"revoked policy {sha}"
    assert register(workspace)[0].revocation.revoked_at == datetime.fromisoformat(DECIDED)


@pytest.mark.parametrize("kind", ["unsafe", "empty", "invalid", "missing"])
def test_source_proof_refusal_names_role(tmp_path: Path, kind: str) -> None:
    path = private(tmp_path / "proof.json", b"" if kind == "empty" else b"{")
    if kind == "missing":
        path.unlink()
    if kind == "unsafe":
        link = tmp_path / "link.json"
        link.symlink_to(path)
        path = link
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_source_proof(path)
    assert caught.value.detail == "source_proof"


@pytest.mark.parametrize("published", [True, False])
def test_source_line_includes_both_pages_and_missing_digest(published: bool) -> None:
    def page(kind: str) -> SourceEvidence:
        return SourceEvidence(
            kind=kind,
            requested_url=ROBOTS,
            hops=(),
            state="published" if published else "absent",
            status=200 if published else 404,
            retrieved_at=RUN_AT,
            sha256="a" * 64 if published else None,
            size=1 if published else None,
        )

    source = CurrentSource(
        run_started_at=RUN_AT, robots=page("robots_txt"), terms=page("website_terms")
    )
    digest = "a" * 64 if published else "-"
    state = "published" if published else "absent"
    assert _source_line(source) == (
        f"source proof run {RUN_AT.isoformat()} robots_txt {state} {digest} "
        f"website_terms {state} {digest}"
    )


@pytest.mark.parametrize("changed", [False, True])
def test_show_exact_source_summary(httpx_mock: HTTPXMock, tmp_path: Path, changed: bool) -> None:
    proof = _published(httpx_mock, tmp_path / "fetch")
    scope = ReleaseScope(workspace=tmp_path / "workspace", ledger=tmp_path / "ledger")
    workspace = open_release_workspace(scope)
    policy = standing_policy()
    if not changed:
        hashes = {
            "robots_txt": hashlib.sha256(ROBOTS_BODY).hexdigest(),
            "terms_page": hashlib.sha256(TERMS_BODY).hexdigest(),
        }
        policy = policy.model_copy(
            update={
                "evidence": tuple(
                    item.model_copy(update={"sha256": hashes.get(item.kind.value, item.sha256)})
                    for item in policy.evidence
                )
            }
        )
    sha = write_record(workspace, "policies", policy)
    lines = policy_show_impl(scope, sha, AT, proof).splitlines()
    assert lines[-3] == (
        f"source proof run {RUN_AT.isoformat()} "
        f"robots_txt published {hashlib.sha256(ROBOTS_BODY).hexdigest()} "
        f"website_terms published {hashlib.sha256(TERMS_BODY).hexdigest()}"
    )
    assert lines[-2] == (
        "source faults robots_changed,terms_changed" if changed else "source matches"
    )


@pytest.mark.parametrize("status", [302, 404, 503])
def test_terms_preserve_status_and_redirect(
    httpx_mock: HTTPXMock, tmp_path: Path, status: int
) -> None:
    httpx_mock.add_response(url=ROBOTS, text="User-agent: *\n")
    httpx_mock.add_response(url=TERMS, status_code=status, headers={"Location": "/elsewhere"})
    httpx_mock.add_response(url=CH1, content=b"page")
    with httpx.Client(follow_redirects=True) as client:
        run = run_fetch(
            client,
            (CH1,),
            prepare_out(tmp_path / "out"),
            FetchSettings(terms_url=TERMS, clock=_Clock().as_fetch_clock()),
        )
    assert run.terms.status == status
    assert run.terms.hops[0].location == "/elsewhere"
    assert run.terms.state == ("absent" if status == 404 else "unreadable")


@pytest.mark.parametrize("failure", ["not_published", "missing", "not_private", "mismatch"])
def test_verified_evidence_diagnostic(tmp_path: Path, failure: str) -> None:
    evidence = SourceEvidence(
        kind="robots_txt",
        requested_url=ROBOTS,
        hops=(),
        state="absent" if failure == "not_published" else "published",
        status=404 if failure == "not_published" else 200,
        retrieved_at=datetime(2100, 1, 1, tzinfo=UTC),
        sha256=None if failure == "not_published" else hashlib.sha256(b"body").hexdigest(),
        size=None if failure == "not_published" else 4,
    )
    name = evidence_file_name(evidence)
    if failure in ("not_private", "mismatch"):
        path = private(tmp_path / name, b"body" if failure == "not_private" else b"edit")
        if failure == "not_private":
            path.chmod(0o644)
    with pytest.raises(RegjeringenFetchError) as caught:
        verified_source_bytes(tmp_path, evidence)
    assert (caught.value.reason, caught.value.detail) == (
        f"evidence_{failure}",
        evidence.state if name is None else name,
    )


def test_non_success_source_body_is_not_consumed(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    class UnreadableBody(httpx.SyncByteStream):
        def __iter__(self):
            raise httpx.ReadError("a non-success body must not be read")
            yield b""  # Make this a stream generator.

    httpx_mock.add_response(url=ROBOTS, status_code=404, stream=UnreadableBody())
    httpx_mock.add_response(url=CH1, content=b"page")
    with httpx.Client() as client:
        run = run_fetch(
            client,
            (CH1,),
            prepare_out(tmp_path / "out"),
            FetchSettings(clock=_Clock().as_fetch_clock()),
        )
    assert run.robots.state == "absent"
    assert run.robots.status == 404
    assert run.records[0].outcome == "fetched"


def test_robots_evidence_uses_latest_request_time(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    redirected = "https://www.regjeringen.no/robots-redirect.txt"
    httpx_mock.add_response(url=ROBOTS, status_code=302, headers={"Location": redirected})
    httpx_mock.add_response(url=redirected, text="User-agent: *\n")
    httpx_mock.add_response(url=CH1, content=b"page")
    clock = _Clock()
    with httpx.Client() as client:
        run = run_fetch(
            client,
            (CH1,),
            prepare_out(tmp_path / "out"),
            FetchSettings(clock=clock.as_fetch_clock()),
        )
    assert run.robots.retrieved_at == run.started_at + timedelta(seconds=5)
    assert run.robots.hops[-1].url == redirected
    assert run.records[0].retrieved_at == run.started_at + timedelta(seconds=10)
