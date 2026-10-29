"""Three ways a Lovdata print states its enactment that the readers missed (#637 points 3, 4, 6).

* ``Fastsatt ved kgl.res. av 21. desember 1990`` — a state instrument's date
  is the vedtaksdato, and the instrument, as the text writes it, the organ;
* ``Fastsatt av Røros kommunestyre, sak 78/24, 29. august 2024`` — a case
  number between the organ and the date;
* ``forskrift av 30. oktober 19841`` — a footnote marker the PDF glued to a
  year, whose footnote line (``1 Ikke kunngjort i Norsk Lovtidend.``) the
  print's stripping removes.

Every line is synthetic.
"""

from __future__ import annotations

from datetime import date

import pytest

from lovspor.promotion import ExtractedDocument
from lovspor.promotion.extract import extract_received, extract_regulation
from lovspor.promotion.fields import read_regulation
from lovspor.promotion.identity import content_hash
from lovspor.promotion.lovdata_markup import strip_markup
from lovspor.promotion.models import ReceivedForm
from lovspor.promotion.stated_dates import stated_vedtatt
from tests.unit.promotion_fixtures import html_page, minimal_pdf
from tests.unit.test_promotion_lovdata_markup import BODY, LOVDATA_PRINT, PRINT_HEAD, TITLE

PDF = "application/pdf"
HTML = "text/html; charset=utf-8"
SECTIONS = (
    "§ 1. Formål",
    "Formålet med fredningen er å bevare et stort, variert myrsystem med et rikt plante- og "
    "dyreliv, og det er forbudt å fjerne planter eller plantedeler fra reservatet.",
    "§ 2. Ikrafttredelse",
    "Forskriften trer i kraft straks.",
)


def _received(lines: tuple[str, ...]) -> ExtractedDocument:
    result = extract_received(minimal_pdf((*PRINT_HEAD[:-4], *lines)), PDF)
    assert isinstance(result, ExtractedDocument), result
    return result


class TestStateInstrument:
    @pytest.mark.parametrize(
        ("enactment", "organ"),
        [
            ("fastsatt ved statsrådsbeslutning av 21. desember 1990", "statsrådsbeslutning"),
            (
                "vedtatt av Eksempel kommunestyre, sak 78/90, 21. desember 1990",
                "Eksempel kommunestyre",
            ),
        ],
    )
    def test_body_own_enactment_reads_new_forms_but_not_a_repealed_regulations(
        self, enactment: str, organ: str
    ) -> None:
        # stated_dates.py: only the body's own adoption statement counts.
        own = f"Forskriften er {enactment}."
        cited = f"Den tidligere forskriften, som ble {enactment}, oppheves."
        for statement, expected in [(own, date(1990, 12, 21)), (cited, None)]:
            read = read_regulation((TITLE, "Fremmet av Departementet.", *SECTIONS, statement))
            assert isinstance(read, tuple)
            regulation, fields = read
            verdict = stated_vedtatt(regulation)
            assert regulation.vedtaksdato == fields.vedtatt == verdict.value == expected
            assert fields.vedtatt_av == (organ if expected else None)
            assert verdict.status == ("stated" if expected else "absent")
            assert [s.evidence for s in verdict.statements] == ([own[:-1]] if expected else [])

    @pytest.mark.parametrize(
        "enactment",
        [
            "Fastsatt ved kgl.res. av 21. desember 1990.",
            "Fastsatt av Eksempel kommunestyre, sak 78/90, 21. desember 1990.",
        ],
    )
    @pytest.mark.parametrize("day", [21, 22])
    def test_new_forms_resolve_all_statements_without_moving_the_identity_date(
        self, enactment: str, day: int
    ) -> None:
        # fields.py requires agreement for front matter, but the first date
        # continues to seed identity even when a later statement disagrees.
        extracted = _received(
            (
                TITLE,
                enactment,
                *SECTIONS,
                f"Forskriften er vedtatt av Eksempel kommunestyre {day}. desember 1990.",
            )
        )
        verdict = stated_vedtatt(extracted.regulation)
        assert [s.date for s in verdict.statements] == [date(1990, 12, 21), date(1990, 12, day)]
        assert extracted.regulation.vedtaksdato == date(1990, 12, 21)
        if day == 21:
            assert verdict.status == "stated"
            assert extracted.fields.vedtatt == verdict.value == date(1990, 12, 21)
        else:
            assert verdict.status == "held"
            assert verdict.hold_reason == "conflicting_statements"
            assert extracted.fields.vedtatt is verdict.value is None

    @pytest.mark.parametrize(
        ("enactment", "organ"),
        [
            ("Fastsatt ved kgl.res. av 21. desember 1990. Fremmet av Departementet.", "kgl.res."),
            ("Fastsatt ved kgl. res. av 21. desember 1990, fremmet av Departementet.", "kgl. res."),
            ("Fastsatt ved Kronprinsreg.res. av 21. desember 1990.", "Kronprinsreg.res."),
            ("Hjemmel: Fastsatt ved kgl.res 21. desember 1990 med hjemmel i lov.", "kgl.res"),
            ("Fastsatt ved kongelig resolusjon 21. desember 1990.", "kongelig resolusjon"),
        ],
    )
    def test_the_instrument_dates_the_enactment(self, enactment: str, organ: str) -> None:
        extracted = _received((TITLE, enactment, *SECTIONS))
        assert extracted.fields.vedtatt == date(1990, 12, 21)
        assert extracted.fields.vedtatt_av == organ
        assert extracted.regulation.vedtaksdato == date(1990, 12, 21)

    def test_the_statement_is_kept_verbatim_as_evidence(self) -> None:
        read = read_regulation((TITLE, "Fastsatt ved kgl.res. av 21. desember 1990.", *SECTIONS))
        assert isinstance(read, tuple)
        vedtatt = stated_vedtatt(read[0])
        assert vedtatt.status == "stated"
        assert [s.evidence for s in vedtatt.statements] == [
            "Fastsatt ved kgl.res. av 21. desember 1990"
        ]

    def test_the_observed_channel_reads_it_the_same_way(self) -> None:
        page = html_page((TITLE, "Fastsatt ved kgl.res. av 21. desember 1990.", *SECTIONS))
        result = extract_regulation(page, HTML)
        assert isinstance(result, ExtractedDocument), result
        assert result.fields.vedtatt == date(1990, 12, 21)
        assert result.fields.vedtatt_av == "kgl.res."

    def test_an_instrument_cited_in_the_body_is_not_the_enactment(self) -> None:
        body = ("Vedtaket ved kgl.res. av 5. oktober 1962 gjelder fortsatt.",)
        extracted = _received((TITLE, "Fremmet av Departementet.", *SECTIONS, *body))
        assert extracted.fields.vedtatt is None
        assert extracted.fields.vedtatt_av is None


