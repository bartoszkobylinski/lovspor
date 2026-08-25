"""Stage 8 aggregation: SCORING.md §6 metrics over scored case-runs.

Consumes the two arms' scorings and produces the pair report: each metric as
absolute numerator / denominator / rate per arm, the control-treatment delta,
and confidence intervals — all deterministic, on a fixed recorded seed, so the
same scores produce the same report bytes.

Two §6 rules carry the honesty of the numbers and are enforced here, not left
to the reader: a metric with an empty denominator has no rate (never a
flattering zero), and unresolved material is reported beside the metrics it
could have affected, never silently dropped.

``llhb-metrics-v3`` implements the confirmatory analysis plan (ruling #30):

* the primary estimand is answer-level and **unconditional** — asserted H1 over
  every eligible case, not only over cases that cited something (#30(a));
* a case that produced no score drops **pairwise** from every metric, and the
  count of such pairs is gated before any effect is interpreted (#30(e));
* C8 is reported as counts over all its cases, and the ratio that drops the
  unresolved ones survives only under an explicit resolved-case name (#30(c));
* arm rates carry Wilson intervals, the delta a paired bootstrap of n = 10,000.

``retrieved_directly`` is deliberately conservative: only a successful
``get_section`` of the case's expected provision is deterministic from the
committed trace alone — tool payloads are not committed (F3), so a search
result that happened to contain the provision does not count. The metric built
on it is named for that narrowness (issue #107).
"""

import math
import random
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, model_validator

from lovspor.headings import canonical_section_id
from lovspor.llhb.outcomes import CaseOutcome, ScoredCase
from lovspor.llhb.reporting import ArmScoring
from lovspor.llhb.scoring import SCORER_VERSION, CaseScore, CriterionVerdict

METRICS_VERSION = "llhb-metrics-v3"
BOOTSTRAP_SEED = 42
BOOTSTRAP_RESAMPLES = 10_000
PRIMARY_METRIC = "unconditional_h1_rate"
# Ruling #30(e), stated there as shares of the 250-case frozen pair: a run over
# either ceiling has no confirmatory verdict. Scorer errors are the tighter
# gate because they indict the instrument, not the provider.
MODEL_ERROR_MAX_PAIRS = 5
SCORER_ERROR_MAX_PAIRS = 2
_CI_LOW_Q = 0.025
_CI_HIGH_Q = 0.975
_WILSON_Z = 1.959963984540054  # two-sided 95%

_Bundle = tuple[Mapping[str, Any], Mapping[str, Any], CaseScore]
_Sample = tuple[float, float]  # (numerator, denominator) contribution of one case
_Sampler = Callable[[_Bundle], _Sample]


class MetricEstimate(BaseModel, frozen=True):
    """One metric on one arm: absolute counts, rate, Wilson interval."""

    numerator: float
    denominator: float
    rate: float | None
    ci_low: float | None
    ci_high: float | None


class MetricPair(BaseModel, frozen=True):
    """One metric across the pair; None where an arm is out of scope."""

    control: MetricEstimate | None
    treatment: MetricEstimate | None
    delta: float | None
    delta_ci_low: float | None
    delta_ci_high: float | None


class UnresolvedBuckets(BaseModel, frozen=True):
    control: dict[str, int]
    treatment: dict[str, int]


class OutcomeCounts(BaseModel, frozen=True):
    """The five-way reason code, counted. Papers may collapse the last three
    into one UNRESOLVED column; artifacts never do (ruling #30(c))."""

    PASS: int
    FAIL: int
    UNRESOLVED_SEMANTIC: int
    SCORER_ERROR: int
    MODEL_ERROR: int
    total: int


class ArmPair(BaseModel, frozen=True):
    control: OutcomeCounts
    treatment: OutcomeCounts


class Eligibility(BaseModel, frozen=True):
    """Whether this pair may carry a confirmatory verdict at all (#30(e))."""

    pairs_total: int
    pairs_scored: int
    model_error_pairs: tuple[str, ...]
    scorer_error_pairs: tuple[str, ...]
    model_error_gate_passed: bool
    scorer_error_gate_passed: bool
    confirmatory_eligible: bool


class Missingness(BaseModel, frozen=True):
    """What the dropped pairs could have done to the primary effect.

    Each missing pair-difference is in {-1, 0, +1}, so m of them move the
    all-cases delta by at most m/N either way. A *confirmed* verdict has to
    survive the unfavourable end of that band (analysis plan §3).
    """

    dropped_pairs: int
    delta_all_cases: float | None
    worst_case_low: float | None
    worst_case_high: float | None
    sign_survives_worst_case: bool | None


