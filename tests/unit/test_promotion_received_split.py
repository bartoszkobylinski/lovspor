"""How a Lovdata print or Lovtidend text the authority sent is split (#636).

On the received channel the print's own structure is the anchor: its first
published line is the title Lovdata states, and the body opens at the first
section — ``§ 1`` or ``Kapittel 1`` as before, else a bare roman numeral
(``I``, ``II.``) or the first numbered point (``1.``), as older
verneforskrifter and kgl.res. decisions are set, else after the lines that
state the enactment. An authority's own file and the observed channel keep
the shared anchor rules. Every line is synthetic, shaped on Røros's answer.
"""

from __future__ import annotations

import pytest

from lovspor.promotion import ExtractedDocument, ExtractionHoldReason, HeldExtraction
from lovspor.promotion.anchors import MAX_TITLE_CHARS, first_numbered_section
from lovspor.promotion.extract import EXTRACTOR_VERSION, extract_received, extract_regulation
from lovspor.promotion.fields import read_received_regulation
from lovspor.promotion.models import ReceivedForm
from tests.unit.promotion_fixtures import html_page, minimal_pdf
from tests.unit.test_promotion_lovdata_markup import PRINT_HEAD

PDF = "application/pdf"
HTML = "text/html; charset=utf-8"
FILLER = (
    "Det er forbudt å fjerne planter eller plantedeler fra reservatet, og nye "
    "plantearter må ikke innføres i området, herunder ved treslagskifte eller såing."
)
VERNEFORSKRIFT = (
    "Forskrift om Eksempelfloen naturreservat, Eksempel kommune, Eksempel fylke.",
    "Fastsatt ved Kronprinsreg.res. av 21. desember 1990. Fremmet av Miljøverndepartementet.",
    "I",
    "I medhold av lov om naturvern av 19. juni 1970 nr. 63 § 8 er myrområdet fredet.",
    "II",
    "Det fredete området berører følgende gnr/bnr: 90/7, 132/250.",
    "IV",
    "For reservatet gjelder følgende bestemmelser:",
    f"1. Vegetasjonen er fredet mot enhver skade og ødeleggelse. {FILLER}",
    "X",
    "Forskriften trer i kraft straks.",
)
KGL_RES_POINTS = (
    "Forskrift om kommunenavn, Eksempel kommune, Eksempel fylke.",
    "Fastsatt ved kgl.res. av 5. oktober 1962. Fremmet av Kommunal- og arbeidsdepartementet.",
    "I medhold av midlertidig lov av 21. juni 1956 om en alminnelig revisjon blir bestemt:",
    f"1. Den nye kommunes navn fastsettes til Eksempel. {FILLER}",
    "2. Kommunestyret i den nye kommunen skal ha 27 medlemmer.",
    "3. Denne resolusjon trer i kraft straks.",
)
BOUNDARY = (
    "Regulering av grensen mellom Eksempel og Naboen kommuner, Eksempel fylke.",
    "Fastsatt ved kgl. res. av 21. august 1987, fremmet av Kommunal- og arbeidsdepartementet.",
    f"Grensen endres slik at gnr. 1, bnr. 2 i Eksempel kommune overføres til Naboen. {FILLER}",
)
WRAPPED_ISSUER = (
    "Forskrift etter konsesjonsloven § 5, Eksempel kommune, Eksempel fylke.",
    "Fastsatt ved kgl.res. av 3. mars 1989 med hjemmel i konsesjonsloven § 5. Fremmet av",
    "Landbruksdepartementet.",
    "I",
    f"Konsesjonsfriheten for bebygd eiendom settes ut av kraft for området. {FILLER}",
    "II",
    "Denne forskrift trer i kraft straks.",
)
VEDLEGG = (
    "Verneplan for Eksempelfjella. Vedlegg 1. Forskrift om Eksempelfjella landskapsvernområde,",
    "Eksempel og Naboen kommuner, Eksempel fylke",
    "Hjemmel: Fastsatt ved kgl.res 9. januar 2009 med hjemmel i lov 19. juni 1970 nr. 63 § 5.",
    "§ 1. Avgrensing",
    f"Landskapsvernområdet berører følgende gnr./bnr.: 81/1, 81/2. {FILLER}",
    "§ 2. Formål",
    "Formålet er å ta vare på et større naturområde.",
)
COMPOUND_TITLE = (
    "Renovasjonsforskrift, Eksempel kommune, Eksempel fylke.",
    "Fastsatt av Eksempel kommunestyre 25. februar 1999 med hjemmel i forurensningsloven § 30.",
    "Kapittel 1. Generelle bestemmelser",
    "§ 1. Virkeområde",
    f"Forskriften gjelder oppsamling, innsamling og transport av forbruksavfall. {FILLER}",
)
IDENTIFICATION_ONLY = (
    "Forskrift om betalingsregulativ til matrikkelloven, Eksempel kommune, Eksempel fylke",
    "Hjemmel: Fastsatt av Eksempel kommunestyre 28. januar 2010 med hjemmel i lov 17. juni "
    "2005 nr. 101 om eigedomsregistrering (matrikkellova) § 32, jf. forskrift 26. juni 2009 "
    "nr. 864 om eiendomsregistrering (matrikkelforskriften) § 16, og lov 10. februar 1967 om "
    "behandlingsmåten i forvaltningssaker § 37.",
)


def _print(lines: tuple[str, ...]) -> bytes:
    return minimal_pdf((*PRINT_HEAD[:-4], *lines))


def _received(lines: tuple[str, ...]) -> ExtractedDocument:
    result = extract_received(_print(lines), PDF)
    assert isinstance(result, ExtractedDocument), result
    return result


