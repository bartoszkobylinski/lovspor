"""A standing policy clears only against a current source proof (#738, Amendment 1 point 8).

Local evidence that still hashes to its signed digest proves what was signed,
never what regjeringen.no publishes now. The proof is a fetch run whose stored
robots.txt and terms bytes re-hash to their record, at most 24 hours old.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.forarbeider import release_commands
from lovspor.forarbeider.policy_source import (
    CurrentSource,
    SourceFault,
    source_faults,
)
from lovspor.regjeringen_fetch.fetch import FetchClock, FetchSettings, prepare_out, run_fetch
from lovspor.regjeringen_fetch.models import FetchRun, SourceEvidence, evidence_file_name
from tests.unit.cli_output import said
from tests.unit.forarbeider_policy_support import PolicyDesk, shown_phrase, standing_policy

ROBOTS = "https://www.regjeringen.no/robots.txt"
TERMS = "https://www.regjeringen.no/no/om-nettstedet/vilkar/id0000001/"
CH1 = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=1"
ROBOTS_BODY = b"User-agent: *\nDisallow: /sok\n"
TERMS_BODY = b"<html><body>synthetic terms</body></html>"
RUN_AT = datetime(2100, 4, 5, 12, 0, tzinfo=UTC)
AT = "2100-04-05T18:00:00Z"


@pytest.fixture(autouse=True)
def _terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_commands, "_interactive", lambda: True)


def _fetch(
    httpx_mock: HTTPXMock, out: Path, robots: dict[str, Any], terms: dict[str, Any] | None
) -> Path:
    """One real fetch run, offline: its run-record path."""
    httpx_mock.add_response(url=ROBOTS, **robots)
    if terms is not None:
        httpx_mock.add_response(url=TERMS, **terms)
    httpx_mock.add_response(url=CH1, content=b"<html>page</html>")
    clock = FetchClock(now=lambda: RUN_AT, monotonic=lambda: 0.0, sleep=lambda _: None)
    settings = FetchSettings(clock=clock, terms_url=None if terms is None else TERMS)
    with httpx.Client() as client:
        run_fetch(client, (CH1,), prepare_out(out), settings)
    (record,) = sorted(out.glob("fetch-run-*.json"))
    return record


def _stored(run_file: Path, evidence: SourceEvidence | None) -> Path:
    assert evidence is not None
    return run_file.parent / str(evidence_file_name(evidence))


def _desk_bound_to(tmp_path: Path, run_file: Path) -> PolicyDesk:
    """A desk whose robots and terms evidence are the bytes the fetch stored."""
    run = FetchRun.model_validate_json(run_file.read_bytes())
    desk = PolicyDesk(tmp_path / "desk")
    desk.evidence["robots_txt"] = _stored(run_file, run.robots)
    desk.evidence["terms_page"] = _stored(run_file, run.terms)
    return desk


def _show(desk: PolicyDesk, sha: str, proof: Path | None, at: str = AT) -> Any:
    extra = [] if proof is None else ["--source-proof", str(proof)]
    return desk.invoke(["show", sha, "--at", at, *extra])


def _published(httpx_mock: HTTPXMock, out: Path, terms: bytes = TERMS_BODY) -> Path:
    return _fetch(httpx_mock, out, {"content": ROBOTS_BODY}, {"content": terms})


def test_a_matching_current_proof_allows_automatic_clearance(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    run_file = _published(httpx_mock, tmp_path / "fetch")
    desk = _desk_bound_to(tmp_path, run_file)
    result = _show(desk, desk.signed(), run_file)
    assert result.exit_code == 0, result.output
    assert said("state usable", result.output)
    assert said("source matches", result.output)
    assert said("automatic clearance allowed", result.output)


def test_no_current_proof_denies_even_with_intact_local_evidence(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    run_file = _published(httpx_mock, tmp_path / "fetch")
    desk = _desk_bound_to(tmp_path, run_file)
    result = _show(desk, desk.signed(), None)
    assert result.exit_code == 0, result.output
    assert said("state usable", result.output)
    assert said("source proof missing", result.output)
    assert said("source faults source_proof_missing", result.output)
    assert said("automatic clearance denied", result.output)
    assert not said("automatic clearance allowed", result.output)


def test_changed_terms_deny_although_the_signed_copy_is_intact(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    signed_run = _published(httpx_mock, tmp_path / "signed")
    desk = _desk_bound_to(tmp_path, signed_run)
    sha = desk.signed()
    later = _published(httpx_mock, tmp_path / "later", TERMS_BODY.replace(b"terms", b"Terms"))
    result = _show(desk, sha, later)
    assert said("state usable", result.output)
    assert said("source faults terms_changed", result.output)
    assert said("automatic clearance denied", result.output)


def test_changed_robots_deny(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    desk = _desk_bound_to(tmp_path, _published(httpx_mock, tmp_path / "signed"))
    sha = desk.signed()
    robots = {"content": ROBOTS_BODY + b"Disallow: /ny\n"}
    later = _fetch(httpx_mock, tmp_path / "later", robots, {"content": TERMS_BODY})
    result = _show(desk, sha, later)
    assert said("source faults robots_changed", result.output)
    assert said("automatic clearance denied", result.output)


@pytest.mark.parametrize(
    ("robots", "terms", "fault"),
    [
        ({"status_code": 404}, {"content": TERMS_BODY}, "robots_not_published"),
        ({"content": ROBOTS_BODY}, {"status_code": 404}, "terms_not_published"),
        ({"content": ROBOTS_BODY}, {"status_code": 503}, "terms_not_published"),
        ({"content": ROBOTS_BODY}, None, "terms_not_published"),
    ],
)
def test_an_absent_or_unreadable_source_page_denies(
    httpx_mock: HTTPXMock,
    tmp_path: Path,
    robots: dict[str, Any],
    terms: dict[str, Any] | None,
    fault: str,
) -> None:
    desk = _desk_bound_to(tmp_path, _published(httpx_mock, tmp_path / "signed"))
    sha = desk.signed()
    later = _fetch(httpx_mock, tmp_path / "later", robots, terms)
    result = _show(desk, sha, later)
    assert result.exit_code == 0, result.output
    assert said(fault, result.output)
    assert said("automatic clearance denied", result.output)


@pytest.mark.parametrize("at", ["2100-04-06T12:00:01Z", "2100-04-05T11:59:59Z"])
def test_a_proof_older_than_a_day_or_from_after_the_moment_denies(
    httpx_mock: HTTPXMock, tmp_path: Path, at: str
) -> None:
    run_file = _published(httpx_mock, tmp_path / "fetch")
    desk = _desk_bound_to(tmp_path, run_file)
    result = _show(desk, desk.signed(), run_file, at)
    assert said("source faults source_proof_stale", result.output)
    assert said("automatic clearance denied", result.output)


def test_a_proof_exactly_a_day_old_still_holds(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    run_file = _published(httpx_mock, tmp_path / "fetch")
    desk = _desk_bound_to(tmp_path, run_file)
    result = _show(desk, desk.signed(), run_file, "2100-04-06T12:00:00Z")
    assert said("automatic clearance allowed", result.output)


def test_tampered_stored_proof_bytes_refuse(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    signed_run = _published(httpx_mock, tmp_path / "signed")
    desk = _desk_bound_to(tmp_path, signed_run)
    sha = desk.signed()
    later = _published(httpx_mock, tmp_path / "later")
    run = FetchRun.model_validate_json(later.read_bytes())
    _stored(later, run.terms).write_bytes(b"<html>edited</html>")
    result = _show(desk, sha, later)
    assert result.exit_code == 1
    assert said("source_proof", result.output)
    assert not said("automatic clearance allowed", result.output)


def test_a_malformed_proof_record_refuses(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    run_file = _published(httpx_mock, tmp_path / "fetch")
    desk = _desk_bound_to(tmp_path, run_file)
    sha = desk.signed()
    run_file.write_bytes(b'{"schema_version": 1}')
    result = _show(desk, sha, run_file)
    assert result.exit_code == 1
    assert said("source_proof", result.output)


@pytest.mark.parametrize("page_kind", ["robots", "terms"])
@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        ("missing", "evidence_missing"),
        ("readable", "evidence_not_private"),
        ("symlink", "evidence_not_private"),
        ("same_size_edit", "evidence_mismatch"),
    ],
)
def test_show_refuses_unsafe_or_corrupt_pages_in_current_source_proof(
    httpx_mock: HTTPXMock, tmp_path: Path, page_kind: str, damage: str, reason: str
) -> None:
    """Operations' current-source-proof contract requires both stored pages verified."""
    signed_run = _published(httpx_mock, tmp_path / "signed")
    desk = _desk_bound_to(tmp_path, signed_run)
    sha = desk.signed()
    later = _published(httpx_mock, tmp_path / "later")
    run = FetchRun.model_validate_json(later.read_bytes())
    page = run.robots if page_kind == "robots" else run.terms
    stored = _stored(later, page)
    original = stored.read_bytes()
    if damage == "missing":
        stored.unlink()
    elif damage == "readable":
        stored.chmod(0o640)
    elif damage == "symlink":
        target = stored.with_suffix(".backup")
        stored.rename(target)
        stored.symlink_to(target)
    else:
        stored.write_bytes(bytes([original[0] ^ 1]) + original[1:])

    before = {path: path.read_bytes() for path in desk.workspace.rglob("*.json")}
    signed_before = {path: path.read_bytes() for path in desk.evidence.values()}
    proof_before = later.read_bytes()
    result = _show(desk, sha, later)

    assert result.exit_code == 1, result.output
    assert said(f"release_record_corrupt: source_proof {reason}", result.output)
    assert not said("automatic clearance allowed", result.output)
    assert {path: path.read_bytes() for path in desk.workspace.rglob("*.json")} == before
    assert later.read_bytes() == proof_before
    assert {path: path.read_bytes() for path in desk.evidence.values()} == signed_before