class PrimaryResult(BaseModel, frozen=True):
    """The one metric that carries a verdict, and the verdict."""

    metric: str
    delta: float | None
    ci_low: float | None
    ci_high: float | None
    verdict: str
    missingness: Missingness


class PairReport(BaseModel, frozen=True):
    """§6 metrics for one control/treatment pair, plus the buckets.

    The confirmatory fields are optional only so that reports written before
    this version still parse as what they are. A report claiming to be this
    version must carry all of them — see ``_confirmatory_layer_is_complete``.
    """

    metrics: dict[str, MetricPair]
    per_category: dict[str, MetricPair]
    unresolved: UnresolvedBuckets
    scorer_version: str
    metrics_version: str
    bootstrap: dict[str, int]
    outcomes: ArmPair | None = None
    c8_outcomes: ArmPair | None = None
    case_outcomes: dict[str, dict[str, str]] | None = None
    eligibility: Eligibility | None = None
    primary: PrimaryResult | None = None

    @model_validator(mode="after")
    def _confirmatory_layer_is_complete(self) -> "PairReport":
        """A v3 report without its reason codes, gates or primary result would
        read as a confirmatory artifact while carrying none of what ruling #30
        requires one to carry. Older versions are read, never re-labelled."""
        if self.metrics_version != METRICS_VERSION:
            return self
        missing = [
            name
            for name in ("outcomes", "c8_outcomes", "case_outcomes", "eligibility", "primary")
            if getattr(self, name) is None
        ]
        if missing:
            raise ValueError(f"{METRICS_VERSION} report is missing {', '.join(missing)}")
        return self


