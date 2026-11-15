"""FC3: span-level rights and privacy candidates, apart from extraction, never a clearance."""

import hashlib
from datetime import UTC, datetime
from typing import get_args

import pytest

from lovspor.forarbeider.document_classify import (
    CLASSIFIER_VERSION,
    DocumentClassification,
    PrivacyCandidate,
    RightsCandidate,
    classify_document,
)
from lovspor.forarbeider.document_extract import SourcePage, extract_document
from lovspor.forarbeider.document_models import DocumentExtraction, Span, extraction_text
from tests.unit.test_forarbeider_document_extract import (
    HEAD,
    QUOTED,
    URL,
    chapter_page,
    page,
    section,
)

WHEN = datetime(2100, 4, 1, 12, tzinfo=UTC)


def extracted(body: str | None = None) -> DocumentExtraction:
    return extract_document([SourcePage(payload=page(body), url=URL, retrieved_at=WHEN)])


def labels(body: str | None = None) -> dict[str, tuple[RightsCandidate, PrivacyCandidate]]:
    document = extracted(body)
    text = {
        entry.id: entry.text
        for item in document.parts
        for entry in item.entries
        if isinstance(entry, Span)
    }
    return {
        text[label.entry_id]: (label.rights, label.privacy)
        for label in classify_document(document).entries
        if label.entry_id in text
    }


OFFICIAL = (RightsCandidate.OFFICIAL_TEXT_CANDIDATE, PrivacyCandidate.NO_DETECTOR_HIT)
QUOTE = (RightsCandidate.QUOTATION_REQUIRES_ASSESSMENT, PrivacyCandidate.NO_DETECTOR_HIT)


def test_quoted_spans_need_assessment_and_the_text_around_them_does_not() -> None:
    assert labels(QUOTED) == {
        "2 Bakgrunn": OFFICIAL,
        "2.1 Høringen": OFFICIAL,
        "Departementet sendte forslaget på høring.": OFFICIAL,
        "Første sitat fra en høringsinstans.": QUOTE,
        "Andre sitat, en annen instans.": QUOTE,
        "Departementet er enig.": OFFICIAL,
    }


def test_a_generic_mention_of_horing_marks_nothing() -> None:
    body = HEAD + section("<p>Høringsinstansene og høringsnotatet nevnes her.</p>")
    assert set(labels(body).values()) == {OFFICIAL}


@pytest.mark.parametrize(
    "text",
    ["Kontakt ola@example.invalid.", "Ring tlf. 12345678.", "Fødselsnummer 01019912345."],
)
def test_personal_data_patterns_require_privacy_review(text: str) -> None:
    body = HEAD + section(f"<p>{text}</p>")
    assert labels(body)[text] == (
        RightsCandidate.OFFICIAL_TEXT_CANDIDATE,
        PrivacyCandidate.PRIVACY_REVIEW_REQUIRED,
    )


def test_special_category_term_requires_privacy_review_in_that_span_only() -> None:
    body = HEAD + section("<p>Opplysninger om diagnose.</p><p>Nøytral tekst.</p>")
    assert labels(body)["Opplysninger om diagnose."][1] is PrivacyCandidate.PRIVACY_REVIEW_REQUIRED
    assert labels(body)["Nøytral tekst."] == OFFICIAL


def test_third_party_marker_makes_the_span_a_possible_third_party_work() -> None:
    body = HEAD + section("<p>Gjengitt med tillatelse fra forlaget.</p>")
    assert labels(body)["Gjengitt med tillatelse fra forlaget."][0] is (
        RightsCandidate.POSSIBLE_THIRD_PARTY
    )


def test_annex_spans_are_possible_third_party_material() -> None:
    document = extract_document([chapter_page(4, "1 Oppdiktet vedlegg")])
    in_part = {entry.id for entry in document.parts[0].entries}
    rights = {
        label.rights for label in classify_document(document).entries if label.entry_id in in_part
    }
    assert rights == {RightsCandidate.POSSIBLE_THIRD_PARTY}


def test_document_gaps_are_unresolved_on_both_axes() -> None:
    classification = classify_document(extracted())
    tail = classification.entries[-3:]
    assert [label.signals for label in tail] == [("gap:not_fetched",)] * 3
    assert {(label.rights, label.privacy) for label in tail} == {
        (RightsCandidate.UNRESOLVED, PrivacyCandidate.UNRESOLVED)
    }


