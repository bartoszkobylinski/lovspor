"""vedtatt / ikraft with every statement verbatim; disagreement is held (ADR-0016 Decision 2)."""

from __future__ import annotations

from datetime import date

import pytest

from lovspor.promotion.fields import read_regulation
from lovspor.promotion.models import ExtractedRegulation, RegulationFields
from lovspor.promotion.stated_dates import StatedDate, stated_ikraft, stated_vedtatt
from tests.unit.promotion_fixtures import REGULATION_LINES


def _read(*lines: str) -> tuple[ExtractedRegulation, RegulationFields]:
    read = read_regulation(lines)
    assert isinstance(read, tuple)
    return read


def _flat(regulation: ExtractedRegulation) -> str:
    return regulation.full_text.replace("\n", " ")


def _evidence_is_verbatim(regulation: ExtractedRegulation, stated: StatedDate) -> bool:
    return all(s.evidence in _flat(regulation) for s in stated.statements)


class TestTheFixtureRegulation:
    def test_both_dates_are_stated_once_and_agree_with_the_front_matter(self) -> None:
        regulation, fields = _read(*REGULATION_LINES)

        vedtatt, ikraft = stated_vedtatt(regulation), stated_ikraft(regulation)

        assert (vedtatt.status, vedtatt.value) == ("stated", fields.vedtatt)
        assert (ikraft.status, ikraft.value) == ("stated", fields.ikraft)
        assert [s.evidence for s in vedtatt.statements] == [
            "Vedtatt av kommunestyret i møte 12.12.2019"
        ]
        assert [s.evidence for s in ikraft.statements] == ["trer i kraft 1. januar 2020"]


class TestVedtatt:
    @pytest.mark.parametrize("in_block", [True, False])
    @pytest.mark.parametrize("written_date", ["12. desember 2019", "12.12.2019", "2019-12-12"])
    def test_enactment_keeps_date_and_evidence_for_every_written_form(
        self, in_block: bool, written_date: str
    ) -> None:
        clause = f"Forskriften er vedtatt av\nkommunestyret den {written_date}"
        regulation = ExtractedRegulation(
            title="Forskrift om slam",
            identification_block=clause if in_block else "Forskrift om slam",
            body="§ 1" if in_block else clause,
        )

        stated = stated_vedtatt(regulation)

        assert (stated.status, stated.value) == ("stated", date(2019, 12, 12))
        [statement] = stated.statements
        assert statement.date == date(2019, 12, 12)
        assert statement.text is None
        evidence = clause.replace("\n", " ")
        assert statement.evidence == (
            evidence.removeprefix("Forskriften er ") if in_block else evidence
        )

    @pytest.mark.parametrize("in_block", [True, False])
    def test_a_blank_enactment_date_is_held_not_absent(self, in_block: bool) -> None:
        """A stated draft date is a hold, not an absence (Codex test on PR #577)."""
        clause = "Forskriften er vedtatt av kommunestyret xx.xx.2020."
        regulation = ExtractedRegulation(
            title="Forskrift om slam",
            identification_block=clause if in_block else "Forskrift om slam",
            body="§ 1" if in_block else clause,
        )

        stated = stated_vedtatt(regulation)

        assert (stated.status, stated.hold_reason) == ("held", "placeholder_date")
        [statement] = stated.statements
        assert statement.evidence.startswith("Forskriften er vedtatt")
        assert "xx.xx.2020" in statement.evidence
        assert _evidence_is_verbatim(regulation, stated)

    @pytest.mark.parametrize("blank_in_block", [True, False])
    def test_a_dated_statement_does_not_hide_a_blank_one(self, blank_in_block: bool) -> None:
        """Every statement is kept, so one draft blank keeps the date held (Codex, PR #577)."""
        valid = "Forskriften er vedtatt av kommunestyret 12.12.2019."
        blank = "Forskriften er vedtatt av kommunestyret xx.xx.2020."
        regulation = ExtractedRegulation(
            title="Forskrift om slam",
            identification_block=blank if blank_in_block else valid,
            body=valid if blank_in_block else blank,
        )

        stated = stated_vedtatt(regulation)

        assert (stated.status, stated.hold_reason) == ("held", "placeholder_date")
        assert sorted(str(s.date) for s in stated.statements) == ["2019-12-12", "None"]

    def test_a_blank_date_outside_an_enactment_sentence_is_not_a_statement(self) -> None:
        regulation = ExtractedRegulation(
            title="Forskrift om slam",
            identification_block="Forskrift om slam",
            body="§ 1 Søknad sendes innen xx.xx.2020.",
        )

        assert stated_vedtatt(regulation).status == "absent"

    def test_the_same_date_stated_twice_is_one_stated_date(self) -> None:
        regulation, _ = _read(
            "Forskrift om slam, Eksempel kommune",
            "Vedtatt av kommunestyret 12.12.2019.",
            "§ 1 Formål",
            "Forskriften er vedtatt av kommunestyret den 12. desember 2019.",
        )

        stated = stated_vedtatt(regulation)

        assert (stated.status, stated.value, len(stated.statements)) == (
            "stated",
            date(2019, 12, 12),
            2,
        )
        assert _evidence_is_verbatim(regulation, stated)

    def test_two_different_dates_are_held_with_both_kept(self) -> None:
        regulation, fields = _read(
            "Forskrift om slam, Eksempel kommune",
            "Vedtatt av kommunestyret 12.12.2019.",
            "§ 1 Formål",
            "Forskriften er vedtatt av bystyret den 3. mars 2020.",
        )

        stated = stated_vedtatt(regulation)

        assert fields.vedtatt == date(2019, 12, 12)
        assert (stated.status, stated.value, stated.hold_reason) == (
            "held",
            None,
            "conflicting_statements",
        )
        assert [s.date for s in stated.statements] == [date(2019, 12, 12), date(2020, 3, 3)]

    def test_a_statute_date_is_never_the_vedtaksdato(self) -> None:
        regulation, _ = _read(
            "Forskrift om slam, Eksempel kommune",
            "Fastsatt med hjemmel i lov av 14. juni 2002.",
            "§ 1 Formål",
        )

        assert stated_vedtatt(regulation) == StatedDate(status="absent")


