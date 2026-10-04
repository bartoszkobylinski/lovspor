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


def _lines_of_length(length: int, *, marks: int = 0) -> tuple[str, ...]:
    """The regulation and a padding line: ``length`` characters joined, ``marks`` U+FFFD."""
    head = (*REGULATION_LINES, "�" * marks + " Merknad")
    pad = length - len("\n".join(head)) - 1
    assert pad > 0
    lines = (*head, "a" * pad)
    assert len("\n".join(lines)) == length
    return lines


def test_text_of_exactly_the_minimum_is_not_empty() -> None:
    lines = ("Forskrift om gebyr og avfall i kommunen", "a" * 160)
    assert len("\n".join(lines)) == 200
    assert _held(html_page(lines)).reason == ExtractionHoldReason.NO_BODY


def test_text_one_under_the_minimum_split_over_lines_is_empty() -> None:
    lines = ("Forskrift om gebyr og avfall i kommunen", "a" * 80, "b" * 78)
    assert len("\n".join(lines)) == 199
    assert _held(html_page(lines)).reason == ExtractionHoldReason.EMPTY_TEXT


def test_function_word_share_of_exactly_the_minimum_is_prose() -> None:
    line = " ".join(["avfall"] * 19 + ["og"])
    assert _held(html_page((line, line))).reason == ExtractionHoldReason.NO_BODY


def test_one_function_word_in_a_hundred_is_garbled() -> None:
    line = " ".join(["avfall"] * 99 + ["og"])
    held = _held(html_page((line,)))
    assert (held.reason, held.detail) == (
        ExtractionHoldReason.GARBLED_TEXT,
        "text does not read as Norwegian prose",
    )


def test_replacement_characters_over_the_share_are_garbled() -> None:
    held = _held(html_page((*REGULATION_LINES, "�" * 20)))
    assert held.reason == ExtractionHoldReason.GARBLED_TEXT


def test_unprintable_share_of_exactly_the_maximum_is_still_prose() -> None:
    lines = _lines_of_length(600, marks=6)
    assert isinstance(extract_regulation(html_page(lines), HTML), ExtractedDocument)


def test_lovdata_print_detail() -> None:
    lines = ("Utskrift fra Lovdata - 03.10.2026 12:00", *REGULATION_LINES)
    assert _held(html_page(lines)).detail == "the text is a print out of Lovdata"


def test_placeholder_date_detail() -> None:
    lines = tuple(line.replace("12.12.2019", "X.X.2019") for line in REGULATION_LINES)
    assert _held(html_page(lines)).detail == "a date is a draft's placeholder"


def test_unreadable_hold_keeps_the_form_and_the_readers_reason() -> None:
    held = _held(b"%PDF-1.4 broken", PDF)
    assert (held.reason, held.source_form) == (ExtractionHoldReason.UNREADABLE, SourceForm.PDF)
    assert held.detail.startswith("PDF did not parse: ")


def test_split_and_personal_data_holds_keep_the_form() -> None:
    no_body = tuple(line for line in REGULATION_LINES if not line.startswith("§"))
    personal = (*REGULATION_LINES, "Kontaktperson: Kari Testperson")
    for lines, reason in (
        (no_body, ExtractionHoldReason.NO_BODY),
        (personal, ExtractionHoldReason.PERSONAL_DATA),
    ):
        held = _held(minimal_docx(lines), DOCX)
        assert (held.reason, held.source_form) == (reason, SourceForm.DOCX)


WRAPPED_PDF_LINES = (
    "Forskrift om permisjon fra grunnskoleopplæringa i Eksempel",
    "kommune",
    "Fastsatt av kommunestyret i Eksempel kommune 21.06.2024 med hjemmel i lov 9. juni",
    "2023 nr. 30 om grunnskolen og den vidaregåande opplæringa (opplæringslova) § 2-2 fjerde",
    "ledd.",
    "§ 1 Formål",
    "Forskriften skal bidra til høy grad av skolenærvær og å redusere fravær for alle",
    "elever ved de kommunale grunnskolene i Eksempel kommune.",
    "§ 2 Iverksetting",
    "Forskriften trer i kraft 01.08.2024.",
)


def test_pdf_title_takes_its_wrapped_continuation() -> None:
    document = _extracted(minimal_pdf(WRAPPED_PDF_LINES), PDF)
    assert document.fields.title == (
        "Forskrift om permisjon fra grunnskoleopplæringa i Eksempel kommune"
    )
    assert document.regulation.body.split("\n")[1] == (
        "Forskriften skal bidra til høy grad av skolenærvær og å redusere fravær for alle "
        "elever ved de kommunale grunnskolene i Eksempel kommune."
    )
    assert document.fields.hjemmel == (
        "lov 9. juni 2023 nr. 30 om grunnskolen og den vidaregåande opplæringa "
        "(opplæringslova) § 2-2 fjerde ledd",
    )


def test_wrapped_pdf_extraction_is_deterministic() -> None:
    pdf = minimal_pdf(WRAPPED_PDF_LINES)
    assert extract_regulation(pdf, PDF) == extract_regulation(pdf, PDF)


def test_a_cms_feedback_widget_is_not_part_of_the_body() -> None:
    page = html_page((*REGULATION_LINES, "Fant du det du trengte?"))
    assert _extracted(page).regulation == _extracted(html_page()).regulation


def test_a_byline_still_reaches_the_regulation_text() -> None:
    page = html_page((*REGULATION_LINES, "Publisert av Ola Nordmann"))
    assert _extracted(page).regulation.body.endswith("Publisert av Ola Nordmann")
