"""What a review decision can state (#738, ADR-0018 PR3A)."""

import hashlib
import json
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider.review_models import (
    ALL_ATTESTATIONS,
    Attestation,
    AttestationKind,
    ReviewDecision,
    ReviewReason,
    ReviewStatus,
    ReviewSubject,
    may_follow,
    review_text,
    subject_key,
    subject_of,
)
from tests.unit.forarbeider_review_support import (
    MARKER,
    chapter,
    with_changed_source,
    with_changed_text,
    withheld,
)

EVIDENCE = "sha256:" + "a" * 64


def attestations() -> list[Attestation]:
    return [
        Attestation(kind=kind, attested=True, evidence=("adr:0018",)) for kind in AttestationKind
    ]


def fields(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "subject": subject_of(chapter()),
        "revision": 1,
        "previous_decision_sha256": None,
        "reviewer": "reviewer-1",
        "decided_at": "2100-04-02T12:00:00Z",
        "status": ReviewStatus.HELD,
        "reason_codes": (ReviewReason.AWAITING_EVIDENCE,),
        "evidence": ("issue:738",),
        "attestations": (),
        "grants_publication": False,
        "grants_crawling": False,
    }
    return base | changes


def approved(**changes: Any) -> dict[str, Any]:
    return (
        fields(
            status=ReviewStatus.APPROVED,
            reason_codes=(ReviewReason.ATTESTATIONS_COMPLETE,),
            evidence=("adr:0018",),
            attestations=tuple(attestations()),
        )
        | changes
    )


def test_a_held_decision_validates() -> None:
    decision = ReviewDecision(**fields())
    assert decision.status is ReviewStatus.HELD


def test_time_is_stored_in_utc() -> None:
    decision = ReviewDecision(**fields(decided_at="2100-04-02T14:00:00+02:00"))
    assert decision.decided_at.isoformat() == "2100-04-02T12:00:00+00:00"


@pytest.mark.parametrize("value", ["2100-04-02T12:00:00", "not a time"])
def test_a_time_without_an_offset_is_invalid(value: str) -> None:
    with pytest.raises(ValidationError):
        ReviewDecision(**fields(decided_at=value))


@pytest.mark.parametrize(
    "reviewer",
    ["", "Ola Nordmann", "ola@example.no", "-leading", "UPPER", "a" * 40, "ola\n", None],
)
def test_a_reviewer_is_a_handle_never_a_name_or_address(reviewer: str | None) -> None:
    with pytest.raises(ValidationError):
        ReviewDecision(**fields(reviewer=reviewer))


@pytest.mark.parametrize("reviewer", ["r", "reviewer-1", "a" * 39])
def test_valid_handles(reviewer: str) -> None:
    assert ReviewDecision(**fields(reviewer=reviewer)).reviewer == reviewer


def test_evidence_is_required() -> None:
    with pytest.raises(ValidationError):
        ReviewDecision(**fields(evidence=()))


@pytest.mark.parametrize(
    "reference",
    [
        "note:Ola Nordmann",
        "free text",
        "https://example.no/x",
        "adr:18",
        "issue:0",
        "pr:01",
        "sha256:abc",
        "sha256:" + "A" * 64,
        "issue:12345678",
        "issue:1\n",
    ],
)
def test_evidence_cannot_carry_free_text(reference: str) -> None:
    with pytest.raises(ValidationError):
        ReviewDecision(**fields(evidence=(reference,)))


@pytest.mark.parametrize("reference", ["adr:0018", "issue:1", "pr:752", EVIDENCE])
def test_evidence_grammar(reference: str) -> None:
    assert ReviewDecision(**fields(evidence=(reference,))).evidence == (reference,)


@pytest.mark.parametrize("name", ["evidence", "reason_codes"])
def test_lists_are_sorted_and_single(name: str) -> None:
    values = {
        "evidence": (("issue:9", "adr:0018"), ("adr:0018", "adr:0018")),
        "reason_codes": (
            (ReviewReason.THIRD_PARTY_WORK, ReviewReason.AWAITING_EVIDENCE),
            (ReviewReason.AWAITING_EVIDENCE, ReviewReason.AWAITING_EVIDENCE),
        ),
    }
    for value in values[name]:
        with pytest.raises(ValidationError):
            ReviewDecision(**fields(**{name: value}))


def test_reasons_are_required() -> None:
    with pytest.raises(ValidationError):
        ReviewDecision(**fields(reason_codes=()))


@pytest.mark.parametrize("name", ["grants_publication", "grants_crawling"])
def test_no_decision_can_grant_publication_or_crawling(name: str) -> None:
    with pytest.raises(ValidationError):
        ReviewDecision(**approved(**{name: True}))


def test_unknown_fields_are_refused() -> None:
    with pytest.raises(ValidationError):
        ReviewDecision(**fields(rationale=MARKER))


def test_a_complete_approval_validates() -> None:
    assert ReviewDecision(**approved()).status is ReviewStatus.APPROVED


def test_all_four_attestations_are_named() -> None:
    assert {kind.value for kind in ALL_ATTESTATIONS} == {
        "privacy_basis",
        "source_completeness",
        "source_rights_policy",
        "third_party_checks",
    }


@pytest.mark.parametrize("missing", range(4))
def test_an_approval_short_of_one_attestation_is_invalid(missing: int) -> None:
    partial = attestations()
    del partial[missing]
    with pytest.raises(ValidationError):
        ReviewDecision(**approved(attestations=tuple(partial)))


