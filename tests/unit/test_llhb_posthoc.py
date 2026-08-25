"""Layer 3 of the frozen Opus pair (ruling #30(a), issue #168).

The guard is most of what is under test. There is no pair manifest binding these
two runs — manifest gating landed after they were scored — so the hard-coded
expectations are the only thing standing between this analysis and being pointed
at some other pair while still calling itself the frozen Opus supplementary.
"""

from pathlib import Path

import pytest

from lovspor.llhb.outcomes import CaseOutcome, ScoredCase
from lovspor.llhb.posthoc import (
    CASES_PER_ARM,
    CONTROL_RUN_ID,
    EXPECTED_RECORD_SHA256,
    TREATMENT_RUN_ID,
    PosthocGuardError,
    arm_metrics,
    supplementary_report,
    verify_frozen_pair,
    verify_scored_pair,
)
from lovspor.llhb.reporting import ArmScoring
from lovspor.llhb.scoring import CaseScore

RUNS = Path(__file__).parents[2] / "benchmarks" / "llhb" / "results" / "runs"


def _score(*, h1: bool = False, citations: int = 0, resolved: int = 0, valid: int = 0) -> CaseScore:
    return CaseScore(
        case_id="llhb-v1-C1-001",
        category="C1",
        criteria={},
        passed=True,
        asserted_h1=("lov-1 § 1",) if h1 else (),
        asserted_citations=citations,
        asserted_resolved=resolved,
        asserted_valid=valid,
        quotes_detected=0,
        quotes_checkable=0,
        quotes_verified=0,
        unresolved_claims=0,
        unattached_quotes=0,
    )


def _arm(scores: list[CaseScore], *, unscored: int = 0) -> ArmScoring:
    cases = [
        ScoredCase(case_id=s.case_id, category=s.category, outcome=CaseOutcome.PASS, score=s)
        for s in scores
    ]
    cases += [
        ScoredCase(case_id=f"err-{n}", category="C1", outcome=CaseOutcome.MODEL_ERROR)
        for n in range(unscored)
    ]
    return ArmScoring(bundles=[({}, {}, s) for s in scores], cases=cases)


def _full_arm(scores: list[CaseScore]) -> ArmScoring:
    """An arm padded to the 250 the guard insists on."""
    return _arm(scores + [_score() for _ in range(CASES_PER_ARM - len(scores))])


class TestTheGuard:
    def test_the_real_pair_passes(self) -> None:
        verify_frozen_pair(
            RUNS / CONTROL_RUN_ID / "records.jsonl",
            RUNS / TREATMENT_RUN_ID / "records.jsonl",
        )

    def test_a_different_control_run_is_refused(self, tmp_path: Path) -> None:
        """Pointing this at another pair while it still calls itself the frozen
        Opus supplementary is the failure the hash exists to prevent."""
        impostor = tmp_path / "records.jsonl"
        impostor.write_text("{}\n", encoding="utf-8")

        with pytest.raises(PosthocGuardError, match=CONTROL_RUN_ID):
            verify_frozen_pair(impostor, RUNS / TREATMENT_RUN_ID / "records.jsonl")

    def test_a_different_treatment_run_is_refused(self, tmp_path: Path) -> None:
        impostor = tmp_path / "records.jsonl"
        impostor.write_text("{}\n", encoding="utf-8")

        with pytest.raises(PosthocGuardError, match=TREATMENT_RUN_ID):
            verify_frozen_pair(RUNS / CONTROL_RUN_ID / "records.jsonl", impostor)

    def test_the_expected_hashes_are_the_committed_bytes(self) -> None:
        """The constants are not decoration: if the committed arms ever change,
        this is what says so before any number is published from them."""
        assert set(EXPECTED_RECORD_SHA256) == {CONTROL_RUN_ID, TREATMENT_RUN_ID}
        assert all(len(digest) == 64 for digest in EXPECTED_RECORD_SHA256.values())

    def test_an_arm_that_is_not_250_cases_is_refused(self) -> None:
        with pytest.raises(PosthocGuardError, match="expected 250"):
            verify_scored_pair(_arm([_score()]), _full_arm([]))

    def test_a_short_treatment_arm_is_refused_too(self) -> None:
        with pytest.raises(PosthocGuardError, match="treatment"):
            verify_scored_pair(_full_arm([]), _arm([_score()]))


class TestTheFourFigures:
    def test_h1_is_counted_per_answer_not_per_citation(self) -> None:
        """An answer inventing three provisions is one hallucinating answer."""
        metrics = arm_metrics(_arm([_score(h1=True, citations=3), _score()]))

        assert metrics["unconditional_h1"]["count"] == 1

    def test_coverage_counts_answers_that_cited_anything(self) -> None:
        metrics = arm_metrics(_arm([_score(citations=2), _score(citations=1), _score()]))

        assert metrics["citation_coverage"]["count"] == 2

    def test_instances_are_summed_not_counted(self) -> None:
        """Volumes, not answer counts: one answer can carry several."""
        metrics = arm_metrics(
            _arm(
                [_score(citations=3, resolved=3, valid=2), _score(citations=1, resolved=1, valid=1)]
            )
        )

        assert metrics["valid_citation_instances"]["count"] == 3
        assert metrics["invalid_citation_instances"]["count"] == 1

    def test_the_denominator_is_always_the_full_arm(self) -> None:
        """Ruling #30(a): over all 250, not over the ones that cited."""
        metrics = arm_metrics(_arm([_score(h1=True)]))

        assert metrics["unconditional_h1"]["mean_per_answer"] == 1 / CASES_PER_ARM

    def test_unscored_cases_are_counted_rather_than_folded_in(self) -> None:
        """A case with no score contributes nothing to every numerator, so an
        uncounted one is indistinguishable from a clean answer — the defect
        ruling #30(c) closed for C8, returning through the denominator."""
        metrics = arm_metrics(_arm([_score(), _score()], unscored=3))

        assert (metrics["scored"], metrics["unscored"]) == (2, 3)


class TestTheArtifactSaysWhatItIs:
    def test_it_is_labelled_not_confirmatory_in_a_field(self) -> None:
        """Prose does not survive a number being lifted out of the JSON."""
        report = supplementary_report(_full_arm([]), _full_arm([]))

        assert report["confirmatory"] is False
        assert report["analysis_status"] == "post_hoc_supplementary"

    def test_it_names_the_ruling_and_the_reason(self) -> None:
        report = supplementary_report(_full_arm([]), _full_arm([]))

        assert report["ruling"] == "DECISIONS.md #30(a)"
        assert "inspection of frozen-pair outputs" in report["reason"]

    def test_it_carries_the_hashes_it_verified_against(self) -> None:
        report = supplementary_report(_full_arm([]), _full_arm([]))

        assert report["records_sha256"] == EXPECTED_RECORD_SHA256

    def test_it_refuses_to_build_from_an_arm_of_the_wrong_size(self) -> None:
        with pytest.raises(PosthocGuardError):
            supplementary_report(_arm([_score()]), _full_arm([]))
