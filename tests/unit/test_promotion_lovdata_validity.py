"""A repealed regulation's validity, read from Lovdata's block before removal (#639).

Lovdata stamps a repealed regulation ``OPPHEVET`` and writes its validity as a
range on the ``Ikrafttredelse`` line. Both lines are markup and are removed, so
the facts are read from them first, the way the ``Dato`` line is kept for
identity. A block that states neither leaves both out: nothing is inferred.

Every line below is synthetic, built on the fixtures of
``test_promotion_lovdata_markup.py``; no Lovdata text enters the repository.
"""

from __future__ import annotations

from datetime import date

import pytest

from lovspor.promotion import ExtractedDocument, HeldExtraction
from lovspor.promotion.extract import extract_received, extract_regulation
from lovspor.promotion.lovdata_header import header_validity
from lovspor.promotion.lovdata_markup import strip_markup
from lovspor.promotion.models import HeaderValidity, ReceivedForm, RegulationStatus
from tests.unit.promotion_fixtures import minimal_docx, minimal_pdf
from tests.unit.test_promotion_lovdata_markup import (
    BODY,
    DOCX_HEAD,
    LOVDATA_PRINT,
    LOVTIDEND,
    ORIGINAL,
    PREAMBLE,
    PRINT_HEAD,
    TITLE,
)

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
RANGE = "03.02.2001 \u2013 31.03.2001"
REPEALED_HEAD = (
    PRINT_HEAD[0],
    TITLE,
    "OPPHEVET",
    PRINT_HEAD[2],
    PRINT_HEAD[3],
    f"Ikrafttredelse {RANGE}",
    *PRINT_HEAD[5:],
)
REPEALED_PRINT = (*REPEALED_HEAD, PREAMBLE, *BODY)
REPEALED = HeaderValidity(status=RegulationStatus.REPEALED, ikraft_end=date(2001, 3, 31))


def _validity(lines: tuple[str, ...]) -> HeaderValidity:
    stripped = strip_markup(lines, ReceivedForm.LOVDATA_PRINT)
    assert stripped is not None
    return stripped.validity


def _received(payload: bytes, content_type: str = PDF) -> ExtractedDocument:
    result = extract_received(payload, content_type)
    assert isinstance(result, ExtractedDocument), result
    return result


def _docx_head(ikraft: str, *, stamp: bool) -> tuple[str, ...]:
    head = list(DOCX_HEAD)
    head[4] = ikraft
    if stamp:
        head.insert(1, "OPPHEVET")
    return tuple(head)


class TestHeaderValidity:
    def test_the_stamp_and_the_range_are_read(self) -> None:
        assert header_validity(REPEALED_HEAD) == REPEALED

    def test_a_block_stating_neither_is_empty(self) -> None:
        assert header_validity(PRINT_HEAD) == HeaderValidity()

    def test_a_single_ikraft_date_states_no_end(self) -> None:
        head = tuple(line for line in REPEALED_HEAD if line != f"Ikrafttredelse {RANGE}")
        assert header_validity((*head, "Ikrafttredelse 03.02.2001")).ikraft_end is None

    @pytest.mark.parametrize(
        "ikraft",
        [
            "Ikrafttredelse 03.02.2001 - 31.03.2001",
            "Ikrafttredelse 03.02.2001\u201431.03.2001",
            "Ikrafttredelse: 03.02.2001 \u2013 31.03.2001",
            "Ikrafttredelse 3.2.2001 \u2013 31.3.2001",
        ],
    )
    def test_a_range_is_read_whatever_its_dash(self, ikraft: str) -> None:
        assert header_validity((ikraft,)).ikraft_end == date(2001, 3, 31)

    @pytest.mark.parametrize(
        "ikraft",
        [
            "Ikrafttredelse 03.02.2001 \u2013 31.02.2001",
            "Ikrafttredelse 03.02.2001 \u2013 31.03.01",
            "Ikrafttredelse 03.02.2001 \u2013",
            "Ikrafttredelse 03.02.2001 \u2013 31.03.2001 og 01.05.2001",
            "ikrafttredelse 03.02.2001 \u2013 31.03.2001",
        ],
    )
    def test_a_range_that_is_not_two_real_dates_states_no_end(self, ikraft: str) -> None:
        assert header_validity((ikraft,)).ikraft_end is None

    @pytest.mark.parametrize("stamp", ["Opphevet", "OPPHEVET ved forskrift", "IKKE OPPHEVET"])
    def test_only_the_stamp_line_is_the_stamp(self, stamp: str) -> None:
        assert header_validity((stamp,)).status is None

    def test_a_docx_export_states_the_range_on_the_line_after_its_label(self) -> None:
        head = _docx_head(RANGE, stamp=True)
        assert header_validity(head) == REPEALED

    def test_a_bare_label_on_the_last_line_states_no_end(self) -> None:
        assert header_validity(("Ikrafttredelse",)).ikraft_end is None

    @pytest.mark.parametrize("start", ["31.02.2001", "29.02.2001", "00.02.2001", "03.13.2001"])
    def test_an_invalid_start_date_states_no_range_end(self, start: str) -> None:
        # lovdata_header's contract requires *two* real calendar dates.
        assert header_validity((f"Ikrafttredelse {start} - 31.03.2001",)).ikraft_end is None

    def test_a_leap_day_is_a_real_range_endpoint(self) -> None:
        assert header_validity(("Ikrafttredelse 28.02.2000 - 29.02.2000",)).ikraft_end == date(
            2000, 2, 29
        )


