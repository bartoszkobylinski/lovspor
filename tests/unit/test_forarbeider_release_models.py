"""The PR3B-1 records refuse every shape that could state more than was checked."""

from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider.release_models import (
    ALL_CONDITIONS,
    ChapterIdentity,
    ChapterOutcome,
    DenyReason,
    DocumentIdentity,
    EvidenceArtifact,
    PreflightReport,
    ReleaseAuthorization,
    ReleaseDossier,
    ReleaseGate,
    ReviewPin,
    chapter_identity,
    destination_of,
    document_of,
)
from tests.unit.forarbeider_review_support import chapter, withheld

URL = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/"
DIGEST = "a" * 64


def document() -> DocumentIdentity:
    return document_of(chapter())


def identity(number: int = 2) -> ChapterIdentity:
    return ChapterIdentity(
        chapter_id=f"regjeringen-9000001-ch{number}",
        chapter_number=number,
        source_page_url=f"{URL}?ch={number}",
        source_sha256=DIGEST,
        text_sha256=DIGEST,
        extractor_version=1,
        normalizer_version=1,
    )


def artifact(**changes: Any) -> dict[str, Any]:
    fields = {"kind": "source_rights", "path": "/private/e.txt", "sha256": DIGEST}
    return {**fields, "size_bytes": 1, "chapters": (2,), **changes}


def dossier(**changes: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "record_type": "forarbeider_release_dossier",
        "document": document(),
        "chapters": (identity(2), identity(3)),
        "evidence": (artifact(),),
        "reviewer": "reviewer-a",
        "reviewed_at": "2100-04-03T12:00:00+02:00",
        "legal_sufficiency_decided_by_code": False,
        **changes,
    }


def authorization(**changes: Any) -> dict[str, Any]:
    pin = ReviewPin(subject_key=DIGEST, revision=1, head_sha256=DIGEST)
    return {
        "schema_version": 1,
        "record_type": "forarbeider_release_authorization_candidate",
        "document": document(),
        "chapters": ({"chapter": identity(2), "review": pin},),
        "dossier_sha256": DIGEST,
        "purpose_sha256": DIGEST,
        "destination": destination_of(document()),
        "owner": "owner-b",
        "decided_at": "2100-04-04T12:00:00Z",
        "valid_from": "2100-04-04T12:00:00Z",
        "valid_until": "2100-05-01T00:00:00Z",
        "revocation_conditions": ALL_CONDITIONS,
        "grants_publication": False,
        "identity_authenticated": False,
        **changes,
    }


def outcome(**changes: Any) -> ChapterOutcome:
    fields: dict[str, Any] = {
        "chapter_number": 2,
        "chapter_id": "regjeringen-9000001-ch2",
        "source_sha256": DIGEST,
        "text_sha256": DIGEST,
        "hold_reasons": ("review_required",),
        "missing_evidence": (),
        "reasons": (),
        "eligible": True,
        **changes,
    }
    return ChapterOutcome(**fields)


def report(**changes: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "record_type": "forarbeider_release_preflight",
        "outcome": "eligible_preview",
        "publication_authorized": False,
        "writes_corpus": False,
        "asserts_complete_proposition": False,
        "legal_sufficiency_decided_by_code": False,
        "checkpoint_basis": "operator_supplied_unauthenticated",
        "evaluated_at": "2100-04-10T12:00:00Z",
        "document": document(),
        "listed_chapters": (1, 2, 3, 4),
        "dossier_sha256": DIGEST,
        "authorization_sha256": DIGEST,
        "coverage": "partial_curated_excerpt",
        "proposed_destination": destination_of(document()),
        "planned_chapters": (2,),
        "document_reasons": (),
        "chapters": (outcome(),),
        **changes,
    }


def test_document_and_destination_come_from_the_record() -> None:
    stated = document()
    assert stated.id == "regjeringen-9000001"
    assert stated.document_type == "prop-l"
    assert destination_of(stated) == "forarbeider/2099-2100/prop-l/9000001.md"


def test_a_chapter_without_a_body_has_no_identity_to_release() -> None:
    assert chapter_identity(withheld(chapter())) is None
    stated = chapter_identity(chapter())
    assert stated is not None
    assert stated.text_sha256 == chapter().text_sha256
    assert stated.chapter_id == "regjeringen-9000001-ch2"