class TestCaseNumber:
    @pytest.mark.parametrize(
        "enactment",
        [
            "Fastsatt ved statsrådsbeslutning med hjemmel i lov av 14. juni 2002.",
            "Fastsatt av Eksempel kommunestyre, sak 78/24, med hjemmel i lov av 14. juni 2002.",
        ],
    )
    def test_new_forms_do_not_take_a_statutes_date_as_the_enactment(self, enactment: str) -> None:
        # fields.py's contract: a hjemmel date never dates the enactment.
        read = read_regulation((TITLE, enactment, *SECTIONS))
        assert isinstance(read, tuple)
        regulation, fields = read
        assert regulation.vedtaksdato is None
        assert fields.vedtatt is None
        assert stated_vedtatt(regulation).status == "absent"

    @pytest.mark.parametrize(
        "enactment",
        [
            "Hjemmel: Fastsatt av Eksempel kommunestyre, sak 78/24, 29. august 2024 med hjemmel "
            "i lov.",
            "Fastsatt av Eksempel kommunestyre, sak 78/24, 29. august 2024.",
            "Fastsatt av Eksempel kommunestyre, sak 78/24 29. august 2024.",
            "Fastsatt av Eksempel kommunestyre 29. august 2024, sak 78/24, med hjemmel i lov.",
        ],
    )
    def test_a_case_number_between_organ_and_date_is_stepped_over(self, enactment: str) -> None:
        extracted = _received((TITLE, enactment, *SECTIONS))
        assert extracted.fields.vedtatt == date(2024, 8, 29)
        assert extracted.fields.vedtatt_av == "Eksempel kommunestyre"

    def test_the_case_number_is_not_part_of_the_organ(self) -> None:
        read = read_regulation(
            (TITLE, "Fastsatt av Eksempel kommunestyre, sak 78/24, 29. august 2024.", *SECTIONS)
        )
        assert isinstance(read, tuple)
        assert read[1].vedtatt_av == "Eksempel kommunestyre"


