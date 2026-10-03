"""The deterministic extractor: captured bytes to a regulation or a hold (ADR-0016 S2)."""

from __future__ import annotations

import inspect
from datetime import date

import pytest
from pydantic import TypeAdapter

from lovspor.promotion import (
    ExtractedDocument,
    ExtractionHoldReason,
    ExtractionResult,
    HeldExtraction,
    PersonalDataKind,
    SourceForm,
    content_hash,
)
from lovspor.promotion.extract import EXTRACTOR_VERSION, extract_regulation
from tests.unit.promotion_fixtures import (
    REGULATION_LINES,
    SIDEBAR,
    html_page,
    minimal_docx,
    minimal_pdf,
)

HTML = "text/html; charset=utf-8"
PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _extracted(payload: bytes, content_type: str = HTML) -> ExtractedDocument:
    result = extract_regulation(payload, content_type)
    assert isinstance(result, ExtractedDocument), result
    return result


def _held(payload: bytes, content_type: str = HTML) -> HeldExtraction:
    result = extract_regulation(payload, content_type)
    assert isinstance(result, HeldExtraction), result
    return result


def test_html_page_extracts_block_body_and_fields() -> None:
    document = _extracted(html_page())
    assert document.source_form == SourceForm.HTML
    assert document.extractor_version == EXTRACTOR_VERSION
    assert document.regulation.identification_block == "\n".join(REGULATION_LINES[:2])
    assert document.regulation.body == "\n".join(REGULATION_LINES[2:])
    assert document.fields.title == "Forskrift om renovasjon og slam, Eksempel kommune"
    assert document.fields.vedtatt == date(2019, 12, 12)
    assert document.fields.vedtatt_av == "kommunestyret"
    assert document.fields.ikraft == date(2020, 1, 1)


@pytest.mark.parametrize(
    ("payload", "content_type", "form"),
    [(minimal_pdf(), PDF, SourceForm.PDF), (minimal_docx(), DOCX, SourceForm.DOCX)],
)
def test_pdf_and_docx_carry_the_same_text_as_the_page(
    payload: bytes, content_type: str, form: SourceForm
) -> None:
    document = _extracted(payload, content_type)
    assert document.source_form == form
    assert document.regulation == _extracted(html_page()).regulation


def test_extraction_is_deterministic() -> None:
    assert extract_regulation(html_page(), HTML) == extract_regulation(html_page(), HTML)
    assert extract_regulation(minimal_pdf(), PDF) == extract_regulation(minimal_pdf(), PDF)


def test_boilerplate_only_change_keeps_the_content_hash() -> None:
    """A new update stamp and a different CMS sidebar teaser are not a new version."""
    before = _extracted(html_page())
    after = _extracted(
        html_page(
            updated="Sist oppdatert 02.10.2026",
            sidebar=SIDEBAR.replace("skoleregler", "parkering"),
        )
    )
    assert html_page() != html_page(updated="Sist oppdatert 02.10.2026")
    assert content_hash(after.regulation.full_text) == content_hash(before.regulation.full_text)


def test_a_changed_regulation_text_changes_the_content_hash() -> None:
    changed = tuple(line.replace("1. januar 2020", "1. juli 2020") for line in REGULATION_LINES)
    before = _extracted(html_page())
    after = _extracted(html_page(changed))
    assert content_hash(after.regulation.full_text) != content_hash(before.regulation.full_text)


def test_the_source_url_is_not_an_input() -> None:
    """A URL saying ``forslag`` or ``høringsutkast`` cannot move the extraction (study §6.3)."""
    assert list(inspect.signature(extract_regulation).parameters) == ["payload", "content_type"]


def test_shifted_glyph_pdf_is_held_as_garbled() -> None:
    held = _held(minimal_pdf(glyph_shift=-29), PDF)
    assert (held.reason, held.source_form) == (ExtractionHoldReason.GARBLED_TEXT, SourceForm.PDF)


def test_cipher_text_without_control_characters_is_held_as_garbled() -> None:
    assert _held(minimal_pdf(glyph_shift=3), PDF).reason == ExtractionHoldReason.GARBLED_TEXT


def test_pdf_with_no_text_is_held_as_empty() -> None:
    held = _held(minimal_pdf(()), PDF)
    assert held.reason == ExtractionHoldReason.EMPTY_TEXT


def test_short_text_is_held_as_empty() -> None:
    held = _held(html_page(("Forskrift om gebyr", "§ 1 Gebyr", "Gebyret er kr 100.")))
    assert held.reason == ExtractionHoldReason.EMPTY_TEXT


def test_unsupported_media_type_is_held() -> None:
    held = _held(b"\x89PNG", "image/png")
    assert (held.reason, held.source_form) == (ExtractionHoldReason.UNSUPPORTED_FORMAT, None)


@pytest.mark.parametrize(
    ("payload", "content_type"),
    [(b"%PDF-1.4 broken", PDF), (b"not a zip", DOCX), (b"<frameset></frameset>", HTML)],
)
def test_bytes_a_reader_refuses_are_held_as_unreadable(payload: bytes, content_type: str) -> None:
    assert _held(payload, content_type).reason == ExtractionHoldReason.UNREADABLE


def test_placeholder_date_is_held() -> None:
    lines = tuple(line.replace("12.12.2019", "X.X.2019") for line in REGULATION_LINES)
    assert _held(html_page(lines)).reason == ExtractionHoldReason.PLACEHOLDER_DATE


def test_lovdata_print_is_held() -> None:
    lines = ("Utskrift fra Lovdata - 03.10.2026 12:00", *REGULATION_LINES)
    assert _held(html_page(lines)).reason == ExtractionHoldReason.LOVDATA_COPY


def test_no_first_section_is_held() -> None:
    lines = tuple(line for line in REGULATION_LINES if not line.startswith("§"))
    assert _held(html_page(lines)).reason == ExtractionHoldReason.NO_BODY


def test_no_title_is_held() -> None:
    assert _held(html_page(REGULATION_LINES[1:])).reason == ExtractionHoldReason.NO_TITLE


def test_planted_fodselsnummer_is_held_for_review_not_redacted() -> None:
    lines = (*REGULATION_LINES, "Dispensasjon er gitt til eier med fødselsnummer 01019012480.")
    held = _held(html_page(lines))
    assert held.reason == ExtractionHoldReason.PERSONAL_DATA
    assert [hit.kind for hit in held.personal_data] == [PersonalDataKind.FODSELSNUMMER]
    assert held.personal_data[0].line == len(REGULATION_LINES) + 1
    assert "01019012480" not in held.model_dump_json()


def test_contact_details_in_the_page_footer_are_chrome_not_a_hold() -> None:
    page = html_page().replace(b"</footer>", b"<p>Tlf. 912 34 567 ola@eksempel.no</p></footer>")
    assert isinstance(extract_regulation(page, HTML), ExtractedDocument)


def test_result_round_trips_through_its_discriminated_union() -> None:
    adapter: TypeAdapter[ExtractionResult] = TypeAdapter(ExtractionResult)
    for result in (extract_regulation(html_page(), HTML), extract_regulation(b"", "image/png")):
        assert adapter.validate_json(adapter.dump_json(result)) == result