def _held(lines: tuple[str, ...]) -> HeldExtraction:
    result = extract_received(_print(lines), PDF)
    assert isinstance(result, HeldExtraction), result
    return result


class TestBodyAnchors:
    def test_a_bare_roman_numeral_opens_the_body(self) -> None:
        extracted = _received(VERNEFORSKRIFT)
        assert extracted.regulation.title == VERNEFORSKRIFT[0]
        assert extracted.regulation.identification_block == "\n".join(VERNEFORSKRIFT[:2])
        assert extracted.regulation.body.startswith("I\nI medhold av lov om naturvern")
        assert extracted.received_form is ReceivedForm.LOVDATA_PRINT

    def test_i_medhold_is_a_sentence_not_a_section(self) -> None:
        lines = tuple(line for line in VERNEFORSKRIFT if line != "I")
        extracted = _received(lines)
        assert extracted.regulation.body.startswith("II\nDet fredete")

    def test_the_first_numbered_point_opens_the_body(self) -> None:
        extracted = _received(KGL_RES_POINTS)
        assert extracted.regulation.identification_block == "\n".join(KGL_RES_POINTS[:3])
        assert extracted.regulation.body.startswith("1. Den nye kommunes navn")

    def test_a_decision_with_no_section_opens_after_the_enactment(self) -> None:
        extracted = _received(BOUNDARY)
        assert extracted.regulation.identification_block == "\n".join(BOUNDARY[:2])
        assert extracted.regulation.body == BOUNDARY[2]

    def test_an_enactment_wrapped_over_two_lines_stays_in_the_block(self) -> None:
        extracted = _received(WRAPPED_ISSUER)
        assert extracted.regulation.identification_block == "\n".join(WRAPPED_ISSUER[:3])
        assert extracted.regulation.body.startswith("I\nKonsesjonsfriheten")
        assert extracted.fields.vedtatt_av == "kgl.res."

    def test_a_section_sign_still_wins_over_a_numbered_point(self) -> None:
        extracted = _received(COMPOUND_TITLE)
        assert extracted.regulation.body.startswith("Kapittel 1. Generelle bestemmelser")

    def test_identification_lines_alone_are_no_body(self) -> None:
        held = _held(IDENTIFICATION_ONLY)
        assert held.reason is ExtractionHoldReason.NO_BODY
        assert "nothing after the lines that state the enactment" in held.detail


class TestTitle:
    def test_the_first_line_is_the_title_whatever_it_opens_with(self) -> None:
        assert _received(COMPOUND_TITLE).regulation.title == COMPOUND_TITLE[0]

    def test_a_wrapped_vedlegg_title_is_read_across_its_lines(self) -> None:
        extracted = _received(VEDLEGG)
        assert extracted.regulation.title == f"{VEDLEGG[0]} {VEDLEGG[1]}"
        assert extracted.regulation.body.startswith("§ 1. Avgrensing")

    def test_a_first_line_too_long_for_a_title_is_held(self) -> None:
        long = "Forskrift om " + " ".join(["eksempelomraade"] * 20) + ", Eksempel kommune."
        assert len(long) > MAX_TITLE_CHARS
        lines = (long, *VERNEFORSKRIFT[1:])
        assert _held(lines).reason is ExtractionHoldReason.NO_TITLE


class TestUnchangedElsewhere:
    @pytest.mark.parametrize(
        ("lines", "reason"),
        [
            (VERNEFORSKRIFT, ExtractionHoldReason.NO_BODY),
            (BOUNDARY, ExtractionHoldReason.NO_BODY),
            (COMPOUND_TITLE, ExtractionHoldReason.NO_TITLE),
            (VEDLEGG, ExtractionHoldReason.NO_TITLE),
        ],
    )
    def test_the_observed_channel_holds_the_same_shapes(
        self, lines: tuple[str, ...], reason: ExtractionHoldReason
    ) -> None:
        result = extract_regulation(html_page(lines), HTML)
        assert isinstance(result, HeldExtraction)
        assert result.reason is reason

    @pytest.mark.parametrize(
        ("lines", "reason"),
        [
            (VERNEFORSKRIFT, ExtractionHoldReason.NO_BODY),
            (COMPOUND_TITLE, ExtractionHoldReason.NO_TITLE),
        ],
    )
    def test_an_authority_original_keeps_the_shared_anchors(
        self, lines: tuple[str, ...], reason: ExtractionHoldReason
    ) -> None:
        result = extract_received(minimal_pdf(lines), PDF)
        assert isinstance(result, HeldExtraction)
        assert result.reason is reason

    def test_the_same_bytes_give_the_same_result(self) -> None:
        payload = _print(VERNEFORSKRIFT)
        assert extract_received(payload, PDF) == extract_received(payload, PDF)
        assert _received(VERNEFORSKRIFT).extractor_version == EXTRACTOR_VERSION


class TestAnchors:
    @pytest.mark.parametrize(
        ("lines", "index"),
        [
            (("Tittel", "I", "tekst"), 1),
            (("Tittel", "II.", "tekst"), 1),
            (("Tittel", "I medhold av lov", "1. punkt", "x"), 2),
            (("Tittel", "1.", "x"), None),
            (("Tittel", "10. oktober", "x"), None),
            (("Tittel", "I. Innledning ........ 3", "I", "x"), 2),
            (("Tittel", "tekst"), None),
        ],
    )
    def test_first_numbered_section(self, lines: tuple[str, ...], index: int | None) -> None:
        assert first_numbered_section(lines) == index

    def test_read_received_regulation_refuses_empty_lines(self) -> None:
        assert read_received_regulation(()) is ExtractionHoldReason.NO_TITLE
