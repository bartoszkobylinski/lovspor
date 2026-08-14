"""Unit tests for serving-side not-in-force classification (ADR-0009 T0).

Fixture note lines are verbatim from the lovverk corpus at ``24ce112aa``
(burettslagslova, cotif-loven, bilansvarslova, opplæringslova, advokatloven)
— the measured shapes the ADR names, not invented ones.
"""

from datetime import date

import pytest
from pydantic import ValidationError

from lovspor.temporal import (
    AmendmentEvent,
    InForceStatus,
    MarkerClass,
    TemporalNotice,
    append_notice,
    build_notice,
    extract_events,
    extract_never_in_force,
    in_force_at,
    render_notice,
)

EVAL = date(2026, 8, 14)

# lover/burettslagslova-brl.md:815 — nynorsk periphrastic future + delegated
# commencement. The notebook prototype missed this form entirely.
PERIPHRASTIC_PENDING = (
    "> **Vert endra** ved lov [19 juni 2026 nr. 48](lov/2026-06-19-48) "
    "(i kraft frå den tid Kongen bestemmer).\n"
)

# lover/cotif-loven.md:41 — ADR-0009 fixture: the relative marker carries two
# years that belong to a referenced instrument and MUST NOT resolve to a date.
COTIF_RELATIVE = (
    "> Endret ved [lov 19 juni 2009 nr. 107](lov/2009-06-19-107) "
    "(ikr. 3 sep 2010 iflg. [res. 3 sep 2010 nr. 1239](forskrift/2010-09-03-1239)). "
    "**Endres** ved lov [20 juni 2023 nr. 81](lov/2023-06-20-81) "
    "(i kraft samtidig som endringene av 30. september 2015 og 26. september 2018 "
    "i Overenskomst om internasjonal jernbanetrafikk iflg. "
    "[res. 20 juni 2023 nr. 957](forskrift/2023-06-20-957)).\n"
)

# lover/bilansvarslova-bal.md:214 — head verb uses ``med`` for ``ved``, four
# past events with arrived dates, then one announced pending event. Kind and
# announced-ness resolve from the nearest preceding verb, never the head verb.
MIXED_MED_NOTE = (
    "> Endra med lover [27 nov 1992 nr. 113](lov/1992-11-27-113) (ikr. 1 jan 1994), "
    "[21 juni 2002 nr. 41](lov/2002-06-21-41) (ikr. 1 jan 2003), "
    "[8 juni 2007 nr. 19](lov/2007-06-08-19) (ikr. 11 juni 2007), "
    "[26 mai 2020 nr. 46](lov/2020-05-26-46) (ikr. 1 jan 2021 iflg. "
    "[res. 26 mai 2020 nr. 1054](forskrift/2020-05-26-1054)). "
    "**Vert endra** ved lov [20 juni 2025 nr. 80](lov/2025-06-20-80) "
    "(i kraft frå den tid Kongen bestemmer).\n"
)

# lover/opplæringslova.md:101 — announced amendment with an explicit future
# commencement date, after past events including the same act twice with two
# scoped dates (partial commencement; must not collapse).
DATED_FUTURE_NOTE = (
    "> Endra ved lover [25 juni 2024 nr. 53](lov/2024-06-25-53) "
    "(i kraft 1 juli 2024 iflg. [res. 25 juni 2024 nr. 1212](forskrift/2024-06-25-1212)), "
    "[25 juni 2024 nr. 53](lov/2024-06-25-53) (i kraft 1 juli 2026), "
    "[12 juni 2026 nr. 22](lov/2026-06-12-22) (i kraft 1 juli 2026 iflg. "
    "[res. 12 juni 2026 nr. 1076](forskrift/2026-06-12-1076)). "
    "**Vert endra** ved lov [12 juni 2026 nr. 22](lov/2026-06-12-22) "
    "(i kraft 1 juli 2028).\n"
)

PAST_DATED_NOTE = "> Endret ved lov [21 juni 2002 nr. 41](lov/2002-06-21-41) (ikr. 1 jan 2003).\n"

ABSENT_MARKER_NOTE = "> Endret ved lov [17 juni 2005 nr. 62](lov/2005-06-17-62).\n"

# lover/advokatloven.md:383 — footnote body line for a provision never brought
# into force.
NEVER_IN_FORCE_DOC = (
    "### § 12. Organisering av advokatvirksomhet\n"
    "\n"
    "Advokatvirksomhet kan organiseres.1\n"
    "\n"
    "1 Tredje ledd er ikke satt i kraft (i kraft fra den tid Kongen bestemmer).\n"
)


def _doc(note: str, heading: str = "### § 1. Formål") -> str:
    return f"{heading}\n\nLovtekst.\n\n{note}"