class TestStripMarkup:
    def test_a_repealed_print_carries_its_validity(self) -> None:
        assert _validity(REPEALED_PRINT) == REPEALED

    def test_a_print_in_force_carries_none(self) -> None:
        assert _validity(LOVDATA_PRINT) == HeaderValidity()

    def test_the_stamp_in_the_regulation_text_is_not_read(self) -> None:
        assert _validity((*LOVDATA_PRINT, "OPPHEVET")).status is None

    def test_a_range_in_the_regulation_text_is_not_read(self) -> None:
        assert _validity((*LOVDATA_PRINT, f"Ikrafttredelse {RANGE}")).ikraft_end is None

    def test_the_published_lines_are_those_of_the_print_in_force(self) -> None:
        repealed = strip_markup(REPEALED_PRINT, ReceivedForm.LOVDATA_PRINT)
        in_force = strip_markup(LOVDATA_PRINT, ReceivedForm.LOVDATA_PRINT)
        assert repealed is not None and in_force is not None
        assert repealed.lines == in_force.lines
        assert repealed.identification == in_force.identification

    @pytest.mark.parametrize(
        ("lines", "form"),
        [(LOVTIDEND, ReceivedForm.LOVTIDEND), (ORIGINAL, ReceivedForm.AUTHORITY_ORIGINAL)],
    )
    def test_other_forms_carry_none(self, lines: tuple[str, ...], form: ReceivedForm) -> None:
        stripped = strip_markup((*lines, "OPPHEVET", f"Ikrafttredelse {RANGE}"), form)
        assert stripped is not None
        assert stripped.validity == HeaderValidity()


class TestExtractReceived:
    @pytest.mark.parametrize(
        ("stamp", "ikraft", "status", "end"),
        [
            (True, "03.02.2001", RegulationStatus.REPEALED, None),
            (False, RANGE, None, date(2001, 3, 31)),
        ],
    )
    def test_status_and_range_end_are_independent(
        self, stamp: bool, ikraft: str, status: RegulationStatus | None, end: date | None
    ) -> None:
        lines = (*_docx_head(ikraft, stamp=stamp), PREAMBLE, *BODY)
        fields = _received(minimal_docx(lines), DOCX).fields
        assert (fields.status, fields.ikraft_end) == (status, end)

    def test_the_fields_say_repealed_and_when_it_stopped_applying(self) -> None:
        fields = _received(minimal_pdf(REPEALED_PRINT)).fields
        assert fields.status is RegulationStatus.REPEALED
        assert fields.ikraft_end == date(2001, 3, 31)

    def test_a_docx_export_says_the_same(self) -> None:
        lines = (*_docx_head(RANGE, stamp=True), PREAMBLE, *BODY)
        fields = _received(minimal_docx(lines), DOCX).fields
        assert (fields.status, fields.ikraft_end) == (REPEALED.status, REPEALED.ikraft_end)

    def test_a_print_in_force_states_neither(self) -> None:
        fields = _received(minimal_pdf(LOVDATA_PRINT)).fields
        assert (fields.status, fields.ikraft_end) == (None, None)

    def test_ikraft_is_not_filled_from_the_range(self) -> None:
        in_force = _received(minimal_pdf(LOVDATA_PRINT)).fields
        assert _received(minimal_pdf(REPEALED_PRINT)).fields.ikraft == in_force.ikraft

    @pytest.mark.parametrize("stated", ["1. februar 2001", "straks"])
    def test_the_texts_explicit_ikraft_survives_a_different_header_range(self, stated: str) -> None:
        # operations.md #639: ikraft stays the text's statement, never the range start.
        body = (*BODY, "§ 3. Ikrafttredelse", f"Forskriften trer i kraft {stated}.")
        fields = _received(minimal_docx((*REPEALED_HEAD, PREAMBLE, *body)), DOCX).fields
        assert fields.ikraft == (date(2001, 2, 1) if stated != "straks" else None)
        assert fields.ikraft_text == ("straks" if stated == "straks" else None)
        assert (fields.status, fields.ikraft_end) == (REPEALED.status, REPEALED.ikraft_end)

    def test_an_invalid_range_does_not_discard_the_repeal_stamp(self) -> None:
        lines = (*_docx_head("03.02.2001 - 31.02.2001", stamp=True), PREAMBLE, *BODY)
        fields = _received(minimal_docx(lines), DOCX).fields
        assert fields.status is RegulationStatus.REPEALED
        assert fields.ikraft_end is None

    def test_the_extracted_text_does_not_move(self) -> None:
        repealed = _received(minimal_pdf(REPEALED_PRINT)).regulation
        assert repealed == _received(minimal_pdf(LOVDATA_PRINT)).regulation

    def test_the_observed_channel_still_holds_it(self) -> None:
        assert isinstance(extract_regulation(minimal_pdf(REPEALED_PRINT), PDF), HeldExtraction)
