"""Stage 8 quote detection: purported-verbatim spans in a model answer.

SCORING.md §4: text inside Norwegian or ASCII quotation marks attached
by its sentence to a citation, or introduced by a frozen
verbatim-marker cue. Detection is pure text work — verification against
the pinned corpus happens in the scorer, not here.
"""

import pytest

from lovspor.llhb.citations import ExtractedCitation
from lovspor.llhb.quote_detection import (
    QUOTE_DETECTION_VERSION,
    VERBATIM_CUES,
    DetectedQuote,
    detect_quotes,
)
from lovspor.llhb.stances import Stance


def citation(text: str, needle: str, section_id: str = "1") -> ExtractedCitation:
    """A minimal extracted citation positioned at ``needle`` in ``text``."""
    start = text.index(needle)
    return ExtractedCitation(
        text=needle,
        start=start,
        end=start + len(needle),
        section_id=section_id,
        section_id_raw=section_id,
        act_key="testloven",
        act_text="testloven",
        act_binding="before",
        stance=Stance.ASSERTED,
    )


class TestQuoteMarks:
    def test_detects_a_guillemet_quote(self) -> None:
        answer = "Testloven § 1 sier «Loven gjelder for alle.» om dette."

        quotes = detect_quotes(answer, [citation(answer, "§ 1")])

        assert len(quotes) == 1
        assert quotes[0].text == "Loven gjelder for alle."
        assert answer[quotes[0].start : quotes[0].end] == "Loven gjelder for alle."

    def test_detects_an_ascii_double_quote(self) -> None:
        answer = 'Testloven § 1 sier "Loven gjelder for alle." om dette.'

        quotes = detect_quotes(answer, [citation(answer, "§ 1")])

        assert [quote.text for quote in quotes] == ["Loven gjelder for alle."]

    def test_detects_an_ascii_single_quote(self) -> None:
        answer = "Testloven § 1 sier 'Loven gjelder.' om dette."

        quotes = detect_quotes(answer, [citation(answer, "§ 1")])

        assert [quote.text for quote in quotes] == ["Loven gjelder."]

    def test_an_apostrophe_inside_a_word_is_not_a_quote(self) -> None:
        answer = "Testloven § 1 nevner Ola's rettigheter og Kari's plikter."

        assert detect_quotes(answer, [citation(answer, "§ 1")]) == []

    def test_an_apostrophe_inside_a_single_quoted_span_does_not_close_it(self) -> None:
        answer = "Testloven § 1 sier 'Ola's rettigheter gjelder.' om dette."

        quotes = detect_quotes(answer, [citation(answer, "§ 1")])

        assert [quote.text for quote in quotes] == ["Ola's rettigheter gjelder."]

    def test_an_unclosed_mark_detects_nothing(self) -> None:
        answer = "Testloven § 1 sier «Loven gjelder for alle."

        assert detect_quotes(answer, [citation(answer, "§ 1")]) == []

    def test_two_quotes_are_both_detected_in_order(self) -> None:
        answer = "Testloven § 1 sier «første» og § 2 sier «andre» her."

        quotes = detect_quotes(
            answer, [citation(answer, "§ 1"), citation(answer, "§ 2", section_id="2")]
        )

        assert [quote.text for quote in quotes] == ["første", "andre"]


class TestAttachment:
    def test_attaches_to_the_citation_in_the_same_sentence(self) -> None:
        answer = "Testloven § 1 lyder «Loven gjelder.» Dette er klart."
        cited = citation(answer, "§ 1")

        quotes = detect_quotes(answer, [cited])

        assert quotes[0].attached == cited

    def test_a_quote_with_no_citation_in_its_sentence_is_unattached(self) -> None:
        answer = "Testloven § 1 er viktig. Det står «Loven gjelder.» i teksten."

        quotes = detect_quotes(answer, [citation(answer, "§ 1")])

        assert len(quotes) == 1
        assert quotes[0].attached is None

    def test_attaches_to_the_nearest_preceding_citation(self) -> None:
        answer = "Både § 1 og § 2 i testloven, som sier «Loven gjelder.» om det."
        first = citation(answer, "§ 1")
        second = citation(answer, "§ 2", section_id="2")

        quotes = detect_quotes(answer, [first, second])

        assert quotes[0].attached == second

    def test_a_following_citation_in_the_sentence_still_attaches(self) -> None:
        answer = "Det står «Loven gjelder.» i testloven § 1 her."
        cited = citation(answer, "§ 1")

        quotes = detect_quotes(answer, [cited])

        assert quotes[0].attached == cited


