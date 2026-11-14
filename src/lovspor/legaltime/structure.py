"""Structure map ``S`` and placement history (ADR-0023 R3, R8; Phase B3, #861).

Beside the label map ``P``, every step boundary has a structure map: each **live**
``provision_id`` → its current (chapter stamp, part stamp) pair, plus the act **outline**:
chapter stamp → the part that encloses it, as the announcements so far state it. Both
are keyed by identity and chapter stamp, never by a section label, so a move or a reused
label cannot carry a stamp to the wrong provision.

Stamps are the engine's own data-name forms (``kap16``, ``del3``), read exactly as
``document._enclosing`` reads the enacting structure and as ``instructions._placed`` stamps
an inserted provision. An empty stamp (``None``) means the source places the provision
under no section so named; it is a stated fact, not a missing one.

Each identity's ``Membership`` is an immutable history of placement events. Its current
pair is the last event's, or *unresolved* when the last event proves no pair; a repeal
ends the membership and keeps every event. Nothing here is inferred:

* step 0 stamps every enacted identity from the enacting member's structure; a missing
  stamp, a stamp for no identity, or a chapter under two parts is a hard failure;
* an insertion takes the pair its inserting structure states. A pair the structure does
  not complete is recorded as unresolved, never guessed;
* a placement clause (R3's closed grammar) yields a pair only from ``S_P`` and the outline
  before the step; anything else is a refusal of the whole operation.

The B1 step (``lovspor.legaltime.step``) calls these primitives in its intrinsic phase
and builds ``S_Q`` from accepted operations only. Nothing in the replay, the report or
any surface imports this module, so replay output and ``REPLAY_VERSION`` do not change.
"""

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Literal, Self

from pydantic import Field, model_validator

from lovspor.legaltime.document import Document
from lovspor.legaltime.errors import LegalTimeError
from lovspor.legaltime.identity import _ACT, EndProof, LabelProof, Ledger, _Validated
from lovspor.legaltime.instructions import _PART_WORDS
from lovspor.legaltime.text import ordinal, section_key

# A chapter stamp is a chapter section's data-name, lowercased: ``kap16``, ``kap9a``. A
# roman data-name (``kapVII`` → ``kapvii``) is a valid stamp, though ``_enclosing`` stamps
# none today; a clause naming one finds it only where the outline holds it.
_CHAPTER = r"kap(?:\d+|[ivxlc]+)[a-z]?"
_PART = r"del\d+"

Cause = Literal["enacted", "inserted", "placed"]
_NOWHERE = "?"  # no part stamp has this form, so a chapter missing from the outline never matches

INCOMPLETE = "placement incomplete"
OUTSIDE = "placement outside the grammar"
NO_STRUCTURE = "placement incomplete: no structure map S"
UNSTATED = "placement not completed by the inserting structure"
OUTLINE_CONFLICT = "placement conflicts with the act outline"


class StructureError(LegalTimeError):
    """Structure evidence the replay cannot build ``S`` from: missing or conflicting."""


class Pair(_Validated):
    """A (chapter stamp or empty, part stamp or empty) pair, as the source states it."""

    chapter: str | None = Field(default=None, pattern=_CHAPTER)
    part: str | None = Field(default=None, pattern=_PART)


class PlacementEvent(_Validated):
    """One placement of one identity, with the proof of the sentence or member stating it.

    ``pair`` is None when the event proves no complete pair (``unresolved`` says why).
    A step-0 event carries the minting proof (``effective=None``, ``label_minted_at``).
    """

    cause: Cause
    pair: Pair | None
    proof: LabelProof
    clause: str | None = None  # the placement clause a rename carried (R3)
    unresolved: str | None = None

    @model_validator(mode="after")
    def _sourced(self) -> Self:
        if (self.pair is None) != (self.unresolved is not None):
            raise ValueError("an event either proves a pair or says why it does not")
        if (self.cause == "placed") != (self.clause is not None):
            raise ValueError("only a placement event carries a clause, and it always does")
        if (self.cause == "enacted") != (self.proof.effective is None):
            raise ValueError("only step 0 places without an effective date")
        if self.cause == "placed" and self.pair is None:
            raise ValueError("a placement event that proves no pair is a refusal, not an event")
        return self


