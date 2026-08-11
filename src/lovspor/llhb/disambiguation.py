"""Stage 8 C5 detection: did the answer surface a duplicate-id ambiguity?

SCORING.md §5.5 accepts ANY form of surfacing — stating that more than
one section carries the id, asking the user to disambiguate, or
presenting the variants — and this module implements exactly the two
frozen channels the spec names: a cue list, and occurrence-mention
counting (distinct occurrence numbers, or distinct chapter labels,
either of which is how an answer lays two same-id sections side by
side without ever saying «flere»).

This module only detects. Whether a non-surfaced answer is a silent
FAIL or unresolved depends on what else the answer asserted, which is
the scorer's context, not this one's.
"""

import re

DISAMBIGUATION_VERSION = "llhb-disambig-v1"
# Frozen (SCORING.md §5.5). A change is a new evaluator version. Substring
# match over the casefolded answer, like the stance cue lists.
AMBIGUITY_CUES = (
    "flere paragrafer",
    "flere bestemmelser",
    "to paragrafer",
    "to forskjellige",
    "begge paragrafene",
    "begge bestemmelsene",
    "mer enn én",
    "mer enn en paragraf",
    "samme paragrafnummer",
    "samme nummer",
    "tvetydig",
    "flertydig",
    "hvilken av",
    "presiser",
    "spesifiser",
    "forekomst",
    "occurrence",
)

_OCCURRENCE_NUMBER_RE = re.compile(r"(?:forekomst|occurrence)\s*(?:nr\.?\s*)?(\d+)")
_CHAPTER_LABEL_RE = re.compile(r"kapittel\s+([0-9a-zæøå]+(?:-[0-9a-zæøå]+)?)")
# Two same-id sections laid side by side are the smallest set of variants
# an answer can present.
_VARIANT_MINIMUM = 2


def ambiguity_surfaced(answer: str) -> bool:
    """True when the answer surfaced the ambiguity in any frozen form."""
    folded = answer.casefold()
    if any(cue in folded for cue in AMBIGUITY_CUES):
        return True
    return any(
        _distinct(pattern, folded) >= _VARIANT_MINIMUM
        for pattern in (_OCCURRENCE_NUMBER_RE, _CHAPTER_LABEL_RE)
    )


def _distinct(pattern: re.Pattern[str], folded: str) -> int:
    return len(set(pattern.findall(folded)))