class TestVerbatimCues:
    def test_a_cue_with_a_colon_captures_to_the_sentence_end(self) -> None:
        """«§ 1 lyder: Loven gjelder for alle.» — no quotation marks, but
        the cue presents what follows as the provision's own words."""
        answer = "Testloven § 1 lyder: Loven gjelder for alle. Neste setning."
        cited = citation(answer, "§ 1")

        quotes = detect_quotes(answer, [cited])

        assert len(quotes) == 1
        assert quotes[0].text == "Loven gjelder for alle."
        assert quotes[0].attached == cited
        assert quotes[0].via_cue == "lyder"

    def test_a_cue_followed_by_a_marked_quote_is_one_quote_not_two(self) -> None:
        answer = "Testloven § 1 lyder: «Loven gjelder for alle.» Neste."

        quotes = detect_quotes(answer, [citation(answer, "§ 1")])

        assert [quote.text for quote in quotes] == ["Loven gjelder for alle."]

    def test_a_cue_without_a_colon_detects_nothing_extra(self) -> None:
        """«slik ordlyden er» mid-sentence presents nothing as verbatim."""
        answer = "Testloven § 1 har en ordlyd som er klar. Ingen sitater her."

        assert detect_quotes(answer, [citation(answer, "§ 1")]) == []

    def test_a_cue_with_no_text_after_the_colon_detects_nothing(self) -> None:
        answer = "Testloven § 1 lyder:   "

        assert detect_quotes(answer, [citation(answer, "§ 1")]) == []

    def test_cue_detection_is_case_insensitive(self) -> None:
        answer = "Testloven § 1 LYDER: Loven gjelder. Neste setning."

        quotes = detect_quotes(answer, [citation(answer, "§ 1")])

        assert [(quote.text, quote.via_cue) for quote in quotes] == [("Loven gjelder.", "lyder")]

    def test_the_cue_list_is_the_frozen_one(self) -> None:
        assert VERBATIM_CUES == ("lyder", "ordlyd", "heter det")


class TestContract:
    def test_the_version_is_pinned(self) -> None:
        assert QUOTE_DETECTION_VERSION == "llhb-quote-detect-v1"

    def test_the_result_is_a_frozen_model(self) -> None:
        answer = "Testloven § 1 sier «Loven gjelder.» her."
        quote = detect_quotes(answer, [citation(answer, "§ 1")])[0]

        assert isinstance(quote, DetectedQuote)
        assert quote.model_config.get("frozen") is True

    def test_an_empty_answer_detects_nothing(self) -> None:
        assert detect_quotes("", []) == []


class TestEveryCueIsLive:
    @pytest.mark.parametrize("cue", VERBATIM_CUES)
    def test_each_frozen_cue_introduces_a_quote(self, cue: str) -> None:
        """Same reason as the ambiguity-cue sweep: frozen evaluator
        surface, every entry exercised on its own."""
        answer = f"Testloven § 1 {cue}: Slik tekst star her. Neste setning."
        quotes = detect_quotes(answer, [citation(answer, "§ 1")])

        assert [quote.via_cue for quote in quotes] == [cue]
        assert quotes[0].text == "Slik tekst star her."


