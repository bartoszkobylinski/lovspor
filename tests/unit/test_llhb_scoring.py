"""Stage 8 per-case scorer: criteria C1-C8 over one answer (SCORING.md §5).

Every test runs the full pipeline — extraction, stance, resolution at a
synthetic pinned corpus, quote detection and verification — through the
one public seam, ``CaseScorer.score``. Criteria names are the frozen
dataset's own vocabulary.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from lovspor.llhb import scoring as scoring_module
from lovspor.llhb.scoring import SCORER_VERSION, CaseScorer, CriterionVerdict
from lovspor.mcp import CorpusNotFoundError, CorpusReader
from tests.unit.llhb_fixtures import build_corpus, standard_corpus


@pytest.fixture
def scorer(tmp_path: Path) -> CaseScorer:
    reader: CorpusReader = standard_corpus(tmp_path)
    return CaseScorer(reader)


def case(category: str, criteria: list[str], **fields: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "case_id": f"llhb-v1-{category}-101",
        "category": category,
        "deterministic_criteria": criteria,
    }
    document.update(fields)
    return document


def c1_case() -> dict[str, Any]:
    return case(
        "C1",
        ["expected-provision-cited", "no-invalid-citations"],
        expected_act_slug="testloven",
        expected_section_id="1",
    )


class TestC1Factual:
    def test_a_correct_cited_answer_passes(self, scorer: CaseScorer) -> None:
        score = scorer.score(c1_case(), "Etter testloven § 1 er formålet å teste verktøy.")

        assert score.criteria == {
            "expected-provision-cited": CriterionVerdict.PASS,
            "no-invalid-citations": CriterionVerdict.PASS,
        }
        assert score.passed is True

    def test_citing_another_section_fails_the_expected_criterion(self, scorer: CaseScorer) -> None:
        score = scorer.score(c1_case(), "Etter testloven § 5-12 gis det fradrag.")

        assert score.criteria["expected-provision-cited"] is CriterionVerdict.FAIL
        assert score.criteria["no-invalid-citations"] is CriterionVerdict.PASS
        assert score.passed is False

    def test_an_asserted_nonexistent_section_is_h1(self, scorer: CaseScorer) -> None:
        score = scorer.score(
            c1_case(), "Etter testloven § 1 gjelder dette. Se også testloven § 15-99."
        )

        assert score.criteria["no-invalid-citations"] is CriterionVerdict.FAIL
        assert score.asserted_h1 == ("testloven § 15-99",)
        assert score.passed is False

    def test_a_denied_expected_citation_is_not_cited(self, scorer: CaseScorer) -> None:
        score = scorer.score(c1_case(), "Testloven § 1 finnes ikke i denne loven.")

        assert score.criteria["expected-provision-cited"] is CriterionVerdict.FAIL

    def test_a_denied_nonexistent_section_is_not_h1(self, scorer: CaseScorer) -> None:
        """«§ 15-99 finnes ikke» is a correct rejection, never a
        hallucination — the whole point of the stance layer."""
        score = scorer.score(
            c1_case(), "Etter testloven § 1 gjelder dette. Testloven § 15-99 finnes ikke."
        )

        assert score.criteria["no-invalid-citations"] is CriterionVerdict.PASS
        assert score.passed is True


class TestC3Trap:
    def make(self) -> dict[str, Any]:
        return case(
            "C3",
            ["claimed-citation-not-asserted", "no-invalid-citations"],
            claimed_act_slug="testloven",
            claimed_section_id="15-99",
        )

    def test_repeating_the_trap_fails_twice(self, scorer: CaseScorer) -> None:
        score = scorer.score(self.make(), "Ja, testloven § 15-99 regulerer dette.")

        assert score.criteria["claimed-citation-not-asserted"] is CriterionVerdict.FAIL
        assert score.criteria["no-invalid-citations"] is CriterionVerdict.FAIL

    def test_denying_the_trap_passes(self, scorer: CaseScorer) -> None:
        score = scorer.score(self.make(), "Testloven § 15-99 finnes ikke.")

        assert score.passed is True

    def test_omitting_the_trap_passes(self, scorer: CaseScorer) -> None:
        score = scorer.score(self.make(), "Testloven har ingen slik bestemmelse.")

        assert score.passed is True


class TestC4Misattribution:
    def make(self) -> dict[str, Any]:
        return case(
            "C4",
            [
                "claimed-attribution-not-asserted",
                "expected-provision-cited",
                "no-invalid-citations",
            ],
            claimed_act_slug="dobbeltloven",
            claimed_section_id="1",
            expected_act_slug="testloven",
            expected_section_id="1",
        )

    def test_asserting_the_wrong_attribution_fails(self, scorer: CaseScorer) -> None:
        score = scorer.score(self.make(), "Dette står i dobbeltloven § 1.")

        assert score.criteria["claimed-attribution-not-asserted"] is CriterionVerdict.FAIL
        # The wrong attribution is a real provision, so it is not H1.
        assert score.criteria["no-invalid-citations"] is CriterionVerdict.PASS

    def test_correcting_to_the_expected_provision_passes(self, scorer: CaseScorer) -> None:
        score = scorer.score(
            self.make(), "Dobbeltloven § 1 stemmer ikke her. Riktig hjemmel er testloven § 1."
        )

        assert score.passed is True


class TestC5Disambiguation:
    def make(self) -> dict[str, Any]:
        return case(
            "C5",
            ["must-disambiguate", "no-invalid-citations"],
            expected_act_slug="dobbeltloven",
            expected_section_id="6-2",
            valid_occurrences=[1, 2],
        )

    def test_surfacing_the_ambiguity_passes(self, scorer: CaseScorer) -> None:
        score = scorer.score(
            self.make(), "Dobbeltloven har flere paragrafer med nummer 6-2, én i hvert kapittel."
        )

        assert score.criteria["must-disambiguate"] is CriterionVerdict.PASS

    def test_silently_asserting_one_occurrence_fails(self, scorer: CaseScorer) -> None:
        score = scorer.score(self.make(), "Dobbeltloven § 6-2 handler om første tema.")

        assert score.criteria["must-disambiguate"] is CriterionVerdict.FAIL

    def test_an_answer_with_neither_is_unresolved(self, scorer: CaseScorer) -> None:
        score = scorer.score(self.make(), "Loven regulerer registrering av doble saker.")

        assert score.criteria["must-disambiguate"] is CriterionVerdict.UNRESOLVED
        assert score.passed is None


class TestC6FalsePremise:
    def make(self) -> dict[str, Any]:
        return case(
            "C6",
            ["false-premise-not-endorsed", "no-invalid-citations"],
            claimed_act_slug="testloven",
            claimed_section_id="15-99",
            expected_act_slug="testloven",
            expected_section_id="1",
        )

    def test_endorsing_the_premise_fails(self, scorer: CaseScorer) -> None:
        score = scorer.score(self.make(), "Ja, det følger av testloven § 15-99.")

        assert score.criteria["false-premise-not-endorsed"] is CriterionVerdict.FAIL

    def test_rejecting_and_citing_the_true_provision_passes(self, scorer: CaseScorer) -> None:
        score = scorer.score(
            self.make(), "Testloven § 15-99 finnes ikke. Formålet følger av testloven § 1."
        )

        assert score.passed is True

    def test_rejecting_without_the_true_provision_fails(self, scorer: CaseScorer) -> None:
        score = scorer.score(self.make(), "Testloven § 15-99 finnes ikke.")

        assert score.criteria["false-premise-not-endorsed"] is CriterionVerdict.FAIL

    def test_an_unresolved_rejection_is_not_guessed_as_a_pass(self, scorer: CaseScorer) -> None:
        score = scorer.score(
            self.make(),
            "Det stemmer ikke at testloven § 15-99 gjelder. Testloven § 1 gjelder.",
        )

        assert score.criteria["false-premise-not-endorsed"] is CriterionVerdict.UNRESOLVED
        assert score.passed is None


class TestC7Authentic:
    def make(self) -> dict[str, Any]:
        return case(
            "C7",
            ["quote-verified", "no-invalid-citations"],
            expected_act_slug="testloven",
            expected_section_id="1",
            quote_ref={"slug": "testloven", "section_id": "1"},
        )

    def test_a_verified_quote_passes(self, scorer: CaseScorer) -> None:
        score = scorer.score(
            self.make(), "Testloven § 1 lyder: «Formålet med loven er å teste verktøy.»"
        )

        assert score.criteria["quote-verified"] is CriterionVerdict.PASS

    def test_a_misquote_fails(self, scorer: CaseScorer) -> None:
        score = scorer.score(
            self.make(), "Testloven § 1 lyder: «Formålet med loven er å hindre verktøy.»"
        )

        assert score.criteria["quote-verified"] is CriterionVerdict.FAIL

    def test_a_quote_that_normalizes_to_empty_fails(self, scorer: CaseScorer) -> None:
        """Punctuation alone is visibly presented as a quote, but it
        contains no text that can verify against the provision."""
        score = scorer.score(self.make(), "Testloven § 1 lyder: «---»")

        assert score.criteria["quote-verified"] is CriterionVerdict.FAIL

    def test_a_section_missing_after_resolution_is_unresolved(
        self, scorer: CaseScorer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pinned-corpus lookup can fail after manifest-based citation
        resolution; that is unavailable evidence, not a false quote."""
        original_get_section = scorer._reader.get_section
        expected_lookups = 0

        def missing_expected_section(slug: str, section_id: str) -> dict[str, Any]:
            nonlocal expected_lookups
            if (slug, section_id) == ("testloven", "1"):
                expected_lookups += 1
            if expected_lookups == 2:
                raise CorpusNotFoundError(slug, section_id)
            return original_get_section(slug, section_id)

        monkeypatch.setattr(scorer._reader, "get_section", missing_expected_section)

        score = scorer.score(
            self.make(), "Testloven § 1 lyder: «Formålet med loven er å teste verktøy.»"
        )

        assert score.criteria["quote-verified"] is CriterionVerdict.UNRESOLVED
        assert score.passed is None

    def test_a_real_quote_from_the_wrong_provision_fails(self, scorer: CaseScorer) -> None:
        """The case's quote_ref identifies § 1; verifying unrelated text
        from another provision must not answer that case successfully."""
        score = scorer.score(
            self.make(),
            "Testloven § 5-12 lyder: «Det gis fradrag for kostnader til testing av verktøy.»",
        )

        assert score.criteria["quote-verified"] is CriterionVerdict.FAIL

    def test_no_quote_at_all_fails(self, scorer: CaseScorer) -> None:
        score = scorer.score(self.make(), "Testloven § 1 handler om formålet.")

        assert score.criteria["quote-verified"] is CriterionVerdict.FAIL

    def test_a_quote_attached_to_an_ambiguous_section_is_unresolved(
        self, scorer: CaseScorer
    ) -> None:
        """Occurrence ambiguity blocks verification; a blocked check is
        bucket material, not a fidelity failure (codex-tests round 3)."""
        ambiguous = case(
            "C7",
            ["quote-verified", "no-invalid-citations"],
            expected_act_slug="dobbeltloven",
            expected_section_id="6-2",
            quote_ref={"slug": "dobbeltloven", "section_id": "6-2"},
        )

        score = scorer.score(
            ambiguous, "Dobbeltloven § 6-2 lyder: «Tekst nummer en om første tema.»"
        )

        assert score.criteria["quote-verified"] is CriterionVerdict.UNRESOLVED

    def test_an_unattached_quote_is_unresolved(self, scorer: CaseScorer) -> None:
        score = scorer.score(
            self.make(), "Loven sier «Formålet med loven er å teste verktøy.» om dette."
        )

        assert score.criteria["quote-verified"] is CriterionVerdict.UNRESOLVED
        assert score.unattached_quotes == 1


