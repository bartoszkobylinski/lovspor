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

from lovspor.llhb.outcomes import CaseOutcome
from lovspor.llhb.reporting import ReportingError, score_arm
from lovspor.llhb.scoring import CaseScore, CaseScorer, CriterionVerdict
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

        arm = score_arm(scorer, cases, records)

        assert [b[2].case_id for b in arm.bundles] == ["llhb-v1-C1-101", "llhb-v1-C1-102"]
        assert arm.bundles[0][2].passed is True
        assert arm.bundles[1][2].passed is False
        assert [c.outcome for c in arm.cases] == [CaseOutcome.PASS, CaseOutcome.FAIL]
        # The scored case carries the record's own case_id and the case's own
        # category — not a stand-in for either (e.g. a stringified None).
        assert [c.case_id for c in arm.cases] == ["llhb-v1-C1-101", "llhb-v1-C1-102"]
        assert [c.category for c in arm.cases] == ["C1", "C1"]
        assert arm.cases[0].score == arm.bundles[0][2]
        assert arm.cases[1].score == arm.bundles[1][2]

    def test_a_record_for_an_unknown_case_is_refused(self, scorer: CaseScorer) -> None:
        with pytest.raises(
            ReportingError, match=r"^record for llhb-v1-C1-999 has no case in the dataset$"
        ):
            score_arm(scorer, {}, [record("llhb-v1-C1-999")])

    def test_an_incomplete_record_is_a_model_error_not_a_refusal(self, scorer: CaseScorer) -> None:
        """Ruling #30(e) gates model errors by count and drops the affected
        pairs pairwise. Both are impossible if the arm aborts on the first
        one, so an incomplete record is classified and carried, not raised."""
        broken = {**record("llhb-v1-C1-101"), "completed": False, "final_answer": None}

        arm = score_arm(scorer, {"llhb-v1-C1-101": case("llhb-v1-C1-101")}, [broken])

        assert arm.bundles == []
        assert [c.outcome for c in arm.cases] == [CaseOutcome.MODEL_ERROR]
        assert arm.cases[0].score is None
        assert arm.cases[0].detail == "incomplete run record"

    def test_an_incomplete_record_carries_the_errors_it_recorded(self, scorer: CaseScorer) -> None:
        broken = {
            **record("llhb-v1-C1-101"),
            "completed": False,
            "final_answer": None,
            "errors": ["upstream 529"],
        }

        arm = score_arm(scorer, {"llhb-v1-C1-101": case("llhb-v1-C1-101")}, [broken])

        assert arm.cases[0].detail == "incomplete run record: ['upstream 529']"

    def test_a_completed_record_without_an_answer_is_a_model_error(
        self, scorer: CaseScorer
    ) -> None:
        broken = {**record("llhb-v1-C1-101"), "final_answer": None}

        arm = score_arm(scorer, {"llhb-v1-C1-101": case("llhb-v1-C1-101")}, [broken])

        assert [c.outcome for c in arm.cases] == [CaseOutcome.MODEL_ERROR]
        assert arm.cases[0].detail == "completed record carries no final_answer string"

    def test_a_scorer_that_breaks_is_its_own_outcome(self, scorer: CaseScorer) -> None:
        """A broken instrument is not a badly-behaved model: ruling #30(e)
        gates the two separately, and the tighter gate is on this one."""

        class Exploding:
            def score(self, case: object, answer: str) -> object:
                raise ValueError("cue table missing")

        arm = score_arm(
            Exploding(),  # type: ignore[arg-type]
            {"llhb-v1-C1-101": case("llhb-v1-C1-101")},
            [record("llhb-v1-C1-101")],
        )

        assert arm.bundles == []
        assert [c.outcome for c in arm.cases] == [CaseOutcome.SCORER_ERROR]
        assert arm.cases[0].detail == "ValueError: cue table missing"

    def test_a_semantically_unresolved_answer_remains_scored(self) -> None:
        """A scorer that runs but cannot decide is distinct from both error
        paths and must remain available to metric unresolved buckets."""

        class Unresolved:
            def score(self, case: object, answer: str) -> CaseScore:
                return CaseScore(
                    case_id="llhb-v1-C1-101",
                    category="C1",
                    criteria={"expected-provision-cited": CriterionVerdict.UNRESOLVED},
                    passed=None,
                    asserted_h1=(),
                    asserted_citations=0,
                    asserted_resolved=0,
                    asserted_valid=0,
                    quotes_detected=0,
                    quotes_checkable=0,
                    quotes_verified=0,
                    unresolved_claims=1,
                    unattached_quotes=0,
                )

        arm = score_arm(
            Unresolved(),  # type: ignore[arg-type]
            {"llhb-v1-C1-101": case("llhb-v1-C1-101")},
            [record("llhb-v1-C1-101")],
        )

        assert len(arm.bundles) == 1
        assert [c.outcome for c in arm.cases] == [CaseOutcome.UNRESOLVED_SEMANTIC]
        assert arm.cases[0].score is arm.bundles[0][2]
        assert arm.cases[0].detail is None

    def test_a_case_the_dataset_does_not_hold_is_still_a_refusal(self, scorer: CaseScorer) -> None:
        """Classification is for cases that failed. A record with no case is a
        composition defect: scoring past it compares two experiments."""
        with pytest.raises(ReportingError):
            score_arm(scorer, {}, [record("llhb-v1-C1-404")])
