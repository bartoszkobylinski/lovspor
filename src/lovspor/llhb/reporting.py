"""Stage 8 reporting: scored bundles from committed run artifacts.

The composition layer between records on disk and the §6 report. The
fairness gate has already refused unfair pairs by the time this runs;
this layer refuses to be the backstop that quietly scores what slipped
past it — a record for a case the dataset does not hold, an incomplete
case, an answer that is not there.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from lovspor.errors import LovsporError
from lovspor.llhb.scoring import CaseScore, CaseScorer

Bundle = tuple[Mapping[str, Any], Mapping[str, Any], CaseScore]


class ReportingError(LovsporError):
    """The committed artifacts cannot be composed into a scored arm."""


def score_arm(
    scorer: CaseScorer,
    cases_by_id: Mapping[str, Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
) -> list[Bundle]:
    """Every record scored against its own case, sorted by case id."""
    bundles = []
    for record in sorted(records, key=lambda r: str(r.get("case_id"))):
        case_id = str(record.get("case_id"))
        case = cases_by_id.get(case_id)
        if case is None:
            raise ReportingError(f"record for {case_id} has no case in the dataset")
        bundles.append((case, record, scorer.score(case, _answer(record, case_id))))
    return bundles


def _answer(record: Mapping[str, Any], case_id: str) -> str:
    if not record.get("completed"):
        raise ReportingError(
            f"{case_id} is incomplete; an unscored error is not a scoreable answer"
        )
    answer = record.get("final_answer")
    if not isinstance(answer, str):
        raise ReportingError(f"{case_id} is completed but carries no final_answer string")
    return answer