class Membership(_Validated):
    """One identity's structure history: minted placement first, then placement events."""

    provision_id: str = Field(min_length=1)
    events: tuple[PlacementEvent, ...] = Field(min_length=1)
    removed: EndProof | None = None

    @property
    def live(self) -> bool:
        return self.removed is None

    @property
    def current(self) -> Pair | None:
        """The current pair; None when unresolved or once the identity is repealed."""
        return self.events[-1].pair if self.live else None

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.events[0].cause == "placed":
            raise ValueError("a membership starts at its minting")
        if any(e.cause != "placed" for e in self.events[1:]):
            raise ValueError("only placement events follow the minting")
        if self.removed is not None and self.removed.ended_by != "repeal":
            raise ValueError("only a repeal removes an identity from the structure")
        return self


class ChapterEntry(_Validated):
    """Outline: ``chapter`` stands in ``part``, as stated by the witness's proof."""

    chapter: str = Field(pattern=_CHAPTER)
    part: str | None = Field(default=None, pattern=_PART)
    witness: str  # the provision_id whose stated pair first registered the chapter
    proof: LabelProof


class Structure(_Validated):
    """``S`` at one step boundary: every identity's membership and the act outline."""

    act: str = Field(pattern=_ACT.pattern)
    memberships: tuple[Membership, ...] = ()
    outline: tuple[ChapterEntry, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        ids = [m.provision_id for m in self.memberships]
        chapters = [e.chapter for e in self.outline]
        if ids != sorted(set(ids)) or chapters != sorted(set(chapters)):
            raise ValueError("memberships and outline are unique and kept in key order")
        parts = {e.chapter: e.part for e in self.outline}
        for pair in (e.pair for m in self.memberships for e in m.events if e.pair is not None):
            if pair.chapter is not None and parts.get(pair.chapter, _NOWHERE) != pair.part:
                raise ValueError(f"a pair the outline does not state: {pair}")
        return self

    def membership(self, provision_id: str) -> Membership | None:
        return next((m for m in self.memberships if m.provision_id == provision_id), None)

    def live_map(self) -> dict[str, Pair | None]:
        """``S``: live provision_id → current pair (None: unresolved)."""
        return {m.provision_id: m.current for m in self.memberships if m.live}

    def enclosing(self, chapter: str) -> ChapterEntry | None:
        return next((e for e in self.outline if e.chapter == chapter), None)

    def parts(self) -> frozenset[str]:
        """Every part the structure has stated, in the outline or in any pair."""
        stated = {e.part for e in self.outline}
        stated |= {e.pair.part for m in self.memberships for e in m.events if e.pair}
        return frozenset(p for p in stated if p is not None)

    def canonical_json(self) -> str:
        """Byte-stable serialization for tests and evidence; not a report schema."""
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )


def _structure(
    act: str, memberships: Iterable[Membership], outline: Iterable[ChapterEntry]
) -> Structure:
    """Build ``S``, turning an inconsistency into the replay's hard failure."""
    try:
        return Structure(
            act=act,
            memberships=tuple(sorted(memberships, key=lambda m: m.provision_id)),
            outline=tuple(sorted(outline, key=lambda e: e.chapter)),
        )
    except ValueError as error:
        raise StructureError(str(error)) from error


# ----------------------------------------------------------------------------- step 0


def stamps_of(document: Document) -> dict[str, Pair]:
    """Section key → (chapter, part) of every provision of an enacting member (``_enclosing``)."""
    stamps: dict[str, Pair] = {}
    for provision in document.provisions:
        key = section_key(provision.label)
        if key is None or key in stamps:
            raise StructureError(f"{document.member}: provision {provision.key!r} has no one key")
        stamps[key] = Pair(chapter=provision.chapter, part=provision.part)
    return stamps