class TestGluedFootnote:
    GLUED = "Forskriften erstatter forskrift av 30. oktober 19841 med senere endringer."
    FOOTNOTE = "1 Ikke kunngjort i Norsk Lovtidend."

    @pytest.mark.parametrize("marker", ["1", "12"])
    def test_unglued_enactment_matches_clean_text_dates_evidence_and_hash(
        self, marker: str
    ) -> None:
        # operations.md #637: removing the glued marker restores the stated
        # year before enactment fields and the published version are read.
        enactment = "Fastsatt ved kgl.res. av 30. oktober 1984."
        clean = _received((TITLE, enactment, *SECTIONS))
        glued = _received(
            (
                TITLE,
                enactment.replace("1984", f"1984{marker}"),
                *SECTIONS,
                f"{marker} Ikke kunngjort i Norsk Lovtidend.",
            )
        )
        assert glued.regulation == clean.regulation
        assert glued.fields == clean.fields
        assert glued.regulation.vedtaksdato == glued.fields.vedtatt == date(1984, 10, 30)
        verdict = stated_vedtatt(glued.regulation)
        assert verdict.status == "stated"
        assert [statement.evidence for statement in verdict.statements] == [enactment[:-1]]
        assert content_hash(glued.regulation.full_text) == content_hash(clean.regulation.full_text)

    def test_authority_original_does_not_remove_a_footnote_or_unglue_its_year(self) -> None:
        # lovdata_markup.py limits this editorial cleanup to Lovdata prints.
        lines = (TITLE, self.GLUED, *SECTIONS, self.FOOTNOTE)
        stripped = strip_markup(lines, ReceivedForm.AUTHORITY_ORIGINAL)
        assert stripped is not None
        assert stripped.lines == lines
        assert stripped.identification == ()

    def test_multiple_removed_footnotes_are_unglued_and_stripping_is_idempotent(self) -> None:
        line = "Forskriftene fra 19841, 199012 og 20012 oppheves."
        stripped = strip_markup(
            (
                *LOVDATA_PRINT,
                line,
                self.FOOTNOTE,
                "12 Ikke kunngjort i Norsk Lovtidend.",
            ),
            ReceivedForm.LOVDATA_PRINT,
        )
        assert stripped is not None
        assert stripped.lines[-1] == "Forskriftene fra 1984, 1990 og 20012 oppheves."
        assert self.FOOTNOTE not in stripped.lines
        assert "12 Ikke kunngjort i Norsk Lovtidend." not in stripped.lines
        again = strip_markup((*PRINT_HEAD[:-4], *stripped.lines), ReceivedForm.LOVDATA_PRINT)
        assert again is not None
        assert again.lines == stripped.lines

    @pytest.mark.parametrize("number", ["119841", "198410", "19841a", "1984 nr. 1"])
    def test_marker_removal_preserves_numbers_without_a_glued_year_boundary(
        self, number: str
    ) -> None:
        line = f"Henvisningen {number} beholdes."
        stripped = strip_markup((*LOVDATA_PRINT, line, self.FOOTNOTE), ReceivedForm.LOVDATA_PRINT)
        assert stripped is not None
        assert stripped.lines[-1] == line

    def test_the_marker_of_a_removed_footnote_is_unglued_from_the_year(self) -> None:
        lines = (*LOVDATA_PRINT, self.GLUED, self.FOOTNOTE)
        stripped = strip_markup(lines, ReceivedForm.LOVDATA_PRINT)
        assert stripped is not None
        assert stripped.lines[-1] == (
            "Forskriften erstatter forskrift av 30. oktober 1984 med senere endringer."
        )

    def test_only_that_footnote_s_number_is_unglued(self) -> None:
        line = "Forskriften erstatter forskrift av 30. oktober 19842 og 19841 med senere endringer."
        stripped = strip_markup((*LOVDATA_PRINT, line, self.FOOTNOTE), ReceivedForm.LOVDATA_PRINT)
        assert stripped is not None
        assert stripped.lines[-1] == (
            "Forskriften erstatter forskrift av 30. oktober 19842 og 1984 med senere endringer."
        )

    def test_without_the_footnote_line_nothing_is_touched(self) -> None:
        stripped = strip_markup((*LOVDATA_PRINT, self.GLUED), ReceivedForm.LOVDATA_PRINT)
        assert stripped is not None
        assert stripped.lines[-1] == self.GLUED

    def test_a_bare_number_after_a_year_is_not_a_marker(self) -> None:
        line = "Forskrift av 30. oktober 1984 nr. 1 og regulativ 20011 gjelder."
        stripped = strip_markup((*LOVDATA_PRINT, line, self.FOOTNOTE), ReceivedForm.LOVDATA_PRINT)
        assert stripped is not None
        assert stripped.lines[-1] == (
            "Forskrift av 30. oktober 1984 nr. 1 og regulativ 2001 gjelder."
        )

    def test_a_lovtidend_text_is_left_alone(self) -> None:
        lines = ("NORSK LOVTIDEND", TITLE, self.GLUED, *BODY, self.FOOTNOTE)
        stripped = strip_markup(lines, ReceivedForm.LOVTIDEND)
        assert self.GLUED in stripped.lines
