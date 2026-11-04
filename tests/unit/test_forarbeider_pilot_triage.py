"""What a reason means for the chapter in front of the owner (#738, ADR-0018 PR4 review).

Three preflight reasons each cover something given and something not given yet:
``review_not_approved`` is a pending, held or rejected review; ``not_authorized``
is a chapter the owner's authorization leaves out; ``purpose_not_authorized``
and ``purpose_evidence_missing`` are a purpose absent or one that differs from
the owner's. The audit reads the same verified inputs the preflight read to
tell them apart, so a rejection or an exclusion never prints as waiting. The
preflight report itself, and so its digest, is the same whatever the audit says.
"""

from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider.release_models import ReleaseAuthorization
from lovspor.forarbeider.release_store import read_record, write_record
from lovspor.forarbeider.review_models import ReviewStatus
from tests.unit.forarbeider_release_support import (
    OWNER,
    PRIVATE_MARKER,
    REVIEWER,
    Desk,
    at_a_terminal,  # noqa: F401
    said_digest,
)
from tests.unit.test_forarbeider_pilot_audit import KINDS, _audit, _reasons, _verdicts

pytestmark = pytest.mark.usefixtures("at_a_terminal")


def _counts(output: str) -> str:
    line = next(line for line in output.splitlines() if line.startswith("counts "))
    return line.split(" coverage ")[0]


def _rewritten(desk: Desk, sha: str, **fields: Any) -> str:
    """A stored authorization pointed at another dossier, as preflight must re-check."""
    record = read_record(desk.workspace, "authorizations", sha, ReleaseAuthorization)
    assert record is not None
    return write_record(desk.workspace, "authorizations", record.model_copy(update=fields))


def _purpose_options(desk: Desk, third: str) -> list[str]:
    """Every gate bound to chapters 2 and 3; the purpose to 2 and, by ``third``, to 3."""
    options: list[str] = []
    for kind, path in desk.evidence.items():
        if kind != "publication_purpose":
            options += ["--evidence", f"{kind}@2,3={path}"]
    if third == "foreign":
        stranger = desk.write_evidence("publication_purpose", "-other")
        return [*options, "--evidence", f"publication_purpose@2={stranger}"]
    options += ["--evidence", f"publication_purpose@2={desk.evidence['publication_purpose']}"]
    if third == "conflicting":
        other = desk.write_evidence("publication_purpose", "-3")
        options += ["--evidence", f"publication_purpose@3={other}"]
    return options


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    ("status", "verdict"),
    [(ReviewStatus.PENDING, "HOLD"), (ReviewStatus.HELD, "HOLD"), (ReviewStatus.REJECTED, "DENY")],
)
def test_a_rejected_review_denies_where_a_pending_or_held_one_holds(
    tmp_path: Path, kind: str, status: ReviewStatus, verdict: str
) -> None:
    desk = Desk(tmp_path / kind, kind)
    desk.decide(2, ReviewStatus.APPROVED)
    if status is ReviewStatus.PENDING:
        desk.decide(3, ReviewStatus.HELD)
    desk.decide(3, status, at="2100-04-03T12:00:00Z")

    result = _audit(desk, None, [])

    assert result.exit_code == 2, result.output
    assert _verdicts(result.output) == {2: "HOLD", 3: verdict}
    assert "review_not_approved" in _reasons(result.output, 3)
    held, denied = (2, 0) if verdict == "HOLD" else (1, 1)
    assert _counts(result.output).endswith(f"audited 2 pass 0 hold {held} deny {denied}")


@pytest.mark.parametrize("kind", KINDS)
def test_a_rejection_after_an_approval_denies(tmp_path: Path, kind: str) -> None:
    desk = Desk(tmp_path / kind, kind)
    desk.approve_all()
    desk.decide(3, ReviewStatus.REJECTED, at="2100-04-03T12:00:00Z")

    result = _audit(desk, None, desk.all_checkpoints())

    assert _verdicts(result.output) == {2: "HOLD", 3: "DENY"}


@pytest.mark.parametrize("kind", KINDS)
def test_a_rejection_reopened_to_pending_holds_again(tmp_path: Path, kind: str) -> None:
    desk = Desk(tmp_path / kind, kind)
    desk.decide(3, ReviewStatus.REJECTED)
    desk.decide(3, ReviewStatus.PENDING, at="2100-04-03T12:00:00Z")

    result = _audit(desk, None, [])

    assert _verdicts(result.output)[3] == "HOLD"


