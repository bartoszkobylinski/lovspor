"""Unit tests for the ``get_temporal_events`` serving composition (ADR-0012).

Covers the pure composition layer only — derivation strictness, mechanical
``section_id`` narrowing, optional ``valid_at`` evaluation bounded by the
knowledge horizon, and response shape. Corpus/state resolution and the
reconciliation field are the MCP layer's job and are tested there.

Fixture note lines follow the measured corpus shapes pinned by
``test_temporal.py`` (dated, periphrastic pending, relative, unrecognised),
not invented ones.
"""

import json
from datetime import date

import pytest

from lovspor.temporal import TEMPORAL_PARSER_VERSION, TemporalDerivationError
from lovspor.temporal_events import TemporalEventsRequest, compose_temporal_events

HORIZON = date(2026, 6, 1)

DATED_NOTE = (
    "> Endret ved [lov 19 juni 2009 nr. 107](lov/2009-06-19-107) "
    "(ikr. 3 sep 2010 iflg. [res. 3 sep 2010 nr. 1239](forskrift/2010-09-03-1239)).\n"
)

PENDING_NOTE = (
    "> **Vert endra** ved lov [19 juni 2026 nr. 48](lov/2026-06-19-48) "
    "(i kraft frå den tid Kongen bestemmer).\n"
)

RELATIVE_NOTE = (
    "> **Endres** ved lov [20 juni 2023 nr. 81](lov/2023-06-20-81) "
    "(i kraft samtidig som endringene av 30. september 2015 og 26. september 2018 "
    "i Overenskomst om internasjonal jernbanetrafikk iflg. "
    "[res. 20 juni 2023 nr. 957](forskrift/2023-06-20-957)).\n"
)

UNRECOGNISED_NOTE = (
    "> Endres ved lov [1 jan 2027 nr. 1](lov/2027-01-01-1) (i kraft når departementet bestemmer).\n"
)

REPEAL_NOTE = (
    "> Opphevet ved [lov 19 juni 2009 nr. 107](lov/2009-06-19-107) "
    "(ikr. 3 sep 2010 iflg. [res. 3 sep 2010 nr. 1239](forskrift/2010-09-03-1239)).\n"
)

TWO_SECTION_BODY = (
    "## Kapittel 1. Innleiande føresegner\n\n"
    f"{PENDING_NOTE}\n"
    "### § 1-1. Formål\n\n"
    "Lovtekst.\n\n"
    f"{DATED_NOTE}\n"
    "### § 8-7 a. Særskilde reglar\n\n"
    "Paragrafen er ikke satt i kraft.\n\n"
    f"{RELATIVE_NOTE}\n"
)


def _request(**overrides: object) -> TemporalEventsRequest:
    base: dict[str, object] = {"horizon": HORIZON}
    return TemporalEventsRequest.model_validate(base | overrides)


def _doc(note: str, heading: str = "### § 1. Formål") -> str:
    return f"{heading}\n\nLovtekst.\n\n{note}"


# ---------- unevaluated responses (no valid_at) ----------


def test_serves_events_problems_and_markers_from_the_body() -> None:
    result = compose_temporal_events(TWO_SECTION_BODY, _request())

    assert result["temporal_parser_version"] == TEMPORAL_PARSER_VERSION
    assert [event["provision"] for event in result["events"]] == [
        "Kapittel 1. Innleiande føresegner",
        "§ 1-1",
        "§ 8-7 a",
    ]
    assert [marker["provision"] for marker in result["never_in_force"]] == ["§ 8-7 a"]
    assert result["problems"] == []


def test_without_valid_at_no_evaluation_fields_are_served() -> None:
    result = compose_temporal_events(TWO_SECTION_BODY, _request())

    assert "valid_at" not in result
    assert "knowledge_horizon" not in result
    for event in result["events"]:
        assert "commencement_status" not in event
        assert "status_reason" not in event
    for marker in result["never_in_force"]:
        assert "commencement_status" not in marker


def test_unevaluated_response_is_byte_stable() -> None:
    first = json.dumps(compose_temporal_events(TWO_SECTION_BODY, _request()), sort_keys=True)
    second = json.dumps(compose_temporal_events(TWO_SECTION_BODY, _request()), sort_keys=True)

    assert first == second


def test_relative_marker_serves_as_success_with_no_guessed_date() -> None:
    """ADR-0009's cotif contract: relative markers are events, not failures."""
    result = compose_temporal_events(_doc(RELATIVE_NOTE), _request())

    (event,) = result["events"]
    assert event["marker_class"] == "relative"
    assert event["commencement_kind"] == "ambiguous"
    assert event["valid_from"] is None


def test_zero_events_is_a_successful_empty_answer() -> None:
    result = compose_temporal_events("### § 1. Formål\n\nLovtekst.\n", _request())

    assert result["events"] == []
    assert result["never_in_force"] == []
    assert result["problems"] == []


# ---------- strict derivation (ADR-0012 point 3) ----------


def test_unrecognised_marker_is_a_typed_derivation_failure() -> None:
    with pytest.raises(TemporalDerivationError):
        compose_temporal_events(_doc(UNRECOGNISED_NOTE), _request())


def test_derivation_failure_serves_no_partial_layer() -> None:
    body = f"{_doc(DATED_NOTE)}\n{UNRECOGNISED_NOTE}"

    with pytest.raises(TemporalDerivationError):
        compose_temporal_events(body, _request())


# ---------- valid_at evaluation with the knowledge horizon ----------


