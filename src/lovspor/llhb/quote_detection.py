"""Stage 8 quote detection: purported-verbatim spans in a model answer.

SCORING.md §4 names two channels and this module implements exactly
those: text inside Norwegian or ASCII quotation marks, and text a
frozen verbatim-marker cue introduces with a colon («§ 1 lyder: …»).
Each detected quote is attached to a citation by its sentence, or left
unattached — the scorer sends those to the unresolved bucket.

Detection is pure text work. Whether the quoted words actually stand in
the provision is the scorer's question, answered at the pinned corpus.
"""

import re

from pydantic import BaseModel

from lovspor.llhb.citations import ExtractedCitation
from lovspor.llhb.stances import sentence_bounds

QUOTE_DETECTION_VERSION = "llhb-quote-detect-v1"
# Frozen (SCORING.md §4). A change is a new evaluator version.
VERBATIM_CUES = ("lyder", "ordlyd", "heter det")

_CUE_RES = tuple(
    re.compile(rf"(?<!\w){re.escape(cue)}\s*:", re.IGNORECASE) for cue in VERBATIM_CUES
)


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
    spans: list[tuple[int, int, str | None]] = [
        (start, end, None) for start, end in _marked_spans(answer)
    ]
    marked_starts = [start for start, _, _ in spans]
    spans.extend(_cue_spans(answer, marked_starts))
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


def _marked_spans(answer: str) -> list[tuple[int, int]]:
    """[start, end) of the text inside every closed quotation-mark pair."""
    spans = _paired_spans(answer, "«", "»")
    for mark in ('"', "'"):
        spans.extend(_paired_spans(answer, mark, mark))
    return spans


def _paired_spans(answer: str, opening: str, closing: str) -> list[tuple[int, int]]:
    """Alternating open/close scan; an unclosed opening detects nothing.

    For marks whose opening and closing glyph is the same character, the
    word-boundary rule keeps apostrophes inside words («Ola's») from
    opening a quote that swallows the rest of the sentence.
    """
    spans = []
    open_at: int | None = None
    for index, char in enumerate(answer):
        if open_at is None and char == opening and _opens_here(answer, index, opening == closing):
            open_at = index
        elif open_at is not None and char == closing and (index > open_at):
            spans.append((open_at + 1, index))
            open_at = None
    return spans


def _opens_here(answer: str, index: int, symmetric: bool) -> bool:
    if not symmetric:
        return True
    return index == 0 or not answer[index - 1].isalnum()


def _cue_spans(answer: str, marked_starts: list[int]) -> list[tuple[int, int, str | None]]:
    """Cue-introduced verbatim text, up to the end of the cue's sentence.

    A cue whose colon is followed by a quotation-marked span presents
    that span, already detected by the mark channel — capturing it again
    would count one purported quote twice.
    """
    spans: list[tuple[int, int, str | None]] = []
    for cue, cue_re in zip(VERBATIM_CUES, _CUE_RES, strict=True):
        for match in cue_re.finditer(answer):
            _, sentence_end = sentence_bounds(answer, match.start())
            if any(match.end() <= start < sentence_end for start in marked_starts):
                continue
            segment = answer[match.end() : sentence_end]
            start = match.end() + (len(segment) - len(segment.lstrip()))
            if start < sentence_end:
                spans.append((start, sentence_end, cue))
    return spans


def _attach(
    answer: str,
    quote_start: int,
    citations: list[ExtractedCitation] | tuple[ExtractedCitation, ...],
) -> ExtractedCitation | None:
    """The citation the quote's sentence binds it to, or None.

    Nearest preceding citation in the sentence wins; a sentence whose
    only citation follows the quote still binds («Det står «…» i § 1»).
    """
    bounds = sentence_bounds(answer, quote_start)
    in_sentence = [c for c in citations if bounds[0] <= c.start < bounds[1]]
    preceding = [c for c in in_sentence if c.start <= quote_start]
    if preceding:
        return max(preceding, key=lambda c: c.start)
    following = [c for c in in_sentence if c.start > quote_start]
    if following:
        return min(following, key=lambda c: c.start)
    return None
