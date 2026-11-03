"""Decision order and the authorized purpose (review #5738285220 on PR #765).

P1: for every planned chapter the audit chain must run forwards in time:
its latest PR3A approval <= the owner's decision <= ``--at``, and the dossier's
review <= the owner's decision. Times are compared as instants, so an offset
never changes the order, and equal instants are allowed.

P2: the one purpose the owner authorized must itself be evidence bound to
every chapter it plans. A purpose bound to chapter 2 does not clear chapter 3.

``authorize`` refuses a scope that breaks either rule, so an authorization
breaking one is reachable only as a record an earlier build wrote. Those cases
write such a record with the release store, then check that preflight denies it.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider.release_models import ReleaseAuthorization, digest
from lovspor.forarbeider.release_store import read_record, write_record
from lovspor.forarbeider.review_models import ReviewStatus
from tests.unit.cli_output import said
from tests.unit.forarbeider_release_support import (
    Desk,
    at_a_terminal,  # noqa: F401
    chapter_reasons,
    said_digest,
)

pytestmark = pytest.mark.usefixtures("at_a_terminal")


def _stored(desk: Desk, sha: str) -> ReleaseAuthorization:
    record = read_record(desk.workspace, "authorizations", sha, ReleaseAuthorization)
    assert record is not None
    return record


def _rewritten(desk: Desk, sha: str, **fields: Any) -> str:
    """An authorization as an earlier build could have stored it: canonical, with other fields."""
    record = _stored(desk, sha).model_copy(update=fields)
    return write_record(desk.workspace, "authorizations", record)


def _authorizations(desk: Desk) -> list[Path]:
    return sorted((desk.workspace / "authorizations").glob("*.json"))


# P1: recording an authorization


def test_an_owner_decision_before_the_dossier_review_is_refused(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    desk.dossier_at = "2100-04-05T12:00:00Z"
    dossier = said_digest(desk.dossier().output, "dossier")

    result = desk.authorize(dossier)

    assert result.exit_code == 1, result.output
    assert said("refused: release_out_of_order: dossier", result.output)
    assert _authorizations(desk) == []


def test_an_owner_decision_before_an_approval_is_refused(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.decide(2, ReviewStatus.APPROVED)
    desk.decide(3, ReviewStatus.APPROVED, "2100-04-04T12:00:01Z")
    dossier = said_digest(desk.dossier().output, "dossier")

    result = desk.authorize(dossier)

    assert result.exit_code == 1, result.output
    assert said(f"refused: release_out_of_order: {desk.results[3].id}", result.output)
    assert _authorizations(desk) == []


def test_chapters_approved_on_different_days_are_authorized_after_the_last(
    tmp_path: Path,
) -> None:
    desk = Desk(tmp_path)
    desk.decide(2, ReviewStatus.APPROVED, "2100-04-01T13:00:00Z")
    desk.decide(3, ReviewStatus.APPROVED, "2100-04-03T18:00:00Z")
    _, authorization = desk.released()

    report = desk.report(desk.preflight(authorization, desk.all_checkpoints()).output)

    assert report["outcome"] == "eligible_preview"
    assert report["planned_chapters"] == [2, 3]


def test_equal_instants_in_other_offsets_are_in_order(tmp_path: Path) -> None:
    """Approval, dossier review and owner decision at one instant, in three spellings."""
    desk = Desk(tmp_path)
    desk.decide(2, ReviewStatus.APPROVED, "2100-04-04T14:00:00+02:00")
    desk.decide(3, ReviewStatus.APPROVED, "2100-04-04T12:00:00Z")
    desk.dossier_at = "2100-04-04T07:00:00-05:00"
    _, authorization = desk.released()

    at = "2100-04-04T13:00:00+01:00"
    report = desk.report(desk.preflight(authorization, desk.all_checkpoints(), at=at).output)

    assert report["outcome"] == "eligible_preview"
    assert report["planned_chapters"] == [2, 3]


def test_order_is_by_instant_not_by_the_text_of_the_timestamp(tmp_path: Path) -> None:
    """``12:00+02:00`` sorts after ``11:00Z`` as text but is an hour earlier."""
    desk = Desk(tmp_path)
    desk.decide(2, ReviewStatus.APPROVED, "2100-04-04T11:00:00Z")
    desk.decide(3, ReviewStatus.APPROVED, "2100-04-02T12:00:00Z")
    desk.decided = "2100-04-04T12:00:00+02:00"
    dossier = said_digest(desk.dossier().output, "dossier")

    result = desk.authorize(dossier, valid="2100-04-04T10:00:00Z/2100-05-01T00:00:00Z")

    assert result.exit_code == 1, result.output
    assert said(f"refused: release_out_of_order: {desk.results[2].id}", result.output)


# P1: an authorization already stored out of order


def test_a_stored_owner_decision_before_the_dossier_is_denied(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    early = _rewritten(desk, authorization, decided_at=datetime.fromisoformat("2100-04-02T18:00Z"))

    result = desk.preflight(early, desk.all_checkpoints())

    assert result.exit_code == 2, result.output
    report = desk.report(result.output)
    assert report["outcome"] == "deny"
    assert "authorized_before_dossier" in report["document_reasons"]
    assert report["planned_chapters"] == []


def test_a_stored_owner_decision_before_an_approval_is_denied(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.decide(2, ReviewStatus.APPROVED, "2100-04-01T13:00:00Z")
    desk.decide(3, ReviewStatus.APPROVED, "2100-04-02T12:00:00Z")
    desk.dossier_at = "2100-04-01T14:00:00Z"
    _, authorization = desk.released()
    early = _rewritten(desk, authorization, decided_at=datetime.fromisoformat("2100-04-01T18:00Z"))

    report = desk.report(desk.preflight(early, desk.all_checkpoints()).output)

    assert report["document_reasons"] == []
    assert chapter_reasons(report, 2) == []
    assert chapter_reasons(report, 3) == ["authorized_before_approval"]
    assert report["planned_chapters"] == [2]


def test_a_preflight_before_the_approval_it_relies_on_is_denied(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.decide(2, ReviewStatus.APPROVED, "2100-04-01T13:00:00Z")
    desk.decide(3, ReviewStatus.APPROVED, "2100-04-02T12:00:00Z")
    desk.dossier_at = "2100-04-01T14:00:00Z"
    _, authorization = desk.released()
    early = _rewritten(
        desk,
        authorization,
        decided_at=datetime.fromisoformat("2100-04-01T18:00Z"),
        valid_from=datetime.fromisoformat("2100-04-01T18:00Z"),
    )

    at = "2100-04-01T20:00:00Z"
    report = desk.report(desk.preflight(early, desk.all_checkpoints(), at=at).output)

    assert report["document_reasons"] == []
    assert set(chapter_reasons(report, 3)) == {
        "authorized_before_approval",
        "evaluated_before_approval",
    }
    assert report["planned_chapters"] == [2]


def test_a_stale_reapproval_is_out_of_order_as_well_as_superseded(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    desk.decide(3, ReviewStatus.HELD, "2100-04-05T12:00:00Z")
    desk.decide(3, ReviewStatus.APPROVED, "2100-04-06T12:00:00Z")

    report = desk.report(desk.preflight(authorization, desk.all_checkpoints()).output)

    assert chapter_reasons(report, 3) == ["authorized_before_approval", "review_superseded"]
    assert report["planned_chapters"] == [2]


def test_a_held_review_after_the_owner_decision_is_not_an_order_fault(tmp_path: Path) -> None:
    """Only an approval is ordered against the owner; a later hold is already a denial."""
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    desk.decide(3, ReviewStatus.HELD, "2100-04-05T12:00:00Z")

    report = desk.report(desk.preflight(authorization, desk.all_checkpoints()).output)

    assert chapter_reasons(report, 3) == ["review_not_approved", "review_superseded"]


# P2: one authorized purpose, bound to every planned chapter


def _split_purposes(desk: Desk) -> tuple[list[str], Path, Path]:
    """Every gate bound to chapters 2 and 3, except the purpose: A for 2, B for 3."""
    options: list[str] = []
    for kind, path in desk.evidence.items():
        if kind != "publication_purpose":
            options += ["--evidence", f"{kind}@2,3={path}"]
    first = desk.evidence["publication_purpose"]
    second = desk.write_evidence("publication_purpose", "-3")
    options += ["--evidence", f"publication_purpose@2={first}"]
    options += ["--evidence", f"publication_purpose@3={second}"]
    return options, first, second


def test_two_chapters_with_different_purposes_cannot_be_authorized_together(
    tmp_path: Path,
) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    options, _, _ = _split_purposes(desk)
    dossier = said_digest(desk.dossier(options).output, "dossier")

    result = desk.authorize(dossier)

    assert result.exit_code == 1, result.output
    assert said("refused: release_not_eligible: publication_purpose", result.output)
    assert _authorizations(desk) == []


def test_two_purposes_each_covering_every_chapter_are_ambiguous(tmp_path: Path) -> None:
    """v1 holds one purpose; the first of two is not chosen for the owner."""
    desk = Desk(tmp_path)
    desk.approve_all()
    second = desk.write_evidence("publication_purpose", "-other")
    options = [*desk.evidence_options(), "--evidence", f"publication_purpose@2,3={second}"]
    dossier = said_digest(desk.dossier(options).output, "dossier")

    result = desk.authorize(dossier)

    assert result.exit_code == 1, result.output
    assert said("refused: release_not_eligible: publication_purpose", result.output)


def test_a_purpose_bound_to_only_one_chapter_cannot_authorize_both(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    options: list[str] = []
    for kind, path in desk.evidence.items():
        chapters = "2" if kind == "publication_purpose" else "2,3"
        options += ["--evidence", f"{kind}@{chapters}={path}"]
    dossier = said_digest(desk.dossier(options).output, "dossier")

    result = desk.authorize(dossier)

    assert result.exit_code == 1, result.output
    assert said("refused: release_not_eligible: publication_purpose", result.output)


def test_one_purpose_bound_to_both_chapters_is_the_one_authorized(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    purpose = desk.evidence["publication_purpose"].read_bytes()

    record = _stored(desk, authorization)
    report = desk.report(desk.preflight(authorization, desk.all_checkpoints()).output)

    assert record.purpose_sha256 == digest(purpose)
    assert report["planned_chapters"] == [2, 3]


def test_a_stored_purpose_for_chapter_2_does_not_clear_chapter_3(tmp_path: Path) -> None:
    """The review's repro: the owner authorized A, chapter 3 holds only B."""
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    options, _, _ = _split_purposes(desk)
    split = said_digest(desk.dossier(options).output, "dossier")
    stored = _rewritten(desk, authorization, dossier_sha256=split)

    result = desk.preflight(stored, desk.all_checkpoints())

    report = desk.report(result.output)
    assert report["document_reasons"] == []
    assert chapter_reasons(report, 2) == []
    assert chapter_reasons(report, 3) == ["purpose_not_authorized"]
    assert report["planned_chapters"] == [2]
    assert report["coverage"] == "partial_curated_excerpt"
    assert report["publication_authorized"] is False


@pytest.mark.parametrize("deleted", [False, True], ids=["changed", "deleted"])
def test_a_changed_or_deleted_authorized_purpose_denies_every_chapter(
    tmp_path: Path, deleted: bool
) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    purpose = desk.evidence["publication_purpose"]
    if deleted:
        purpose.unlink()
    else:
        purpose.write_bytes(b"another purpose statement")

    result = desk.preflight(authorization, desk.all_checkpoints())

    assert result.exit_code == 2, result.output
    report = desk.report(result.output)
    assert "purpose_evidence_missing" in report["document_reasons"]
    for number in (2, 3):
        assert "purpose_not_authorized" in chapter_reasons(report, number)
    assert report["planned_chapters"] == []
