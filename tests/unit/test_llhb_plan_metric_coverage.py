"""Every metric the analysis plan mandates must be computable (issue #178).

`ANALYSIS-PLAN-fable5-v1.md` is the preregistration for the Fable confirmatory
run: its blob SHA-256 travels in each arm's metadata, and editing it after the
first model call voids confirmatory status. So the plan cannot be amended to
match the code — the code has to match the plan, and it has to match it
*before* the ceremony rather than at the reporting step, when both arms are
already executed and frozen (ruling #30: "Reporting-layer changes mandated by
the plan land BEFORE the confirmatory run").

§5.1 was mandated and had no sampler. Nothing said so; the gap would have
surfaced during the run it was most expensive to surface in. This module is
the thing that says so.
"""

import re
from pathlib import Path

from lovspor.llhb.metrics import _SAMPLERS, PRIMARY_METRIC

PLAN = Path(__file__).parents[2] / "benchmarks" / "llhb" / "ANALYSIS-PLAN-fable5-v1.md"

#: Each numbered item of the plan's §5, mapped to the samplers that compute it.
#: Explicit rather than parsed from prose: a mapping a human has to write is a
#: mapping a human has to think about, and this file exists to force that
#: thought when the plan changes.
PLAN_SECONDARY_METRICS: dict[int, tuple[str, ...]] = {
    1: ("citation_coverage",),
    2: ("citation_hallucination_rate",),
    3: ("citation_accuracy",),
    4: ("valid_citations_per_answer", "invalid_citations_per_answer"),
    5: ("correct_provision_identification",),
    6: ("false_premise_rejection_rate",),
    7: ("quote_fidelity",),
    8: ("no_invention_rate_resolved_cases",),
    9: ("post_direct_retrieval_hallucination_rate",),
}


def _numbered_items() -> list[int]:
    """The numbers of the plan's §5 items, read from the plan itself."""
    section = re.search(r"^## 5\. .*?(?=^## 6\.)", PLAN.read_text(), re.S | re.M)
    assert section is not None, "the plan has no section 5"
    return [int(n) for n in re.findall(r"^(\d+)\. ", section.group(0), re.M)]


def test_the_plan_still_has_the_metrics_this_mapping_describes() -> None:
    """A plan revision that adds or drops an item turns this red rather than
    silently leaving a mandated metric uncomputed."""
    assert _numbered_items() == sorted(PLAN_SECONDARY_METRICS)


def test_every_mandated_metric_has_a_sampler() -> None:
    """The failure this file was written for: §5.1 named a metric nothing
    could compute, and the ceremony would have found out after freezing."""
    missing = {
        item: [name for name in names if name not in _SAMPLERS]
        for item, names in PLAN_SECONDARY_METRICS.items()
    }

    assert {item: names for item, names in missing.items() if names} == {}


def test_the_primary_metric_is_not_hidden_among_the_secondary_ones() -> None:
    """§5 is *secondary* metrics, explicitly carrying no confirmatory label.
    The primary estimand must not be mapped in here as though it were one."""
    mapped = {name for names in PLAN_SECONDARY_METRICS.values() for name in names}

    assert PRIMARY_METRIC not in mapped
    assert PRIMARY_METRIC in _SAMPLERS


def test_no_sampler_is_unaccounted_for() -> None:
    """The other direction: a sampler nobody's plan asks for is either dead
    weight or an undocumented metric, and both deserve to be noticed."""
    mapped = {name for names in PLAN_SECONDARY_METRICS.values() for name in names}

    assert set(_SAMPLERS) - mapped - {PRIMARY_METRIC} == {"misattribution_rate"}