@pytest.mark.parametrize("problem", ["missing", "readable"])
def test_a_missing_or_unsafe_proof_record_refuses(
    httpx_mock: HTTPXMock, tmp_path: Path, problem: str
) -> None:
    run_file = _published(httpx_mock, tmp_path / "fetch")
    desk = _desk_bound_to(tmp_path, run_file)
    sha = desk.signed()
    if problem == "missing":
        run_file.unlink()
    else:
        run_file.chmod(0o644)
    result = _show(desk, sha, run_file)
    assert result.exit_code == 1
    assert said("source_proof", result.output)
    assert not said("automatic clearance", result.output)


def _source(robots_sha: str | None, terms_sha: str | None, at: datetime) -> CurrentSource:
    def page(kind: str, sha: str | None) -> SourceEvidence:
        state = "absent" if sha is None else "published"
        return SourceEvidence.model_validate(
            {
                "kind": kind,
                "requested_url": TERMS if kind == "website_terms" else ROBOTS,
                "hops": (),
                "state": state,
                "status": 404 if sha is None else 200,
                "retrieved_at": at,
                "sha256": sha,
                "size": None if sha is None else 1,
            }
        )

    return CurrentSource(
        run_started_at=at,
        robots=page("robots_txt", robots_sha),
        terms=page("website_terms", terms_sha),
    )


