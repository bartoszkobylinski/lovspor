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

from datetime import date

import pytest

from lovspor.promotion import ExtractedDocument, ExtractionHoldReason, HeldExtraction
from lovspor.promotion.anchors import MAX_TITLE_CHARS, first_numbered_section
from lovspor.promotion.extract import EXTRACTOR_VERSION, extract_received, extract_regulation
from lovspor.promotion.fields import read_received_regulation
from lovspor.promotion.models import PersonalDataKind, ReceivedForm
from tests.unit.promotion_fixtures import html_page, minimal_pdf
from tests.unit.test_promotion_lovdata_markup import LOVTIDEND, PRINT_HEAD

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
    @pytest.mark.parametrize("ending", [".", ":", ";"])
    @pytest.mark.parametrize(
        "prefix",
        [
            "Hjemmel:",
            "Heimel:",
            "Fastsatt",
            "Fastsett",
            "Vedtatt",
            "Vedteke",
            "Fremmet",
            "Fremja",
            "Kunngjort",
        ],
    )
    def test_fallback_keeps_enactment_lines_out_of_body(self, prefix: str, ending: str) -> None:
        # operations.md: enactment lines and their wraps precede the fallback body.
        lines = (
            BOUNDARY[0],
            f"{prefix} med hjemmel i",
            f"lov om kommunal inndeling{ending}",
            BOUNDARY[2],
        )
        result = read_received_regulation(lines)
        assert not isinstance(result, ExtractionHoldReason), result
        regulation, _ = result
        assert regulation.identification_block == "\n".join(lines[:3])
        assert regulation.body == lines[3]

    def test_unsectioned_body_follows_all_enactment_lines_and_their_wraps(self) -> None:
        # decisions.md §20: the fallback is after enactment, including wraps.
        lines = (
            BOUNDARY[0],
            "Hjemmel: Fastsatt ved kgl.res. av 21. august 1987 med hjemmel i",
            "lov om kommunal inndeling.",
            "Fremmet av",
            "Kommunal- og arbeidsdepartementet.",
            BOUNDARY[2],
        )
        result = read_received_regulation(lines)
        assert not isinstance(result, ExtractionHoldReason), result
        regulation, _ = result
        assert regulation.identification_block == "\n".join(lines[:5])
        assert regulation.body == lines[5]

    def test_section_anchor_takes_precedence_over_an_earlier_numbered_point(self) -> None:
        lines = (*COMPOUND_TITLE[:2], "1. Tidligere vedtak omtales her.", *COMPOUND_TITLE[2:])
        extracted = _received(lines)
        assert extracted.regulation.identification_block == "\n".join(lines[:3])
        assert extracted.regulation.body == "\n".join(COMPOUND_TITLE[2:])

    def test_wrapped_enactment_without_any_body_is_still_held(self) -> None:
        lines = (BOUNDARY[0], "Fastsatt ved kgl.res. av 21. august 1987. Fremmet av", FILLER)
        assert _held(lines).reason is ExtractionHoldReason.NO_BODY

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
    @pytest.mark.parametrize("header", [PRINT_HEAD[:-4], LOVTIDEND[:5]])
    def test_received_split_still_holds_a_truncated_title(self, header: tuple[str, ...]) -> None:
        # title.py's contract still applies through the new received reader.
        lines = ("Forskrift om", *VERNEFORSKRIFT[1:])
        result = extract_received(minimal_pdf((*header, *lines)), PDF)
        assert isinstance(result, HeldExtraction), result
        assert result.reason is ExtractionHoldReason.TITLE_TRUNCATED
        assert "cut off" in result.detail

    @pytest.mark.parametrize("length", [MAX_TITLE_CHARS, MAX_TITLE_CHARS + 1])
    def test_first_line_title_length_boundary(self, length: int) -> None:
        title = "A" * length
        result = read_received_regulation((title, *VERNEFORSKRIFT[1:]))
        if length > MAX_TITLE_CHARS:
            assert result is ExtractionHoldReason.NO_TITLE
        else:
            assert not isinstance(result, ExtractionHoldReason), result
            regulation, _ = result
            assert regulation.title == title
            assert regulation.body == "\n".join(VERNEFORSKRIFT[2:])

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
    @pytest.mark.parametrize("anchor", ["I", "1. Første punkt", None])
    def test_new_body_shapes_preserve_enactment_fields_and_bound_hjemmel(
        self, anchor: str | None
    ) -> None:
        # fields.py: only the identification block supplies hjemmel; the
        # new splitter must keep that boundary for each newly accepted shape.
        identification = (
            "Renovasjonsforskrift, Eksempel kommune.",
            "Hjemmel: LOV-1981-03-13-6.",
            "Fastsatt av Eksempel kommunestyre 25. februar 1999.",
        )
        body = (
            *((anchor,) if anchor is not None else ()),
            FILLER,
            "Hjemmel: LOV-2009-06-19-100.",
        )
        extracted = _received((*identification, *body))
        assert extracted.regulation.identification_block == "\n".join(identification)
        assert extracted.regulation.body == "\n".join(body)
        assert extracted.regulation.vedtaksdato == date(1999, 2, 25)
        assert extracted.fields.vedtatt == date(1999, 2, 25)
        assert extracted.fields.vedtatt_av == "Eksempel kommunestyre"
        assert extracted.fields.hjemmel == ("LOV-1981-03-13-6",)

    @pytest.mark.parametrize("lines", [VERNEFORSKRIFT, KGL_RES_POINTS, BOUNDARY, COMPOUND_TITLE])
    def test_lovtidend_uses_the_received_splitter(self, lines: tuple[str, ...]) -> None:
        # The same new rules apply to both forms named in decisions.md §20.
        result = extract_received(minimal_pdf((*LOVTIDEND[:5], *lines)), PDF)
        assert isinstance(result, ExtractedDocument), result
        expected = _received(lines)
        assert result.received_form is ReceivedForm.LOVTIDEND
        assert result.regulation.title == expected.regulation.title
        assert result.regulation.identification_block == expected.regulation.identification_block
        assert result.regulation.body == expected.regulation.body
        assert result.regulation.vedtaksdato == expected.regulation.vedtaksdato
        assert result.regulation.removed_identification == LOVTIDEND[4]
        assert result.fields == expected.fields

    @pytest.mark.parametrize("lines", [VERNEFORSKRIFT, BOUNDARY])
    def test_newly_accepted_body_still_reaches_the_personal_data_gate(
        self, lines: tuple[str, ...]
    ) -> None:
        # §20 explicitly changes phone-bearing stubs from no_body to personal_data.
        held = _held((*lines, "Telefon: 12 34 56 78"))
        assert held.reason is ExtractionHoldReason.PERSONAL_DATA
        assert [(hit.kind, hit.line) for hit in held.personal_data] == [
            (PersonalDataKind.PHONE, len(lines) + 1)
        ]

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
    @pytest.mark.parametrize("entry", ["1. Vegetasjonen ........ 3", "1. Vegetasjonen …… 3"])
    def test_numbered_contents_entry_does_not_open_the_body(self, entry: str) -> None:
        lines = (*KGL_RES_POINTS[:2], entry, *KGL_RES_POINTS[2:])
        result = read_received_regulation(lines)
        assert not isinstance(result, ExtractionHoldReason), result
        regulation, _ = result
        assert regulation.identification_block == "\n".join(lines[:4])
        assert regulation.body == "\n".join(KGL_RES_POINTS[3:])

    @pytest.mark.parametrize("anchor", ["§ 1. Virkeområde", "I", "1. Første punkt"])
    def test_body_anchor_without_a_preceding_title_is_held(self, anchor: str) -> None:
        assert read_received_regulation((anchor, FILLER)) is ExtractionHoldReason.NO_BODY

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
