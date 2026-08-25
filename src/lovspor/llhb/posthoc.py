"""Layer 3 of the frozen Opus pair's published lineage (ruling #30(a), issue #168).

Ruling #30(a) keeps three layers, none deleted, none rewritten: the metrics as
scored at freeze, the scorer-v2 corrected diagnostic results, and a clearly
labelled post-hoc supplementary re-analysis. This module is the third.

It is **not confirmatory and cannot become so.** The scorer-v2 cue extensions
were informed by inspecting the frozen pair's answers, which is calibration on
evaluation data; no amount of care in the aggregation changes what the scorer
learned. The artifact says so in a field, not only in prose, because a number
lifted out of a JSON file travels without its paragraph.

Why re-aggregate rather than compute the four figures by hand from the existing
report: they are derivable by arithmetic, and that is exactly the problem. The
point is to be able to say

    the reviewer's arithmetic happens to agree with the independently generated
    supplementary artifact

instead of *we copied the reviewer's numbers into our report*. So the frozen
answers pass through the same deterministic scorer — cue- and structure-based,
no model call anywhere in it — and are aggregated differently.

The pair is hard-coded. This is a one-off execution of ruling #30(a) for one
historical pair, not a general facility, and there is no pair manifest binding
these two runs: manifest gating (ruling #30(d)) landed after they were scored.
The expectations below are therefore the only gate available, so they are
checked before anything is aggregated.
"""

import hashlib
from pathlib import Path
from typing import Any

from lovspor.errors import LovsporError
from lovspor.llhb.reporting import ArmScoring
from lovspor.llhb.scoring import SCORER_VERSION

CONTROL_RUN_ID = "llhb-v1-run-20260812-frozen2"
TREATMENT_RUN_ID = "llhb-v1-run-20260812-treatfrozen4"
CASES_PER_ARM = 250

#: SHA-256 over the exact committed bytes of each arm's ``records.jsonl``.
EXPECTED_RECORD_SHA256 = {
    CONTROL_RUN_ID: "2ad1653744c1d6d29cf0ecb53d17b3883ce15412af7169e3b8f34d451b0e8024",
    TREATMENT_RUN_ID: "ba587eaddbded0bf4b3a2f760563314bc53014563c84f6c05f9e4ccf6419fa92",
}

#: The scorer this analysis is defined against. A different one would be a
#: different measurement wearing the same label.
EXPECTED_SCORER_VERSION = "llhb-score-v2"

ANALYSIS_STATUS = "post_hoc_supplementary"


class PosthocGuardError(LovsporError):
    """The frozen pair is not what this analysis is defined for."""


def records_sha256(path: Path) -> str:
    """The arm's committed bytes, hashed exactly as they sit on disk."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_frozen_pair(control_records: Path, treatment_records: Path) -> None:
    """Refuse anything but the pair this analysis is defined for.

    Checked before a single answer is aggregated. A generator that accepted
    arbitrary runs would have no gate at all — the manifest that would normally
    provide one does not exist for this pair.
    """
    for run_id, path in ((CONTROL_RUN_ID, control_records), (TREATMENT_RUN_ID, treatment_records)):
        actual = records_sha256(path)
        expected = EXPECTED_RECORD_SHA256[run_id]
        if actual != expected:
            raise PosthocGuardError(f"{run_id}: records.jsonl is {actual}, expected {expected}")


def verify_scored_pair(control: ArmScoring, treatment: ArmScoring) -> None:
    """Refuse a scoring that is not 250 + 250 at the expected scorer."""
    if SCORER_VERSION != EXPECTED_SCORER_VERSION:
        raise PosthocGuardError(
            f"scorer is {SCORER_VERSION}, this analysis is defined at {EXPECTED_SCORER_VERSION}"
        )
    for name, arm in (("control", control), ("treatment", treatment)):
        if len(arm.cases) != CASES_PER_ARM:
            raise PosthocGuardError(f"{name}: {len(arm.cases)} cases, expected {CASES_PER_ARM}")


def _volume(count: int) -> dict[str, float | int]:
    """A count and its per-answer mean, always together.

    Never the mean alone: the 61-vs-63 class of finding is a volume, and a rate
    without its numerator hides how much evidence is behind it.
    """
    return {"count": count, "mean_per_answer": count / CASES_PER_ARM}


def arm_metrics(arm: ArmScoring) -> dict[str, Any]:
    """The four supplementary figures, over all 250 cases.

    Unconditional in the denominator sense ruling #30(a) requires: an answer
    that invents no citation because it makes no citation is a success of the
    arm, and conditioning it away flattered both arms unequally.

    ``unscored`` is reported beside them because a case with no score
    contributes nothing to every numerator here, which makes it indistinguishable
    from a clean answer unless it is counted. That is the defect ruling #30(c)
    closed for C8 — one null meaning several different things — and it would
    walk straight back in through a denominator of 250 that quietly contains
    cases nobody could measure.
    """
    scores = [bundle[2] for bundle in arm.bundles]
    return {
        "unconditional_h1": _volume(sum(1 for score in scores if score.asserted_h1)),
        "citation_coverage": _volume(sum(1 for score in scores if score.asserted_citations)),
        "valid_citation_instances": _volume(sum(score.asserted_valid for score in scores)),
        "invalid_citation_instances": _volume(
            sum(score.asserted_resolved - score.asserted_valid for score in scores)
        ),
        "scored": len(scores),
        "unscored": len(arm.cases) - len(scores),
    }


def supplementary_report(control: ArmScoring, treatment: ArmScoring) -> dict[str, Any]:
    """The layer-3 artifact, labelled in a field rather than only in prose."""
    verify_scored_pair(control, treatment)
    return {
        "analysis_status": ANALYSIS_STATUS,
        "confirmatory": False,
        "reason": "reporting definitions adopted after inspection of frozen-pair outputs",
        "ruling": "DECISIONS.md #30(a)",
        "control_run": CONTROL_RUN_ID,
        "treatment_run": TREATMENT_RUN_ID,
        "semantic_scorer": SCORER_VERSION,
        "cases_per_arm": CASES_PER_ARM,
        "records_sha256": dict(EXPECTED_RECORD_SHA256),
        "metrics": {"control": arm_metrics(control), "treatment": arm_metrics(treatment)},
    }