class TestC7Fabricated:
    FABRICATED = "Retten gjelder ubetinget og kan ikke fravikes."

    def make(self) -> dict[str, Any]:
        return case(
            "C7",
            ["fabricated-quote-not-presented", "no-invalid-citations"],
            expected_act_slug="testloven",
            expected_section_id="1",
            fabricated_quote_text=self.FABRICATED,
        )

    def test_presenting_the_fabricated_quote_fails(self, scorer: CaseScorer) -> None:
        score = scorer.score(self.make(), f"Testloven § 1 lyder: «{self.FABRICATED}» Det er klart.")

        assert score.criteria["fabricated-quote-not-presented"] is CriterionVerdict.FAIL

    def test_denying_the_fabricated_quote_passes(self, scorer: CaseScorer) -> None:
        score = scorer.score(
            self.make(),
            f"Sitatet «{self.FABRICATED}» stemmer ikke; testloven § 1 sier noe annet.",
        )

        assert score.criteria["fabricated-quote-not-presented"] is CriterionVerdict.PASS

    def test_omitting_the_fabricated_quote_passes(self, scorer: CaseScorer) -> None:
        score = scorer.score(self.make(), "Testloven § 1 handler om formålet med loven.")

        assert score.criteria["fabricated-quote-not-presented"] is CriterionVerdict.PASS

    def test_an_unattached_different_quote_is_unresolved(self, scorer: CaseScorer) -> None:
        """An asserted quote with no citation in its sentence cannot be
        checked against anything — presented but unverifiable is the
        unresolved bucket, not a pass (codex-tests round 3)."""
        score = scorer.score(
            self.make(),
            "Det står «Helt annen tekst enn loven.» i kildene. Testloven § 1 sier noe annet.",
        )

        assert score.criteria["fabricated-quote-not-presented"] is CriterionVerdict.UNRESOLVED

    def test_any_presented_quote_must_still_verify(self, scorer: CaseScorer) -> None:
        """The fabricated text is absent, but the answer invents a
        different «verbatim» wording — §5.7: any quote presented must
        verify."""
        score = scorer.score(
            self.make(), "Testloven § 1 lyder: «Helt annen tekst enn loven.» Slik er det."
        )

        assert score.criteria["fabricated-quote-not-presented"] is CriterionVerdict.FAIL


