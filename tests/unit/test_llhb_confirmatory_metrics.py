"""The confirmatory reporting layer (ruling #30, llhb-metrics-v3).

What v2 could not express and this layer must: a case that produced no score
is not an unresolved answer, a pair missing from one arm is missing from both,
and an effect that only exists while the missing data is assumed friendly is
not confirmed.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.llhb import metrics as metrics_module
from lovspor.llhb.metrics import (
    MODEL_ERROR_MAX_PAIRS,
    PRIMARY_METRIC,
    SCORER_ERROR_MAX_PAIRS,
    Eligibility,
    MetricPair,
    Missingness,
    PairReport,
    compute_pair_report,
)
from lovspor.llhb.outcomes import CaseOutcome, ScoredCase, outcome_of
from lovspor.llhb.reporting import ArmScoring
from lovspor.llhb.scoring import CaseScore, CriterionVerdict

Bundle = tuple[dict[str, Any], dict[str, Any], CaseScore]


def score(case_id: str, category: str = "C1", **overrides: Any) -> CaseScore:
    fields: dict[str, Any] = {
        "case_id": case_id,
        "category": category,
        "criteria": {"expected-provision-cited": CriterionVerdict.PASS},
        "passed": True,
        "asserted_h1": (),
        "asserted_citations": 1,
        "asserted_resolved": 1,
        "asserted_valid": 1,
        "quotes_detected": 0,
        "quotes_checkable": 0,
        "quotes_verified": 0,
        "unresolved_claims": 0,
        "unattached_quotes": 0,
    }
    fields.update(overrides)
    return CaseScore(**fields)


def bundle(case_score: CaseScore) -> Bundle:
    case = {
        "case_id": case_score.case_id,
        "category": case_score.category,
        "expected_act_slug": "testloven",
        "expected_section_id": "1",
    }
    record = {"case_id": case_score.case_id, "completed": True, "tool_calls": []}
    return (case, record, case_score)


def scored(case_score: CaseScore) -> ScoredCase:
    return ScoredCase(
        case_id=case_score.case_id,
        category=case_score.category,
        outcome=outcome_of(case_score),
        score=case_score,
    )


def failed(case_id: str, outcome: CaseOutcome, category: str = "C1") -> ScoredCase:
    return ScoredCase(case_id=case_id, category=category, outcome=outcome, detail="synthetic")


def arm(scores: list[CaseScore], failures: list[ScoredCase] | None = None) -> ArmScoring:
    return ArmScoring(
        bundles=[bundle(s) for s in scores],
        cases=[scored(s) for s in scores] + list(failures or []),
    )


def ids(count: int, start: int = 101) -> list[str]:
    return [f"llhb-v1-C1-{n}" for n in range(start, start + count)]


class TestPairwiseDrop:
    def test_one_arm_failing_removes_the_case_from_both(self) -> None:
        """A pair is the unit. Scoring the control's answer for a case the
        treatment never answered would compare a case against nothing."""
        good = [score(i) for i in ids(3)]
        control = arm(good)
        treatment = arm(
            [s for s in good if s.case_id != "llhb-v1-C1-102"],
            [failed("llhb-v1-C1-102", CaseOutcome.MODEL_ERROR)],
        )

        report = compute_pair_report(control, treatment)

        assert report.eligibility.pairs_total == 3
        assert report.eligibility.pairs_scored == 2
        assert report.eligibility.model_error_pairs == ("llhb-v1-C1-102",)
        assert report.metrics[PRIMARY_METRIC].control is not None
        assert report.metrics[PRIMARY_METRIC].control.denominator == 2

    def test_a_pair_failing_in_both_arms_is_counted_once(self) -> None:
        good = [score(i) for i in ids(2)]
        rest = [s for s in good if s.case_id != "llhb-v1-C1-101"]
        broken = [failed("llhb-v1-C1-101", CaseOutcome.MODEL_ERROR)]

        report = compute_pair_report(arm(rest, broken), arm(rest, broken))

        assert report.eligibility.model_error_pairs == ("llhb-v1-C1-101",)
        assert report.eligibility.pairs_scored == 1

    def test_a_model_error_beats_a_scorer_error_in_the_same_pair(self) -> None:
        """The pair is unusable either way; counting it against the tighter
        instrument gate as well would fail a run for the provider's outage."""
        rest = [score("llhb-v1-C1-102")]

        report = compute_pair_report(
            arm(rest, [failed("llhb-v1-C1-101", CaseOutcome.MODEL_ERROR)]),
            arm(rest, [failed("llhb-v1-C1-101", CaseOutcome.SCORER_ERROR)]),
        )

        assert report.eligibility.model_error_pairs == ("llhb-v1-C1-101",)
        assert report.eligibility.scorer_error_pairs == ()

    def test_pairs_scored_subtracts_both_error_kinds_at_once(self) -> None:
        """With a model error and a scorer error both present, the scored
        count must drop each once — adding one in instead of subtracting it
        would overcount the pairs left to measure."""
        good = [score(i) for i in ids(3, start=300)]
        broken = [
            failed("llhb-v1-C1-101", CaseOutcome.MODEL_ERROR),
            failed("llhb-v1-C1-102", CaseOutcome.SCORER_ERROR),
        ]

        report = compute_pair_report(arm(good, broken), arm(good, broken))

        assert report.eligibility.pairs_total == 5
        assert report.eligibility.pairs_scored == 3


