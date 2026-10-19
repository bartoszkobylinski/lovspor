"""Lovdata's markup in a received text, found and removed (ADR-0016 A1-3, #632).

Every line below is synthetic. The layouts follow what a text-layer reader
gets from a Lovdata print, a DOCX exported from Lovdata and a Norsk Lovtidend
avd. II PDF, with invented ids, titles and wording, so no Lovdata text enters
the repository.
"""

from __future__ import annotations

import pytest

from lovspor.promotion.lovdata_header import lovdata_dato
from lovspor.promotion.lovdata_markup import received_form, residual_markup, strip_markup
from lovspor.promotion.models import ReceivedForm

TITLE = "Forskrift om eksempelvedtekt, Eksempel kommune, Eksempel fylke."
PRINT_HEAD = (
    "Utskrift fra Lovdata - 01.02.2026 09:30",
    TITLE,
    "Dato FOR-2001-02-03-9001",
    "Publisert II 2001 99",
    "Ikrafttredelse 03.02.2001",
    "Sist endret FOR-2005-06-07-9002 fra 07.07.2005",
    "Endrer FOR-1990-01-01-9003",
    "Gjelder for Eksempel kommune, Eksempel fylke",
    "Hjemmel LOV-1995-08-04-53-§14",
    "Korttittel Forskrift om eksempelvedtekt",
    "Innholdsfortegnelse",
    TITLE,
    "Innholdsfortegnelse",
    "Kapittel I. Alminnelige bestemmelser",
    "Kapittel II. Orden",
)
PREAMBLE = (
    "Hjemmel: Fastsatt av Eksempel kommunestyre 2. februar 2001 med hjemmel i lov "
    "4. august 1995 nr. 53 om politiet § 14."
)
BODY = (
    "Kapittel I. Alminnelige bestemmelser",
    "§ 1. Virkeområde",
    "Forskriften gjelder i hele kommunen.",
    "0 Endret ved forskrift 7 juni 2005 nr. 9002 (i kraft 7 juli 2005).",
    "Kapittel II. Orden",
    "§ 2. Ro og orden",
    "Ingen må forstyrre den alminnelige ro og orden.",
)
LOVDATA_PRINT = (*PRINT_HEAD, PREAMBLE, *BODY)

DOCX_HEAD = (
    TITLE,
    "Dato",
    "FOR-2017-06-22-9004",
    "Ikrafttredelse",
    "01.07.2017",
    "Gjelder for",
    "Eksempel kommune, Eksempel fylke",
    "Sist endret",
    "FOR-2019-01-01-9005",
    "Kunngjort",
    "23.06.2017 kl. 15.45",
    "Korttittel",
    "Eksempelforskrift",
    "Innholdsfortegnelse",
    TITLE,
    "Innholdsfortegnelse2",
    "Kapittel 13",
    "Kapittel 24",
)
CORRECTIONS = (
    "For ev. rettelser se nederst i den elektroniske versjonen: "
    "https://lovdata.no/LTII/forskrift/2025-12-11-9006"
)
LOVTIDEND = (
    CORRECTIONS,
    "NORSK LOVTIDEND",
    "Avd. II Regionale og lokale forskrifter mv.",
    "Utgitt i henhold til lov 19. juni 1969 nr. 53.",
    "Kunngjort 9. januar 2026 kl. 14.15 PDF-versjon 9. januar 2026 11.12.2025 nr. 9006",
    "Forskrift om eksempelgebyr, Eksempel kommune, Eksempel fylke",
    "Hjemmel: Fastsatt av Eksempel kommunestyre 11. desember 2025 med hjemmel i lov § 33-1.",
    "§ 1. Virkeområde",
    "Forskriften gjelder gebyr for saksbehandling.",
    f"{CORRECTIONS} 8 4 Fri fredag",
    "§ 2. Ikrafttredelse",
    "Forskriften trer i kraft 1. januar 2026.",
)
ORIGINAL = (
    "Forskrift om eksempelvedtekt, Eksempel kommune",
    "Vedtatt av Eksempel kommunestyre 2. februar 2001.",
    "§ 1. Virkeområde",
    "Endret ved forskrift 7 juni 2005 nr. 9002.",
    "Forskriften gjelder i hele kommunen.",
)


class TestForm:
    def test_a_print_with_the_utskrift_stamp_is_a_lovdata_print(self) -> None:
        assert received_form(LOVDATA_PRINT) is ReceivedForm.LOVDATA_PRINT

    def test_a_docx_export_with_the_block_alone_is_a_lovdata_print(self) -> None:
        assert received_form((*DOCX_HEAD, PREAMBLE, *BODY)) is ReceivedForm.LOVDATA_PRINT

    def test_a_browser_print_chrome_alone_is_a_lovdata_print(self) -> None:
        chrome = "29.04.2019 https://lovdata.no/dokument/LF/forskrift/2001-02-03-9001"
        assert received_form((chrome, *ORIGINAL)) is ReceivedForm.LOVDATA_PRINT

    def test_the_lovtidend_banner_is_lovtidend(self) -> None:
        assert received_form(LOVTIDEND) is ReceivedForm.LOVTIDEND

    def test_a_text_without_lovdata_markers_is_the_authority_original(self) -> None:
        assert received_form(ORIGINAL) is ReceivedForm.AUTHORITY_ORIGINAL

    def test_a_lowercase_banner_is_not_lovtidend(self) -> None:
        assert received_form(("Norsk Lovtidend", *ORIGINAL)) is ReceivedForm.AUTHORITY_ORIGINAL