class TestExtractEvents:
    def test_periphrastic_future_is_announced_pending(self) -> None:
        events = extract_events(_doc(PERIPHRASTIC_PENDING))
        assert len(events) == 1
        event = events[0]
        assert event.announced is True
        assert event.kind == "amended"
        assert event.marker_class is MarkerClass.PENDING_INDETERMINATE
        assert event.valid_from is None
        assert event.amending_act == "19 juni 2026 nr. 48"

    def test_relative_marker_never_resolves_to_a_date(self) -> None:
        events = extract_events(_doc(COTIF_RELATIVE))
        assert len(events) == 2
        past, announced = events
        assert past.announced is False
        assert past.marker_class is MarkerClass.EXPLICIT_DATE
        assert past.valid_from == date(2010, 9, 3)
        assert announced.announced is True
        assert announced.marker_class is MarkerClass.RELATIVE
        assert announced.valid_from is None

    def test_mixed_med_note_attributes_by_nearest_verb(self) -> None:
        events = extract_events(_doc(MIXED_MED_NOTE))
        assert len(events) == 5
        assert [e.announced for e in events] == [False, False, False, False, True]
        assert events[0].valid_from == date(1994, 1, 1)
        assert events[4].marker_class is MarkerClass.PENDING_INDETERMINATE

    def test_partial_commencement_keeps_separate_events(self) -> None:
        events = extract_events(_doc(DATED_FUTURE_NOTE))
        same_act = [e for e in events if e.amending_act == "25 juni 2024 nr. 53"]
        assert {e.valid_from for e in same_act} == {date(2024, 7, 1), date(2026, 7, 1)}

    def test_announced_with_future_date(self) -> None:
        events = extract_events(_doc(DATED_FUTURE_NOTE))
        assert events[-1].announced is True
        assert events[-1].marker_class is MarkerClass.EXPLICIT_DATE
        assert events[-1].valid_from == date(2028, 7, 1)

    def test_absent_marker_is_absent_not_guessed(self) -> None:
        events = extract_events(_doc(ABSENT_MARKER_NOTE))
        assert len(events) == 1
        assert events[0].marker_class is MarkerClass.ABSENT
        assert events[0].valid_from is None

    def test_unrecognised_marker_yields_no_date(self) -> None:
        note = (
            "> Endres ved lov [1 jan 2027 nr. 1](lov/2027-01-01-1) "
            "(i kraft når departementet bestemmer).\n"
        )
        events = extract_events(_doc(note))
        assert events[0].marker_class is MarkerClass.UNRECOGNISED
        assert events[0].valid_from is None

    def test_invalid_calendar_date_fails_loudly(self) -> None:
        note = "> Endres ved lov [1 jan 2027 nr. 1](lov/2027-01-01-1) (i kraft 31 februar 2027).\n"
        with pytest.raises(ValueError, match="day is out of range for month"):
            extract_events(_doc(note))

    def test_ordinary_prose_is_not_parsed_as_an_amendment_note(self) -> None:
        markdown = (
            "### § 1. Formål\n\n"
            "Loven endres ved lov 1 januar 2027 nr. 1, men dette er vanlig brødtekst.\n"
        )
        assert extract_events(markdown) == []

    def test_provision_attribution_tracks_headings(self) -> None:
        markdown = (
            "### § 1. Formål\n\nTekst.\n\n### § 2. Virkeområde\n\nTekst.\n\n" + PERIPHRASTIC_PENDING
        )
        events = extract_events(markdown)
        assert events[0].provision == "§ 2"

    def test_scope_prefixed_note(self) -> None:
        note = (
            "> Kapitlet tilføyd ved lov [17 juni 2005 nr. 62](lov/2005-06-17-62) "
            "(ikr. 1 jan 2006).\n"
        )
        events = extract_events(_doc(note))
        assert events[0].kind == "inserted"
        assert events[0].scope == "chapter"