def test_gaps_are_unresolved() -> None:
    body = HEAD + section("<table><tr><td>Kontakt ola@example.invalid</td></tr></table>")
    document = extracted(body)
    gap_labels = [
        label
        for label in classify_document(document).entries
        if label.entry_id.endswith("/kap2-1/1")
    ]
    assert [(label.rights, label.privacy) for label in gap_labels] == [
        (RightsCandidate.UNRESOLVED, PrivacyCandidate.PRIVACY_REVIEW_REQUIRED)
    ]


def test_spans_of_a_part_with_repaired_markup_are_unresolved() -> None:
    body = HEAD + section("<p>en<p>to")
    assert {label[0] for label in labels(body).values()} == {RightsCandidate.UNRESOLVED}


def test_every_entry_is_classified_once_and_in_order() -> None:
    document = extracted(QUOTED + section("<table><tr><td>x</td></tr></table>", "kap2-9"))
    ids = [entry.id for item in document.parts for entry in item.entries]
    ids += [gap.id for gap in document.gaps]
    assert [label.entry_id for label in classify_document(document).entries] == ids


def test_classification_names_the_extraction_it_read() -> None:
    document = extracted(QUOTED)
    classification = classify_document(document)
    assert classification.extraction_sha256 == (
        hashlib.sha256(extraction_text(document).encode()).hexdigest()
    )
    assert classification.classifier_version == CLASSIFIER_VERSION
    assert classification.document_id == document.document_id


def test_nothing_here_is_a_clearance_or_release_eligibility() -> None:
    classification = classify_document(extracted(QUOTED))
    assert classification.cleared is False
    assert classification.automatic_release_eligible is False
    assert get_args(DocumentClassification.model_fields["cleared"].annotation) == (False,)
    assert get_args(
        DocumentClassification.model_fields["automatic_release_eligible"].annotation
    ) == (False,)
    assert "cleared" not in {item.value for item in RightsCandidate}


def test_classification_is_deterministic() -> None:
    assert classify_document(extracted(QUOTED)) == classify_document(extracted(QUOTED))


@pytest.mark.parametrize(
    "markup", ['<span hidden="">Skjult tekst</span>', "<code>Kode tekst</code>"]
)
def test_problematic_inline_content_is_never_an_official_text_candidate(markup: str) -> None:
    body = HEAD + section(f"<p>Departementet skriver {markup} her.</p>")
    document = extracted(body)
    entries = {entry.id: entry for item in document.parts for entry in item.entries}
    by_text = {
        entries[label.entry_id].text: label
        for label in classify_document(document).entries
        if label.entry_id in entries
    }
    inner = "Skjult tekst" if "hidden" in markup else "Kode tekst"
    assert by_text[inner].rights is RightsCandidate.UNRESOLVED
    assert by_text[inner].signals[0].startswith("gap:")
    assert by_text["Departementet skriver"].rights is RightsCandidate.OFFICIAL_TEXT_CANDIDATE
    assert all(
        inner not in entries[label.entry_id].text
        for label in classify_document(document).entries
        if label.entry_id in entries and label.rights is RightsCandidate.OFFICIAL_TEXT_CANDIDATE
    )


def test_inline_gap_privacy_is_read_from_its_own_text() -> None:
    body = HEAD + section('<p>Nøytral <span hidden="">ola@example.invalid</span> tekst.</p>')
    document = extracted(body)
    gap = next(e for e in document.parts[0].entries if not isinstance(e, Span))
    label = next(item for item in classify_document(document).entries if item.entry_id == gap.id)
    assert (label.rights, label.privacy) == (
        RightsCandidate.UNRESOLVED,
        PrivacyCandidate.PRIVACY_REVIEW_REQUIRED,
    )
    assert label.signals == ("gap:hidden_content", "personal_data_pattern")


def test_hidden_ancestor_of_the_region_leaves_every_span_unresolved() -> None:
    raw = page().replace(b'<div class="contentArea', b'<div hidden="" class="contentArea')
    document = extract_document([SourcePage(payload=raw, url=URL, retrieved_at=WHEN)])
    codes = {(e.code.value, e.scope) for e in document.parts[0].entries if not isinstance(e, Span)}
    assert ("hidden_content", "part") in codes
    in_part = {e.id for e in document.parts[0].entries if isinstance(e, Span)}
    rights = {
        item.rights for item in classify_document(document).entries if item.entry_id in in_part
    }
    assert rights == {RightsCandidate.UNRESOLVED}
