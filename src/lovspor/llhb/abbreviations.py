"""Frozen abbreviation table for the LLHB citation extractor.

The table is the ONLY path from an abbreviation token to an act name:
no fuzzy matching, no model-memory expansion, no derivation from the
corpus at run time. It maps an abbreviation surface form (as written in
answers, including the trailing dot) to the act *name* the name index
resolves — never directly to a slug, so the table stays valid across
corpus states and an entry whose act is absent from a given corpus
resolves to ``unknown act`` instead of silently vanishing.

Entries are the standard short forms used by Norwegian legal practice
and Lovdata for the most-cited acts. The table is deliberately small:
LLHB v1 needs the forms a model plausibly emits for high-traffic acts,
not an exhaustive legal dictionary. Every entry has a test; adding an
entry is a versioned change to the evaluator freeze surface.
"""

from typing import Final

ABBREVIATIONS_VERSION: Final = "llhb-abbrev-v1"
"""Version stamp recorded in run metadata as part of the evaluator freeze."""

ABBREVIATIONS: Final[dict[str, str]] = {
    "aml.": "arbeidsmiljøloven",
    "avtl.": "avtaleloven",
    "fvl.": "forvaltningsloven",
    "ftrl.": "folketrygdloven",
    "grl.": "grunnloven",
    "sktl.": "skatteloven",
    "strl.": "straffeloven",
    "tvl.": "tvisteloven",
}
"""Abbreviation surface form (lowercase, with trailing dot) → act name."""


def expand_abbreviation(token: str) -> str | None:
    """Expand ``token`` via the frozen table; ``None`` when not an entry.

    Matching is exact after casefold — a token that is not literally in
    the table (missing dot, extra suffix, unknown alias) is NOT an
    abbreviation for LLHB purposes and stays in the answer text as
    ordinary prose.
    """
    return ABBREVIATIONS.get(token.casefold())
