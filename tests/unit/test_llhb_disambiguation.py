"""Stage 8 C5 detection: did the answer surface a duplicate-id ambiguity?

SCORING.md §5.5: PASS = the ambiguity is surfaced in ANY form — stating
that more than one section carries the id, asking the user to
disambiguate, or presenting the variants. Detection is frozen cue rules
plus occurrence-mention counting; whether a non-surfaced answer is a
FAIL or lands in the unresolved bucket is the scorer's decision, not
this module's.
"""

import pytest

from lovspor.llhb.disambiguation import (
    AMBIGUITY_CUES,
    DISAMBIGUATION_VERSION,
    ambiguity_surfaced,
)


class TestCues:
    @pytest.mark.parametrize(
        "answer",
        [
            "Det finnes flere paragrafer med nummer 6-2 i denne loven.",
            "Loven har to forskjellige § 6-2, én i hvert kapittel.",
            "Referansen er flertydig; § 6-2 opptrer mer enn én gang.",
            "Hvilken av de to bestemmelsene mener du? Kan du presisere?",
            "Merk at § 6-2 har forekomst 1 og forekomst 2.",
        ],
        ids=["flere-paragrafer", "to-forskjellige", "flertydig", "asks-which", "forekomst"],
    )
    def test_a_surfacing_answer_is_detected(self, answer: str) -> None:
        assert ambiguity_surfaced(answer) is True

    def test_detection_is_case_insensitive(self) -> None:
        assert ambiguity_surfaced("FLERE PARAGRAFER har dette nummeret.") is True

    def test_a_silent_single_answer_is_not_surfaced(self) -> None:
        answer = "Testloven § 6-2 sier at avtalen kan sies opp med tre måneders varsel."

        assert ambiguity_surfaced(answer) is False

    def test_hvilken_alone_is_not_a_cue(self) -> None:
        """«Hvilken paragraf gjelder?» is a question about the law, not a
        request to disambiguate a duplicate id."""
        assert ambiguity_surfaced("Hvilken paragraf gjelder for oppsigelse?") is False


class TestVariantPresentation:
    def test_two_distinct_chapters_count_as_presenting_variants(self) -> None:
        """«I kapittel 2 står § 6-2 om X; i kapittel 6 står § 6-2 om Y» —
        no cue word, but the answer laid both occurrences out."""
        answer = "I kapittel 2 står § 6-2 om oppsigelse, og i kapittel 6 står § 6-2 om tilsyn."

        assert ambiguity_surfaced(answer) is True

    def test_the_same_chapter_twice_is_not_a_variant(self) -> None:
        answer = "Kapittel 2 gjelder her. Kapittel 2 sier også mer om dette."

        assert ambiguity_surfaced(answer) is False

    def test_two_unrelated_chapters_are_not_section_variants(self) -> None:
        """Chapters mentioned as narrative structure, with no « § » in
        their sentences, are not two occurrences of one section id
        (codex-tests round 3): counting them surfaced ambiguity on
        answers that never discussed a duplicate at all."""
        answer = "Kapittel 1 gir bakgrunnen, mens kapittel 2 beskriver tilsynet."

        assert ambiguity_surfaced(answer) is False

    def test_chapters_with_different_section_ids_are_not_variants(self) -> None:
        """Two chapters each discussing a DIFFERENT section is a normal
        walk through the law, not two occurrences of one id
        (codex-tests round 5): variants share the id they vary on."""
        answer = "I kapittel 2 står § 1 om virkeområdet. I kapittel 6 står § 9 om tilsyn."

        assert ambiguity_surfaced(answer) is False

    def test_the_same_id_across_two_sentences_still_counts(self) -> None:
        answer = "I kapittel 2 står § 6-2 om oppsigelse. I kapittel 6 står § 6-2 om tilsyn."

        assert ambiguity_surfaced(answer) is True

    def test_two_distinct_occurrence_numbers_count(self) -> None:
        answer = "Tool-svaret nevner occurrence 1 og occurrence 2 for denne id-en."

        assert ambiguity_surfaced(answer) is True

    def test_one_occurrence_number_repeated_does_not_count(self) -> None:
        # "occurrence"/"forekomst" as a bare word is itself a cue, so the
        # counting channel is exercised via chapter labels; this guards the
        # distinctness rule of the numeric channel through the public seam.
        answer = "Se kapittel 4. Se kapittel 4 igjen."

        assert ambiguity_surfaced(answer) is False


class TestContract:
    def test_the_version_is_pinned(self) -> None:
        assert DISAMBIGUATION_VERSION == "llhb-disambig-v1"

    def test_the_cue_list_is_frozen_and_nonempty(self) -> None:
        assert isinstance(AMBIGUITY_CUES, tuple)
        assert "flere paragrafer" in AMBIGUITY_CUES

    def test_an_empty_answer_is_not_surfaced(self) -> None:
        assert ambiguity_surfaced("") is False


class TestEveryCueIsLive:
    @pytest.mark.parametrize("cue", AMBIGUITY_CUES)
    def test_each_frozen_cue_detects_on_its_own(self, cue: str) -> None:
        """The list is frozen evaluator surface: a cue nobody exercises is
        a cue a mutation can corrupt without a test noticing."""
        assert ambiguity_surfaced(f"Svaret nevner {cue} i denne sammenhengen.") is True
