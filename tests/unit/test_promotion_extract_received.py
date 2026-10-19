"""The received channel's extractor: Lovdata's markup removed, the id minted first (#632).

ADR-0016 A1-3: a Lovdata print or a Lovtidend avd. II PDF an authority sends
in answer to an innsynskrav is extracted without Lovdata's markup; the
observed channel still holds the same bytes. A1-4: ``vedtatt_av`` is the
issuer the text names, a state body's included. Every line is synthetic.
"""

from __future__ import annotations

import pytest

from lovspor.promotion import (
    Authority,
    AuthorityType,
    ExtractedDocument,
    ExtractionHoldReason,
    HeldExtraction,
    HeldIdentity,
    HoldReason,
    MintedIdentity,
    mint_identity,
)
from lovspor.promotion.extract import extract_received, extract_regulation
from lovspor.promotion.models import ReceivedForm
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page, minimal_docx, minimal_pdf
from tests.unit.test_promotion_lovdata_markup import (
    BODY,
    DOCX_HEAD,
    LOVDATA_PRINT,
    LOVTIDEND,
    PREAMBLE,
    PRINT_HEAD,
    TITLE,
)

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
HTML = "text/html; charset=utf-8"
EKSEMPEL = Authority(id="4646", type=AuthorityType.KOMMUNE, name="Eksempel", klass_version="2024")


def _received(payload: bytes, content_type: str = PDF) -> ExtractedDocument:
    result = extract_received(payload, content_type)
    assert isinstance(result, ExtractedDocument), result
    return result


def _held(payload: bytes, content_type: str = PDF) -> HeldExtraction:
    result = extract_received(payload, content_type)
    assert isinstance(result, HeldExtraction), result
    return result


def _doc_id(extracted: ExtractedDocument) -> str:
    identity = mint_identity(extracted.regulation, EKSEMPEL)
    assert isinstance(identity, MintedIdentity), identity
    return identity.doc_id


class TestLovdataPrint:
    def test_the_observed_channel_still_holds_it(self) -> None:
        held = extract_regulation(minimal_pdf(LOVDATA_PRINT), PDF)
        assert isinstance(held, HeldExtraction)
        assert held.reason is ExtractionHoldReason.LOVDATA_COPY

    def test_the_received_channel_extracts_it_as_a_lovdata_print(self) -> None:
        extracted = _received(minimal_pdf(LOVDATA_PRINT))
        assert extracted.received_form is ReceivedForm.LOVDATA_PRINT

    @pytest.mark.parametrize(
        "markup",
        ["Utskrift fra Lovdata", "Dato", "Korttittel", "Innholdsfortegnelse", "Endret ved"],
    )
    def test_no_markup_is_in_the_text(self, markup: str) -> None:
        assert markup not in _received(minimal_pdf(LOVDATA_PRINT)).regulation.full_text

    def test_the_text_is_the_regulation_alone(self) -> None:
        regulation = _received(minimal_pdf(LOVDATA_PRINT)).regulation
        assert regulation.identification_block == f"{TITLE}\n{PREAMBLE}"
        assert regulation.body.split("\n")[:2] == list(BODY[:2])

    def test_the_id_is_minted_from_the_dato_line(self) -> None:
        assert _doc_id(_received(minimal_pdf(LOVDATA_PRINT))) == "lf-20010203-9001"

    def test_a_docx_export_mints_from_the_line_after_dato(self) -> None:
        extracted = _received(minimal_docx((*DOCX_HEAD, PREAMBLE, *BODY)), DOCX)
        assert extracted.received_form is ReceivedForm.LOVDATA_PRINT
        assert _doc_id(extracted) == "lf-20170622-9004"
        assert "FOR-" not in extracted.regulation.full_text

    def test_a_block_with_no_bound_is_held(self) -> None:
        lines = tuple(line for line in LOVDATA_PRINT if not line.startswith("Innhold"))
        held = _held(minimal_pdf(lines))
        assert held.reason is ExtractionHoldReason.LOVDATA_COPY
        assert "cannot be bounded" in held.detail

    def test_markup_left_after_removal_is_held(self) -> None:
        lines = (*LOVDATA_PRINT, "Se https://lovdata.no/dokument/LF/forskrift/2001-02-03-9001")
        held = _held(minimal_pdf(lines))
        assert held.reason is ExtractionHoldReason.LOVDATA_COPY
        assert "remains after removal" in held.detail

    def test_the_same_bytes_give_the_same_result(self) -> None:
        payload = minimal_pdf(LOVDATA_PRINT)
        assert extract_received(payload, PDF) == extract_received(payload, PDF)