@pytest.mark.parametrize(
    "changes",
    [
        {"chapter_id": "regjeringen-9000001-ch3"},
        {"chapter_number": 3},
        {"source_page_url": "https://example.org/?ch=2"},
        {"text_sha256": None},
    ],
)
def test_a_chapter_identity_states_one_chapter(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ChapterIdentity(**{**identity().model_dump(), **changes})


def test_a_dossier_is_stored_in_utc() -> None:
    assert ReleaseDossier(**dossier()).reviewed_at.isoformat() == "2100-04-03T10:00:00+00:00"


@pytest.mark.parametrize(
    "changes",
    [
        {"chapters": (identity(3), identity(2))},
        {"chapters": (identity(2), identity(2))},
        {"evidence": (artifact(chapters=(4,)),)},
        {"evidence": (artifact(), artifact())},
        {"evidence": (artifact(kind="source_rights"), artifact(kind="publication_purpose"))},
        {"evidence": ()},
        {"legal_sufficiency_decided_by_code": True},
        {"reviewer": "Ola Nordmann"},
        {"document": DocumentIdentity(**{**document().model_dump(), "id": "regjeringen-9000002"})},
    ],
)
def test_a_dossier_refuses_unbound_or_unordered_content(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ReleaseDossier(**dossier(**changes))


def test_a_chapter_of_another_document_is_refused() -> None:
    other = {**document().model_dump(), "source_url": URL.replace("9000001", "9000002")}
    with pytest.raises(ValidationError, match="another document"):
        ReleaseDossier(**dossier(document=DocumentIdentity(**other)))


@pytest.mark.parametrize(
    "changes",
    [
        {"chapters": (4, 2)},
        {"chapters": (2, 2)},
        {"chapters": ()},
        {"path": "relative/e.txt"},
        {"size_bytes": 0},
        {"sha256": "adr:0018"},
    ],
)
def test_evidence_is_a_file_bound_to_chapters(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        EvidenceArtifact(**artifact(**changes))


def test_an_authorization_states_utc_and_grants_nothing() -> None:
    stated = ReleaseAuthorization(**authorization(valid_from="2100-04-04T14:00:00+02:00"))
    assert stated.valid_from.isoformat() == "2100-04-04T12:00:00+00:00"
    assert stated.grants_publication is False
    assert stated.identity_authenticated is False


@pytest.mark.parametrize(
    "changes",
    [
        {"valid_until": "2100-04-04T12:00:00Z"},
        {"valid_until": "2100-04-01T12:00:00Z"},
        {"destination": "manifest.json"},
        {"destination": "forarbeider/2099-2100/prop-l/9000002.md"},
        {"revocation_conditions": ALL_CONDITIONS[1:]},
        {"grants_publication": True},
        {"identity_authenticated": True},
        {"chapters": ()},
    ],
)
def test_an_authorization_is_scoped_and_bounded(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ReleaseAuthorization(**authorization(**changes))


def test_an_eligible_chapter_has_nothing_against_it() -> None:
    with pytest.raises(ValidationError):
        outcome(reasons=(DenyReason.REVIEW_MISSING,))
    with pytest.raises(ValidationError):
        outcome(missing_evidence=(ReleaseGate.PRIVACY,))
    assert outcome(eligible=False, reasons=(DenyReason.REVIEW_MISSING,)).eligible is False


def test_a_preview_report_validates() -> None:
    stated = PreflightReport(**report(evaluated_at="2100-04-10T14:00:00+02:00"))
    assert stated.evaluated_at.isoformat() == "2100-04-10T12:00:00+00:00"


@pytest.mark.parametrize(
    "changes",
    [
        {"publication_authorized": True},
        {"writes_corpus": True},
        {"asserts_complete_proposition": True},
        {"planned_chapters": ()},
        {"planned_chapters": (3,)},
        {"outcome": "deny"},
        {"proposed_destination": None},
        {"document_reasons": (DenyReason.DOSSIER_MISSING,)},
        {"coverage": "none"},
    ],
)
def test_a_report_cannot_overstate(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PreflightReport(**report(**changes))


def test_a_deny_report_proposes_nothing() -> None:
    denied = outcome(eligible=False, reasons=(DenyReason.REVIEW_MISSING,))
    fields = report(
        outcome="deny",
        coverage="none",
        proposed_destination=None,
        planned_chapters=(),
        chapters=(denied,),
    )
    assert PreflightReport(**fields).outcome == "deny"
    with pytest.raises(ValidationError):
        PreflightReport(**{**fields, "proposed_destination": destination_of(document())})
    with pytest.raises(ValidationError):
        PreflightReport(**{**fields, "coverage": "partial_curated_excerpt"})