class TestInForceAt:
    def test_dated_past_is_in_force(self) -> None:
        event = _event(MarkerClass.EXPLICIT_DATE, valid_from=date(2003, 1, 1))
        assert in_force_at(event, EVAL) is InForceStatus.IN_FORCE

    def test_dated_future_is_not_in_force(self) -> None:
        event = _event(MarkerClass.EXPLICIT_DATE, valid_from=date(2028, 7, 1))
        assert in_force_at(event, EVAL) is InForceStatus.NOT_IN_FORCE

    def test_commencement_day_itself_is_in_force(self) -> None:
        event = _event(MarkerClass.EXPLICIT_DATE, valid_from=EVAL)
        assert in_force_at(event, EVAL) is InForceStatus.IN_FORCE

    def test_pending_is_not_in_force(self) -> None:
        event = _event(MarkerClass.PENDING_INDETERMINATE)
        assert in_force_at(event, EVAL) is InForceStatus.NOT_IN_FORCE

    @pytest.mark.parametrize(
        "marker_class",
        [MarkerClass.RELATIVE, MarkerClass.UNRECOGNISED, MarkerClass.ABSENT],
    )
    def test_epistemic_classes_are_indeterminate(self, marker_class: MarkerClass) -> None:
        assert in_force_at(_event(marker_class), EVAL) is InForceStatus.INDETERMINATE

    def test_total_over_every_marker_class(self) -> None:
        for marker_class in MarkerClass:
            valid_from = EVAL if marker_class is MarkerClass.EXPLICIT_DATE else None
            status = in_force_at(_event(marker_class, valid_from=valid_from), EVAL)
            assert isinstance(status, InForceStatus)


class TestAmendmentEventValidation:
    def test_explicit_date_requires_valid_from(self) -> None:
        with pytest.raises(ValidationError, match="valid_from is set exactly"):
            _event(MarkerClass.EXPLICIT_DATE)

    def test_non_dated_marker_rejects_valid_from(self) -> None:
        with pytest.raises(ValidationError, match="valid_from is set exactly"):
            _event(MarkerClass.UNRECOGNISED, valid_from=EVAL)


class TestNeverInForce:
    def test_body_marker_detected_and_attributed(self) -> None:
        markers = extract_never_in_force(NEVER_IN_FORCE_DOC)
        assert len(markers) == 1
        assert markers[0].provision == "§ 12"
        assert "ikke satt i kraft" in markers[0].text

    def test_note_lines_are_not_double_counted(self) -> None:
        assert extract_never_in_force(_doc(PERIPHRASTIC_PENDING)) == []


class TestBuildNotice:
    def test_no_events_means_no_notice(self) -> None:
        assert build_notice(_doc(PAST_DATED_NOTE), EVAL) is None

    def test_epistemic_unknown_gets_no_notice(self) -> None:
        # Amended ADR-0009 §3b: unknown is an epistemic state, not a finding
        # of not-in-force — no banner.
        assert build_notice(_doc(ABSENT_MARKER_NOTE), EVAL) is None

    def test_announced_event_triggers_notice(self) -> None:
        notice = build_notice(_doc(PERIPHRASTIC_PENDING), EVAL)
        assert notice is not None
        assert notice.evaluation_date == EVAL
        assert len(notice.events) == 1

    def test_notice_filters_in_force_events(self) -> None:
        notice = build_notice(_doc(MIXED_MED_NOTE), EVAL)
        assert notice is not None
        assert len(notice.events) == 1
        assert notice.events[0].announced is True

    def test_never_in_force_triggers_notice(self) -> None:
        notice = build_notice(NEVER_IN_FORCE_DOC, EVAL)
        assert notice is not None
        assert notice.events == []
        assert len(notice.never_in_force) == 1

    def test_default_provision_labels_headingless_body(self) -> None:
        notice = build_notice(PERIPHRASTIC_PENDING, EVAL, default_provision="§ 4-2")
        assert notice is not None
        assert notice.events[0].provision == "§ 4-2"


class TestRendering:
    def test_render_is_deterministic(self) -> None:
        first = _render(_doc(DATED_FUTURE_NOTE))
        second = _render(_doc(DATED_FUTURE_NOTE))
        assert first == second

    def test_render_carries_evaluation_date_and_verbatim_marker(self) -> None:
        rendered = _render(_doc(DATED_FUTURE_NOTE))
        assert "2026-08-14" in rendered
        assert "i kraft 1 juli 2028" in rendered

    def test_append_notice_without_events_is_byte_identical(self) -> None:
        markdown = _doc(PAST_DATED_NOTE)
        assert append_notice(markdown, EVAL) == markdown

    def test_append_notice_keeps_document_prefix(self) -> None:
        markdown = _doc(PERIPHRASTIC_PENDING)
        appended = append_notice(markdown, EVAL)
        assert appended.startswith(markdown)
        assert "Kongen bestemmer" in appended[len(markdown) :]


def _event(
    marker_class: MarkerClass,
    valid_from: date | None = None,
) -> AmendmentEvent:
    return AmendmentEvent(
        provision="§ 1",
        scope="provision",
        kind="amended",
        announced=False,
        amending_act="1 jan 2020 nr. 1",
        marker_class=marker_class,
        valid_from=valid_from,
        raw_marker=None,
        source_line=1,
    )


def _render(markdown: str) -> str:
    notice = build_notice(markdown, EVAL)
    assert notice is not None
    assert isinstance(notice, TemporalNotice)
    return render_notice(notice)