class TestEligibilityGates:
    def test_exactly_the_model_error_ceiling_still_passes(self) -> None:
        """The gate is <=, not <: the ceiling itself is still eligible."""
        at_ceiling = ids(MODEL_ERROR_MAX_PAIRS)
        good = [score(i) for i in ids(4, start=200)]
        broken = [failed(i, CaseOutcome.MODEL_ERROR) for i in at_ceiling]

        report = compute_pair_report(arm(good, broken), arm(good, broken))

        assert report.eligibility.model_error_gate_passed is True
        assert report.eligibility.confirmatory_eligible is True

    def test_exactly_the_scorer_error_ceiling_still_passes(self) -> None:
        at_ceiling = ids(SCORER_ERROR_MAX_PAIRS)
        good = [score(i) for i in ids(4, start=200)]
        broken = [failed(i, CaseOutcome.SCORER_ERROR) for i in at_ceiling]

        report = compute_pair_report(arm(good, broken), arm(good, broken))

        assert report.eligibility.scorer_error_gate_passed is True
        assert report.eligibility.confirmatory_eligible is True

    def test_too_many_model_errors_forfeit_the_verdict(self) -> None:
        over = MODEL_ERROR_MAX_PAIRS + 1
        broken_ids = ids(over)
        good = [score(i) for i in ids(4, start=200)]
        broken = [failed(i, CaseOutcome.MODEL_ERROR) for i in broken_ids]

        report = compute_pair_report(arm(good, broken), arm(good, broken))

        assert report.eligibility.model_error_gate_passed is False
        assert report.eligibility.confirmatory_eligible is False
        assert report.primary.verdict == "no_confirmatory_verdict"

    def test_the_scorer_gate_is_the_tighter_one(self) -> None:
        broken_ids = ids(SCORER_ERROR_MAX_PAIRS + 1)
        good = [score(i) for i in ids(4, start=200)]
        broken = [failed(i, CaseOutcome.SCORER_ERROR) for i in broken_ids]

        report = compute_pair_report(arm(good, broken), arm(good, broken))

        assert len(broken_ids) <= MODEL_ERROR_MAX_PAIRS
        assert report.eligibility.scorer_error_gate_passed is False
        assert report.primary.verdict == "no_confirmatory_verdict"

    def test_a_clean_pair_keeps_its_verdict(self) -> None:
        good = [score(i) for i in ids(4)]

        report = compute_pair_report(arm(good), arm(good))

        assert report.eligibility.confirmatory_eligible is True
        assert report.primary.verdict in ("confirmed", "inconclusive", "reversed")


class TestVerdict:
    def test_a_consistent_positive_effect_is_confirmed(self) -> None:
        """Control hallucinates on every case, treatment never does: every
        bootstrap resample sees the same per-case difference, so the delta CI
        is degenerate at the observed value and does not touch zero."""
        control = [score(i, asserted_h1=("testloven § 99",)) for i in ids(4)]
        treatment = [score(i) for i in ids(4)]

        report = compute_pair_report(arm(control), arm(treatment))

        assert report.metrics[PRIMARY_METRIC].delta_ci_low == pytest.approx(1.0)
        assert report.primary.verdict == "confirmed"

    def test_a_consistent_negative_effect_is_reversed(self) -> None:
        """Treatment hallucinates on every case, control never does: the
        delta (control - treatment) is degenerate at -1, entirely below
        zero — the opposite of what a beneficial treatment would show."""
        control = [score(i) for i in ids(4)]
        treatment = [score(i, asserted_h1=("testloven § 99",)) for i in ids(4)]

        report = compute_pair_report(arm(control), arm(treatment))

        assert report.metrics[PRIMARY_METRIC].delta_ci_high == pytest.approx(-1.0)
        assert report.primary.verdict == "reversed"

    def test_primary_result_carries_the_pair_metric_verbatim(self) -> None:
        """``_primary`` reads its delta/ci_low/ci_high off the primary
        metric's own MetricPair — a hand-built PrimaryResult substituting
        None for any of them would go undetected by verdict-only checks."""
        control = [score(i, asserted_h1=("testloven § 99",)) for i in ids(4)]
        treatment = [score(i) for i in ids(4)]

        report = compute_pair_report(arm(control), arm(treatment))

        metric = report.metrics[PRIMARY_METRIC]
        assert report.primary.delta == metric.delta
        assert report.primary.ci_low == metric.delta_ci_low
        assert report.primary.ci_high == metric.delta_ci_high
        assert report.primary.delta is not None
        assert report.primary.ci_low is not None
        assert report.primary.ci_high is not None