def test_evaluated_response_echoes_valid_at_and_horizon() -> None:
    result = compose_temporal_events(
        TWO_SECTION_BODY,
        _request(valid_at=date(2026, 1, 1)),
    )

    assert result["valid_at"] == "2026-01-01"
    assert result["knowledge_horizon"] == HORIZON.isoformat()


def test_every_evaluated_object_carries_the_verdict_pair() -> None:
    """ADR-0012 point 5: no unevaluated raw marker in an evaluated response."""
    result = compose_temporal_events(
        TWO_SECTION_BODY,
        _request(valid_at=date(2026, 1, 1)),
    )

    for obj in [*result["events"], *result["never_in_force"]]:
        assert obj["commencement_status"] in {"in_effect", "not_in_effect", "indeterminate"}
        assert "status_reason" in obj


def test_dated_event_decides_itself_on_both_sides_of_its_date() -> None:
    before = compose_temporal_events(_doc(DATED_NOTE), _request(valid_at=date(2010, 9, 2)))
    on_the_day = compose_temporal_events(_doc(DATED_NOTE), _request(valid_at=date(2010, 9, 3)))

    assert before["events"][0]["commencement_status"] == "not_in_effect"
    assert on_the_day["events"][0]["commencement_status"] == "in_effect"


def test_pending_within_horizon_is_not_in_effect() -> None:
    result = compose_temporal_events(_doc(PENDING_NOTE), _request(valid_at=date(2026, 5, 1)))

    (event,) = result["events"]
    assert event["commencement_status"] == "not_in_effect"
    assert event["status_reason"] is None


def test_pending_past_horizon_is_indeterminate_beyond_knowledge_horizon() -> None:
    """An old state must never certify what the King decided after it."""
    result = compose_temporal_events(_doc(PENDING_NOTE), _request(valid_at=date(2026, 7, 1)))

    (event,) = result["events"]
    assert event["commencement_status"] == "indeterminate"
    assert event["status_reason"] == "beyond_knowledge_horizon"


def test_dated_event_answers_even_past_the_horizon() -> None:
    result = compose_temporal_events(_doc(DATED_NOTE), _request(valid_at=date(2026, 7, 1)))

    assert result["events"][0]["commencement_status"] == "in_effect"
    assert result["events"][0]["status_reason"] is None


def test_relative_marker_evaluates_to_indeterminate() -> None:
    result = compose_temporal_events(_doc(RELATIVE_NOTE), _request(valid_at=date(2026, 1, 1)))

    assert result["events"][0]["commencement_status"] == "indeterminate"


def test_commenced_repeal_is_in_effect() -> None:
    """The REPEAL operates — deliberately not a provision-validity claim."""
    result = compose_temporal_events(_doc(REPEAL_NOTE), _request(valid_at=date(2026, 1, 1)))

    (event,) = result["events"]
    assert event["kind"] == "repealed"
    assert event["commencement_status"] == "in_effect"


def test_never_in_force_marker_is_bounded_by_the_horizon() -> None:
    body = "### § 2. Unnatak\n\nParagrafen er ikke satt i kraft.\n"
    within = compose_temporal_events(body, _request(valid_at=date(2026, 5, 1)))
    past = compose_temporal_events(body, _request(valid_at=date(2026, 7, 1)))

    assert within["never_in_force"][0]["commencement_status"] == "not_in_effect"
    assert past["never_in_force"][0]["commencement_status"] == "indeterminate"
    assert past["never_in_force"][0]["status_reason"] == "beyond_knowledge_horizon"


def test_no_served_field_is_named_bare_in_force() -> None:
    result = compose_temporal_events(
        TWO_SECTION_BODY,
        _request(valid_at=date(2026, 1, 1)),
    )

    assert "in_force" not in json.dumps(result).replace("never_in_force", "")


# ---------- mechanical section_id narrowing (ADR-0012 point 7) ----------


def test_narrowing_filters_to_the_attributed_provision_label() -> None:
    result = compose_temporal_events(TWO_SECTION_BODY, _request(section_id="1-1"))

    assert [event["provision"] for event in result["events"]] == ["§ 1-1"]
    assert result["never_in_force"] == []


def test_narrowing_matches_the_corpus_spelling_variants() -> None:
    """``§ 8-7 a`` in the heading and ``8-7a`` in the request name one label."""
    result = compose_temporal_events(TWO_SECTION_BODY, _request(section_id="8-7a"))

    assert [event["provision"] for event in result["events"]] == ["§ 8-7 a"]
    assert [marker["provision"] for marker in result["never_in_force"]] == ["§ 8-7 a"]


def test_chapter_scoped_events_are_not_expanded_into_sections() -> None:
    """No containment inference: a chapter event never enters a § answer."""
    result = compose_temporal_events(TWO_SECTION_BODY, _request(section_id="1-1"))

    assert all("Kapittel" not in event["provision"] for event in result["events"])


def test_problems_are_never_narrowed() -> None:
    """ADR-0012 point 3: problem kinds are part of every successful response."""
    body = (
        "### § 1-1. Formål\n\n"
        "Lovtekst.\n\n"
        "> Endret ved lov 1 jan 2027 nr. 1 uten lenke.\n\n"
        "### § 2-1. Verkeområde\n\n"
        "Lovtekst.\n\n"
        f"{DATED_NOTE}"
    )
    whole = compose_temporal_events(body, _request())
    narrowed = compose_temporal_events(body, _request(section_id="2-1"))

    assert whole["problems"] != []
    assert narrowed["problems"] == whole["problems"]