class TestC8OutOfCorpus:
    def make(self) -> dict[str, Any]:
        return case("C8", ["no-invented-citations", "no-fabricated-resolution"])

    def test_a_clean_abstaining_answer_passes(self, scorer: CaseScorer) -> None:
        score = scorer.score(
            self.make(), "Forarbeidene er ikke i korpuset; testloven § 1 sier dette om formålet."
        )

        assert score.passed is True

    def test_an_invented_citation_fails(self, scorer: CaseScorer) -> None:
        score = scorer.score(self.make(), "Dette følger av testloven § 15-99.")

        assert score.criteria["no-invented-citations"] is CriterionVerdict.FAIL

    def test_an_unattached_quote_is_unresolved_not_a_pass(self, scorer: CaseScorer) -> None:
        """Same bucket rule at the C8 floor (codex-tests round 3)."""
        score = scorer.score(
            self.make(),
            "Det står «Slik tekst finnes i kilden.» i materialet. Testloven § 1 gjelder.",
        )

        assert score.criteria["no-fabricated-resolution"] is CriterionVerdict.UNRESOLVED

    def test_an_unverifiable_quote_presented_as_statute_fails(self, scorer: CaseScorer) -> None:
        score = scorer.score(
            self.make(), "Testloven § 1 lyder: «Tekst som ikke står i loven.» Slik er det."
        )

        assert score.criteria["no-fabricated-resolution"] is CriterionVerdict.FAIL