def _eligible(confirmatory: bool = True) -> Eligibility:
    return Eligibility(
        pairs_total=10,
        pairs_scored=10,
        model_error_pairs=(),
        scorer_error_pairs=(),
        model_error_gate_passed=True,
        scorer_error_gate_passed=True,
        confirmatory_eligible=confirmatory,
    )


def _missingness_stub(survives: bool | None = None) -> Missingness:
    return Missingness(
        dropped_pairs=0,
        delta_all_cases=None,
        worst_case_low=None,
        worst_case_high=None,
        sign_survives_worst_case=survives,
    )


def _pair(delta: float | None, low: float | None, high: float | None) -> MetricPair:
    return MetricPair(
        control=None, treatment=None, delta=delta, delta_ci_low=low, delta_ci_high=high
    )


class TestVerdictDirect:
    """Direct calls to ``_verdict`` pin edges the report-level scenarios
    cannot reach — the delta CI bounds it receives from ``_two_arm_pair``
    are always both-None or both-not-None together, so a real report can
    never exercise the one-sided-None branch, and hitting the exact
    boundary values (a CI edge of precisely 0) is not practical through
    the bootstrap."""

    def test_one_sided_none_ci_is_inconclusive(self) -> None:
        verdict = metrics_module._verdict(_pair(0.5, None, 0.9), _eligible(), _missingness_stub())

        assert verdict == "inconclusive"

    def test_ci_high_of_exactly_zero_is_not_reversed(self) -> None:
        verdict = metrics_module._verdict(_pair(-0.1, -0.5, 0.0), _eligible(), _missingness_stub())

        assert verdict != "reversed"

    def test_a_ci_high_short_of_one_is_not_reversed(self) -> None:
        verdict = metrics_module._verdict(_pair(0.3, -0.1, 0.5), _eligible(), _missingness_stub())

        assert verdict != "reversed"

    def test_a_non_positive_ci_low_does_not_confirm_even_if_worst_case_survives(self) -> None:
        verdict = metrics_module._verdict(
            _pair(0.0, -0.1, 0.5), _eligible(), _missingness_stub(survives=True)
        )

        assert verdict != "confirmed"

    def test_ci_low_of_exactly_zero_does_not_confirm(self) -> None:
        verdict = metrics_module._verdict(
            _pair(0.3, 0.0, 0.6), _eligible(), _missingness_stub(survives=True)
        )

        assert verdict != "confirmed"

    def test_a_positive_ci_low_with_worst_case_surviving_is_confirmed(self) -> None:
        verdict = metrics_module._verdict(
            _pair(0.3, 0.1, 0.6), _eligible(), _missingness_stub(survives=True)
        )

        assert verdict == "confirmed"


class TestPrimaryEstimand:
    def test_an_answer_that_cited_nothing_still_counts(self) -> None:
        """Ruling #30(a): the primary rate is over every case, not only over
        answers that cited something. Conditioning flattered the arms unequally
        — the conditional metric is kept beside it, not instead of it."""
        silent = score(
            "llhb-v1-C1-101", asserted_citations=0, asserted_resolved=0, asserted_valid=0
        )
        speaking = score("llhb-v1-C1-102", asserted_h1=("testloven § 99",))
        both = [silent, speaking]

        report = compute_pair_report(arm(both), arm(both))

        primary = report.metrics[PRIMARY_METRIC].control
        conditional = report.metrics["citation_hallucination_rate"].control
        assert primary is not None and conditional is not None
        assert (primary.numerator, primary.denominator) == (1.0, 2.0)
        assert (conditional.numerator, conditional.denominator) == (1.0, 1.0)


