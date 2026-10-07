"""Source-explicit relations of a local regulation (ADR-0016 1e, slice S10).

A relation is recorded only where the regulation's own text states it, with
the sentence that states it kept verbatim as ``evidence``. Three kinds are
read:

* ``hjemmel`` — after ``med hjemmel (i)``, ``i medhold av`` or ``Hjemmel:``;
* ``amends`` — after ``endrer``, ``endring(er) i/av``, or beside a passive
  ``endres``;
* ``repeals`` — after ``opphever``, ``oppheving av``, or beside a passive
  ``oppheves`` / ``oppheva`` / ``opphevet``;
* ``amended_by`` / ``repealed_by`` — after ``endret ved`` / ``opphevet ved``
  (``av``, ``gjennom``): the text names the act that changed *it*.

A **target** is named in one of three forms: a Lovdata id (``LOV-…``,
``FOR-…``), a law or regulation by date and number (``lov 13. mars 1981 nr.
6``), or a law's short name (``forurensningsloven``). A target takes the kind
of the nearest cue before it in its sentence; with none before it, the
nearest *passive* cue after it ("Forskrift … oppheves."). Not recorded, as
citations rather than relations: a target with no cue — so the regulation's
own number in its title ("Forskrift 5. mai 2020 nr. 900 om endring i …") is
never a target of itself — a target right after ``jf.`` / ``jamfør``, a law
as anything but a hjemmel (a municipality amends no statute; "forskrift om
skjenking etter alkoholloven oppheves" repeals the forskrift), and, in the
body, a hjemmel cue outside the regulation's statement of its own basis
("Forskriften er gitt med hjemmel i …", a ``Hjemmel`` line): "vedtak i
medhold av denne forskriften kan påklages etter forvaltningsloven" names no
hjemmel.

Resolution links a target only when its form names exactly one current
corpus document: an id or a date and number matched to a central (``nl-``,
``sf-``) or local (``lf-``) record, or a short name equal to exactly one
current central law's parenthesised short title. Anything else — no number,
not in the corpus, a name two laws share — stays as text with the reason it
did not resolve. Nothing is inferred from a title's similarity.

``evidence`` is a substring of the regulation's extracted text with its line
breaks read as spaces, the form the fields are read from (``fields.py``).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from lovspor.promotion.dates import DATE, parse_stated_date
from lovspor.promotion.models import ExtractedRegulation

TargetKind = Literal["lov", "forskrift"]
LovdataKey = tuple[TargetKind, date, int]


class RelationKind(StrEnum):
    HJEMMEL = "hjemmel"
    AMENDS = "amends"
    REPEALS = "repeals"
    AMENDED_BY = "amended_by"
    REPEALED_BY = "repealed_by"


class Unresolved(StrEnum):
    """Why a stated target stays text: a closed set, never a guess."""

    NO_NUMBER = "no_number"
    NOT_IN_CORPUS = "not_in_corpus"
    AMBIGUOUS_NAME = "ambiguous_name"
    SELF_REFERENCE = "self_reference"


# "i medhold av denne forskriften" is a decision made under THIS regulation,
# never its hjemmel.
_NOT_ITSELF = r"(?!\s+(?:denne\s+forskrift\w*|forskriften|forskrifta)\b)"
# Each cue ends on its own boundary: ``Hjemmel:`` ends on a colon, not a word.
_FORWARD_CUES: tuple[tuple[RelationKind, str], ...] = (
    (
        RelationKind.HJEMMEL,
        rf"(?:med\s+(?:hjemmel|heimel)(?:\s+i)?\b|i\s+med(?:hold|hald)\s+av\b){_NOT_ITSELF}",
    ),
    (RelationKind.HJEMMEL, r"(?:hjemmel|heimel)\s*:"),
    (RelationKind.AMENDS, r"endrer\b|endring(?:ar|er)?\s+(?:i|av)\b"),
    (RelationKind.REPEALS, r"opphever\b|oppheving\s+av\b"),
    (RelationKind.AMENDED_BY, r"(?:endret|endra)\s+(?:ved|av|gjennom)\b"),
    (RelationKind.REPEALED_BY, r"(?:opphevet|oppheva)\s+(?:ved|av|gjennom)\b"),
)
_PASSIVE_CUES: tuple[tuple[RelationKind, str], ...] = (
    (RelationKind.AMENDS, r"(?:endres|endrast|vert\s+endra|blir\s+endret)\b"),
    (RelationKind.REPEALS, r"(?:oppheves|opphevast|oppheva|opphevet)\b"),
)
_CUE = re.compile(
    "|".join(
        f"(?P<{'p' if passive else 'f'}{index}>\\b(?:{pattern}))"
        for passive, cues in ((False, _FORWARD_CUES), (True, _PASSIVE_CUES))
        for index, (_, pattern) in enumerate(cues)
    ),
    re.IGNORECASE,
)
_SECTION = r"(?:\s+§§?\s*\d+[a-z]?(?:-\d+[a-z]?)?)?"
_TARGET = re.compile(
    r"(?P<lovdata>\b(?P<lkind>LOV|FOR)-(?P<lid>\d{4}-\d{2}-\d{2})-(?P<lnum>\d+))"
    rf"|(?P<dated>\b(?P<dkind>lov|lova|loven|forskrift|forskrifta|forskriften)"
    rf"(?:\s+(?:av|frå|fra|den))?\s+(?P<ddate>{DATE})(?:\s*,?\s*nr\.?\s*(?P<dnum>\d+))?)"
    rf"|(?P<named>\b(?:[a-zæøå]+-\s*(?:og|eller)\s+)?[a-zæøå]{{2,}}(?:loven|lova)\b){_SECTION}",
    re.IGNORECASE,
)
# In the body, a hjemmel cue counts only where the regulation states its own
# basis; elsewhere "i medhold av" is about appeals or other acts.
_OWN_BASIS = re.compile(
    r"^(?:§\s*\d+\w*\.?\s+)?(?:hjemmel|heimel)\b"
    r"|\b(?:denne\s+)?forskrift(?:en|a)?\s+(?:er|blir|vert)\s+"
    r"(?:gitt|gjeve|gjeven|fastsatt|fastsett|vedtatt|vedteke|hjemlet|heimla)\b",
    re.IGNORECASE,
)
_CITATION = re.compile(r"\b(?:jf|jfr|jamfør|jamfr)\.?\s*$", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=\.)\s+(?=[A-ZÆØÅ§])")
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


class StatedTarget(BaseModel):
    """A target as the text names it: verbatim, plus what its form says it is."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    form: Literal["lovdata_id", "date_and_number", "dated", "short_name"]
    kind: TargetKind | None = None
    stated_date: date | None = None
    number: int | None = None