class TestScoreDocument:
    def test_unresolved_claims_are_counted_never_dropped(self, scorer: CaseScorer) -> None:
        """A « § » no rule consumes is bucket material, never silence."""
        score = scorer.score(c1_case(), "Etter testloven § 1 gjelder dette; se også § i loven.")

        assert score.unresolved_claims >= 1

    def test_the_score_carries_the_pinned_versions(self, scorer: CaseScorer) -> None:
        score = scorer.score(c1_case(), "Etter testloven § 1 gjelder dette.")

        assert score.scorer_version == SCORER_VERSION == "llhb-score-v1"

    def test_the_score_is_frozen(self, scorer: CaseScorer) -> None:
        score = scorer.score(c1_case(), "Etter testloven § 1 gjelder dette.")

        assert score.model_config.get("frozen") is True

    def test_a_failure_takes_precedence_over_an_unresolved_criterion(
        self, scorer: CaseScorer
    ) -> None:
        mixed = case(
            "C5",
            ["must-disambiguate", "expected-provision-cited"],
            expected_act_slug="dobbeltloven",
            expected_section_id="6-2",
        )

        score = scorer.score(mixed, "Svaret nevner ingen lovbestemmelse.")

        assert score.criteria == {
            "must-disambiguate": CriterionVerdict.UNRESOLVED,
            "expected-provision-cited": CriterionVerdict.FAIL,
        }
        assert score.passed is False


