"""Stage 8 aggregation: SCORING.md §6 metrics over scored case-runs.

Everything is deterministic: the bootstrap runs on a fixed seed, so the
same scores produce byte-identical reports. Tests build scores by hand
— the per-case scorer has its own suite; this layer only counts.
"""

from lovspor.llhb.metrics import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    METRICS_VERSION,
    compute_pair_report,
    retrieved_correct,
)
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


class TestRetrievedCorrect:
    def test_a_successful_get_section_of_the_expected_pair_counts(self) -> None:
        rec = record("llhb-v1-C1-101", [get_section_call("testloven", "1")])

        assert retrieved_correct(case("llhb-v1-C1-101"), rec) is True

    def test_another_section_does_not_count(self) -> None:
        rec = record("llhb-v1-C1-101", [get_section_call("testloven", "5-12")])

        assert retrieved_correct(case("llhb-v1-C1-101"), rec) is False

    def test_an_errored_call_does_not_count(self) -> None:
        call = {**get_section_call("testloven", "1"), "is_error": True}

        assert retrieved_correct(case("llhb-v1-C1-101"), record("x", [call])) is False

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

        assert retrieved_correct(case("llhb-v1-C1-101"), record("x", [call])) is False


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

        report = compute_pair_report(control, treatment)

        chr_metric = report.metrics["citation_hallucination_rate"]
        assert chr_metric.control is not None and chr_metric.treatment is not None
        assert chr_metric.control.numerator == 1
        assert chr_metric.control.denominator == 2
        assert chr_metric.control.rate == 0.5
        assert chr_metric.treatment.rate == 0.0
        assert chr_metric.delta == 0.5

    def test_a_metric_with_an_empty_denominator_has_no_rate(self) -> None:
        control, treatment = self.make_pair()

        report = compute_pair_report(control, treatment)

        quote = report.metrics["quote_fidelity"]
        assert quote.control is not None
        assert quote.control.denominator == 0
        assert quote.control.rate is None
        assert quote.delta is None

    def test_post_retrieval_hallucination_is_treatment_only(self) -> None:
        control, treatment = self.make_pair()

        report = compute_pair_report(control, treatment)

        prh = report.metrics["post_retrieval_hallucination_rate"]
        assert prh.control is None
        assert prh.treatment is not None
        assert prh.treatment.denominator == 2
        assert prh.treatment.numerator == 0

    def test_confidence_intervals_are_deterministic_and_ordered(self) -> None:
        control, treatment = self.make_pair()

        first = compute_pair_report(control, treatment)
        second = compute_pair_report(control, treatment)

        est = first.metrics["citation_hallucination_rate"].control
        assert est is not None
        assert est.rate is not None
        assert est.ci_low is not None and est.ci_high is not None
        assert 0.0 <= est.ci_low <= est.rate <= est.ci_high <= 1.0
        assert first == second

    def test_the_report_carries_the_versions_and_buckets(self) -> None:
        control, treatment = self.make_pair()

        report = compute_pair_report(control, treatment)

        assert report.metrics_version == METRICS_VERSION == "llhb-metrics-v1"
        assert report.scorer_version == SCORER_VERSION
        assert report.bootstrap == {"seed": BOOTSTRAP_SEED, "resamples": BOOTSTRAP_RESAMPLES}
        assert report.unresolved.control["unresolved_claims"] == 0
        assert report.per_category["C1"].control is not None
        assert report.per_category["C1"].control.denominator == 2

    def test_mismatched_case_sets_are_refused(self) -> None:
        control, _ = self.make_pair()

        try:
            compute_pair_report(control, control[:1])
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

        report = compute_pair_report(control, treatment)

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

        report = compute_pair_report(control, treatment)

        fpr = report.metrics["false_premise_rejection_rate"]
        assert fpr.control is not None and fpr.treatment is not None
        assert fpr.control.denominator == 0
        assert fpr.treatment.denominator == 1
        assert report.unresolved.control["cases_unresolved"] == 1