@pytest.mark.parametrize("kind", KINDS)
def test_an_approved_chapter_passes_beside_a_rejected_one(tmp_path: Path, kind: str) -> None:
    desk = Desk(tmp_path / kind, kind)
    desk.approve_all()
    _, authorization = desk.released(chapters=(2,))
    desk.decide(3, ReviewStatus.REJECTED, at="2100-04-05T12:00:00Z")

    result = _audit(desk, authorization, desk.all_checkpoints())

    assert result.exit_code == 2, result.output
    assert _verdicts(result.output) == {2: "PASS", 3: "DENY"}
    assert _counts(result.output).endswith("audited 2 pass 1 hold 0 deny 1")


@pytest.mark.parametrize("kind", KINDS)
def test_a_chapter_the_owner_left_out_denies_and_the_excerpt_stays_partial(
    tmp_path: Path, kind: str
) -> None:
    desk = Desk(tmp_path / kind, kind)
    desk.approve_all()
    _, authorization = desk.released(chapters=(2,))

    result = _audit(desk, authorization, desk.all_checkpoints())

    assert result.exit_code == 2, result.output
    assert _verdicts(result.output) == {2: "PASS", 3: "DENY"}
    assert "not_authorized" in _reasons(result.output, 3)
    assert "coverage partial_curated_excerpt" in result.output
    report = desk.report(result.output)
    assert report["planned_chapters"] == [2]
    assert report["publication_authorized"] is False


@pytest.mark.parametrize("kind", KINDS)
def test_no_authorization_at_all_still_holds(tmp_path: Path, kind: str) -> None:
    desk = Desk(tmp_path / kind, kind)
    desk.approve_all()
    assert desk.dossier().exit_code == 0

    result = _audit(desk, None, desk.all_checkpoints())

    assert _verdicts(result.output) == {2: "HOLD", 3: "HOLD"}
    assert "authorization_missing" in _reasons(result.output, 2)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(("third", "verdict"), [("absent", "HOLD"), ("conflicting", "DENY")])
def test_an_absent_purpose_holds_and_a_conflicting_one_denies(
    tmp_path: Path, kind: str, third: str, verdict: str
) -> None:
    desk = Desk(tmp_path / kind, kind)
    desk.approve_all()
    _, authorization = desk.released()
    other = said_digest(desk.dossier(_purpose_options(desk, third)).output, "dossier")
    stored = _rewritten(desk, authorization, dossier_sha256=other)

    result = _audit(desk, stored, desk.all_checkpoints())

    assert result.exit_code == 2, result.output
    assert _verdicts(result.output) == {2: "PASS", 3: verdict}
    assert "purpose_not_authorized" in _reasons(result.output, 3)


@pytest.mark.parametrize("kind", KINDS)
def test_a_dossier_without_the_owners_purpose_denies_every_chapter(
    tmp_path: Path, kind: str
) -> None:
    """Chapter 3 declares no purpose, but the dossier holds one that is not the owner's."""
    desk = Desk(tmp_path / kind, kind)
    desk.approve_all()
    _, authorization = desk.released()
    other = said_digest(desk.dossier(_purpose_options(desk, "foreign")).output, "dossier")
    stored = _rewritten(desk, authorization, dossier_sha256=other)

    result = _audit(desk, stored, desk.all_checkpoints())

    assert _verdicts(result.output) == {2: "DENY", 3: "DENY"}
    assert "purpose_evidence_missing" in _reasons(result.output, 3)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("state", ["rejected", "excluded", "conflicting"])
def test_the_triage_never_changes_the_stored_report(tmp_path: Path, kind: str, state: str) -> None:
    desk = Desk(tmp_path / kind, kind)
    desk.approve_all()
    _, authorization = desk.released(chapters=(2,) if state == "excluded" else (2, 3))
    if state == "rejected":
        desk.decide(3, ReviewStatus.REJECTED, at="2100-04-05T12:00:00Z")
    if state == "conflicting":
        other = said_digest(desk.dossier(_purpose_options(desk, state)).output, "dossier")
        authorization = _rewritten(desk, authorization, dossier_sha256=other)
    preflight = desk.preflight(authorization, desk.all_checkpoints())
    sha = said_digest(preflight.output, "report")
    path = desk.workspace / "reports" / f"{sha}.json"
    before = path.read_bytes()

    audit = _audit(desk, authorization, desk.all_checkpoints())

    assert said_digest(audit.output, "report") == sha
    assert path.read_bytes() == before
    assert _verdicts(audit.output)[3] == "DENY"
    for private in (str(tmp_path), PRIVATE_MARKER, REVIEWER, OWNER):
        assert private not in audit.output
