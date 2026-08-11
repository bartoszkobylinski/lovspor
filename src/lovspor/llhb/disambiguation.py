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

from lovspor.llhb.stances import sentence_bounds

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

# Occurrence-number mentions need no channel of their own: «forekomst»
# and «occurrence» are cues, so any answer counting occurrences has
# already surfaced by the time counting could matter. The counting
# channel that earns its keep is chapter labels — how an answer lays two
# same-id sections side by side without ever saying «flere».
_CHAPTER_LABEL_RE = re.compile(r"kapittel\s+([0-9a-zæøå]+(?:-[0-9a-zæøå]+)?)", re.IGNORECASE)
# Two same-id sections laid side by side are the smallest set of variants
# an answer can present.
_VARIANT_MINIMUM = 2


def ambiguity_surfaced(answer: str) -> bool:
    """True when the answer surfaced the ambiguity in any frozen form."""
    if any(cue in answer.casefold() for cue in AMBIGUITY_CUES):
        return True
    return _distinct_near_sections(_CHAPTER_LABEL_RE, answer) >= _VARIANT_MINIMUM


def _distinct_near_sections(pattern: re.Pattern[str], answer: str) -> int:
    """Distinct labels, counted only in sentences that mention a « § ».

    Chapters named as narrative structure («Kapittel 1 gir bakgrunnen»)
    are not two occurrences of one section id; laying variants side by
    side means naming the section in the same breath as each label.
    """
    labels = set()
    for match in pattern.finditer(answer):
        start, end = sentence_bounds(answer, match.start())
        if "§" in answer[start:end]:
            labels.add(match.group(1).casefold())
    return len(labels)
