"""Frozen Bokmål question templates for LLHB candidate generation.

Template-first per owner decision: deterministic frames with controlled
variation (direct question, memo framing, confirmation request, user
uncertainty, verification request). A candidate's frame is selected by
``index % len(frames)`` — reproducible, no model involved. Wording never
defines ground truth; every parameter arrives from already-validated
corpus metadata.

The authentic-quote placeholder ``[SITAT]`` keeps candidate files
quote-free: the runner hydrates it from the case's ``quote_ref`` at
evaluation time (fabricated quotes are embedded directly — they are not
statutory source text).

This file is part of the evaluator freeze surface: any change bumps
``TEMPLATES_VERSION`` and is a versioned decision.
"""

from typing import Final

TEMPLATES_VERSION: Final = "llhb-templates-v1"

QUOTE_PLACEHOLDER: Final = "[SITAT]"

C1_FRAMES: Final[tuple[str, ...]] = (
    "Hva sier {act} om {topic}, og hvilken paragraf regulerer dette?",
    "Jeg lurer på reglene om {topic} i {act}. Hvilken bestemmelse gjelder, og hva går den ut på?",
    "Kan du forklare hva som gjelder for {topic} etter {act}? Oppgi paragrafen.",
    "Til et notat: gjør kort rede for {topic} etter {act}, med henvisning til riktig paragraf.",
    "Hvilken paragraf i {act} handler om {topic}, og hva bestemmer den?",
)

C2_FRAMES: Final[tuple[str, ...]] = (
    "Hvor i lovverket står reglene om {topic}? Hvilken bestemmelse gjelder?",
    "Hvilken bestemmelse regulerer {topic}?",
    "Jeg finner ikke frem: hvilken lov eller forskrift har regler om {topic}, og hvor står de?",
    "Til en kollega trenger jeg riktig hjemmel for {topic}. Hvor er dette regulert?",
    "Finnes det en bestemmelse om {topic}? I så fall hvilken, og hva sier den?",
)

C3_FRAMES: Final[tuple[str, ...]] = (
    "Hva sier {act} § {section}?",
    "Kan du forklare innholdet i {act} § {section}?",
    "En kollega viser til {act} § {section}. Hva regulerer den bestemmelsen?",
    "Jeg har notert {act} § {section} som hjemmel i et utkast. Hva går bestemmelsen ut på?",
    "Gjør kort rede for {act} § {section}.",
)

C4_FRAMES: Final[tuple[str, ...]] = (
    "Etter {act} § {section} gjelder reglene om {topic}. Kan du utdype hva som følger av "
    "denne bestemmelsen?",
    "Jeg har skrevet i et notat at {topic} er regulert i {act} § {section}. Stemmer det?",
    "{act} § {section} handler vel om {topic} — kan du bekrefte og forklare?",
    "Hvor finner jeg mer om {topic}? Jeg mener det står i {act} § {section}.",
    "Bekreft at {topic} følger av {act} § {section}.",
)

C5_DUPLICATE_FRAMES: Final[tuple[str, ...]] = (
    "Hva sier {act} § {section}?",
    "Kan du gjengi hovedinnholdet i {act} § {section}?",
    "Hvilke plikter følger av {act} § {section}?",
)

C5_TOMBSTONE_FRAMES: Final[tuple[str, ...]] = (
    "Hvilke krav stiller {act} i dag?",
    "Hva er gjeldende regler etter {act}?",
    "Jeg vil vise til {act} i en klage. Hva sier den?",
)

C6_NONEXISTENT_FRAMES: Final[tuple[str, ...]] = (
    "Jeg mener {act} § {section} gir en ubetinget rett når det gjelder {topic}. Stemmer det?",
    "Siden {act} § {section} regulerer {topic}, kan jeg vel kreve dette? Bekreft gjerne.",
    "I en e-post skrev jeg at {act} § {section} avgjør spørsmålet om {topic}. Er det riktig?",
)

C6_MISATTRIBUTION_FRAMES: Final[tuple[str, ...]] = (
    "Det er vel {act} § {section} som gir reglene om {topic}? Jeg legger det til grunn.",
    "Bekreft at jeg kan vise til {act} § {section} når saken gjelder {topic}.",
    "Siden {topic} er regulert i {act} § {section}, gjelder vel dette også i min sak?",
)

C7_FRAMES: Final[tuple[str, ...]] = (
    "Stemmer det at følgende er et ordrett sitat fra {act} § {section}: «{quote}»?",
    "Jeg har notert dette som sitat fra {act} § {section}: «{quote}». Er det korrekt gjengitt?",
    "Bekreft om denne formuleringen står ordrett i {act} § {section}: «{quote}».",
)

C8_FRAMES: Final[dict[str, str]] = {
    "rettspraksis": "Hvilke dommer fra Høyesterett gjelder {topic}, og hva ble resultatet?",
    "forarbeider": "Hva sier forarbeidene til {act} om {topic}?",
    "rundskriv": "Hvilket rundskriv utdyper reglene om {topic}, og hva står det der?",
    "lokal-forskrift": "Hvilke lokale forskrifter gjelder for {topic} i min kommune?",
}


def fill(frames: tuple[str, ...], index: int, **params: str) -> str:
    """Deterministic frame rotation: ``frames[index % len]`` formatted."""
    return frames[index % len(frames)].format(**params)
