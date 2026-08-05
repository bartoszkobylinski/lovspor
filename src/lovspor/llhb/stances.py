"""Rule-based stance classification for extracted citations.

Decides whether an answer *asserts* a citation as valid law, *denies*
it («§ 15-99 finnes ikke»), presents it as a *correction* («riktig
bestemmelse er § 15-7»), or is *unresolved*. Only asserted citations
may count toward hallucination metrics; a model that repeats a false
citation in order to reject it must never be scored as hallucinating
it (SCORING.md §2).

The mechanism is deliberately the smallest deterministic one that
satisfies the spec: frozen cue phrase lists plus sentence-local window
rules. No LLM, no syntax parsing. Anything the rules cannot attach
lands in UNRESOLVED — counted and published, never guessed.

Window rules, per citation within its sentence (citations sorted by
position; ``after`` runs to the next citation or sentence end,
``before`` from the previous citation or sentence start):

1. a denial cue in the citation's *after* window → DENIED;
2. else a correction cue in its *before* window → CORRECTED;
3. else a denial cue anywhere in the sentence that no citation's
   *after* window consumed → UNRESOLVED (a denial is present but the
   rules cannot attach it — never guess the target);
4. else ASSERTED.

Sentence boundaries are ``[.!?]`` followed by whitespace and an
uppercase letter, or a newline. The uppercase requirement keeps
abbreviation dots («aml. § 15-7») from splitting a sentence.
"""

import re
from collections.abc import Sequence
from enum import StrEnum
from typing import Final

STANCE_RULES_VERSION: Final = "llhb-stance-v1"
"""Version stamp recorded in run metadata as part of the evaluator freeze."""

DENIAL_CUES: Final[tuple[str, ...]] = (
    "finnes ikke",
    "eksisterer ikke",
    "finnes ingen",
    "er opphevet",
    "ble opphevet",
    "er ikke riktig",
    "stemmer ikke",
    "er feil",
    "er ugyldig",
    "ikke finnes",
    "ikke eksisterer",
)

CORRECTION_CUES: Final[tuple[str, ...]] = (
    "riktig bestemmelse",
    "korrekt bestemmelse",
    "riktig hjemmel",
    "korrekt hjemmel",
    "riktig paragraf",
    "det riktige er",
    "i stedet",
    "skal være",
)

_SENTENCE_BOUNDARY = re.compile(r"[.!?](?=\s+[A-ZÆØÅ])|\n")


class Stance(StrEnum):
    ASSERTED = "asserted"
    DENIED = "denied"
    CORRECTED = "corrected"
    UNRESOLVED = "unresolved"


def sentence_bounds(text: str, pos: int) -> tuple[int, int]:
    """[start, end) of the sentence containing ``pos``."""
    start = 0
    end = len(text)
    for match in _SENTENCE_BOUNDARY.finditer(text):
        boundary = match.end()
        if boundary <= pos:
            start = boundary
        elif match.start() >= pos:
            end = match.start() + (0 if match.group() == "\n" else 1)
            break
    return start, end


def _contains_cue(window: str, cues: tuple[str, ...]) -> bool:
    folded = window.casefold()
    return any(cue in folded for cue in cues)


def _windows_in_sentence(
    span: tuple[int, int],
    neighbours: list[tuple[int, int]],
    bounds: tuple[int, int],
) -> tuple[tuple[int, int], tuple[int, int]]:
    """(before, after) window bounds for ``span`` among sorted ``neighbours``."""
    start, end = bounds
    before_edge = max((n_end for n_start, n_end in neighbours if n_end <= span[0]), default=start)
    after_edge = min((n_start for n_start, n_end in neighbours if n_start >= span[1]), default=end)
    return (before_edge, span[0]), (span[1], after_edge)


def _unconsumed_denial(text: str, spans: list[tuple[int, int]], bounds: tuple[int, int]) -> bool:
    """A denial cue in the sentence outside every citation's after window."""
    start, end = bounds
    folded = text[start:end].casefold()
    for cue in DENIAL_CUES:
        offset = folded.find(cue)
        while offset != -1:
            cue_start = start + offset
            if not _cue_consumed(cue_start, spans, bounds):
                return True
            offset = folded.find(cue, offset + 1)
    return False


def _cue_consumed(cue_start: int, spans: list[tuple[int, int]], bounds: tuple[int, int]) -> bool:
    for span in spans:
        _, after = _windows_in_sentence(span, [s for s in spans if s != span], bounds)
        if after[0] <= cue_start < after[1]:
            return True
    return False


def classify_stances(text: str, spans: Sequence[tuple[int, int]]) -> list[Stance]:
    """Stance for each citation span of ``text``, in input order."""
    stances: list[Stance] = []
    for span in spans:
        bounds = sentence_bounds(text, span[0])
        siblings = sorted(s for s in spans if s != span and bounds[0] <= s[0] < bounds[1])
        before, after = _windows_in_sentence(span, siblings, bounds)
        if _contains_cue(text[after[0] : after[1]], DENIAL_CUES):
            stances.append(Stance.DENIED)
        elif _contains_cue(text[before[0] : before[1]], CORRECTION_CUES):
            stances.append(Stance.CORRECTED)
        elif _unconsumed_denial(text, [span, *siblings], bounds):
            stances.append(Stance.UNRESOLVED)
        else:
            stances.append(Stance.ASSERTED)
    return stances