class TestDato:
    def test_the_id_on_the_dato_line(self) -> None:
        assert lovdata_dato(LOVDATA_PRINT) == (2, 2)

    def test_the_id_on_the_line_after_a_bare_dato(self) -> None:
        assert lovdata_dato(DOCX_HEAD) == (1, 2)

    def test_a_bare_dato_followed_by_a_date_is_not_an_id(self) -> None:
        lines = ("Dato", "12.12.2019", "Ikrafttredelse", "x", "Korttittel", "y")
        assert lovdata_dato(lines) is None

    def test_a_dato_with_a_value_does_not_borrow_the_next_line(self) -> None:
        lines = ("Dato 12.12.2019", "FOR-2001-02-03-9001", "Ikrafttredelse x", "Korttittel y")
        assert lovdata_dato(lines) is None

    def test_a_lone_dato_line_is_not_a_block(self) -> None:
        assert lovdata_dato(("Dato FOR-2001-02-03-9001", *ORIGINAL)) is None

    def test_a_bare_dato_on_the_last_line_is_not_an_id(self) -> None:
        assert lovdata_dato(("Ikrafttredelse x", "Korttittel y", "Dato")) is None


class TestLovdataPrint:
    def test_the_published_lines_are_the_regulation_alone(self) -> None:
        stripped = strip_markup(LOVDATA_PRINT, ReceivedForm.LOVDATA_PRINT)
        assert stripped is not None
        assert stripped.lines == (TITLE, PREAMBLE, *BODY[:3], *BODY[4:])

    def test_the_dato_line_is_kept_for_identity(self) -> None:
        stripped = strip_markup(LOVDATA_PRINT, ReceivedForm.LOVDATA_PRINT)
        assert stripped is not None
        assert stripped.identification == ("Dato FOR-2001-02-03-9001",)

    def test_a_docx_export_keeps_the_dato_and_its_value_line(self) -> None:
        stripped = strip_markup((*DOCX_HEAD, PREAMBLE, *BODY), ReceivedForm.LOVDATA_PRINT)
        assert stripped is not None
        assert stripped.identification == ("Dato", "FOR-2017-06-22-9004")
        assert stripped.lines[:3] == (TITLE, PREAMBLE, BODY[0])

    def test_no_bound_for_the_block_is_none(self) -> None:
        lines = tuple(line for line in LOVDATA_PRINT if not line.startswith("Innhold"))
        assert strip_markup(lines, ReceivedForm.LOVDATA_PRINT) is None

    def test_a_print_without_the_block_is_none(self) -> None:
        lines = (PRINT_HEAD[0], *ORIGINAL)
        assert strip_markup(lines, ReceivedForm.LOVDATA_PRINT) is None

    def test_contents_entries_end_where_one_repeats(self) -> None:
        lines = (*PRINT_HEAD, *BODY)
        stripped = strip_markup(lines, ReceivedForm.LOVDATA_PRINT)
        assert stripped is not None
        assert stripped.lines[:3] == (
            TITLE,
            "Kapittel I. Alminnelige bestemmelser",
            "§ 1. Virkeområde",
        )

    @pytest.mark.parametrize(
        "entry",
        [
            "I",
            "II.",
            "I. Generelle bestemmelser",
            "Kap. 1 Rettigheter",
            "Kapittel 13",
            "Vedlegg 1. Kart",
        ],
    )
    def test_contents_entry_shapes_are_removed(self, entry: str) -> None:
        lines = (*PRINT_HEAD[:13], entry, PREAMBLE, *BODY)
        stripped = strip_markup(lines, ReceivedForm.LOVDATA_PRINT)
        assert stripped is not None
        assert stripped.lines[:2] == (TITLE, PREAMBLE)

    def test_a_sentence_after_the_contents_is_kept(self) -> None:
        lines = (*PRINT_HEAD[:13], "I medhold av lov 4. august 1995 blir bestemt:", *BODY)
        stripped = strip_markup(lines, ReceivedForm.LOVDATA_PRINT)
        assert stripped is not None
        assert stripped.lines[1] == "I medhold av lov 4. august 1995 blir bestemt:"

    @pytest.mark.parametrize(
        "annotation",
        [
            "Endret ved forskrift 22 juni 2023 nr. 9007.",
            "0 Endret ved forskrift 12 nov 2008 nr. 9008 (i kraft 12 des 2008).",
            "0 Opphevet 31 des 2006 - se lov 4 juli 2003 nr. 74 § 32.",
            "0 Tilføyd ved forskrift 1 mars 2010 nr. 9009.",
            "Endringer: Endret ved forskrift 15 mars 2013 nr. 9010 (i kraft 1 juli 2013).",
            "Tilføyd hjemmel: Lov 20. juni 2025 nr. 102 om eksempel § 61.",
            "Endring vedtatt ved forskrift 27 nov 2025 nr. 9011 avventer (Lovdatas anm.).",
            "Opphevet ved forskrift 1 des 2017 nr. 9012.",
            "1 Ikke kunngjort i Norsk Lovtidend.",
        ],
    )
    def test_annotations_and_footnotes_are_removed(self, annotation: str) -> None:
        lines = (*LOVDATA_PRINT, annotation)
        stripped = strip_markup(lines, ReceivedForm.LOVDATA_PRINT)
        assert stripped is not None
        assert stripped.lines[-1] == BODY[-1]

    @pytest.mark.parametrize(
        "text",
        [
            "Fra samme tidspunkt oppheves forskrift 3. mars 1989 nr. 174.",
            "Opphevet ved forskrift 1. desember 2017 er forskrift om eksempel.",
            "Endret bruk av eiendommen krever tillatelse.",
            "1 Landskapet",
            "§ 23. (Opphevet)",
        ],
    )
    def test_regulation_text_that_repeals_or_amends_is_kept(self, text: str) -> None:
        stripped = strip_markup((*LOVDATA_PRINT, text), ReceivedForm.LOVDATA_PRINT)
        assert stripped is not None
        assert stripped.lines[-1] == text

    def test_an_annotation_ending_a_preamble_line_is_cut(self) -> None:
        kept = "Fastsatt ved kgl.res. av 3. mars 1989. Fremmet av Eksempeldepartementet."
        line = f"{kept} Endret 31 aug 2001 nr. 9013, opphevet ved forskrift 7 juni 2007 nr. 9014."
        stripped = strip_markup((*PRINT_HEAD, line, *BODY), ReceivedForm.LOVDATA_PRINT)
        assert stripped is not None
        assert stripped.lines[1] == kept

    def test_an_annotation_a_page_wrap_joined_to_a_line_is_cut(self) -> None:
        line = "Ingen må forstyrre ro og orden 0 Endret ved forskrift 7 juni 2005 nr. 9002."
        stripped = strip_markup((*PRINT_HEAD, PREAMBLE, line), ReceivedForm.LOVDATA_PRINT)
        assert stripped is not None
        assert stripped.lines[-1] == "Ingen må forstyrre ro og orden"

    def test_print_chrome_is_removed_anywhere(self) -> None:
        chrome = "https://lovdata.no/dokument/LF/forskrift/2001-02-03-9001 2/3"
        lines = (*LOVDATA_PRINT[:-1], chrome, LOVDATA_PRINT[-1])
        stripped = strip_markup(lines, ReceivedForm.LOVDATA_PRINT)
        assert stripped is not None
        assert chrome not in stripped.lines
        assert not residual_markup(stripped.lines)


