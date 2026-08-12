"""Stage 8 reporting: scored bundles from committed run artifacts.

The composition layer between records on disk and the §6 report: each
record is paired with its case and scored, and everything that would
silently corrupt a report — a record for a case the dataset does not
hold, an incomplete case, a missing answer — is a typed refusal. The
fairness gate rejects such pairs first; this layer refuses to be the
backstop that quietly scores them anyway.
"""

from pathlib import Path

import pytest

from lovspor.llhb.reporting import ReportingError, score_arm
from lovspor.llhb.scoring import CaseScorer
from tests.unit.llhb_fixtures import standard_corpus


@pytest.fixture
def scorer(tmp_path: Path) -> CaseScorer:
    return CaseScorer(standard_corpus(tmp_path))


def case(case_id: str) -> dict[str, object]:
    return {
        "case_id": case_id,
        "category": "C1",
        "expected_act_slug": "testloven",
        "expected_section_id": "1",
        "deterministic_criteria": ["expected-provision-cited", "no-invalid-citations"],
    }


def record(case_id: str, answer: str = "Etter testloven § 1 gjelder dette.") -> dict[str, object]:
    return {"case_id": case_id, "completed": True, "final_answer": answer, "tool_calls": []}


class TestScoreArm:
    def test_scores_each_record_against_its_own_case(self, scorer: CaseScorer) -> None:
        cases = {"llhb-v1-C1-101": case("llhb-v1-C1-101"), "llhb-v1-C1-102": case("llhb-v1-C1-102")}
        records = [
            record("llhb-v1-C1-102", "Etter testloven § 5-12 gis fradrag."),
            record("llhb-v1-C1-101"),
        ]

        bundles = score_arm(scorer, cases, records)

        assert [b[2].case_id for b in bundles] == ["llhb-v1-C1-101", "llhb-v1-C1-102"]
        assert bundles[0][2].passed is True
        assert bundles[1][2].passed is False

    def test_a_record_for_an_unknown_case_is_refused(self, scorer: CaseScorer) -> None:
        with pytest.raises(
            ReportingError, match=r"^record for llhb-v1-C1-999 has no case in the dataset$"
        ):
            score_arm(scorer, {}, [record("llhb-v1-C1-999")])

    def test_an_incomplete_record_is_refused(self, scorer: CaseScorer) -> None:
        broken = {**record("llhb-v1-C1-101"), "completed": False, "final_answer": None}

        with pytest.raises(
            ReportingError,
            match=r"^llhb-v1-C1-101 is incomplete; an unscored error is not a scoreable answer$",
        ):
            score_arm(scorer, {"llhb-v1-C1-101": case("llhb-v1-C1-101")}, [broken])

    def test_a_completed_record_without_an_answer_is_refused(self, scorer: CaseScorer) -> None:
        broken = {**record("llhb-v1-C1-101"), "final_answer": None}

        with pytest.raises(
            ReportingError,
            match=r"^llhb-v1-C1-101 is completed but carries no final_answer string$",
        ):
            score_arm(scorer, {"llhb-v1-C1-101": case("llhb-v1-C1-101")}, [broken])
