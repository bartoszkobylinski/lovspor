"""Tests for horizon-aware valid-time evaluation (ADR-0012 points 4-5).

The evaluated pair served by ``get_temporal_events`` answers the EVENT
question — "had this source-described event taken effect by the
evaluation date?" — never provision validity, and it refuses certainty
beyond the serving state's knowledge horizon: an old snapshot's
open-ended fact must not certify what happened after the snapshot.
"""

from datetime import date

import pytest

from lovspor.temporal import (
    TEMPORAL_PARSER_VERSION,
    AmendmentEvent,
    CommencementStatus,
    Evaluation,
    EvaluationReason,
    NeverInForceMarker,
    TemporalLayer,
    evaluate_event,
    evaluate_never_in_force,
)

HORIZON = date(2026, 6, 1)


def _event(**overrides: object) -> AmendmentEvent:
    base: dict[str, object] = {
        "provision": "§ 1",
        "scope": "provision",
        "kind": "amended",
        "announced": False,
        "amending_act": "lov 1 januar 2026 nr. 1",
        "amending_act_ref": "lov/2026-01-01-1",
        "marker_class": "explicit_date",
        "commencement_kind": "dated",
        "commencement_instrument": None,
        "provenance": "source_explicit",
        "valid_from": date(2026, 3, 1),
        "raw_marker": "(ikr. 1 mars 2026)",
        "source_note": "Endret ved lov ...",
        "source_line": 10,
    }
    base.update(overrides)
    return AmendmentEvent.model_validate(base)


def _pending(**overrides: object) -> AmendmentEvent:
    return _event(
        marker_class="pending_indeterminate",
        commencement_kind="pending_indeterminate",
        valid_from=None,
        raw_marker="(i kraft fra den tid Kongen bestemmer)",
        **overrides,
    )


def _relative() -> AmendmentEvent:
    return _event(
        marker_class="relative",
        commencement_kind="ambiguous",
        valid_from=None,
        raw_marker="(i kraft samtidig som ...)",
    )


# ---------- parser behaviour version ----------


def test_parser_behaviour_version_exists_and_is_not_the_schema_version() -> None:
    # ADR-0012 point 2c: attestations key on the BEHAVIOUR version; the
    # output-format version cannot stand in for it.
    assert isinstance(TEMPORAL_PARSER_VERSION, int)
    assert TEMPORAL_PARSER_VERSION >= 1
    assert "schema_version" in TemporalLayer.model_fields
    # Two distinct identities: changing one must never silently change
    # the other, so they are separate names with separate homes.
    assert TemporalLayer.model_fields["schema_version"].default == 1


# ---------- dated events: the fact determines its own answer ----------


def test_dated_event_in_effect_on_and_after_its_date() -> None:
    event = _event(valid_from=date(2026, 3, 1))

    assert evaluate_event(event, date(2026, 3, 1), HORIZON) == Evaluation(
        CommencementStatus.IN_EFFECT,
        None,
    )
    assert evaluate_event(event, date(2030, 1, 1), HORIZON) == Evaluation(
        CommencementStatus.IN_EFFECT,
        None,
    )


def test_dated_event_not_in_effect_before_its_date_even_past_horizon() -> None:
    # A source-stated future date answers for any V — including V past
    # the horizon: the fact determines its own answer (ADR-0012 point 5).
    event = _event(valid_from=date(2030, 1, 1))

    verdict = evaluate_event(event, date(2027, 1, 1), HORIZON)

    assert verdict == Evaluation(CommencementStatus.NOT_IN_EFFECT, None)


# ---------- pending_indeterminate: the horizon rule ----------


def test_pending_within_horizon_is_not_in_effect_by_the_sources_own_statement() -> None:
    verdict = evaluate_event(_pending(), date(2026, 5, 1), HORIZON)

    assert verdict == Evaluation(CommencementStatus.NOT_IN_EFFECT, None)


def test_pending_at_the_horizon_is_still_the_sources_statement() -> None:
    verdict = evaluate_event(_pending(), HORIZON, HORIZON)

    assert verdict == Evaluation(CommencementStatus.NOT_IN_EFFECT, None)


def test_pending_beyond_horizon_is_indeterminate_with_the_named_reason() -> None:
    # An old snapshot cannot know what the King decided after it was
    # taken; absence of a later fact is not evidence about a later V.
    verdict = evaluate_event(_pending(), date(2027, 1, 1), HORIZON)

    assert verdict == Evaluation(
        CommencementStatus.INDETERMINATE,
        EvaluationReason.BEYOND_KNOWLEDGE_HORIZON,
    )


# ---------- epistemic classes: no verdict either way ----------


def test_relative_marker_is_indeterminate_within_horizon_without_a_reason() -> None:
    verdict = evaluate_event(_relative(), date(2026, 5, 1), HORIZON)

    assert verdict == Evaluation(CommencementStatus.INDETERMINATE, None)


def test_relative_marker_beyond_horizon_names_the_horizon() -> None:
    verdict = evaluate_event(_relative(), date(2027, 1, 1), HORIZON)

    assert verdict == Evaluation(
        CommencementStatus.INDETERMINATE,
        EvaluationReason.BEYOND_KNOWLEDGE_HORIZON,
    )


@pytest.mark.parametrize(
    ("marker_class", "commencement_kind", "provenance"),
    [
        ("unrecognised", "ambiguous", "source_explicit"),
        ("not_a_commencement_marker", "unknown", "deterministically_derived"),
        ("absent", "unknown", "deterministically_derived"),
    ],
)
def test_other_epistemic_marker_classes_obey_the_horizon(
    marker_class: str,
    commencement_kind: str,
    provenance: str,
) -> None:
    event = _event(
        marker_class=marker_class,
        commencement_kind=commencement_kind,
        provenance=provenance,
        valid_from=None,
    )

    assert evaluate_event(event, HORIZON, HORIZON) == Evaluation(
        CommencementStatus.INDETERMINATE,
        None,
    )
    assert evaluate_event(event, date(2026, 6, 2), HORIZON) == Evaluation(
        CommencementStatus.INDETERMINATE,
        EvaluationReason.BEYOND_KNOWLEDGE_HORIZON,
    )


# ---------- never_in_force markers ----------


def test_never_in_force_within_horizon_binds() -> None:
    marker = NeverInForceMarker(provision="§ 41", text="...", source_line=5)

    verdict = evaluate_never_in_force(marker, date(2026, 5, 1), HORIZON)

    assert verdict == Evaluation(CommencementStatus.NOT_IN_EFFECT, None)


def test_never_in_force_at_horizon_still_binds() -> None:
    marker = NeverInForceMarker(provision="§ 41", text="...", source_line=5)

    assert evaluate_never_in_force(marker, HORIZON, HORIZON) == Evaluation(
        CommencementStatus.NOT_IN_EFFECT,
        None,
    )


def test_never_in_force_beyond_horizon_is_knowledge_limited() -> None:
    marker = NeverInForceMarker(provision="§ 41", text="...", source_line=5)

    verdict = evaluate_never_in_force(marker, date(2027, 1, 1), HORIZON)

    assert verdict == Evaluation(
        CommencementStatus.INDETERMINATE,
        EvaluationReason.BEYOND_KNOWLEDGE_HORIZON,
    )


# ---------- totality ----------


@pytest.mark.parametrize("valid_at", [date(2020, 1, 1), HORIZON, date(2035, 1, 1)])
def test_evaluation_is_total_and_never_raises(valid_at: date) -> None:
    for event in (_event(), _pending(), _relative()):
        verdict = evaluate_event(event, valid_at, HORIZON)
        assert isinstance(verdict.status, CommencementStatus)