def test_source_faults_compare_each_page_with_its_own_signed_digest() -> None:
    policy = standing_policy()
    signed = {item.kind.value: item.sha256 for item in policy.evidence}
    moment = datetime(2100, 4, 5, 12, 0, tzinfo=UTC)
    clean = _source(signed["robots_txt"], signed["terms_page"], moment)
    assert source_faults(policy, clean, moment + timedelta(hours=24)) == ()
    swapped = _source(signed["terms_page"], signed["robots_txt"], moment)
    assert source_faults(policy, swapped, moment) == (
        SourceFault.ROBOTS_CHANGED,
        SourceFault.TERMS_CHANGED,
    )
    assert source_faults(policy, None, moment) == (SourceFault.SOURCE_PROOF_MISSING,)
    late = moment + timedelta(hours=24, microseconds=1)
    assert source_faults(policy, clean, late) == (SourceFault.SOURCE_PROOF_STALE,)


def test_the_oldest_page_sets_the_proof_age() -> None:
    policy = standing_policy()
    signed = {item.kind.value: item.sha256 for item in policy.evidence}
    moment = datetime(2100, 4, 5, 12, 0, tzinfo=UTC)
    source = _source(signed["robots_txt"], signed["terms_page"], moment)
    newer_terms = source.model_copy(
        update={
            "terms": source.terms.model_copy(update={"retrieved_at": moment + timedelta(hours=2)})
        }  # type: ignore[union-attr]
    )
    assert source_faults(policy, newer_terms, moment + timedelta(hours=1)) == (
        SourceFault.SOURCE_PROOF_STALE,
    )
    assert source_faults(policy, newer_terms, moment + timedelta(hours=24, seconds=1)) == (
        SourceFault.SOURCE_PROOF_STALE,
    )
    assert source_faults(policy, newer_terms, moment + timedelta(hours=3)) == ()