class StatedRelation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RelationKind
    target: StatedTarget
    evidence: str


class LinkedDocument(BaseModel):
    """The one corpus document a target resolved to."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: str
    dataset: Literal["lover", "forskrifter", "lokale-forskrifter"]
    address: str


class Relation(BaseModel):
    """A recorded relation: what the text states, and the document it names or why not."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RelationKind
    target_text: str
    evidence: str
    target: LinkedDocument | None
    unresolved: Unresolved | None


def stated_relations(regulation: ExtractedRegulation) -> tuple[StatedRelation, ...]:
    """Every relation the text states, in text order, each with its sentence."""
    found: list[StatedRelation] = []
    for text, in_block in ((regulation.identification_block, True), (regulation.body, False)):
        for line in text.split("\n"):
            for sentence in _SENTENCE_END.split(line):
                found.extend(_in_sentence(sentence.strip(), in_block=in_block))
    return tuple(found)


def _in_sentence(sentence: str, *, in_block: bool) -> Iterator[StatedRelation]:
    """Each cued target; a law only as a hjemmel — a municipality amends no statute."""
    own_basis = in_block or _OWN_BASIS.search(sentence) is not None
    cues = [
        (m.start(), m.end(), cue)
        for m in _CUE.finditer(sentence)
        if (cue := _cue_kind(m))[0] is not RelationKind.HJEMMEL or own_basis
    ]
    for match in _TARGET.finditer(sentence) if cues else ():
        kind = _kind_for(match.start(), match.end(), cues)
        target = _stated_target(match)
        cited = _CITATION.search(sentence[: match.start()]) is not None
        if kind is None or cited or (kind is not RelationKind.HJEMMEL and target.kind == "lov"):
            continue
        yield StatedRelation(kind=kind, target=target, evidence=sentence)


