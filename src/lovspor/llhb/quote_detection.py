"""Stage 8 quote detection: purported-verbatim spans in a model answer.

SCORING.md §4 names two channels and this module implements exactly
those: text inside Norwegian or ASCII quotation marks, and text a
frozen verbatim-marker cue introduces with a colon («§ 1 lyder: …»).
A quotation mark anywhere in a cue's captured window hands the material
to the mark channel — including an unclosed one, which detects nothing:
a half-marked presentation is ambiguous, and an ambiguous presentation
is not scored as verbatim.

Each detected quote is attached to a citation by its sentence, or left
unattached — the scorer sends those to the unresolved bucket.

Detection is pure text work. Whether the quoted words actually stand in
the provision is the scorer's question, answered at the pinned corpus.
"""

import re
from collections.abc import Iterator

from pydantic import BaseModel

from lovspor.llhb.citations import ExtractedCitation
from lovspor.llhb.stances import sentence_bounds

QUOTE_DETECTION_VERSION = "llhb-quote-detect-v1"
# Frozen (SCORING.md §4). A change is a new evaluator version.
VERBATIM_CUES = ("lyder", "ordlyd", "heter det")

_CUE_RES = {
    cue: re.compile(rf"(?<!\w){re.escape(cue)}\s*:", re.IGNORECASE) for cue in VERBATIM_CUES
}
_QUOTE_OPENERS = ("«", '"', "'")


class DetectedQuote(BaseModel, frozen=True):
    """One purported-verbatim span, with the citation its sentence binds."""

    text: str
    start: int
    end: int
    attached: ExtractedCitation | None = None
    via_cue: str | None = None


def detect_quotes(
    answer: str, citations: list[ExtractedCitation] | tuple[ExtractedCitation, ...]
) -> list[DetectedQuote]:
    """Every purported-verbatim span, in answer order."""
    spans = [*_marked_triples(answer), *_cue_spans(answer)]
    spans.sort()
    return [
        DetectedQuote(
            text=answer[start:end],
            start=start,
            end=end,
            attached=_attach(answer, start, citations),
            via_cue=cue,
        )
        for start, end, cue in spans
        if answer[start:end].strip()
    ]


def _marked_triples(answer: str) -> list[tuple[int, int, str | None]]:
    """[start, end, None) for the text inside every closed mark pair."""
    spans = _paired_spans(answer, "«", "»")
    for mark in ('"', "'"):
        spans.extend(_paired_spans(answer, mark, mark))
    return [(start, end, None) for start, end in spans]


def _paired_spans(answer: str, opening: str, closing: str) -> list[tuple[int, int]]:
    """Alternating open/close scan; an unclosed opening detects nothing.

    For marks whose opening and closing glyph is the same character, a
    word-boundary rule applies at both ends: an apostrophe inside a word
    neither opens a quote («Kari's» outside one) nor closes one
    («'Ola's rettigheter gjelder.'» stays a single span).
    """
    spans = []
    symmetric = opening == closing
    open_at = -1
    for index, char in enumerate(answer):
        if open_at < 0 and char == opening and _opens_here(answer, index, symmetric):
            open_at = index
        elif open_at >= 0 and char == closing and _closes_here(answer, index, symmetric):
            spans.append((open_at + 1, index))
            open_at = -1
    return spans


def _opens_here(answer: str, index: int, symmetric: bool) -> bool:
    if not symmetric:
        return True
    return index == 0 or not answer[index - 1].isalnum()


def _closes_here(answer: str, index: int, symmetric: bool) -> bool:
    if not symmetric:
        return True
    return index + 1 == len(answer) or not answer[index + 1].isalnum()


def _cue_spans(answer: str) -> Iterator[tuple[int, int, str | None]]:
    """Cue-introduced verbatim text, up to the end of the cue's sentence.

    A quotation mark anywhere in the window belongs to the mark channel:
    a closed pair is already detected there, and capturing around an
    unclosed one would score an ambiguous presentation as verbatim.
    """
    for cue, cue_re in _CUE_RES.items():
        for match in cue_re.finditer(answer):
            _, sentence_end = sentence_bounds(answer, match.start())
            segment = answer[match.end() : sentence_end]
            start = match.end() + (len(segment) - len(segment.lstrip()))
            if any(answer[index] in _QUOTE_OPENERS for index in range(start, sentence_end)):
                continue
            yield (start, sentence_end, cue)


def _attach(
    answer: str,
    quote_start: int,
    citations: list[ExtractedCitation] | tuple[ExtractedCitation, ...],
) -> ExtractedCitation | None:
    """The citation the quote's sentence binds it to, or None.

    Nearest preceding citation in the sentence wins; a sentence whose
    only citation follows the quote still binds («Det står «…» i § 1»).
    """
    sentence = range(*sentence_bounds(answer, quote_start))
    in_sentence = [c for c in citations if c.start in sentence]
    preceding = [c for c in in_sentence if c.start <= quote_start]
    if preceding:
        return max(preceding, key=lambda c: c.start)
    following = [c for c in in_sentence if c not in preceding]
    if following:
        return min(following, key=lambda c: c.start)
    return None