class TestLovtidend:
    def test_it_is_extracted_without_the_banner(self) -> None:
        extracted = _received(minimal_pdf(LOVTIDEND))
        assert extracted.received_form is ReceivedForm.LOVTIDEND
        for chrome in ("NORSK LOVTIDEND", "Utgitt i henhold", "PDF-versjon", "lovdata.no"):
            assert chrome not in extracted.regulation.full_text

    def test_the_id_is_minted_from_the_kunngjort_line(self) -> None:
        assert _doc_id(_received(minimal_pdf(LOVTIDEND))) == "lf-20251211-9006"

    def test_the_hjemmel_line_feeds_the_fields(self) -> None:
        fields = _received(minimal_pdf(LOVTIDEND)).fields
        assert fields.vedtatt_av == "Eksempel kommunestyre"


class TestAuthorityOriginal:
    def test_it_is_extracted_as_the_observed_channel_does(self) -> None:
        observed = extract_regulation(html_page(REGULATION_LINES), HTML)
        received = _received(html_page(REGULATION_LINES), HTML)
        assert received.received_form is ReceivedForm.AUTHORITY_ORIGINAL
        assert received.model_copy(update={"received_form": None}) == observed

    def test_an_unsupported_format_is_held_as_before(self) -> None:
        assert _held(b"x", "image/png").reason is ExtractionHoldReason.UNSUPPORTED_FORMAT


class TestIssuer:
    """A1-4: the issuer as the text states it, never assumed to be the kommunestyre."""

    @pytest.mark.parametrize(
        ("enactment", "issuer"),
        [
            (
                "Fastsatt ved kgl.res. av 5. oktober 1962. Fremmet av Eksempeldepartementet.",
                "kgl.res.",
            ),
            ("Hjemmel: Fastsatt ved kgl.res 9. januar 2009 med hjemmel i lov.", "kgl.res"),
            ("Fastsatt ved kgl. res. av 21. august 1987, fremmet av departementet.", "kgl. res."),
            ("Fastsatt ved Kronprinsreg.res. av 21. desember 1990.", "Kronprinsreg.res."),
            (
                "Hjemmel: Fastsatt av Miljødirektoratet 27. april 2022 med hjemmel i lov.",
                "Miljødirektoratet",
            ),
            (
                "Fastsatt av Eksempeldepartementet 7. juni 2007 med hjemmel i lov.",
                "Eksempeldepartementet",
            ),
        ],
    )
    def test_the_issuer_is_read_from_the_enactment(self, enactment: str, issuer: str) -> None:
        lines = (*PRINT_HEAD, enactment, *BODY)
        assert _received(minimal_pdf(lines)).fields.vedtatt_av == issuer

    def test_a_text_naming_no_issuer_has_none(self) -> None:
        lines = (*PRINT_HEAD, "Forskriften gjelder i hele kommunen.", *BODY)
        assert _received(minimal_pdf(lines)).fields.vedtatt_av is None

    def test_an_instrument_in_the_body_is_not_the_issuer(self) -> None:
        lines = (*PRINT_HEAD, *BODY, "Fastsatt ved kgl.res. av 5. oktober 1962 gjelder fortsatt.")
        assert _received(minimal_pdf(lines)).fields.vedtatt_av is None


def test_an_own_number_in_the_block_beside_the_dato_is_ambiguous() -> None:
    lines = (*PRINT_HEAD, "Forskrift 3. mars 2001 nr. 9099 om eksempel.", PREAMBLE, *BODY)
    extracted = _received(minimal_pdf(lines))
    identity = mint_identity(extracted.regulation, EKSEMPEL)
    assert len(identity.candidates) == 2


@pytest.mark.parametrize(
    ("lines", "ref_id"),
    [
        (LOVDATA_PRINT, "forskrift/2001-02-03-9001"),
        (LOVTIDEND, "forskrift/2025-12-11-9006"),
    ],
)
def test_removed_identification_still_enforces_central_collision(
    lines: tuple[str, ...], ref_id: str
) -> None:
    """Removing editorial markup must not evade ADR-0016's collision gate."""
    extracted = _received(html_page(lines), HTML)
    identity = mint_identity(extracted.regulation, EKSEMPEL, frozenset({ref_id}))
    assert isinstance(identity, HeldIdentity)
    assert identity.reason is HoldReason.CENTRAL_COLLISION
    assert identity.colliding_ref_id == ref_id