OTHER_TERMS = "https://www.regjeringen.no/no/arkiv/vilkar-2020/id0000002/"


def _forged(run_file: Path, change: Any) -> None:
    doc = json.loads(run_file.read_text())
    change(doc)
    run_file.write_text(json.dumps(doc))


@pytest.mark.parametrize(
    ("change", "fault"),
    [
        (lambda d: d["terms"].update(kind="robots_txt"), "terms_identity"),
        (lambda d: d["robots"].update(requested_url=TERMS), "robots_identity"),
        (lambda d: d["robots"]["hops"][0].update(status=503), "robots_hops"),
        (lambda d: d["terms"]["hops"][0].update(url=OTHER_TERMS), "terms_hops"),
        (lambda d: d.update(user_agent="someone-else/1"), "user_agent"),
    ],
)
def test_a_proof_no_fetch_run_writes_is_refused(
    httpx_mock: HTTPXMock, tmp_path: Path, change: Any, fault: str
) -> None:
    run_file = _published(httpx_mock, tmp_path / "fetch")
    desk = _desk_bound_to(tmp_path, run_file)
    sha = desk.signed()
    _forged(run_file, change)
    result = _show(desk, sha, run_file)
    assert result.exit_code == 1
    assert said(f"source_proof source_proof_inconsistent: {fault}", result.output)
    assert not said("automatic clearance", result.output)


def test_the_same_terms_bytes_from_another_url_deny(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    signed_run = _published(httpx_mock, tmp_path / "signed")
    desk = _desk_bound_to(tmp_path, signed_run)
    sha = desk.signed()
    httpx_mock.add_response(url=ROBOTS, content=ROBOTS_BODY)
    httpx_mock.add_response(url=OTHER_TERMS, content=TERMS_BODY)
    httpx_mock.add_response(url=CH1, content=b"<html>page</html>")
    clock = FetchClock(now=lambda: RUN_AT, monotonic=lambda: 0.0, sleep=lambda _: None)
    out = prepare_out(tmp_path / "elsewhere")
    with httpx.Client() as client:
        run_fetch(client, (CH1,), out, FetchSettings(clock=clock, terms_url=OTHER_TERMS))
    (later,) = out.glob("fetch-run-*.json")
    result = _show(desk, sha, later)
    assert said("source faults terms_url_changed", result.output)
    assert said("automatic clearance denied", result.output)


def test_the_policy_binds_and_shows_the_terms_url(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    desk = _desk_bound_to(tmp_path, _published(httpx_mock, tmp_path / "fetch"))
    shown = desk.shown()
    assert said(f"terms_url {TERMS}", shown.output)
    sha = desk.signed()
    assert desk.record(sha)["terms_url"] == TERMS
    other = desk.sign_args(
        None, "2100-04-04T12:00:00Z", "2100-04-04T12:00:00Z/2100-07-01T00:00:00Z"
    )
    other[other.index("--terms-url") + 1] = OTHER_TERMS
    assert shown_phrase(desk.invoke(other, "\n").output) != shown_phrase(shown.output)


@pytest.mark.parametrize("url", ["https://example.org/terms", "https://www.regjeringen.no/x?y"])
def test_a_terms_url_outside_the_grammar_signs_nothing(
    httpx_mock: HTTPXMock, tmp_path: Path, url: str
) -> None:
    desk = _desk_bound_to(tmp_path, _published(httpx_mock, tmp_path / "fetch"))
    desk.terms_url = url
    result = desk.shown()
    assert result.exit_code == 1
    assert said("release_invalid", result.output)
    assert not (desk.workspace / "policies").exists()