def _cue_kind(match: re.Match[str]) -> tuple[RelationKind, bool]:
    name = next(key for key, value in match.groupdict().items() if value is not None)
    cues = _PASSIVE_CUES if name[0] == "p" else _FORWARD_CUES
    return cues[int(name[1:])][0], name[0] == "p"


def _kind_for(
    start: int, end: int, cues: list[tuple[int, int, tuple[RelationKind, bool]]]
) -> RelationKind | None:
    before = [kind for _, cue_end, (kind, _) in cues if cue_end <= start]
    if before:
        return before[-1]
    after = [kind for cue_start, _, (kind, passive) in cues if passive and cue_start >= end]
    return after[0] if after else None


def _stated_target(match: re.Match[str]) -> StatedTarget:
    text = match.group(0).strip()
    if match.group("lovdata"):
        return StatedTarget(
            text=text,
            form="lovdata_id",
            kind="lov" if match.group("lkind").upper() == "LOV" else "forskrift",
            stated_date=_iso_date(match.group("lid")),
            number=int(match.group("lnum")),
        )
    if match.group("dated"):
        number = int(match.group("dnum")) if match.group("dnum") else None
        return StatedTarget(
            text=text,
            form="dated" if number is None else "date_and_number",
            kind="lov" if match.group("dkind").lower().startswith("lov") else "forskrift",
            stated_date=parse_stated_date(match.group("ddate")),
            number=number,
        )
    return StatedTarget(text=text, form="short_name", kind="lov")


def _iso_date(text: str) -> date | None:
    match = _ISO.fullmatch(text)
    if match is None:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


class TargetIndex(BaseModel):
    """The corpus documents a target can resolve to, keyed by the forms that name them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    by_lovdata: dict[LovdataKey, LinkedDocument]
    by_short_name: dict[str, tuple[LinkedDocument, ...]]


def resolve(stated: StatedRelation, index: TargetIndex, own_doc_id: str) -> Relation:
    """``stated`` linked to the one document its target names, or kept as text."""
    linked, unresolved = _link(stated.target, index)
    if linked is not None and linked.doc_id == own_doc_id:
        linked, unresolved = None, Unresolved.SELF_REFERENCE
    return Relation(
        kind=stated.kind,
        target_text=stated.target.text,
        evidence=stated.evidence,
        target=linked,
        unresolved=unresolved,
    )


def _link(
    target: StatedTarget, index: TargetIndex
) -> tuple[LinkedDocument | None, Unresolved | None]:
    if target.form == "short_name":
        named = index.by_short_name.get(short_name_key(target.text), ())
        if len(named) > 1:
            return None, Unresolved.AMBIGUOUS_NAME
        return (named[0], None) if named else (None, Unresolved.NOT_IN_CORPUS)
    if target.kind is None or target.stated_date is None or target.number is None:
        return None, Unresolved.NO_NUMBER
    linked = index.by_lovdata.get((target.kind, target.stated_date, target.number))
    return (linked, None) if linked else (None, Unresolved.NOT_IN_CORPUS)


def short_name_key(text: str) -> str:
    """A short name as it is compared: its words only, casefolded, ``§`` part dropped."""
    name = text.split("§", 1)[0]
    return " ".join(re.sub(r"\s*-\s*", "- ", name).split()).casefold()