class TestMissingness:
    def test_zero_total_pairs_produces_an_all_none_missingness(self) -> None:
        """With no shared cases at all, every derived field must come back
        None rather than a stand-in value or a crash from a required field
        quietly dropped off the model."""
        report = compute_pair_report(arm([]), arm([]))

        missingness = report.primary.missingness
        assert missingness.dropped_pairs == 0
        assert missingness.delta_all_cases is None
        assert missingness.worst_case_low is None
        assert missingness.worst_case_high is None
        assert missingness.sign_survives_worst_case is None

    def test_the_band_widens_by_one_pair_each(self) -> None:
        good = [score(i) for i in ids(9)]
        rest = good[:-1]
        broken = [failed(good[-1].case_id, CaseOutcome.MODEL_ERROR)]

        report = compute_pair_report(arm(rest, broken), arm(rest, broken))

        missingness = report.primary.missingness
        assert missingness.dropped_pairs == 1
        assert missingness.worst_case_low is not None and missingness.worst_case_high is not None
        assert missingness.worst_case_high - missingness.worst_case_low == pytest.approx(2 / 9)

    def test_an_effect_that_the_worst_case_reverses_is_not_confirmed(self) -> None:
        """One case separating the arms, one pair missing: assign the missing
        difference against the effect and it is gone. That is not a confirmed
        result, whatever the interval over the observed cases says."""
        control = [
            score("llhb-v1-C1-101", asserted_h1=("testloven § 99",)),
            score("llhb-v1-C1-102"),
        ]
        treatment = [score("llhb-v1-C1-101"), score("llhb-v1-C1-102")]
        broken = [failed("llhb-v1-C1-103", CaseOutcome.MODEL_ERROR)]

        report = compute_pair_report(arm(control, broken), arm(treatment, broken))

        assert report.primary.missingness.sign_survives_worst_case is False
        assert report.primary.verdict != "confirmed"

    def test_no_dropped_pairs_leaves_the_question_unasked(self) -> None:
        good = [score(i) for i in ids(3)]

        report = compute_pair_report(arm(good), arm(good))

        assert report.primary.missingness.dropped_pairs == 0
        assert report.primary.missingness.sign_survives_worst_case is None

    def test_a_negative_effect_can_also_survive_the_worst_case(self) -> None:
        """The survival check is symmetric: a negative all-cases delta
        survives only if the unfavourable shift keeps the band's high end
        below zero, not merely its low end above it."""
        treatment_hallucinates = [score(i) for i in ids(5)]
        control = [score(i) for i in ids(5)]
        treatment = [
            score(s.case_id, asserted_h1=("testloven § 99",)) for s in treatment_hallucinates
        ]
        broken = [failed("llhb-v1-C1-106", CaseOutcome.MODEL_ERROR)]

        report = compute_pair_report(arm(control, broken), arm(treatment, broken))

        missingness = report.primary.missingness
        assert missingness.delta_all_cases is not None and missingness.delta_all_cases < 0
        assert missingness.worst_case_high is not None and missingness.worst_case_high < 0
        assert missingness.sign_survives_worst_case is True

    def test_an_all_cases_delta_of_exactly_zero_does_not_survive(self) -> None:
        """Neither branch of the sign check applies to a zero delta: it is
        not a positive effect defending its low end, nor a negative one
        defending its high end, so it cannot survive the worst case."""
        control = [
            score("llhb-v1-C1-101", asserted_h1=("testloven § 99",)),
            score("llhb-v1-C1-102"),
        ]
        treatment = [
            score("llhb-v1-C1-101"),
            score("llhb-v1-C1-102", asserted_h1=("testloven § 99",)),
        ]
        broken = [failed("llhb-v1-C1-103", CaseOutcome.MODEL_ERROR)]

        report = compute_pair_report(arm(control, broken), arm(treatment, broken))

        missingness = report.primary.missingness
        assert missingness.delta_all_cases == 0.0
        assert missingness.sign_survives_worst_case is False


class TestMissingnessBandDirect:
    """Direct calls to ``_missingness_band`` pin the band arithmetic and the
    branch it picks at boundary values a bootstrap-driven report cannot
    reliably land on."""

    def test_a_positive_band_within_the_unit_interval_survives(self) -> None:
        result = metrics_module._missingness_band(5.0, 1, 10)

        assert result.delta_all_cases == 0.5
        assert result.sign_survives_worst_case is True

    def test_a_negative_band_whose_high_end_is_non_negative_does_not_survive(self) -> None:
        result = metrics_module._missingness_band(-2.0, 2, 10)

        assert result.sign_survives_worst_case is False