def test_an_attestation_repeated_is_invalid() -> None:
    doubled = attestations()
    doubled[1] = doubled[0]
    with pytest.raises(ValidationError):
        ReviewDecision(**approved(attestations=tuple(doubled)))


def test_a_negative_attestation_cannot_exist() -> None:
    with pytest.raises(ValidationError):
        Attestation(kind=AttestationKind.PRIVACY_BASIS, attested=False, evidence=("adr:0018",))


def test_an_attestation_without_evidence_cannot_exist() -> None:
    with pytest.raises(ValidationError):
        Attestation(kind=AttestationKind.PRIVACY_BASIS, attested=True, evidence=())


def test_attestations_belong_only_to_an_approval() -> None:
    with pytest.raises(ValidationError):
        ReviewDecision(**fields(attestations=tuple(attestations())))


def test_an_approval_needs_its_own_reason() -> None:
    with pytest.raises(ValidationError):
        ReviewDecision(**approved(reason_codes=(ReviewReason.AWAITING_EVIDENCE,)))


def test_no_other_status_may_use_the_approval_reason() -> None:
    with pytest.raises(ValidationError):
        ReviewDecision(**fields(reason_codes=(ReviewReason.ATTESTATIONS_COMPLETE,)))


def test_a_chapter_without_a_body_cannot_be_approved() -> None:
    with pytest.raises(ValidationError):
        ReviewDecision(**approved(subject=subject_of(withheld(chapter()))))


def test_a_chapter_without_a_body_can_be_held_or_rejected() -> None:
    subject = subject_of(withheld(chapter()))
    assert ReviewDecision(**fields(subject=subject)).subject.text_sha256 is None


def test_first_revision_has_no_predecessor_and_later_ones_do() -> None:
    with pytest.raises(ValidationError):
        ReviewDecision(**fields(previous_decision_sha256="a" * 64))
    with pytest.raises(ValidationError):
        ReviewDecision(**fields(revision=2))
    assert ReviewDecision(**fields(revision=2, previous_decision_sha256="a" * 64)).revision == 2


def test_subject_binds_every_identifying_value() -> None:
    result = chapter()
    subject = subject_of(result)
    assert subject.source_page_url == result.source_page_url
    assert subject.source_sha256 == result.source_sha256
    assert subject.chapter_number == result.chapter_number
    assert subject.text_sha256 == result.text_sha256
    assert subject.extractor_version == result.extractor_version
    assert subject.normalizer_version == result.normalizer_version


def test_subject_refuses_a_chapter_its_url_does_not_state() -> None:
    values = subject_of(chapter()).model_dump() | {"chapter_number": 3}
    with pytest.raises(ValidationError):
        ReviewSubject(**values)


def test_subject_refuses_a_foreign_url() -> None:
    values = subject_of(chapter()).model_dump() | {"source_page_url": "https://example.no/?ch=2"}
    with pytest.raises(ValidationError):
        ReviewSubject(**values)


def test_subject_names_its_chapter() -> None:
    assert subject_of(chapter()).chapter_id == "regjeringen-9000001-ch2"


def test_a_change_of_text_or_source_is_another_subject() -> None:
    result = chapter()
    keys = {
        subject_key(subject_of(result)),
        subject_key(subject_of(with_changed_text(result))),
        subject_key(subject_of(with_changed_source(result))),
    }
    assert len(keys) == 3


def test_the_subject_key_is_stable_and_pinned() -> None:
    assert subject_key(subject_of(chapter())) == subject_key(subject_of(chapter()))
    subject = subject_of(chapter())
    canonical = (
        "{\n"
        '  "chapter_number": 2,\n'
        '  "extractor_version": 1,\n'
        '  "normalizer_version": 1,\n'
        f'  "source_page_url": "{subject.source_page_url}",\n'
        f'  "source_sha256": "{subject.source_sha256}",\n'
        f'  "text_sha256": "{subject.text_sha256}"\n'
        "}"
    )
    assert subject_key(subject) == hashlib.sha256(canonical.encode()).hexdigest()


def test_text_is_deterministic_canonical_and_holds_no_chapter_text() -> None:
    result = with_changed_text(chapter())
    text = review_text(ReviewDecision(**fields(subject=subject_of(result))))
    assert text == review_text(ReviewDecision(**fields(subject=subject_of(result))))
    assert text.endswith("}\n")
    assert list(json.loads(text)) == sorted(json.loads(text))
    assert MARKER not in text


@pytest.mark.parametrize(
    ("current", "following", "allowed"),
    [
        ("pending", "held", True),
        ("pending", "rejected", True),
        ("pending", "approved", True),
        ("pending", "pending", False),
        ("held", "pending", True),
        ("held", "held", True),
        ("held", "rejected", True),
        ("held", "approved", True),
        ("rejected", "pending", True),
        ("rejected", "held", False),
        ("rejected", "rejected", False),
        ("rejected", "approved", False),
        ("approved", "pending", True),
        ("approved", "held", True),
        ("approved", "rejected", True),
        ("approved", "approved", False),
    ],
)
def test_status_transitions(current: str, following: str, allowed: bool) -> None:
    assert may_follow(ReviewStatus(current), ReviewStatus(following)) is allowed