class TestMarkBoundaries:
    """Survivor-killers for the paired-span scan: every boundary in the
    open/close rules is reachable, and each mark glyph is live."""

    def test_a_guillemet_at_the_very_start_opens(self) -> None:
        answer = "«Loven gjelder.» sier testloven § 1 om dette."

        quotes = detect_quotes(answer, [citation(answer, "§ 1")])

        assert [quote.text for quote in quotes] == ["Loven gjelder."]

    def test_a_single_quote_at_the_very_start_opens(self) -> None:
        # The answer deliberately ends with a letter: the index-0 branch
        # of the opening rule must not fall back to peeking at answer[-1].
        answer = "'Loven gjelder.' står i testloven § 1 slik"

        quotes = detect_quotes(answer, [citation(answer, "§ 1")])

        assert [quote.text for quote in quotes] == ["Loven gjelder."]

    def test_a_closing_quote_as_the_last_character_closes(self) -> None:
        answer = "Testloven § 1 sier 'Loven gjelder'"

        quotes = detect_quotes(answer, [citation(answer, "§ 1")])

        assert [quote.text for quote in quotes] == ["Loven gjelder"]

    def test_a_span_ending_in_a_letter_still_closes(self) -> None:
        """The closing rule looks at the character AFTER the mark, never
        at the letter inside the span before it."""
        answer = "Testloven § 1 sier 'Loven gjelder' om dette."

        quotes = detect_quotes(answer, [citation(answer, "§ 1")])

        assert [quote.text for quote in quotes] == ["Loven gjelder"]

    def test_a_guillemet_directly_after_a_letter_still_opens(self) -> None:
        """Asymmetric marks take no word-boundary rule — «...» after a
        missing space is still a quote."""
        answer = "Testloven § 1 sier«Loven gjelder.» her."

        quotes = detect_quotes(answer, [citation(answer, "§ 1")])

        assert [quote.text for quote in quotes] == ["Loven gjelder."]

    def test_a_stray_closer_with_no_opening_detects_nothing(self) -> None:
        answer = "Ingen sitater i testloven § 1 her » slutt."

        assert detect_quotes(answer, [citation(answer, "§ 1")]) == []

    def test_defaults_are_none_not_empty(self) -> None:
        quote = DetectedQuote(text="x", start=0, end=1)

        assert quote.attached is None
        assert quote.via_cue is None


class TestCueWindowHandoff:
    """The cue channel hands any window containing a quotation mark to
    the mark channel — killers for the window-scan boundaries."""

    def test_a_mark_directly_after_the_colon_is_one_quote(self) -> None:
        answer = "Testloven § 1 lyder:«Loven gjelder for alle.» Neste."

        quotes = detect_quotes(answer, [citation(answer, "§ 1")])

        assert [quote.text for quote in quotes] == ["Loven gjelder for alle."]

    def test_a_double_quote_after_a_cue_is_one_quote(self) -> None:
        answer = 'Testloven § 1 lyder: "Loven gjelder for alle." Neste.'

        quotes = detect_quotes(answer, [citation(answer, "§ 1")])

        assert [quote.text for quote in quotes] == ["Loven gjelder for alle."]

    def test_a_single_quote_after_a_cue_is_one_quote(self) -> None:
        answer = "Testloven § 1 lyder: 'Loven gjelder for alle.' Neste."

        quotes = detect_quotes(answer, [citation(answer, "§ 1")])

        assert [quote.text for quote in quotes] == ["Loven gjelder for alle."]

    def test_a_second_cue_still_captures_after_a_marked_first(self) -> None:
        """One cue handing off to the mark channel must not end the scan
        for every later cue."""
        answer = "Testloven § 1 lyder: «Første tekst.» Loven ordlyd: Andre tekst her."

        quotes = detect_quotes(answer, [citation(answer, "§ 1")])

        assert [quote.text for quote in quotes] == ["Første tekst.", "Andre tekst her."]

    def test_a_cue_colon_at_the_answer_end_detects_nothing(self) -> None:
        answer = "Testloven § 1 lyder:"

        assert detect_quotes(answer, [citation(answer, "§ 1")]) == []


class TestAttachmentBoundaries:
    def test_a_citation_at_the_sentence_start_attaches(self) -> None:
        answer = "Noe annet står der. § 1 i testloven sier «Loven gjelder.» her."
        cited = citation(answer, "§ 1")

        quotes = detect_quotes(answer, [cited])

        assert quotes[0].attached == cited

    def test_a_citation_at_the_quote_start_is_preceding(self) -> None:
        """A citation opening the quote's inner text sits AT quote_start
        and binds as preceding, not following."""
        answer = "Se «§ 1 i testloven» som nevnt her."
        cited = citation(answer, "§ 1")

        quotes = detect_quotes(answer, [cited])

        assert quotes[0].attached == cited

    def test_following_attachment_picks_the_nearest_regardless_of_input_order(self) -> None:
        answer = "Det står «Loven gjelder.» i testloven § 1 og § 2 her."
        near = citation(answer, "§ 1")
        far = citation(answer, "§ 2", section_id="2")

        quotes = detect_quotes(answer, [far, near])

        assert quotes[0].attached == near