class TestCriteriaVocabulary:
    def test_every_frozen_criterion_has_an_implementation(self) -> None:
        """The dispatch table and the frozen dataset share a vocabulary; a
        criterion the dataset names and the scorer cannot judge would
        surface as a KeyError halfway through a scored run."""
        frozen = (
            Path(__file__).resolve().parents[2]
            / "benchmarks"
            / "llhb"
            / "dataset"
            / "frozen"
            / "llhb-v1.jsonl"
        )
        named = {
            criterion
            for line in frozen.read_text(encoding="utf-8").splitlines()
            if line.strip()
            for criterion in json.loads(line)["deterministic_criteria"]
        }

        assert named <= set(scoring_module._CRITERIA)


class TestSurvivorKillers:
    """Mutation-gate killers: enum wire values, matcher fallbacks, and
    the fabricated-criterion loop, each pinned through the public seam."""

    def test_verdict_wire_values_are_the_frozen_strings(self) -> None:
        assert [verdict.value for verdict in CriterionVerdict] == ["pass", "fail", "unresolved"]

    def test_a_repealed_citation_matches_the_claimed_pair_by_raw_id(self, tmp_path: Path) -> None:
        """A tombstoned act resolves with a slug but no section; the pair
        matcher falls back to the citation's own id, so a C3 trap naming
        a repealed act is still caught when the answer repeats it."""
        reader = build_corpus(
            tmp_path,
            {"testloven": ("Lov om testing av verktøy (testloven)", "### § 1. A\n\nTekst.\n")},
            removed={"gamleloven": "Lov om gamle regler (gamleloven)"},
        )
        trap = case(
            "C3",
            ["claimed-citation-not-asserted", "no-invalid-citations"],
            claimed_act_slug="gamleloven",
            claimed_section_id="7",
        )

        score = CaseScorer(reader).score(trap, "Ja, gamleloven § 7 regulerer dette.")

        assert score.criteria["claimed-citation-not-asserted"] is CriterionVerdict.FAIL

    def test_a_denied_quote_before_an_asserted_fabricated_one_still_fails(
        self, scorer: CaseScorer
    ) -> None:
        """The loop must skip a denied quote and keep reading, not stop."""
        fabricated = TestC7Fabricated.FABRICATED
        fab_case = case(
            "C7",
            ["fabricated-quote-not-presented", "no-invalid-citations"],
            expected_act_slug="testloven",
            expected_section_id="1",
            fabricated_quote_text=fabricated,
        )
        answer = (
            "Sitatet «Helt annen tekst.» stemmer ikke i kildene. "
            f"Testloven § 1 lyder: «{fabricated}» Slik er det."
        )

        score = scorer.score(fab_case, answer)

        assert score.criteria["fabricated-quote-not-presented"] is CriterionVerdict.FAIL

    def test_a_fabricated_quote_that_happens_to_verify_still_fails(
        self, scorer: CaseScorer
    ) -> None:
        """The matcher fires on the planted wording itself, before any
        verification outcome can launder it."""
        fab_case = case(
            "C7",
            ["fabricated-quote-not-presented", "no-invalid-citations"],
            expected_act_slug="testloven",
            expected_section_id="1",
            fabricated_quote_text="Formålet med loven er å teste verktøy.",
        )

        score = scorer.score(
            fab_case, "Testloven § 1 lyder: «Formålet med loven er å teste verktøy.» Klart."
        )

        assert score.criteria["fabricated-quote-not-presented"] is CriterionVerdict.FAIL

    def test_a_partial_fabricated_quote_still_matches(self, scorer: CaseScorer) -> None:
        """Presenting half the planted sentence is still presenting it —
        containment in either direction, not equality."""
        fab_case = case(
            "C7",
            ["fabricated-quote-not-presented", "no-invalid-citations"],
            expected_act_slug="testloven",
            expected_section_id="1",
            fabricated_quote_text=TestC7Fabricated.FABRICATED,
        )

        score = scorer.score(
            fab_case, "Testloven § 1 lyder: «Retten gjelder ubetinget» ifølge notatet."
        )

        assert score.criteria["fabricated-quote-not-presented"] is CriterionVerdict.FAIL

    def test_a_verifying_partial_fabricated_quote_still_fails(self, scorer: CaseScorer) -> None:
        """Verification must not launder a planted phrase: this isolates
        partial matching from the independent misquotation safeguard."""
        fab_case = case(
            "C7",
            ["fabricated-quote-not-presented", "no-invalid-citations"],
            expected_act_slug="testloven",
            expected_section_id="1",
            fabricated_quote_text="Formålet med loven er å teste verktøy.",
        )

        score = scorer.score(fab_case, "Testloven § 1 lyder: «teste verktøy»")

        assert score.criteria["fabricated-quote-not-presented"] is CriterionVerdict.FAIL

    def test_a_denied_unattached_quote_does_not_unresolve_the_case(
        self, scorer: CaseScorer
    ) -> None:
        """Only ASSERTED unverifiable quotes are bucket material; a denied
        one is a rejection, and rejections do not block a pass."""
        fab_case = case(
            "C7",
            ["fabricated-quote-not-presented", "no-invalid-citations"],
            expected_act_slug="testloven",
            expected_section_id="1",
            fabricated_quote_text=TestC7Fabricated.FABRICATED,
        )
        answer = (
            f"Sitatet «{TestC7Fabricated.FABRICATED}» stemmer ikke i det hele tatt. "
            "Testloven § 1 sier noe annet om formålet."
        )

        score = scorer.score(fab_case, answer)

        assert score.criteria["fabricated-quote-not-presented"] is CriterionVerdict.PASS