class TestC8Reporting:
    def test_c8_is_counted_over_all_its_cases_five_ways(self) -> None:
        """The ratio that drops unresolved cases survives only under its own
        name; the counts that ruling #30(c) mandates sit beside it."""
        passed = score("llhb-v1-C8-101", category="C8")
        failing = score("llhb-v1-C8-102", category="C8", passed=False)
        unresolved = score("llhb-v1-C8-103", category="C8", passed=None)
        broken = [failed("llhb-v1-C8-104", CaseOutcome.MODEL_ERROR, category="C8")]
        scores = [passed, failing, unresolved]

        report = compute_pair_report(arm(scores, broken), arm(scores, broken))

        c8 = report.c8_outcomes.control
        assert (c8.PASS, c8.FAIL, c8.UNRESOLVED_SEMANTIC, c8.MODEL_ERROR, c8.total) == (
            1,
            1,
            1,
            1,
            4,
        )
        resolved = report.metrics["no_invention_rate_resolved_cases"].control
        assert resolved is not None
        assert (resolved.numerator, resolved.denominator) == (1.0, 2.0)

    def test_outcomes_covers_every_category_not_only_c8(self) -> None:
        """``outcomes`` and ``c8_outcomes`` are built from the same tally
        function over different case sets — a swap between the two would
        silently make one field a copy of the other."""
        c1_pass = score("llhb-v1-C1-101")
        c8_fail = score("llhb-v1-C8-102", category="C8", passed=False)
        broken = [failed("llhb-v1-C1-103", CaseOutcome.MODEL_ERROR)]
        scores = [c1_pass, c8_fail]

        report = compute_pair_report(arm(scores, broken), arm(scores, broken))

        overall = report.outcomes.control
        assert (overall.PASS, overall.FAIL, overall.MODEL_ERROR, overall.total) == (1, 1, 1, 3)
        c8 = report.c8_outcomes.control
        assert (c8.PASS, c8.FAIL, c8.MODEL_ERROR, c8.total) == (0, 1, 0, 1)

    def test_every_case_carries_its_reason_code(self) -> None:
        good = [score("llhb-v1-C1-101")]
        broken = [failed("llhb-v1-C1-102", CaseOutcome.SCORER_ERROR)]

        report = compute_pair_report(arm(good, broken), arm(good, broken))

        assert report.case_outcomes["control"] == {
            "llhb-v1-C1-101": "PASS",
            "llhb-v1-C1-102": "SCORER_ERROR",
        }
        assert report.case_outcomes["treatment"] == report.case_outcomes["control"]


class TestIntervalChoice:
    def test_a_proportion_gets_a_wilson_interval_inside_the_unit_range(self) -> None:
        good = [score(i) for i in ids(4)]
        good[0] = score(good[0].case_id, asserted_h1=("testloven § 99",))

        report = compute_pair_report(arm(good), arm(good))

        estimate = report.metrics[PRIMARY_METRIC].control
        assert estimate is not None
        assert estimate.ci_low is not None and estimate.ci_high is not None
        assert 0.0 <= estimate.ci_low < estimate.rate < estimate.ci_high <= 1.0

    def test_a_per_answer_volume_is_a_mean_and_may_exceed_one(self) -> None:
        """A Wilson interval is undefined above 1; the volume metrics of plan
        §5.4 routinely sit there, so they keep the case-level bootstrap."""
        loud = [
            score(i, asserted_citations=4, asserted_resolved=4, asserted_valid=4) for i in ids(3)
        ]

        report = compute_pair_report(arm(loud), arm(loud))

        volume = report.metrics["valid_citations_per_answer"].control
        assert volume is not None
        assert volume.rate == 4.0
        assert volume.ci_low is not None and volume.ci_high is not None


class TestVersionBoundary:
    def test_a_report_from_an_older_version_still_parses(self) -> None:
        """Committed v2 artifacts predate the confirmatory layer. Reading them
        is not re-labelling them: their own version stays on the document."""
        good = [score(i) for i in ids(2)]
        document = compute_pair_report(arm(good), arm(good)).model_dump(mode="json")
        legacy = {
            key: value
            for key, value in document.items()
            if key not in ("outcomes", "c8_outcomes", "case_outcomes", "eligibility", "primary")
        }
        legacy["metrics_version"] = "llhb-metrics-v2"

        parsed = PairReport.model_validate(legacy)

        assert parsed.metrics_version == "llhb-metrics-v2"
        assert parsed.eligibility is None

    def test_a_report_claiming_this_version_must_carry_the_whole_layer(self) -> None:
        good = [score(i) for i in ids(2)]
        document = compute_pair_report(arm(good), arm(good)).model_dump(mode="json")
        document.pop("eligibility")

        with pytest.raises(ValidationError, match="missing eligibility"):
            PairReport.model_validate(document)