def enacted_structure(ledger: Ledger, stamps: Mapping[str, Pair]) -> Structure:
    """Step 0 (R3): every enacted identity stamped from the enacting member's structure.

    Every step-0 identity needs a stamp and every stamp an identity; a chapter stated
    under two parts is conflicting evidence. Each is a hard failure, never a guess.
    """
    labels = {str(i.label): i for i in ledger.identities if i.live}
    if any(i.intervals[0].start.effective is not None for i in ledger.identities):
        raise StructureError("step 0 stamps enacted identities only")
    if set(labels) != set(stamps):
        missing = sorted(set(labels) ^ set(stamps))
        raise StructureError(f"enacting structure and step 0 disagree on {missing}")
    memberships, registered = [], []
    for label, identity in sorted(labels.items(), key=lambda item: item[1].provision_id):
        proof = identity.intervals[0].start
        event = PlacementEvent(cause="enacted", pair=stamps[label], proof=proof)
        memberships.append(Membership(provision_id=identity.provision_id, events=(event,)))
        registered.append((identity.provision_id, stamps[label], proof))
    return _structure(ledger.act, memberships, _outline((), registered))


def _outline(
    known: Iterable[ChapterEntry], stated: Iterable[tuple[str, Pair, LabelProof]]
) -> list[ChapterEntry]:
    """Register each newly stated chapter once; a chapter under two parts is a conflict.

    The witness is the least provision_id stating the chapter, so document order never
    picks it (I6).
    """
    entries = {e.chapter: e for e in known}
    for witness, pair, proof in sorted(stated, key=lambda item: item[0]):
        chapter = pair.chapter
        if chapter is None:
            continue
        entry = entries.get(chapter)
        if entry is None:
            entries[chapter] = ChapterEntry(
                chapter=chapter, part=pair.part, witness=witness, proof=proof
            )
        elif entry.part != pair.part:
            raise StructureError(f"{chapter} is stated under {entry.part} and {pair.part}")
    return list(entries.values())


# --------------------------------------------------------------- R3 placement grammar

_PART_CLAUSE = re.compile(r"som\s+innleder\s+(?P<word>[a-zæøå]+)\s+del")
_CHAPTER_CLAUSE = re.compile(
    r"under\s+kapittel\s+(?P<number>\d+|[IVXLC]+)(?:\s?(?P<letter>[A-Z]))?"
)


class Clause(_Validated):
    """What R3's closed grammar reads from a placement clause: a part or a chapter."""

    part: str | None = Field(default=None, pattern=_PART)
    chapter: str | None = Field(default=None, pattern=_CHAPTER)


def read_clause(clause: str) -> Clause | None:
    """``som innleder <ordinal> del`` → that part; ``under kapittel N`` → that chapter.

    Nothing else is read: the clause is matched whole, so a clause naming both, a range or
    any extra word is outside the grammar.
    """
    text = " ".join(clause.split())
    part = _PART_CLAUSE.fullmatch(text)
    if part is not None:
        word = part["word"].lower()
        number = ordinal(word) or _PART_WORDS.get(word)
        return None if number is None else Clause(part=f"del{number}")
    chapter = _CHAPTER_CLAUSE.fullmatch(text)
    if chapter is None:
        return None
    return Clause(chapter=f"kap{chapter['number'].lower()}{(chapter['letter'] or '').lower()}")


def placed_pair(structure: Structure, provision_id: str, clause: str) -> Pair | str:
    """R3: the pair a placement clause gives ``provision_id`` in ``S_P``, or the refusal.

    A part-only clause gives (empty, part), only when the current chapter is proven empty
    and the part is one the structure states. A chapter-only clause gives (chapter, the part
    the outline encloses it in). Nothing else; no stamp is kept, picked or inferred.
    """
    read = read_clause(clause)
    if read is None:
        return OUTSIDE
    if read.part is not None:
        current = structure.live_map().get(provision_id)
        if current is None or current.chapter is not None or read.part not in structure.parts():
            return INCOMPLETE
        return Pair(chapter=None, part=read.part)
    entry = structure.enclosing(str(read.chapter))
    return INCOMPLETE if entry is None else Pair(chapter=entry.chapter, part=entry.part)


