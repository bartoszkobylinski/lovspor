"""Stage 8 aggregation: SCORING.md §6 metrics over scored case-runs.

Consumes (case, record, CaseScore) bundles for the two arms and
produces the pair report: each metric as absolute numerator /
denominator / rate per arm, the control-treatment delta, and bootstrap
confidence intervals — all deterministic, on a fixed recorded seed, so
the same scores produce the same report bytes.

Two §6 rules carry the honesty of the numbers and are enforced here,
not left to the reader: a metric with an empty denominator has no rate
(never a flattering zero), and unresolved material is reported beside
the metrics it could have affected, never silently dropped.

``retrieved_correct`` is deliberately conservative: only a successful
``get_section`` of the case's expected provision is deterministic from
the committed trace alone — tool payloads are not committed (F3), so a
search result that happened to contain the provision does not count.
"""

import random
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from lovspor.headings import canonical_section_id
from lovspor.llhb.scoring import SCORER_VERSION, CaseScore, CriterionVerdict

METRICS_VERSION = "llhb-metrics-v1"
BOOTSTRAP_SEED = 42
BOOTSTRAP_RESAMPLES = 2000
_CI_LOW_Q = 0.025
_CI_HIGH_Q = 0.975

_Bundle = tuple[Mapping[str, Any], Mapping[str, Any], CaseScore]
_Sample = tuple[float, float]  # (numerator, denominator) contribution of one case
_Sampler = Callable[[_Bundle], _Sample]


class MetricEstimate(BaseModel, frozen=True):
    """One metric on one arm: absolute counts, rate, bootstrap CI."""

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


class PairReport(BaseModel, frozen=True):
    """§6 metrics for one control/treatment pair, plus the buckets."""

    metrics: dict[str, MetricPair]
    per_category: dict[str, MetricPair]
    unresolved: UnresolvedBuckets
    scorer_version: str
    metrics_version: str
    bootstrap: dict[str, int]


def retrieved_correct(case: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    """Did the trace retrieve the case's expected provision, verbatim?"""
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


def compute_pair_report(control: Sequence[_Bundle], treatment: Sequence[_Bundle]) -> PairReport:
    """The §6 report for one pair; refuses arms over different cases."""
    control_ids = [str(b[2].case_id) for b in control]
    treatment_ids = [str(b[2].case_id) for b in treatment]
    if sorted(control_ids) != sorted(treatment_ids):
        raise ValueError("the two arms cover different case sets; nothing to compare")
    metrics = {name: _metric_pair(name, control, treatment) for name in _SAMPLERS}
    return PairReport(
        metrics=metrics,
        per_category=_per_category(control, treatment),
        unresolved=UnresolvedBuckets(control=_buckets(control), treatment=_buckets(treatment)),
        scorer_version=SCORER_VERSION,
        metrics_version=METRICS_VERSION,
        bootstrap={"seed": BOOTSTRAP_SEED, "resamples": BOOTSTRAP_RESAMPLES},
    )


def _metric_pair(name: str, control: Sequence[_Bundle], treatment: Sequence[_Bundle]) -> MetricPair:
    sampler = _SAMPLERS[name]
    if name in _TREATMENT_ONLY:
        return MetricPair(
            control=None,
            treatment=_estimate([sampler(b) for b in treatment]),
            delta=None,
            delta_ci_low=None,
            delta_ci_high=None,
        )
    return _two_arm_pair(sampler, control, treatment)


def _two_arm_pair(
    sampler: _Sampler, control: Sequence[_Bundle], treatment: Sequence[_Bundle]
) -> MetricPair:
    control_est = _estimate([sampler(b) for b in control])
    treatment_est = _estimate([sampler(b) for b in treatment])
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


def _estimate(samples: list[_Sample]) -> MetricEstimate:
    numerator = sum(s[0] for s in samples)
    denominator = sum(s[1] for s in samples)
    if not denominator:
        return MetricEstimate(
            numerator=numerator, denominator=denominator, rate=None, ci_low=None, ci_high=None
        )
    low, high = _bootstrap_ci(samples)
    return MetricEstimate(
        numerator=numerator,
        denominator=denominator,
        rate=numerator / denominator,
        ci_low=low,
        ci_high=high,
    )


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


def _chr_sample(bundle: _Bundle) -> _Sample:
    score = bundle[2]
    in_denominator = 1.0 if score.asserted_citations else 0.0
    return (1.0 if in_denominator and score.asserted_h1 else 0.0, in_denominator)


def _accuracy_sample(bundle: _Bundle) -> _Sample:
    score = bundle[2]
    return (float(score.asserted_valid), float(score.asserted_resolved))


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
    score = bundle[2]
    return (float(score.quotes_verified), float(score.quotes_detected))


def _fpr_sample(bundle: _Bundle) -> _Sample:
    score = bundle[2]
    verdict = score.criteria.get("false-premise-not-endorsed")
    if score.category != "C6" or verdict is CriterionVerdict.UNRESOLVED or verdict is None:
        return (0.0, 0.0)
    return (1.0 if verdict is CriterionVerdict.PASS else 0.0, 1.0)


def _no_invention_sample(bundle: _Bundle) -> _Sample:
    score = bundle[2]
    if score.category != "C8" or score.passed is None:
        return (0.0, 0.0)
    return (1.0 if score.passed else 0.0, 1.0)


def _prh_sample(bundle: _Bundle) -> _Sample:
    """§6, treatment only: hallucinated although the truth was in hand.

    H1 is an asserted nonexistent section; the deterministic H2 signal
    is a C4 wrong attribution the answer asserted.
    """
    case, record, score = bundle
    if not retrieved_correct(case, record):
        return (0.0, 0.0)
    misattributed = score.criteria.get("claimed-attribution-not-asserted")
    hallucinated = bool(score.asserted_h1) or misattributed is CriterionVerdict.FAIL
    return (1.0 if hallucinated else 0.0, 1.0)


_SAMPLERS: dict[str, _Sampler] = {
    "citation_hallucination_rate": _chr_sample,
    "citation_accuracy": _accuracy_sample,
    "misattribution_rate": _misattribution_sample,
    "correct_provision_identification": _cpi_sample,
    "quote_fidelity": _quote_fidelity_sample,
    "false_premise_rejection_rate": _fpr_sample,
    "no_invention_rate": _no_invention_sample,
    "post_retrieval_hallucination_rate": _prh_sample,
}
_TREATMENT_ONLY = frozenset({"post_retrieval_hallucination_rate"})