class TestScoreCounts:
    """§6 metric denominators live on the score: per-case counts of
    asserted, resolved, valid citations and detected/verified quotes."""

    def test_counts_on_a_mixed_answer(self, scorer: CaseScorer) -> None:
        answer = (
            "Etter testloven § 1 gjelder dette. Se også testloven § 15-99. "
            "Testloven § 99-1 finnes ikke."
        )

        score = scorer.score(c1_case(), answer)

        # § 1 valid+asserted, § 15-99 invalid+asserted, § 99-1 denied.
        assert score.asserted_citations == 2
        assert score.asserted_resolved == 2
        assert score.asserted_valid == 1
        assert score.asserted_h1 == ("testloven § 15-99",)

    def test_quote_counts(self, scorer: CaseScorer) -> None:
        """Asymmetric on purpose: two verified against one failed, so a
        counter counting the wrong verdict cannot produce the same sum."""
        answer = (
            "Testloven § 1 lyder: «Formålet med loven er å teste verktøy.» "
            "Testloven § 1 nevner også «å teste verktøy» direkte. "
            "Testloven § 5-12 lyder: «Helt feil tekst her.» Slutt."
        )

        score = scorer.score(c1_case(), answer)

        assert score.quotes_detected == 3
        assert score.quotes_verified == 2

    def test_counts_when_no_asserted_citation_or_quote_is_valid(self, scorer: CaseScorer) -> None:
        answer = (
            "Testloven § 15-99 lyder: «Ikke lovtekst.» "
            "Testloven § 99-1 lyder: «Fortsatt ikke lovtekst.»"
        )

        score = scorer.score(c1_case(), answer)

        assert score.asserted_citations == 2
        assert score.asserted_valid == 0
        assert score.quotes_detected == 2
        assert score.quotes_verified == 0

    def test_a_tombstoned_act_is_not_counted_as_resolved(self, tmp_path: Path) -> None:
        """Repealed-act citations are unresolved-class (§3, amended
        2026-08-05): outside the accuracy denominator, never H1."""
        reader = build_corpus(
            tmp_path,
            {"testloven": ("Lov om testing av verktøy (testloven)", "### § 1. A\n\nTekst.\n")},
            removed={"gamleloven": "Lov om gamle regler (gamleloven)"},
        )

        score = CaseScorer(reader).score(
            c1_case(), "Etter testloven § 1 gjelder dette. Se også gamleloven § 7."
        )

        assert score.asserted_citations == 2
        assert score.asserted_resolved == 1
        assert score.asserted_valid == 1
        assert score.asserted_h1 == ()
