"""Stage 8 aggregation: SCORING.md §6 metrics over scored case-runs.

Everything is deterministic: the bootstrap runs on a fixed seed, so the
same scores produce byte-identical reports. Tests build scores by hand
— the per-case scorer has its own suite; this layer only counts.
"""

from pydantic import ValidationError

from lovspor.llhb import metrics as metrics_module
from lovspor.llhb import reporting as reporting_module
from lovspor.llhb.metrics import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    METRICS_VERSION,
    PRIMARY_METRIC,
    compute_pair_report,
    retrieved_directly,
)
from lovspor.llhb.outcomes import ScoredCase, outcome_of
from lovspor.llhb.reporting import ArmScoring
from lovspor.llhb.scoring import SCORER_VERSION, CaseScore, CriterionVerdict

Bundle = tuple[dict[str, object], dict[str, object], CaseScore]


def score(case_id: str, category: str = "C1", **overrides: object) -> CaseScore:
    fields: dict[str, object] = {
        "case_id": case_id,
        "category": category,
        "criteria": {
            "expected-provision-cited": CriterionVerdict.PASS,
            "no-invalid-citations": CriterionVerdict.PASS,
        },
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
    return CaseScore(**fields)  # type: ignore[arg-type]


def hallucinated(case_id: str, category: str = "C1") -> CaseScore:
    return score(
        case_id,
        category,
        criteria={
            "expected-provision-cited": CriterionVerdict.PASS,
            "no-invalid-citations": CriterionVerdict.FAIL,
        },
        passed=False,
        asserted_h1=("testloven § 15-99",),
        asserted_citations=2,
        asserted_resolved=2,
    )


def record(case_id: str, tool_calls: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {"case_id": case_id, "completed": True, "tool_calls": tool_calls or []}


def get_section_call(slug: str, section_id: str) -> dict[str, object]:
    return {
        "index": 0,
        "name": "mcp__lovverk__get_section",
        "arguments": {"slug": slug, "section_id": section_id},
        "is_error": False,
    }


def case(case_id: str, category: str = "C1") -> dict[str, object]:
    return {
        "case_id": case_id,
        "category": category,
        "expected_act_slug": "testloven",
        "expected_section_id": "1",
        "deterministic_criteria": ["expected-provision-cited", "no-invalid-citations"],
    }


def bundle(case_id: str, arm_score: CaseScore, **record_kwargs: object) -> Bundle:
    return (case(case_id, arm_score.category), record(case_id, **record_kwargs), arm_score)


def arm(bundles: list[Bundle]) -> ArmScoring:
    """An arm where every case was scored — the errors have their own tests."""
    return ArmScoring(
        bundles=list(bundles),
        cases=[
            ScoredCase(
                case_id=str(b[2].case_id),
                category=str(b[2].category),
                outcome=outcome_of(b[2]),
                score=b[2],
            )
            for b in bundles
        ],
    )


def pair_report(control: list[Bundle], treatment: list[Bundle]) -> object:
    return compute_pair_report(arm(control), arm(treatment))


class TestRetrievedDirectly:
    def test_a_successful_get_section_of_the_expected_pair_counts(self) -> None:
        rec = record("llhb-v1-C1-101", [get_section_call("testloven", "1")])

        assert retrieved_directly(case("llhb-v1-C1-101"), rec) is True

    def test_another_section_does_not_count(self) -> None:
        rec = record("llhb-v1-C1-101", [get_section_call("testloven", "5-12")])

        assert retrieved_directly(case("llhb-v1-C1-101"), rec) is False

    def test_section_id_is_compared_in_canonical_form(self) -> None:
        rec = record("llhb-v1-C1-101", [get_section_call("testloven", "§ 1.")])

        assert retrieved_directly(case("llhb-v1-C1-101"), rec) is True

    def test_an_errored_call_does_not_count(self) -> None:
        call = {**get_section_call("testloven", "1"), "is_error": True}

        assert retrieved_directly(case("llhb-v1-C1-101"), record("x", [call])) is False

    def test_a_search_hit_does_not_count(self) -> None:
        """Conservative by design: only a successful get_section of the
        expected pair is deterministic from the committed trace alone —
        payloads are not committed (F3)."""
        call = {
            "index": 0,
            "name": "mcp__lovverk__search_laws",
            "arguments": {"query": "testloven"},
            "is_error": False,
        }

        assert retrieved_directly(case("llhb-v1-C1-101"), record("x", [call])) is False

    def test_a_later_valid_call_counts_after_ignored_trace_entries(self) -> None:
        rec = record(
            "llhb-v1-C1-101",
            [
                {"name": "mcp__lovverk__get_section", "is_error": True},
                {"name": "mcp__lovverk__search_laws", "arguments": {}},
                get_section_call("testloven", "1"),
            ],
        )

        assert retrieved_directly(case("llhb-v1-C1-101"), rec) is True


class TestPairReport:
    def make_pair(self) -> tuple[list[Bundle], list[Bundle]]:
        control = [
            bundle("llhb-v1-C1-101", hallucinated("llhb-v1-C1-101")),
            bundle("llhb-v1-C1-102", score("llhb-v1-C1-102")),
        ]
        treatment = [
            bundle(
                "llhb-v1-C1-101",
                score("llhb-v1-C1-101"),
                tool_calls=[get_section_call("testloven", "1")],
            ),
            bundle(
                "llhb-v1-C1-102",
                score("llhb-v1-C1-102"),
                tool_calls=[get_section_call("testloven", "1")],
            ),
        ]
        return control, treatment

    def test_hallucination_rate_absolute_and_delta(self) -> None:
        control, treatment = self.make_pair()

        report = pair_report(control, treatment)

        chr_metric = report.metrics["citation_hallucination_rate"]
        assert chr_metric.control is not None and chr_metric.treatment is not None
        assert chr_metric.control.numerator == 1
        assert chr_metric.control.denominator == 2
        assert chr_metric.control.rate == 0.5
        assert chr_metric.treatment.rate == 0.0
        assert chr_metric.delta == 0.5

    def test_citation_coverage_uses_every_eligible_answer_as_denominator(self) -> None:
        control, treatment = self.make_pair()
        control[1] = bundle(
            "llhb-v1-C1-102",
            score("llhb-v1-C1-102", asserted_citations=0, asserted_resolved=0, asserted_valid=0),
        )

        coverage = pair_report(control, treatment).metrics["citation_coverage"]

        assert coverage.control is not None and coverage.treatment is not None
        assert (coverage.control.numerator, coverage.control.denominator) == (1, 2)
        assert (coverage.treatment.numerator, coverage.treatment.denominator) == (2, 2)
        assert coverage.delta == -0.5

    def test_delta_subtracts_treatment_when_both_rates_are_nonzero(self) -> None:
        control = [
            bundle("llhb-v1-C1-101", hallucinated("llhb-v1-C1-101")),
            bundle("llhb-v1-C1-102", hallucinated("llhb-v1-C1-102")),
        ]
        treatment = [
            bundle("llhb-v1-C1-101", hallucinated("llhb-v1-C1-101")),
            bundle("llhb-v1-C1-102", score("llhb-v1-C1-102")),
        ]

        metric = pair_report(control, treatment).metrics["citation_hallucination_rate"]

        assert metric.control is not None and metric.treatment is not None
        assert metric.delta == 0.5

    def test_a_metric_with_an_empty_denominator_has_no_rate(self) -> None:
        control, treatment = self.make_pair()

        report = pair_report(control, treatment)

        quote = report.metrics["quote_fidelity"]
        assert quote.control is not None
        assert quote.control.denominator == 0
        assert quote.control.rate is None
        assert quote.delta is None

    def test_post_direct_retrieval_hallucination_is_treatment_only(self) -> None:
        control, treatment = self.make_pair()

        report = pair_report(control, treatment)

        prh = report.metrics["post_direct_retrieval_hallucination_rate"]
        assert prh.control is None
        assert prh.treatment is not None
        assert prh.treatment.denominator == 2
        assert prh.treatment.numerator == 0

    def test_confidence_intervals_are_deterministic_and_ordered(self) -> None:
        control, treatment = self.make_pair()

        first = pair_report(control, treatment)
        second = pair_report(control, treatment)

        est = first.metrics["citation_hallucination_rate"].control
        assert est is not None
        assert est.rate is not None
        assert est.ci_low is not None and est.ci_high is not None
        assert 0.0 <= est.ci_low <= est.rate <= est.ci_high <= 1.0
        assert first == second

    def test_the_report_carries_the_versions_and_buckets(self) -> None:
        control, treatment = self.make_pair()

        report = pair_report(control, treatment)

        assert report.metrics_version == METRICS_VERSION == "llhb-metrics-v3"
        assert report.scorer_version == SCORER_VERSION
        # Literals on purpose: asserting via the constants would mutate in
        # lockstep with the code and never fail.
        assert report.bootstrap == {"seed": 42, "resamples": 10_000}
        # n raised from 2,000 by ruling #30(e), before the confirmatory freeze.
        assert (BOOTSTRAP_SEED, BOOTSTRAP_RESAMPLES) == (42, 10_000)
        assert report.unresolved.control["unresolved_claims"] == 0
        assert report.unresolved.control == {
            "unresolved_claims": 0,
            "unattached_quotes": 0,
            "unverifiable_quotes": 0,
            "cases_unresolved": 0,
        }
        assert report.per_category["C1"].control is not None
        assert report.per_category["C1"].control.denominator == 2

    def test_mismatched_case_sets_are_refused(self) -> None:
        control, _ = self.make_pair()

        try:
            pair_report(control, control[:1])
        except ValueError as exc:
            assert "case" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("a mismatched pair must not aggregate")


class TestCategoryMetrics:
    def test_correct_provision_identification_counts_c1_c2_passes(self) -> None:
        control = [
            bundle("llhb-v1-C1-101", score("llhb-v1-C1-101")),
            bundle("llhb-v1-C2-101", score("llhb-v1-C2-101", category="C2", passed=False)),
            bundle("llhb-v1-C3-101", score("llhb-v1-C3-101", category="C3")),
        ]
        treatment = [
            bundle("llhb-v1-C1-101", score("llhb-v1-C1-101")),
            bundle("llhb-v1-C2-101", score("llhb-v1-C2-101", category="C2")),
            bundle("llhb-v1-C3-101", score("llhb-v1-C3-101", category="C3")),
        ]

        report = pair_report(control, treatment)

        cpi = report.metrics["correct_provision_identification"]
        assert cpi.control is not None and cpi.treatment is not None
        assert cpi.control.denominator == 2
        assert cpi.control.numerator == 1
        assert cpi.treatment.numerator == 2

    def test_unresolved_cases_leave_the_denominator(self) -> None:
        """A C6 case whose stance never resolved is reported, not scored
        — §6: «C6 resolved cases (unresolved reported separately)»."""
        unresolved = score(
            "llhb-v1-C6-101",
            category="C6",
            criteria={"false-premise-not-endorsed": CriterionVerdict.UNRESOLVED},
            passed=None,
        )
        rejected = score(
            "llhb-v1-C6-101",
            category="C6",
            criteria={"false-premise-not-endorsed": CriterionVerdict.PASS},
        )
        control = [bundle("llhb-v1-C6-101", unresolved)]
        treatment = [bundle("llhb-v1-C6-101", rejected)]

        report = pair_report(control, treatment)

        fpr = report.metrics["false_premise_rejection_rate"]
        assert fpr.control is not None and fpr.treatment is not None
        assert fpr.control.denominator == 0
        assert fpr.treatment.denominator == 1
        assert report.unresolved.control["cases_unresolved"] == 1

    def test_metric_definitions_count_failures_and_raw_citation_totals(self) -> None:
        control_scores = [
            score(
                "llhb-v1-C4-101",
                category="C4",
                criteria={"claimed-attribution-not-asserted": CriterionVerdict.FAIL},
                passed=False,
                asserted_citations=3,
                asserted_resolved=2,
                asserted_valid=1,
            ),
            score(
                "llhb-v1-C7-101",
                category="C7",
                quotes_detected=2,
                quotes_checkable=2,
                quotes_verified=1,
            ),
            score("llhb-v1-C8-101", category="C8", passed=False),
        ]
        treatment_scores = [
            score(
                "llhb-v1-C4-101",
                category="C4",
                criteria={"claimed-attribution-not-asserted": CriterionVerdict.PASS},
            ),
            score(
                "llhb-v1-C7-101",
                category="C7",
                quotes_detected=2,
                quotes_checkable=2,
                quotes_verified=2,
            ),
            score("llhb-v1-C8-101", category="C8", passed=True),
        ]

        report = pair_report(
            [bundle(item.case_id, item) for item in control_scores],
            [bundle(item.case_id, item) for item in treatment_scores],
        )

        accuracy = report.metrics["citation_accuracy"]
        misattribution = report.metrics["misattribution_rate"]
        quote_fidelity = report.metrics["quote_fidelity"]
        no_invention = report.metrics["no_invention_rate_resolved_cases"]
        assert accuracy.control is not None
        assert (accuracy.control.numerator, accuracy.control.denominator) == (3, 4)
        assert misattribution.control is not None and misattribution.treatment is not None
        assert (misattribution.control.rate, misattribution.treatment.rate) == (1.0, 0.0)
        assert quote_fidelity.control is not None and quote_fidelity.treatment is not None
        assert (quote_fidelity.control.rate, quote_fidelity.treatment.rate) == (0.5, 1.0)
        assert no_invention.control is not None and no_invention.treatment is not None
        assert (no_invention.control.rate, no_invention.treatment.rate) == (0.0, 1.0)

    def test_post_direct_retrieval_hallucination_counts_h1_and_h2(self) -> None:
        h1 = hallucinated("llhb-v1-C1-101")
        h2 = score(
            "llhb-v1-C4-101",
            category="C4",
            criteria={"claimed-attribution-not-asserted": CriterionVerdict.FAIL},
            passed=False,
        )
        clean = score("llhb-v1-C2-101", category="C2")
        treatment = [
            bundle(
                item.case_id,
                item,
                tool_calls=[get_section_call("testloven", "1")],
            )
            for item in (h1, h2, clean)
        ]
        control = [bundle(item.case_id, item) for item in (h1, h2, clean)]

        metric = pair_report(control, treatment).metrics["post_direct_retrieval_hallucination_rate"]

        assert metric.control is None
        assert metric.treatment is not None
        assert (metric.treatment.numerator, metric.treatment.denominator) == (2, 3)


class TestSurvivorKillers:
    """Mutation-gate killers: aliases, immutability, exact refusal text,
    sampler purity outside a metric's category, and a golden CI."""

    def test_the_public_type_aliases_are_real(self) -> None:
        assert metrics_module._Sample == tuple[float, float]
        assert metrics_module._Bundle == reporting_module.Bundle
        assert reporting_module.Bundle is not None
        assert metrics_module._Sampler is not None

    def test_the_report_models_are_frozen(self) -> None:
        control, treatment = TestPairReport().make_pair()
        report = pair_report(control, treatment)
        est = report.metrics["citation_hallucination_rate"].control
        assert est is not None

        for target, field, value in (
            (report, "scorer_version", "x"),
            (report.metrics["citation_hallucination_rate"], "delta", 1.0),
            (report.unresolved, "control", {}),
            (est, "rate", 1.0),
        ):
            try:
                setattr(target, field, value)
            except (ValidationError, TypeError, AttributeError):
                continue
            raise AssertionError(f"{type(target).__name__}.{field} accepted mutation")

    def test_the_mismatch_refusal_is_verbatim(self) -> None:
        control, _ = TestPairReport().make_pair()

        try:
            pair_report(control, control[:1])
        except ValueError as exc:
            assert str(exc) == "the two arms cover different case sets; nothing to compare"
        else:  # pragma: no cover
            raise AssertionError("must refuse")

    def test_category_samplers_contribute_nothing_outside_their_category(self) -> None:
        """A C1-only pair must leave every category-scoped metric at
        exactly 0/0 — a stray numerator or denominator from the
        out-of-category branch corrupts the rate silently."""
        control, treatment = TestPairReport().make_pair()

        report = pair_report(control, treatment)

        for name in (
            "misattribution_rate",
            "false_premise_rejection_rate",
            "no_invention_rate_resolved_cases",
        ):
            est = report.metrics[name].control
            assert est is not None, name
            assert est.numerator == 0, name
            assert est.denominator == 0, name

    def test_chr_denominator_excludes_citationless_answers(self) -> None:
        citationless = score(
            "llhb-v1-C1-103", asserted_citations=0, asserted_resolved=0, asserted_valid=0
        )
        control = [
            bundle("llhb-v1-C1-101", hallucinated("llhb-v1-C1-101")),
            bundle("llhb-v1-C1-103", citationless),
        ]
        treatment = [
            bundle("llhb-v1-C1-101", score("llhb-v1-C1-101")),
            bundle("llhb-v1-C1-103", citationless),
        ]

        report = pair_report(control, treatment)

        est = report.metrics["citation_hallucination_rate"].control
        assert est is not None
        assert est.denominator == 1

    def test_repeated_outcomes_accumulate_instead_of_overwriting(self) -> None:
        """Each case must add one to its outcome's tally, not stomp it back
        to one — two PASS cases counted as one PASS would silently halve
        every consistent run."""
        cases = [
            ScoredCase(
                case_id="llhb-v1-C1-101",
                category="C1",
                outcome=outcome_of(score("llhb-v1-C1-101")),
                score=score("llhb-v1-C1-101"),
            ),
            ScoredCase(
                case_id="llhb-v1-C1-102",
                category="C1",
                outcome=outcome_of(score("llhb-v1-C1-102")),
                score=score("llhb-v1-C1-102"),
            ),
        ]

        counts = metrics_module._counts(cases)

        assert counts.PASS == 2
        assert counts.total == 2

    def test_fpr_numerator_counts_only_passes(self) -> None:
        passed = score(
            "llhb-v1-C6-101",
            category="C6",
            criteria={"false-premise-not-endorsed": CriterionVerdict.PASS},
        )
        failed = score(
            "llhb-v1-C6-102",
            category="C6",
            criteria={"false-premise-not-endorsed": CriterionVerdict.FAIL},
            passed=False,
        )
        passed_second = score(
            "llhb-v1-C6-102",
            category="C6",
            criteria={"false-premise-not-endorsed": CriterionVerdict.PASS},
        )
        control = [bundle("llhb-v1-C6-101", passed), bundle("llhb-v1-C6-102", failed)]
        treatment = [bundle("llhb-v1-C6-101", passed), bundle("llhb-v1-C6-102", passed_second)]

        report = pair_report(control, treatment)

        fpr = report.metrics["false_premise_rejection_rate"]
        assert fpr.control is not None and fpr.treatment is not None
        assert (fpr.control.numerator, fpr.control.denominator) == (1, 2)
        assert (fpr.treatment.numerator, fpr.treatment.denominator) == (2, 2)


class TestGoldenConfidenceIntervals:
    """One rich pair with pinned CI values: the seed, the resample count,
    the quantile arithmetic and the paired-delta machinery all feed these
    exact numbers — move any of them and the golden breaks."""

    def make_rich_pair(self) -> tuple[list[Bundle], list[Bundle]]:
        control_flags = [True, True, True, False, False, True, False, True]
        treatment_flags = [False, True, False, False, False, False, True, False]
        control = []
        treatment = []
        for index, (c_bad, t_bad) in enumerate(
            zip(control_flags, treatment_flags, strict=True), start=101
        ):
            case_id = f"llhb-v1-C1-{index}"
            control.append(bundle(case_id, hallucinated(case_id) if c_bad else score(case_id)))
            treatment.append(bundle(case_id, hallucinated(case_id) if t_bad else score(case_id)))
        return control, treatment

    def test_golden_ci_and_delta_ci(self) -> None:
        control, treatment = self.make_rich_pair()

        report = pair_report(control, treatment)

        metric = report.metrics["citation_hallucination_rate"]
        assert metric.control is not None and metric.treatment is not None
        assert metric.control.rate == 0.625
        assert metric.treatment.rate == 0.25
        assert metric.delta == 0.375
        golden = (
            metric.control.ci_low,
            metric.control.ci_high,
            metric.treatment.ci_low,
            metric.treatment.ci_high,
            metric.delta_ci_low,
            metric.delta_ci_high,
        )
        assert golden == GOLDEN_CI

    def test_a_zero_denominator_case_in_one_arm_only(self) -> None:
        """One treatment case contributes no denominator: some resamples
        rate one arm only, and the paired machinery must skip those, not
        crash or fabricate."""
        control, treatment = self.make_rich_pair()
        citationless = score(
            "llhb-v1-C1-109", asserted_citations=0, asserted_resolved=0, asserted_valid=0
        )
        control.append(bundle("llhb-v1-C1-109", hallucinated("llhb-v1-C1-109")))
        treatment.append(bundle("llhb-v1-C1-109", citationless))

        report = pair_report(control, treatment)

        metric = report.metrics["citation_hallucination_rate"]
        assert metric.delta is not None
        assert metric.delta_ci_low is not None and metric.delta_ci_high is not None
        assert metric.delta_ci_low <= metric.delta <= metric.delta_ci_high


class TestGoldenAccuracyIntervals:
    """A finer-grained golden: per-citation accuracy with denominators
    1..8 makes resample rates take many distinct values, so a shifted
    quantile index or a changed resample count lands on a different
    number instead of hiding in a coarse distribution."""

    def make_pair(self) -> tuple[list[Bundle], list[Bundle]]:
        control = []
        treatment = []
        for i in range(1, 9):
            case_id = f"llhb-v1-C1-{100 + i}"
            control.append(
                bundle(
                    case_id,
                    score(
                        case_id,
                        asserted_citations=i,
                        asserted_resolved=i,
                        asserted_valid=max(0, i - 2),
                    ),
                )
            )
            treatment.append(
                bundle(
                    case_id,
                    score(
                        case_id,
                        asserted_citations=i,
                        asserted_resolved=i,
                        asserted_valid=i if i % 3 else i - 1,
                    ),
                )
            )
        return control, treatment

    def test_golden_accuracy_cis(self) -> None:
        control, treatment = self.make_pair()

        report = pair_report(control, treatment)

        metric = report.metrics["citation_accuracy"]
        assert metric.control is not None and metric.treatment is not None
        golden = (
            metric.control.rate,
            metric.control.ci_low,
            metric.control.ci_high,
            metric.treatment.ci_low,
            metric.treatment.ci_high,
            metric.delta_ci_low,
            metric.delta_ci_high,
        )
        assert golden == GOLDEN_ACCURACY


# Arm intervals are Wilson score intervals and the delta is a paired bootstrap
# of n = 10,000 (analysis plan §4, ruling #30(e)). Both halves are pinned here:
# the Wilson arithmetic is closed-form, the delta still depends on the seed, the
# resample count and the quantile rule, so either drifting breaks this golden.
GOLDEN_ACCURACY = (
    0.5833333333333334,
    0.42200251574871667,
    0.7285943794855395,
    0.8185534279135275,
    0.9846300133358381,
    -0.5384615384615384,
    -0.2678571428571428,
)
GOLDEN_CI = (
    0.3057423946026273,
    0.8631557141764027,
    0.07147921275210901,
    0.5907245696898311,
    -0.125,
    0.75,
)


class TestQuantileArithmetic:
    def test_the_index_arithmetic_on_a_strictly_increasing_list(self) -> None:
        """Literals against a list where every adjacent value differs, so
        any shift of the index — off-by-one on the length, a doubled
        offset — lands on a provably different number."""
        values = list(map(float, range(100)))

        assert metrics_module._at_quantile(values, 0.025) == 2.0
        assert metrics_module._at_quantile(values, 0.975) == 97.0
        assert metrics_module._at_quantile([5.0], 0.975) == 5.0


class TestQuoteFidelityDenominator:
    """Issue #86: fidelity divides by CHECKABLE quotes; a quote nothing can
    verify (verified=None) is bucket material, never a silent failure."""

    def test_denominator_is_checkable_not_detected(self) -> None:
        control = [
            bundle(
                "llhb-v1-C7-101",
                score(
                    "llhb-v1-C7-101",
                    category="C7",
                    quotes_detected=5,
                    quotes_checkable=2,
                    quotes_verified=1,
                ),
            )
        ]
        treatment = [
            bundle(
                "llhb-v1-C7-101",
                score(
                    "llhb-v1-C7-101",
                    category="C7",
                    quotes_detected=4,
                    quotes_checkable=4,
                    quotes_verified=3,
                ),
            )
        ]
        report = pair_report(control, treatment)
        fidelity = report.metrics["quote_fidelity"]
        assert (fidelity.control.numerator, fidelity.control.denominator) == (1, 2)
        assert (fidelity.treatment.numerator, fidelity.treatment.denominator) == (3, 4)

    def test_unverifiable_mass_lands_in_the_bucket(self) -> None:
        control = [
            bundle(
                "llhb-v1-C7-101",
                score(
                    "llhb-v1-C7-101",
                    category="C7",
                    quotes_detected=5,
                    quotes_checkable=2,
                    quotes_verified=1,
                ),
            )
        ]
        report = pair_report(control, control)
        assert report.unresolved.control["unverifiable_quotes"] == 3


class TestWilsonVsBootstrapSelection:
    """§4/§5.4: proportions get a Wilson interval, per-answer volumes keep
    the case-level bootstrap regardless of where their numerator happens to
    fall. These pin the CI *machinery* chosen, not just its numeric result —
    a mean metric whose numerator lands inside [0, denominator] must still
    take the bootstrap branch, the one case a numerator/denominator range
    check alone would not catch."""

    def test_mean_metrics_use_bootstrap_ci_even_when_within_unit_range(self) -> None:
        control = [
            bundle("llhb-v1-C1-101", score("llhb-v1-C1-101", asserted_valid=0)),
            bundle("llhb-v1-C1-102", score("llhb-v1-C1-102", asserted_valid=1)),
        ]
        treatment = [
            bundle("llhb-v1-C1-101", score("llhb-v1-C1-101", asserted_valid=1)),
            bundle("llhb-v1-C1-102", score("llhb-v1-C1-102", asserted_valid=1)),
        ]

        report = pair_report(control, treatment)

        metric = report.metrics["valid_citations_per_answer"]
        control_samples = [metrics_module._valid_volume_sample(b) for b in control]
        treatment_samples = [metrics_module._valid_volume_sample(b) for b in treatment]
        assert metric.control is not None and metric.treatment is not None
        assert (metric.control.ci_low, metric.control.ci_high) == metrics_module._bootstrap_ci(
            control_samples
        )
        assert (
            metric.treatment.ci_low,
            metric.treatment.ci_high,
        ) == metrics_module._bootstrap_ci(treatment_samples)

    def test_per_category_ci_is_a_wilson_interval(self) -> None:
        """``_per_category`` calls ``_two_arm_pair`` without an explicit
        wilson flag, relying on its default — citation_hallucination_rate
        is a proportion, so that default must be True."""
        control, treatment = TestPairReport().make_pair()

        report = pair_report(control, treatment)

        metric = report.per_category["C1"]
        samples = [metrics_module._chr_sample(b) for b in control]
        numerator = sum(s[0] for s in samples)
        denominator = sum(s[1] for s in samples)
        expected = metrics_module._wilson_interval(numerator, denominator)
        assert metric.control is not None
        assert (metric.control.ci_low, metric.control.ci_high) == expected

    def test_pdrh_uses_a_wilson_interval_not_bootstrap(self) -> None:
        h1 = hallucinated("llhb-v1-C1-101")
        clean = score("llhb-v1-C2-101", category="C2")
        treatment = [
            bundle(item.case_id, item, tool_calls=[get_section_call("testloven", "1")])
            for item in (h1, clean)
        ]
        control = [bundle(item.case_id, item) for item in (h1, clean)]

        report = pair_report(control, treatment)

        metric = report.metrics["post_direct_retrieval_hallucination_rate"]
        samples = [metrics_module._pdrh_sample(b) for b in treatment]
        numerator = sum(s[0] for s in samples)
        denominator = sum(s[1] for s in samples)
        expected = metrics_module._wilson_interval(numerator, denominator)
        assert metric.treatment is not None
        assert (metric.treatment.ci_low, metric.treatment.ci_high) == expected


class TestEstimateBoundaries:
    """``_estimate``'s proportion check spans the whole [0, denominator]
    range inclusive at both ends — these pin each end and the function's
    own default wilson flag, none of which the golden-CI tests (which never
    land exactly at 0%, 100%, or call the function directly) exercise."""

    def test_the_default_wilson_flag_is_true(self) -> None:
        estimate = metrics_module._estimate([(1.0, 4.0), (0.0, 4.0)])

        expected = metrics_module._wilson_interval(1.0, 8.0)
        assert (estimate.ci_low, estimate.ci_high) == expected

    def test_a_zero_numerator_proportion_still_uses_wilson(self) -> None:
        control = [bundle(f"llhb-v1-C1-{i}", score(f"llhb-v1-C1-{i}")) for i in range(101, 104)]

        report = pair_report(control, control)

        metric = report.metrics[PRIMARY_METRIC]
        assert metric.control is not None
        assert metric.control.numerator == 0.0
        expected = metrics_module._wilson_interval(0.0, 3.0)
        assert (metric.control.ci_low, metric.control.ci_high) == expected

    def test_a_full_rate_proportion_still_uses_wilson(self) -> None:
        control = [
            bundle(f"llhb-v1-C1-{i}", hallucinated(f"llhb-v1-C1-{i}")) for i in range(101, 105)
        ]
        treatment = [bundle(f"llhb-v1-C1-{i}", score(f"llhb-v1-C1-{i}")) for i in range(101, 105)]

        report = pair_report(control, treatment)

        metric = report.metrics[PRIMARY_METRIC]
        assert metric.control is not None
        assert metric.control.numerator == metric.control.denominator == 4.0
        expected = metrics_module._wilson_interval(4.0, 4.0)
        assert (metric.control.ci_low, metric.control.ci_high) == expected


class TestWilsonIntervalClamp:
    def test_the_upper_bound_clamps_a_floating_point_overshoot(self) -> None:
        """At rate=1.0 with 11 trials the raw Wilson high end is
        1.0000000000000002 — a genuine floating-point overshoot past the
        formula's mathematical ceiling of 1.0, which the clamp exists to
        catch."""
        _, high = metrics_module._wilson_interval(11.0, 11.0)

        assert high == 1.0


class TestVolumeSamplers:
    def test_invalid_volume_is_resolved_minus_valid_with_a_unit_denominator(self) -> None:
        item = bundle(
            "llhb-v1-C1-101",
            score("llhb-v1-C1-101", asserted_resolved=5, asserted_valid=2),
        )

        numerator, denominator = metrics_module._invalid_volume_sample(item)

        assert numerator == 3.0
        assert denominator == 1.0


class TestPdrhSampler:
    def test_not_retrieved_directly_contributes_nothing(self) -> None:
        item = bundle("llhb-v1-C1-101", hallucinated("llhb-v1-C1-101"))

        numerator, denominator = metrics_module._pdrh_sample(item)

        assert (numerator, denominator) == (0.0, 0.0)
