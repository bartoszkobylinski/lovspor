"""How the pilot audit reads one preflight outcome (#738, ADR-0018 PR4).

Every deny reason is named here with the verdict it gives, so a reason added
to the preflight later fails this file until someone decides what it means;
until then it reads as DENY, never as waiting.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from lovspor.forarbeider import pilot_audit
from lovspor.forarbeider.pilot_audit import (
    WAITING,
    Firm,
    Verdict,
    audit_lines,
    chapter_url,
    verdict_of,
)
from lovspor.forarbeider.release_models import (
    ChapterOutcome,
    DenyReason,
    DocumentIdentity,
    PreflightReport,
    ReleaseGate,
)

HOLD_REASONS = {
    "authorization_missing",
    "authorization_not_yet_valid",
    "body_withheld",
    "checkpoint_missing",
    "dossier_chapter_missing",
    "dossier_missing",
    "evidence_incomplete",
    "observation_missing",
    "purpose_evidence_missing",
    "purpose_not_authorized",
    "review_missing",
    "review_not_approved",
}
DIGEST = "a" * 64
URL = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/"


def _outcome(*reasons: DenyReason, number: int = 2, **fields: Any) -> ChapterOutcome:
    values: dict[str, Any] = {
        "chapter_number": number,
        "chapter_id": f"regjeringen-9000001-ch{number}",
        "source_sha256": DIGEST,
        "text_sha256": DIGEST,
        "hold_reasons": (),
        "missing_evidence": (),
        "reasons": reasons,
        "eligible": not reasons,
    }
    return ChapterOutcome(**{**values, **fields})


def _report(*chapters: ChapterOutcome, **fields: Any) -> PreflightReport:
    eligible = tuple(item.chapter_number for item in chapters if item.eligible)
    preview = bool(eligible) and not fields.get("document_reasons")
    values: dict[str, Any] = {
        "schema_version": 1,
        "record_type": "forarbeider_release_preflight",
        "outcome": "eligible_preview" if preview else "deny",
        "publication_authorized": False,
        "writes_corpus": False,
        "asserts_complete_proposition": False,
        "legal_sufficiency_decided_by_code": False,
        "checkpoint_basis": "operator_supplied_unauthenticated",
        "evaluated_at": datetime(2100, 4, 10, tzinfo=UTC),
        "document": DocumentIdentity(
            id="regjeringen-9000001",
            document_type="prop-l",
            session="2099-2100",
            proposition_number=901,
            source_document_id=9000001,
            source_url=URL,
        ),
        "listed_chapters": (1, 2, 3, 4),
        "dossier_sha256": None,
        "authorization_sha256": None,
        "coverage": "partial_curated_excerpt" if preview else "none",
        "proposed_destination": "forarbeider/2099-2100/prop-l/9000001.md" if preview else None,
        "planned_chapters": eligible,
        "document_reasons": (),
        "chapters": chapters,
    }
    return PreflightReport(**{**values, **fields})


def test_the_waiting_reasons_are_exactly_the_named_ones() -> None:
    assert {reason.value for reason in WAITING} == HOLD_REASONS


@pytest.mark.parametrize("reason", list(DenyReason))
def test_every_deny_reason_has_one_verdict(reason: DenyReason) -> None:
    expected = Verdict.HOLD if reason.value in HOLD_REASONS else Verdict.DENY

    assert verdict_of(_outcome(reason), ()) is expected
    assert verdict_of(_outcome(), (reason,)) is expected


def test_an_eligible_chapter_passes() -> None:
    assert verdict_of(_outcome(), ()) is Verdict.PASS


@pytest.mark.parametrize("document_level", [False, True])
def test_a_future_reason_defaults_to_deny(document_level: bool) -> None:
    # Simulate an enum extension without adding that reason to the waiting list.
    future_reason = "future_preflight_failure"
    outcome = _outcome().model_copy(
        update={"eligible": False, "reasons": () if document_level else (future_reason,)}
    )
    report = _report(outcome).model_copy(
        update={"document_reasons": (future_reason,) if document_level else ()}
    )

    text, passed = audit_lines(report, DIGEST)

    assert passed is False
    assert "audited 1 pass 0 hold 0 deny 1" in text
    assert text.splitlines()[2] == (
        f"chapter 2 DENY source {DIGEST} text {DIGEST} url {URL}?ch=2 reasons {future_reason}"
    )


def test_one_wrong_reason_among_waiting_ones_denies() -> None:
    outcome = _outcome(DenyReason.REVIEW_MISSING, DenyReason.EVIDENCE_DIGEST_MISMATCH)

    assert verdict_of(outcome, ()) is Verdict.DENY
    assert verdict_of(_outcome(DenyReason.REVIEW_MISSING), (DenyReason.CHAPTER_FOREIGN,)) is (
        Verdict.DENY
    )


def test_missing_evidence_alone_holds() -> None:
    outcome = _outcome(eligible=False, missing_evidence=(ReleaseGate.PRIVACY,))

    assert verdict_of(outcome, ()) is Verdict.HOLD


def test_an_ineligible_chapter_with_no_reason_at_all_denies() -> None:
    assert verdict_of(_outcome(eligible=False), ()) is Verdict.DENY


def test_a_chapter_url_is_its_page_of_the_document() -> None:
    assert chapter_url(_report(_outcome()), 3) == f"{URL}?ch=3"


def test_the_lines_state_verdicts_counts_codes_and_no_clearance() -> None:
    report = _report(
        _outcome(),
        _outcome(DenyReason.REVIEW_MISSING, number=3, text_sha256=None),
        _outcome(DenyReason.CHAPTER_FOREIGN, number=4, source_sha256=None),
    )

    text, passed = audit_lines(report, DIGEST)

    assert passed is False
    assert text.splitlines() == [
        f"PILOT_AUDIT regjeringen-9000001 prop-l 2099-2100 report {DIGEST} "
        "publication_authorized=false release_clearance=false",
        "counts listed 4 audited 3 pass 1 hold 1 deny 1 coverage partial_curated_excerpt",
        f"chapter 2 PASS source {DIGEST} text {DIGEST} url {URL}?ch=2 reasons -",
        f"chapter 3 HOLD source {DIGEST} text - url {URL}?ch=3 reasons review_missing",
        f"chapter 4 DENY source - text {DIGEST} url {URL}?ch=4 reasons chapter_foreign",
        pilot_audit.CLEARANCE,
    ]


def test_document_reasons_and_missing_gates_are_listed_on_every_chapter_sorted() -> None:
    report = _report(
        _outcome(
            DenyReason.REVIEW_MISSING,
            eligible=False,
            missing_evidence=(ReleaseGate.SOURCE_RIGHTS, ReleaseGate.PRIVACY),
        ),
        document_reasons=(DenyReason.DOSSIER_MISSING, DenyReason.AUTHORIZATION_MISSING),
    )

    text, passed = audit_lines(report, DIGEST)

    codes = "authorization_missing,dossier_missing,missing:privacy,missing:source_rights"
    assert text.splitlines()[2].endswith(
        f"HOLD source {DIGEST} text {DIGEST} url {URL}?ch=2 reasons {codes},review_missing"
    )
    assert passed is False


def test_only_every_chapter_passing_is_a_pass() -> None:
    assert audit_lines(_report(_outcome(), _outcome(number=3)), DIGEST)[1] is True
    assert audit_lines(_report(), DIGEST)[1] is False


@pytest.mark.parametrize("document_level", [False, True])
def test_every_waiting_reason_together_still_holds(document_level: bool) -> None:
    reasons = tuple(DenyReason(value) for value in sorted(HOLD_REASONS))
    outcome = _outcome(eligible=False) if document_level else _outcome(*reasons)

    assert verdict_of(outcome, reasons if document_level else ()) is Verdict.HOLD


@pytest.mark.parametrize("count", [0, 1, 4])
def test_serialized_report_audits_every_chapter_exactly_once(count: int) -> None:
    report = _report(
        *(_outcome(DenyReason.REVIEW_MISSING, number=number) for number in range(1, count + 1))
    )
    restored = PreflightReport.model_validate_json(report.model_dump_json())

    text, passed = audit_lines(restored, DIGEST)

    chapters = [line for line in text.splitlines() if line.startswith("chapter ")]
    assert chapters == [
        f"chapter {number} HOLD source {DIGEST} text {DIGEST} "
        f"url {URL}?ch={number} reasons review_missing"
        for number in range(1, count + 1)
    ]
    assert f"audited {count} pass 0 hold {count} deny 0" in text
    assert passed is False


def test_a_waiting_reason_known_firm_denies_and_only_that_one() -> None:
    held = _outcome(DenyReason.REVIEW_NOT_APPROVED, DenyReason.CHECKPOINT_MISSING)
    firm = frozenset({DenyReason.REVIEW_NOT_APPROVED})

    assert verdict_of(held, (), firm) is Verdict.DENY
    assert verdict_of(held, ()) is Verdict.HOLD
    assert verdict_of(_outcome(DenyReason.CHECKPOINT_MISSING), (), firm) is Verdict.HOLD
    assert verdict_of(_outcome(), (DenyReason.REVIEW_NOT_APPROVED,), firm) is Verdict.DENY


def test_a_firm_reason_reaches_its_own_chapter_and_a_document_one_every_chapter() -> None:
    firm = Firm(
        document=frozenset({DenyReason.PURPOSE_EVIDENCE_MISSING}),
        chapters={3: frozenset({DenyReason.REVIEW_NOT_APPROVED})},
    )
    report = _report(
        _outcome(DenyReason.REVIEW_NOT_APPROVED),
        _outcome(DenyReason.REVIEW_NOT_APPROVED, number=3),
        _outcome(DenyReason.PURPOSE_EVIDENCE_MISSING, number=4),
    )

    text, passed = audit_lines(report, DIGEST, firm)

    assert [line.split()[2] for line in text.splitlines()[2:5]] == ["HOLD", "DENY", "DENY"]
    assert "audited 3 pass 0 hold 1 deny 2" in text
    assert passed is False
    assert firm.of(2) == {DenyReason.PURPOSE_EVIDENCE_MISSING}
    assert Firm().of(3) == frozenset()