class TestIkraft:
    def test_an_invalid_calendar_date_is_kept_as_a_phrase(self) -> None:
        regulation = ExtractedRegulation(
            title="Forskrift om slam",
            identification_block="Forskrift om slam",
            body="Forskriften trer i kraft 31. februar 2020. Gebyret fastsettes årlig.",
        )

        stated = stated_ikraft(regulation)

        assert (stated.status, stated.value, stated.text) == ("text", None, "31. februar 2020")
        [statement] = stated.statements
        assert statement.date is None
        assert statement.text == "31. februar 2020"
        assert statement.evidence == "trer i kraft 31. februar 2020"

    def test_a_phrase_without_a_sentence_terminator_is_kept(self) -> None:
        regulation = ExtractedRegulation(
            title="Forskrift om slam",
            identification_block="Forskrift om slam",
            body="Forskriften trer i kraft når departementet bestemmer",
        )

        stated = stated_ikraft(regulation)

        assert (stated.status, stated.value, stated.text) == (
            "text",
            None,
            "når departementet bestemmer",
        )
        [statement] = stated.statements
        assert statement.date is None
        assert statement.text == "når departementet bestemmer"
        assert statement.evidence == "trer i kraft når departementet bestemmer"

    def test_a_phrase_stops_before_all_following_sentences(self) -> None:
        regulation = ExtractedRegulation(
            title="Forskrift om slam",
            identification_block="Forskrift om slam",
            body=(
                "Forskriften trer i kraft straks. Gebyret fastsettes årlig. Klage sendes kommunen."
            ),
        )

        stated = stated_ikraft(regulation)

        assert (stated.status, stated.text) == ("text", "straks")
        [statement] = stated.statements
        assert statement.date is None
        assert statement.text == "straks"
        assert statement.evidence == "trer i kraft straks"

    def test_a_phrase_without_a_date_is_kept_verbatim(self) -> None:
        regulation, fields = _read(
            "Forskrift om slam, Eksempel kommune",
            "Vedtatt 1.1.2020.",
            "§ 1",
            "Trer i kraft straks.",
        )

        stated = stated_ikraft(regulation)

        assert (stated.status, stated.value, stated.text) == ("text", None, fields.ikraft_text)
        assert [s.evidence for s in stated.statements] == ["Trer i kraft straks"]

    @pytest.mark.parametrize(
        "clauses",
        [
            ("Forskriften trer i kraft 1. januar 2020.", "§ 4 trer i kraft 1. juli 2020."),
            ("Forskriften trer i kraft 1. januar 2020.", "§ 4 trer i kraft straks."),
            ("§ 3 trer i kraft straks.", "§ 4 trer i kraft når departementet bestemmer."),
        ],
    )
    def test_statements_that_disagree_are_held(self, clauses: tuple[str, str]) -> None:
        regulation, _ = _read(
            "Forskrift om slam, Eksempel kommune", "Vedtatt 1.1.2020.", "§ 1 Formål", *clauses
        )

        stated = stated_ikraft(regulation)

        assert (stated.status, stated.value, stated.text) == ("held", None, None)
        assert len(stated.statements) == 2
        assert _evidence_is_verbatim(regulation, stated)

    def test_a_header_and_a_clause_that_agree_are_one_date(self) -> None:
        regulation, _ = _read(
            "Forskrift om slam, Eksempel kommune",
            "Ikrafttredelse: 01.01.2020",
            "§ 1",
            "Forskriften trer i kraft 1. januar 2020.",
        )

        stated = stated_ikraft(regulation)

        assert (stated.status, stated.value) == ("stated", date(2020, 1, 1))
        assert [s.evidence for s in stated.statements] == [
            "Ikrafttredelse: 01.01.2020",
            "trer i kraft 1. januar 2020",
        ]

    def test_a_draft_blank_date_is_held(self) -> None:
        regulation = ExtractedRegulation(
            title="Forskrift om slam",
            identification_block="Forskrift om slam",
            body="§ 1\nForskriften trer i kraft xx.xx.2020.",
        )

        stated = stated_ikraft(regulation)

        assert (stated.status, stated.hold_reason) == ("held", "placeholder_date")

    def test_no_statement_is_absent(self) -> None:
        regulation, _ = _read("Forskrift om slam, Eksempel kommune", "Vedtatt 1.1.2020.", "§ 1")

        assert stated_ikraft(regulation) == StatedDate(status="absent")
