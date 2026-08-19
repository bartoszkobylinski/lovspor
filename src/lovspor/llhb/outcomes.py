"""Why a case produced no usable score — the five-way taxonomy (ruling #30(c)).

The v2 aggregation carried one ``passed is None`` that meant three different
things: the frozen scorer could not classify the answer, the scorer itself
broke, or the model never produced an answer. Ruling #30(e) gates the last two
separately — a run with too many model errors has no confirmatory verdict, and
a run with too many scorer errors means the instrument is broken, not the
provider — so a single null cannot carry the distinction.

Classification happens here, at the reporting boundary. The semantic layer
(``llhb-score-v2``, stance, cue lists, parsing) is frozen by ruling #30(b) and
is not touched: a scorer failure is caught, never interpreted.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from lovspor.llhb.scoring import CaseScore


class CaseOutcome(StrEnum):
    """What happened to one case-run, in the artifact's own vocabulary."""

    PASS = "PASS"  # noqa: S105 — a verdict word, not a credential
    FAIL = "FAIL"
    UNRESOLVED_SEMANTIC = "UNRESOLVED_SEMANTIC"
    SCORER_ERROR = "SCORER_ERROR"
    MODEL_ERROR = "MODEL_ERROR"


#: Outcomes that carry no CaseScore, so the pair drops from every metric.
UNSCORED = frozenset({CaseOutcome.SCORER_ERROR, CaseOutcome.MODEL_ERROR})


class ScoredCase(BaseModel, frozen=True):
    """One case-run with its outcome, and its score when there is one."""

    case_id: str
    category: str
    outcome: CaseOutcome
    score: CaseScore | None = None
    detail: str | None = None

    @property
    def scored(self) -> bool:
        return self.score is not None


def outcome_of(score: CaseScore) -> CaseOutcome:
    """PASS / FAIL / UNRESOLVED_SEMANTIC, from what the frozen scorer said.

    ``passed is None`` here means exactly one thing — the scorer ran and could
    not classify the answer — because the two error paths never reach a score.
    """
    if score.passed is None:
        return CaseOutcome.UNRESOLVED_SEMANTIC
    return CaseOutcome.PASS if score.passed else CaseOutcome.FAIL


def model_error_reason(record: Mapping[str, Any]) -> str | None:
    """Why this record has no answer to score, or None if it has one."""
    if not record.get("completed"):
        errors = record.get("errors") or []
        return f"incomplete run record: {errors}" if errors else "incomplete run record"
    if not isinstance(record.get("final_answer"), str):
        return "completed record carries no final_answer string"
    return None