class TestLovtidend:
    def test_the_banner_and_the_correction_links_are_removed(self) -> None:
        stripped = strip_markup(LOVTIDEND, ReceivedForm.LOVTIDEND)
        assert stripped is not None
        assert stripped.lines == (*LOVTIDEND[5:9], "8 4 Fri fredag", *LOVTIDEND[10:])

    def test_the_kunngjort_line_is_kept_for_identity(self) -> None:
        stripped = strip_markup(LOVTIDEND, ReceivedForm.LOVTIDEND)
        assert stripped is not None
        assert stripped.identification == (LOVTIDEND[4],)

    def test_the_hjemmel_line_stays(self) -> None:
        stripped = strip_markup(LOVTIDEND, ReceivedForm.LOVTIDEND)
        assert stripped is not None
        assert LOVTIDEND[6] in stripped.lines
        assert not residual_markup(stripped.lines)


class TestOriginal:
    def test_an_original_is_left_as_it_is(self) -> None:
        stripped = strip_markup(ORIGINAL, ReceivedForm.AUTHORITY_ORIGINAL)
        assert stripped is not None
        assert stripped.lines == ORIGINAL
        assert stripped.identification == ()


@pytest.mark.parametrize(
    "line",
    [
        "Utskrift fra Lovdata - 01.02.2026 09:30",
        "Se https://lovdata.no/dokument/LF/forskrift/2001-02-03-9001",
        "Dato FOR-2001-02-03-9001",
        "Endret ved forskrift 22 juni 2023 nr. 9007 (Lovdatas anm.).",
    ],
)
def test_a_marker_left_behind_is_residual(line: str) -> None:
    head = ("Ikrafttredelse 03.02.2001", "Korttittel x") if line.startswith("Dato") else ()
    assert residual_markup((*ORIGINAL, line, *head))


def test_an_original_has_no_residual_markup() -> None:
    assert not residual_markup(ORIGINAL)
