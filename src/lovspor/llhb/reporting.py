"""Stage 8 reporting: scored bundles from committed run artifacts.

The composition layer between records on disk and the §6 report. Two kinds of
defect meet here and they are not the same kind. An artifact that cannot be
composed at all — a record for a case the dataset does not hold — is still a
typed refusal: the fairness gate should have caught it, and scoring past it
would compare two different experiments.

A case that produced no usable score is different. Ruling #30(e) gates model
errors and scorer errors by count and drops the affected pairs pairwise, which
a refusal makes impossible: a run cannot be measured against a threshold it
aborts on. Those cases are therefore classified (``outcomes.CaseOutcome``),
counted, and carried into the report.
"""

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from lovspor.errors import LovsporError
from lovspor.llhb.outcomes import CaseOutcome, ScoredCase, model_error_reason, outcome_of
from lovspor.llhb.scoring import CaseScore, CaseScorer

Bundle = tuple[Mapping[str, Any], Mapping[str, Any], CaseScore]


class ReportingError(LovsporError):
    """The committed artifacts cannot be composed into a scored arm."""


class ArmScoring(NamedTuple):
    """One arm: the bundles metrics can use, and every case's outcome.

    ``cases`` covers every record, scored or not — the five-way reason code
    per case that ruling #30(c) requires artifacts to carry.
    """

    bundles: list[Bundle]
    cases: list[ScoredCase]


def score_arm(
    scorer: CaseScorer,
    cases_by_id: Mapping[str, Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
) -> ArmScoring:
    """Every record scored against its own case, sorted by case id."""
    arm = ArmScoring(bundles=[], cases=[])
    for record in sorted(records, key=lambda r: str(r.get("case_id"))):
        case_id = str(record.get("case_id"))
        case = cases_by_id.get(case_id)
        if case is None:
            raise ReportingError(f"record for {case_id} has no case in the dataset")
        _score_one(scorer, case, record, arm)
    return arm


def _score_one(
    scorer: CaseScorer, case: Mapping[str, Any], record: Mapping[str, Any], arm: ArmScoring
) -> None:
    """Score one record into ``arm``, or record why it could not be scored."""
    case_id = str(record.get("case_id"))
    category = str(case.get("category"))
    reason = model_error_reason(record)
    if reason is not None:
        arm.cases.append(
            ScoredCase(
                case_id=case_id,
                category=category,
                outcome=CaseOutcome.MODEL_ERROR,
                detail=reason,
            )
        )
        return
    try:
        score = scorer.score(case, str(record["final_answer"]))
    except (LovsporError, ValueError, KeyError, TypeError) as exc:
        # The scorer breaking is a fact about the instrument, and ruling #30(e)
        # gates it separately from the provider's failures. Catching it here
        # does not interpret it: the frozen classification logic is untouched.
        arm.cases.append(
            ScoredCase(
                case_id=case_id,
                category=category,
                outcome=CaseOutcome.SCORER_ERROR,
                detail=f"{type(exc).__name__}: {exc}",
            )
        )
        return
    arm.bundles.append((case, record, score))
    arm.cases.append(
        ScoredCase(case_id=case_id, category=category, outcome=outcome_of(score), score=score)
    )