def retrieved_directly(case: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    """Did the trace fetch the case's expected provision by ``get_section``?"""
    expected = (
        str(case.get("expected_act_slug")),
        canonical_section_id(str(case.get("expected_section_id"))),
    )
    for call in record.get("tool_calls") or []:
        if not isinstance(call, Mapping) or call.get("is_error"):
            continue
        if str(call.get("name")) != "mcp__lovverk__get_section":
            continue
        arguments = call.get("arguments") or {}
        pair = (
            str(arguments.get("slug")),
            canonical_section_id(str(arguments.get("section_id"))),
        )
        if pair == expected:
            return True
    return False


def compute_pair_report(control: ArmScoring, treatment: ArmScoring) -> PairReport:
    """The §6 report for one pair; refuses arms over different case sets."""
    ids = _shared_case_ids(control, treatment)
    eligibility = _eligibility(control, treatment, ids)
    scored = (
        frozenset(ids) - set(eligibility.model_error_pairs) - set(eligibility.scorer_error_pairs)
    )
    control_bundles = [b for b in control.bundles if str(b[2].case_id) in scored]
    treatment_bundles = [b for b in treatment.bundles if str(b[2].case_id) in scored]
    metrics = {name: _metric_pair(name, control_bundles, treatment_bundles) for name in _SAMPLERS}
    return PairReport(
        metrics=metrics,
        per_category=_per_category(control_bundles, treatment_bundles),
        unresolved=UnresolvedBuckets(
            control=_buckets(control_bundles), treatment=_buckets(treatment_bundles)
        ),
        outcomes=_outcome_pair(control.cases, treatment.cases),
        c8_outcomes=_outcome_pair(_c8(control.cases), _c8(treatment.cases)),
        case_outcomes={
            "control": _case_outcomes(control.cases),
            "treatment": _case_outcomes(treatment.cases),
        },
        eligibility=eligibility,
        primary=_primary(metrics[PRIMARY_METRIC], control_bundles, treatment_bundles, eligibility),
        scorer_version=SCORER_VERSION,
        metrics_version=METRICS_VERSION,
        bootstrap={"seed": BOOTSTRAP_SEED, "resamples": BOOTSTRAP_RESAMPLES},
    )


def _shared_case_ids(control: ArmScoring, treatment: ArmScoring) -> list[str]:
    control_ids = sorted(c.case_id for c in control.cases)
    treatment_ids = sorted(c.case_id for c in treatment.cases)
    if control_ids != treatment_ids:
        raise ValueError("the two arms cover different case sets; nothing to compare")
    return control_ids


def _eligibility(control: ArmScoring, treatment: ArmScoring, ids: list[str]) -> Eligibility:
    """Pairs drop pairwise: one arm failing removes the case from both."""
    by_id = {c.case_id: c.outcome for c in control.cases}
    other = {c.case_id: c.outcome for c in treatment.cases}
    model_errors = tuple(i for i in ids if CaseOutcome.MODEL_ERROR in (by_id[i], other[i]))
    scorer_errors = tuple(
        i for i in ids if i not in model_errors and CaseOutcome.SCORER_ERROR in (by_id[i], other[i])
    )
    model_gate = len(model_errors) <= MODEL_ERROR_MAX_PAIRS
    scorer_gate = len(scorer_errors) <= SCORER_ERROR_MAX_PAIRS
    return Eligibility(
        pairs_total=len(ids),
        pairs_scored=len(ids) - len(model_errors) - len(scorer_errors),
        model_error_pairs=model_errors,
        scorer_error_pairs=scorer_errors,
        model_error_gate_passed=model_gate,
        scorer_error_gate_passed=scorer_gate,
        confirmatory_eligible=model_gate and scorer_gate,
    )


def _primary(
    pair: MetricPair,
    control: Sequence[_Bundle],
    treatment: Sequence[_Bundle],
    eligibility: Eligibility,
) -> PrimaryResult:
    missingness = _missingness(control, treatment, eligibility)
    return PrimaryResult(
        metric=PRIMARY_METRIC,
        delta=pair.delta,
        ci_low=pair.delta_ci_low,
        ci_high=pair.delta_ci_high,
        verdict=_verdict(pair, eligibility, missingness),
        missingness=missingness,
    )


def _verdict(pair: MetricPair, eligibility: Eligibility, missingness: Missingness) -> str:
    """The plan's three words, or the refusal to say any of them.

    "Confirmed" additionally requires that the worst-case assignment of the
    dropped pairs does not reverse the sign — an effect that only exists while
    the missing data is assumed friendly is not confirmed.
    """
    if not eligibility.confirmatory_eligible:
        return "no_confirmatory_verdict"
    if pair.delta_ci_low is None or pair.delta_ci_high is None:
        return "inconclusive"
    if pair.delta_ci_high < 0:
        return "reversed"
    if pair.delta_ci_low > 0 and missingness.sign_survives_worst_case is not False:
        return "confirmed"
    return "inconclusive"


def _missingness(
    control: Sequence[_Bundle], treatment: Sequence[_Bundle], eligibility: Eligibility
) -> Missingness:
    sampler = _SAMPLERS[PRIMARY_METRIC]
    control_by_id = {str(b[2].case_id): sampler(b)[0] for b in control}
    treatment_by_id = {str(b[2].case_id): sampler(b)[0] for b in treatment}
    total = eligibility.pairs_total
    dropped = total - eligibility.pairs_scored
    if not total:
        return Missingness(
            dropped_pairs=dropped,
            delta_all_cases=None,
            worst_case_low=None,
            worst_case_high=None,
            sign_survives_worst_case=None,
        )
    observed = sum(control_by_id[i] - treatment_by_id[i] for i in control_by_id)
    return _missingness_band(observed, dropped, total)


def _missingness_band(observed: float, dropped: int, total: int) -> Missingness:
    delta = observed / total
    low = (observed - dropped) / total
    high = (observed + dropped) / total
    survives = None if not dropped else (low > 0 if delta > 0 else high < 0 if delta < 0 else False)
    return Missingness(
        dropped_pairs=dropped,
        delta_all_cases=delta,
        worst_case_low=low,
        worst_case_high=high,
        sign_survives_worst_case=survives,
    )


def _outcome_pair(control: Sequence[ScoredCase], treatment: Sequence[ScoredCase]) -> ArmPair:
    return ArmPair(control=_counts(control), treatment=_counts(treatment))


def _counts(cases: Sequence[ScoredCase]) -> OutcomeCounts:
    tally = dict.fromkeys(CaseOutcome, 0)
    for case in cases:
        tally[case.outcome] += 1
    return OutcomeCounts(
        PASS=tally[CaseOutcome.PASS],
        FAIL=tally[CaseOutcome.FAIL],
        UNRESOLVED_SEMANTIC=tally[CaseOutcome.UNRESOLVED_SEMANTIC],
        SCORER_ERROR=tally[CaseOutcome.SCORER_ERROR],
        MODEL_ERROR=tally[CaseOutcome.MODEL_ERROR],
        total=len(cases),
    )


def _c8(cases: Sequence[ScoredCase]) -> list[ScoredCase]:
    return [case for case in cases if case.category == "C8"]


def _case_outcomes(cases: Sequence[ScoredCase]) -> dict[str, str]:
    return {case.case_id: case.outcome.value for case in sorted(cases, key=lambda c: c.case_id)}


def _metric_pair(name: str, control: Sequence[_Bundle], treatment: Sequence[_Bundle]) -> MetricPair:
    sampler = _SAMPLERS[name]
    wilson = name not in _MEAN_METRICS
    if name in _TREATMENT_ONLY:
        return MetricPair(
            control=None,
            treatment=_estimate([sampler(b) for b in treatment], wilson),
            delta=None,
            delta_ci_low=None,
            delta_ci_high=None,
        )
    return _two_arm_pair(sampler, control, treatment, wilson)


def _two_arm_pair(
    sampler: _Sampler,
    control: Sequence[_Bundle],
    treatment: Sequence[_Bundle],
    wilson: bool = True,
) -> MetricPair:
    control_est = _estimate([sampler(b) for b in control], wilson)
    treatment_est = _estimate([sampler(b) for b in treatment], wilson)
    if control_est.rate is None or treatment_est.rate is None:
        return MetricPair(
            control=control_est,
            treatment=treatment_est,
            delta=None,
            delta_ci_low=None,
            delta_ci_high=None,
        )
    low, high = _paired_delta_ci(sampler, control, treatment)
    return MetricPair(
        control=control_est,
        treatment=treatment_est,
        delta=control_est.rate - treatment_est.rate,
        delta_ci_low=low,
        delta_ci_high=high,
    )


def _estimate(samples: list[_Sample], wilson: bool = True) -> MetricEstimate:
    """One arm's estimate. Wilson for proportions, bootstrap for the rest.

    A per-answer volume is a mean, not a proportion — its "rate" can exceed 1,
    where a Wilson interval is not merely wrong but undefined. Those metrics
    keep the case-level bootstrap, which asks nothing of the scale.
    """
    numerator = sum(s[0] for s in samples)
    denominator = sum(s[1] for s in samples)
    if not denominator:
        return MetricEstimate(
            numerator=numerator, denominator=denominator, rate=None, ci_low=None, ci_high=None
        )
    proportion = wilson and 0.0 <= numerator <= denominator
    low, high = _wilson_interval(numerator, denominator) if proportion else _bootstrap_ci(samples)
    return MetricEstimate(
        numerator=numerator,
        denominator=denominator,
        rate=numerator / denominator,
        ci_low=low,
        ci_high=high,
    )


def _wilson_interval(successes: float, trials: float) -> tuple[float, float]:
    """95% Wilson score interval (analysis plan §4, ruling #30(e)).

    Preregistered, and preregistration is what it is worth here: for metrics
    whose unit is a citation instance rather than a case, the interval treats
    instances as independent and so ignores clustering within an answer. The
    delta — the number that carries the verdict — is bootstrapped over cases
    instead, which does not.
    """
    rate = successes / trials
    centre = rate + _WILSON_Z**2 / (2 * trials)
    spread = _WILSON_Z * math.sqrt((rate * (1 - rate) + _WILSON_Z**2 / (4 * trials)) / trials)
    denominator = 1 + _WILSON_Z**2 / trials
    return (max(0.0, (centre - spread) / denominator), min(1.0, (centre + spread) / denominator))


def _bootstrap_ci(samples: list[_Sample]) -> tuple[float | None, float | None]:
    """Percentile CI over case resamples, on the fixed recorded seed."""
    rng = random.Random(BOOTSTRAP_SEED)  # noqa: S311 — statistics, not secrets
    rates = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        picked = [samples[rng.randrange(len(samples))] for _ in samples]
        denominator = sum(p[1] for p in picked)
        if denominator:
            rates.append(sum(p[0] for p in picked) / denominator)
    return _percentiles(rates)


def _paired_delta_ci(
    sampler: _Sampler, control: Sequence[_Bundle], treatment: Sequence[_Bundle]
) -> tuple[float | None, float | None]:
    """Delta CI from resampling case ids once and scoring both arms on
    the same resample — the pair shares its cases, so the uncertainty
    of the difference must too."""
    control_by_id = {str(b[2].case_id): sampler(b) for b in control}
    treatment_by_id = {str(b[2].case_id): sampler(b) for b in treatment}
    ids = sorted(control_by_id)
    rng = random.Random(BOOTSTRAP_SEED)  # noqa: S311 — statistics, not secrets
    deltas = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        picked = [ids[rng.randrange(len(ids))] for _ in ids]
        control_rate = _resample_rate([control_by_id[i] for i in picked])
        treatment_rate = _resample_rate([treatment_by_id[i] for i in picked])
        if control_rate is not None and treatment_rate is not None:
            deltas.append(control_rate - treatment_rate)
    return _percentiles(deltas)


def _resample_rate(samples: list[_Sample]) -> float | None:
    denominator = sum(s[1] for s in samples)
    if not denominator:
        return None
    return sum(s[0] for s in samples) / denominator


def _percentiles(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return (None, None)
    values.sort()
    return (_at_quantile(values, _CI_LOW_Q), _at_quantile(values, _CI_HIGH_Q))


def _at_quantile(sorted_values: list[float], quantile: float) -> float:
    return sorted_values[round(quantile * (len(sorted_values) - 1))]


def _buckets(bundles: Sequence[_Bundle]) -> dict[str, int]:
    return {
        "unresolved_claims": sum(b[2].unresolved_claims for b in bundles),
        "unattached_quotes": sum(b[2].unattached_quotes for b in bundles),
        "unverifiable_quotes": sum(b[2].quotes_detected - b[2].quotes_checkable for b in bundles),
        "cases_unresolved": sum(1 for b in bundles if b[2].passed is None),
    }


def _per_category(
    control: Sequence[_Bundle], treatment: Sequence[_Bundle]
) -> dict[str, MetricPair]:
    """Citation Hallucination Rate per category — the cross-category
    headline metric, broken down along the dataset's own axis."""
    categories = sorted({str(b[2].category) for b in control})
    sampler = _SAMPLERS["citation_hallucination_rate"]
    return {
        category: _two_arm_pair(
            sampler,
            [b for b in control if str(b[2].category) == category],
            [b for b in treatment if str(b[2].category) == category],
        )
        for category in categories
    }


def _unconditional_h1_sample(bundle: _Bundle) -> _Sample:
    """The primary estimand: did this ANSWER assert a nonexistent provision?

    Denominator is every eligible case, including the ones that cited nothing
    at all — an answer that invents no citation because it makes no citation
    is a success of the arm, and conditioning it away flattered both arms
    unequally (ruling #30(a)).
    """
    return (1.0 if bundle[2].asserted_h1 else 0.0, 1.0)


def _coverage_sample(bundle: _Bundle) -> _Sample:
    """Citation coverage — answers asserting at least one citation (plan §5.1).

    The same quantity `_chr_sample` already computes as its denominator, hoisted
    into a metric of its own because the plan mandates it per arm. Without it a
    conditional rate can only be read against an unstated base: 42/215 and 30/249
    are not comparable until the 215 and the 249 are on the page (issue #178).
    """
    return (1.0 if bundle[2].asserted_citations else 0.0, 1.0)


def _chr_sample(bundle: _Bundle) -> _Sample:
    score = bundle[2]
    in_denominator = 1.0 if score.asserted_citations else 0.0
    return (1.0 if in_denominator and score.asserted_h1 else 0.0, in_denominator)


def _accuracy_sample(bundle: _Bundle) -> _Sample:
    score = bundle[2]
    return (float(score.asserted_valid), float(score.asserted_resolved))


def _valid_volume_sample(bundle: _Bundle) -> _Sample:
    """Valid citation instances per answer (analysis plan §5.4)."""
    return (float(bundle[2].asserted_valid), 1.0)


def _invalid_volume_sample(bundle: _Bundle) -> _Sample:
    """Invalid citation instances per answer — the 61-vs-63 class of finding
    is reported as a volume, never hidden behind a rate (plan §5.4)."""
    score = bundle[2]
    return (float(score.asserted_resolved - score.asserted_valid), 1.0)


def _misattribution_sample(bundle: _Bundle) -> _Sample:
    score = bundle[2]
    if score.category != "C4":
        return (0.0, 0.0)
    claimed = score.criteria.get("claimed-attribution-not-asserted")
    return (1.0 if claimed is CriterionVerdict.FAIL else 0.0, 1.0)


def _cpi_sample(bundle: _Bundle) -> _Sample:
    score = bundle[2]
    if score.category not in ("C1", "C2"):
        return (0.0, 0.0)
    return (1.0 if score.passed is True else 0.0, 1.0)


def _quote_fidelity_sample(bundle: _Bundle) -> _Sample:
    # Checkable, not detected: a quote with verified=None has no defined
    # target and belongs to the unverifiable bucket, not the denominator
    # (issue #86).
    score = bundle[2]
    return (float(score.quotes_verified), float(score.quotes_checkable))


def _fpr_sample(bundle: _Bundle) -> _Sample:
    score = bundle[2]
    verdict = score.criteria.get("false-premise-not-endorsed")
    if score.category != "C6" or verdict is CriterionVerdict.UNRESOLVED or verdict is None:
        return (0.0, 0.0)
    return (1.0 if verdict is CriterionVerdict.PASS else 0.0, 1.0)


def _no_invention_resolved_sample(bundle: _Bundle) -> _Sample:
    """C8 over the cases the scorer could resolve — a complete-case analysis,
    and named as one (ruling #30(c)). The counts over all C8 cases live in
    ``c8_outcomes``; this ratio is not the No-Invention Rate of SCORING.md."""
    score = bundle[2]
    if score.category != "C8" or score.passed is None:
        return (0.0, 0.0)
    return (1.0 if score.passed else 0.0, 1.0)


def _pdrh_sample(bundle: _Bundle) -> _Sample:
    """§6, treatment only: hallucinated although the truth was in hand.

    H1 is an asserted nonexistent section; the deterministic H2 signal
    is a C4 wrong attribution the answer asserted.
    """
    case, record, score = bundle
    if not retrieved_directly(case, record):
        return (0.0, 0.0)
    misattributed = score.criteria.get("claimed-attribution-not-asserted")
    hallucinated = bool(score.asserted_h1) or misattributed is CriterionVerdict.FAIL
    return (1.0 if hallucinated else 0.0, 1.0)


_SAMPLERS: dict[str, _Sampler] = {
    PRIMARY_METRIC: _unconditional_h1_sample,
    "citation_coverage": _coverage_sample,
    "citation_hallucination_rate": _chr_sample,
    "citation_accuracy": _accuracy_sample,
    "valid_citations_per_answer": _valid_volume_sample,
    "invalid_citations_per_answer": _invalid_volume_sample,
    "misattribution_rate": _misattribution_sample,
    "correct_provision_identification": _cpi_sample,
    "quote_fidelity": _quote_fidelity_sample,
    "false_premise_rejection_rate": _fpr_sample,
    "no_invention_rate_resolved_cases": _no_invention_resolved_sample,
    "post_direct_retrieval_hallucination_rate": _pdrh_sample,
}
_TREATMENT_ONLY = frozenset({"post_direct_retrieval_hallucination_rate"})
#: Rates that are means per answer, not proportions: no Wilson interval exists
#: for them, and a value above 1 is normal rather than a defect.
_MEAN_METRICS = frozenset({"valid_citations_per_answer", "invalid_citations_per_answer"})


class ArmReport(BaseModel, frozen=True):
    """One arm on its own: per-metric estimates and outcome counts, no delta.

    A single-arm report is a diagnostic baseline under ruling #31 — a model
    with no treatment arm yet. It reuses every sampler of the pair report so
    the numbers are the ones a later pair would carry, and deliberately has
    no ``primary``, no ``delta`` and no verdict field to misread.
    """

    metrics: dict[str, MetricEstimate]
    outcomes: OutcomeCounts
    c8_outcomes: OutcomeCounts
    unresolved: dict[str, int]
    metrics_version: str = METRICS_VERSION


def compute_arm_report(arm: ArmScoring) -> ArmReport:
    """Every §6 metric over one arm alone; treatment-only metrics stay 0/0.

    A single-arm report exists for arms with no partner (ruling #31), which
    in v1 means control arms. A metric defined only for the treatment arm is
    therefore reported empty rather than computed — even if a record carries
    tool activity, which would make the run invalid under ruling #25 rather
    than make the metric meaningful.
    """
    estimates = {
        name: _estimate([sampler(bundle) for bundle in arm.bundles], name not in _MEAN_METRICS)
        if name not in _TREATMENT_ONLY
        else _estimate([])
        for name, sampler in _SAMPLERS.items()
    }
    return ArmReport(
        metrics=estimates,
        outcomes=_counts(arm.cases),
        c8_outcomes=_counts(_c8(arm.cases)),
        unresolved=_buckets(arm.bundles),
    )