def test_editorial_changes_do_not_version_the_regulation_but_body_changes_do() -> None:
    """The version hash names the published regulation, not removed print furniture."""
    editorial = tuple(
        line.replace("01.02.2026 09:30", "02.03.2026 10:45")
        .replace("Publisert II 2001 99", "Publisert II 2001 100")
        .replace("7 juni 2005 nr. 9002", "8 juli 2006 nr. 9003")
        for line in LOVDATA_PRINT
    )
    changed_body = tuple(
        line.replace("Ingen må forstyrre", "Ingen skal forstyrre") for line in LOVDATA_PRINT
    )
    identities = []
    for lines in (LOVDATA_PRINT, editorial, changed_body):
        result = mint_identity(_received(html_page(lines), HTML).regulation, EKSEMPEL)
        assert isinstance(result, MintedIdentity)
        identities.append(result)
    original, reprinted, amended = identities
    assert original == reprinted
    assert amended.doc_id == original.doc_id
    assert amended.content_hash != original.content_hash


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        ("Kontaktperson: Kari Testperson", ExtractionHoldReason.PERSONAL_DATA),
        ("Forskriften trer i kraft X.X.2026.", ExtractionHoldReason.PLACEHOLDER_DATE),
    ],
)
def test_stripping_markup_does_not_bypass_safety_holds(
    extra: str, reason: ExtractionHoldReason
) -> None:
    held = _held(html_page((*LOVDATA_PRINT, extra)), HTML)
    assert held.reason is reason


@pytest.mark.parametrize("content_type", [PDF, DOCX])
def test_received_unreadable_documents_preserve_the_observed_error(content_type: str) -> None:
    payload = b"not a PDF or a DOCX archive"
    observed = extract_regulation(payload, content_type)
    assert isinstance(observed, HeldExtraction)
    assert observed.reason is ExtractionHoldReason.UNREADABLE
    assert observed.detail
    assert extract_received(payload, content_type) == observed


def test_print_metadata_cannot_supply_a_missing_regulation_body() -> None:
    # Keep the retained preamble above the length gate to exercise the split gate.
    preamble = (
        PREAMBLE,
        "Forskriften gjelder for hele kommunen og fastsetter bestemmelser om orden, "
        "ferdsel og bruk av kommunens offentlige områder til beste for innbyggerne.",
    )
    held = _held(html_page((*PRINT_HEAD, *preamble)), HTML)
    assert held.reason is ExtractionHoldReason.NO_BODY


def test_removed_and_retained_identification_of_the_same_id_is_not_ambiguous() -> None:
    lines = (*PRINT_HEAD, "Forskrift 3. februar 2001 nr. 9001 om eksempel.", PREAMBLE, *BODY)
    extracted = _received(html_page(lines), HTML)
    identity = mint_identity(extracted.regulation, EKSEMPEL)
    assert isinstance(identity, MintedIdentity), identity
    assert identity.doc_id == "lf-20010203-9001"
    assert identity.candidates == ("lf-20010203-9001",)


def test_cited_body_id_does_not_collide_after_print_identification_is_removed() -> None:
    lines = (*LOVDATA_PRINT, "Forskrift 3. mars 2001 nr. 9099 om eksempel oppheves.")
    extracted = _received(html_page(lines), HTML)
    identity = mint_identity(
        extracted.regulation, EKSEMPEL, frozenset({"forskrift/2001-03-03-9099"})
    )
    assert isinstance(identity, MintedIdentity), identity
    assert identity.doc_id == "lf-20010203-9001"
    assert identity.candidates == ("lf-20010203-9001",)


def test_an_explicit_issuer_takes_precedence_over_a_cited_state_instrument() -> None:
    enactment = (
        "Fastsatt av Eksempel kommunestyre 3. februar 2001 med hjemmel i lov. "
        "Tidligere forskrift er fastsatt ved kgl.res. av 5. oktober 1962."
    )
    extracted = _received(html_page((*PRINT_HEAD, enactment, *BODY)), HTML)
    assert extracted.fields.vedtatt_av == "Eksempel kommunestyre"