def inserted_pair(structure: Structure, stated: Pair | None) -> Pair | None | str:
    """The pair an insertion's inserting structure proves; None if unresolved; str refuses.

    A chapter the outline already holds takes the part the outline encloses it in, as a
    chapter-only clause does (R3); a part stated against that is conflicting evidence and
    refuses the insertion. A new chapter with no part stated is complete only in an act
    whose structure states no part at all; otherwise its part is unknown, so unresolved.
    An outline entry with an empty part is step 0's ``_enclosing`` reading (a chapter that
    stands outside every part), a stated fact.
    """
    if stated is None:
        return None
    entry = None if stated.chapter is None else structure.enclosing(stated.chapter)
    if entry is not None:
        if stated.part is None or stated.part == entry.part:
            return Pair(chapter=entry.chapter, part=entry.part)
        return OUTLINE_CONFLICT
    return stated if stated.part is not None or not structure.parts() else None


def outline_conflicts(stated: Iterable[tuple[int, Pair]]) -> set[int]:
    """Relational: insertions of one step stating one new chapter under two parts."""
    by_chapter: defaultdict[str, set[str | None]] = defaultdict(set)
    pairs = [(i, p) for i, p in stated if p.chapter is not None]
    for _, pair in pairs:
        by_chapter[str(pair.chapter)].add(pair.part)
    return {i for i, pair in pairs if len(by_chapter[str(pair.chapter)]) > 1}


# ------------------------------------------------------------------------- S_P → S_Q


class Moves(_Validated):
    """The accepted structure effects of one step, keyed by identity (R3, R4(b))."""

    placed: tuple[tuple[str, Pair, str, LabelProof], ...] = ()  # id, pair, clause, proof
    removed: tuple[tuple[str, EndProof], ...] = ()
    minted: tuple[tuple[str, Pair | None, LabelProof], ...] = ()


def advanced(structure: Structure, moves: Moves) -> Structure:
    """``S_Q``: placements replace pairs, repeals remove, insertions join; history kept."""
    placed = {pid: (pair, clause, proof) for pid, pair, clause, proof in moves.placed}
    removed = dict(moves.removed)
    memberships = [_moved(m, placed, removed) for m in structure.memberships]
    joined = [_joined(pid, pair, proof) for pid, pair, proof in moves.minted]
    stated = [(pid, pair, proof) for pid, pair, proof in moves.minted if pair is not None]
    return _structure(structure.act, [*memberships, *joined], _outline(structure.outline, stated))


def _moved(
    membership: Membership,
    placed: Mapping[str, tuple[Pair, str, LabelProof]],
    removed: Mapping[str, EndProof],
) -> Membership:
    pid = membership.provision_id
    if pid in placed:
        return _replaced(membership, *placed[pid])
    if pid in removed:
        return membership.model_copy(update={"removed": removed[pid]})
    return membership


def _replaced(membership: Membership, pair: Pair, clause: str, proof: LabelProof) -> Membership:
    event = PlacementEvent(cause="placed", pair=pair, proof=proof, clause=clause)
    return membership.model_copy(update={"events": (*membership.events, event)})


def _joined(provision_id: str, pair: Pair | None, proof: LabelProof) -> Membership:
    reason = None if pair is not None else UNSTATED
    event = PlacementEvent(cause="inserted", pair=pair, proof=proof, unresolved=reason)
    return Membership(provision_id=provision_id, events=(event,))


def paired(ledger: Ledger, structure: Structure) -> None:
    """``P`` and ``S`` describe one act and one set of identities, live alike (hard failure)."""
    if ledger.act != structure.act:
        raise StructureError(f"a structure of {structure.act} beside a ledger of {ledger.act}")
    live = {i.provision_id: i.live for i in ledger.identities}
    if live != {m.provision_id: m.live for m in structure.memberships}:
        raise StructureError("the label ledger and the structure map name other identities")
