"""Behavioral regressions for received-text mutation survivors; synthetic inputs."""

import pytest

from lovspor.promotion.extract import extract_received
from lovspor.promotion.fields import instrument_issuer
from lovspor.promotion.lovdata_markup import residual_markup, strip_markup
from lovspor.promotion.models import ExtractionHoldReason, HeldExtraction, ReceivedForm, SourceForm
from tests.unit.promotion_fixtures import html_page
from tests.unit.test_promotion_lovdata_markup import CORRECTIONS, PRINT_HEAD, TITLE


def test_residual_print_stamp_can_wrap_between_lines() -> None:
    assert residual_markup(("Utskrift", "fra Lovdata"))


def test_contents_before_metadata_does_not_bound_the_metadata() -> None:
    lines = ("Innholdsfortegnelse", *PRINT_HEAD[1:11], TITLE, "§ 1. Orden")
    stripped = strip_markup(lines, ReceivedForm.LOVDATA_PRINT)
    assert stripped is not None
    assert stripped.lines == (TITLE, "§ 1. Orden")


def test_print_without_a_second_contents_keeps_all_body_lines() -> None:
    lines = (*PRINT_HEAD[:11], TITLE, "§ 1. Orden")
    stripped = strip_markup(lines, ReceivedForm.LOVDATA_PRINT)
    assert stripped is not None
    assert stripped.lines == (TITLE, "§ 1. Orden")


@pytest.mark.parametrize("entries", [(), ("Kapittel I. Orden",)])
def test_contents_at_end_of_print_is_removed_without_reading_past_end(
    entries: tuple[str, ...],
) -> None:
    stripped = strip_markup(
        (*PRINT_HEAD[:11], TITLE, "Innholdsfortegnelse", *entries),
        ReceivedForm.LOVDATA_PRINT,
    )
    assert stripped is not None
    assert stripped.lines == (TITLE,)


def test_repeated_correction_links_cannot_silently_pass_the_residual_gate() -> None:
    # Only one correction banner per extracted line is consumed. A second one
    # is unexpected markup and must keep the received document on hold.
    lines = ("NORSK LOVTIDEND", f"{CORRECTIONS} mellomtekst {CORRECTIONS}")
    held = extract_received(html_page(lines), "text/html")
    assert isinstance(held, HeldExtraction)
    assert held.reason is ExtractionHoldReason.LOVDATA_COPY
    assert held.detail == "Lovdata's markup remains after removal"


@pytest.mark.parametrize(
    ("lines", "reason", "detail"),
    [
        (("Utskrift fra Lovdata", TITLE), ExtractionHoldReason.LOVDATA_COPY, "cannot be bounded"),
        (
            (*PRINT_HEAD[:11], "Se lovdata.no"),
            ExtractionHoldReason.LOVDATA_COPY,
            "remains after removal",
        ),
        ((TITLE,), ExtractionHoldReason.EMPTY_TEXT, "under 200 characters"),
    ],
)
def test_received_holds_preserve_the_recognized_source_format(
    lines: tuple[str, ...], reason: ExtractionHoldReason, detail: str
) -> None:
    held = extract_received(html_page(lines), "text/html")
    assert isinstance(held, HeldExtraction)
    assert held.reason is reason
    assert detail in held.detail
    assert held.source_form is SourceForm.HTML


def test_received_length_gate_counts_text_without_inserted_separator_characters() -> None:
    # 196 visible characters and one normalized separating space: below 200.
    lines = ("og " * 32 + "og", "og " * 32 + "og")
    assert len(" ".join(lines)) == 197
    held = extract_received(html_page(lines), "text/html")
    assert isinstance(held, HeldExtraction)
    assert held.reason is ExtractionHoldReason.EMPTY_TEXT


def test_wrapped_state_instrument_has_spaces_in_returned_issuer() -> None:
    assert instrument_issuer("Fastsatt ved kongelig\nresolusjon 1. januar 2001.") == (
        "kongelig resolusjon"
    )
