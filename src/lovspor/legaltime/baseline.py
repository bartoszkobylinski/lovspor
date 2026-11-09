"""When each legal unit of an enacted act took effect (ADR-0020, Decisions 4 to 6).

The act's commencement is a *default* timing plus the units an instrument
dates apart from it: a section, a ledd of a section, or a chapter. The
evidence is the act's ``dateInForce`` header, the commencement decisions
citing the act (full or partial, with exceptions), and a commencement
statement another act writes into this one (``straffelovens
ikraftsettingslov`` replacing § 411 with "Loven trer i kraft 1. oktober
2015."), all folded in publication order with no precedence rule: two
instruments naming different days for one unit leave it ``unresolved``, and
an instrument whose scope cannot be read unsettles every unit it could have
reached. A unit excepted from a date keeps whatever state it had, never the
date it was excepted from.
"""

import re
from datetime import date, datetime
from hashlib import sha256
from typing import Literal

from lovspor.legaltime.commencement import (
    _DEFAULT_CLAUSE,
    _PENDING_WORDS,
    CONFLICT,
    Decisions,
    DecisionsLike,
    Event,
    Proof,
    Timing,
    _citing,
    _conditional,
    _Decision,
    _decisions,
    _settle,
    _unsettled,
    header_timing,
)
from lovspor.legaltime.document import Document, Frozen
from lovspor.legaltime.instructions import Operation
from lovspor.legaltime.scopes import _RESERVATION, _STRAKS, Scope
from lovspor.legaltime.text import parse_date, split_sentences
from lovspor.legaltime.units import Unit

INSTRUMENT_UNDATED = "commencement instrument's own commencement is not proven"
NAMES_NOTHING = "partial decision names no part or section of the law"
PART_UNPLACED = "partial decision names a part that cannot be placed against the act's units"
OUTSIDE = "partial decision outside the grammar"
BELOW_LEDD = "commencement stated below ledd level"
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
_CITED = re.compile(r"\blov\s+\d{1,2}\.\s*\w+\s+\d{4}(?:\s+nr\.\s*\d+)?", re.I)


InForce = Literal["in_force", "not_yet_in_force", "pending", "unresolved"]


class UnitTiming(Frozen):
    """One unit the commencement evidence dates, or leaves open, apart from the act."""

    unit: Unit
    part: str | None = None  # a part qualifier on a chapter (``Lovvedtaket del I kapittel 16``)
    timing: Timing


class Commencement(Frozen):
    """The act's default commencement and every unit dated apart from it."""

    default: Timing
    units: tuple[UnitTiming, ...] = ()

    def section_timing(self, key: str, chapter: str | None, parts: tuple[str, ...]) -> Timing:
        """The timing of one section: its own unit, else its chapter's, else the default.

        ``parts`` are the labels under which the section may be reached by a part qualifier:
        the act's own part data-name (``del1``) and the amending part that inserted it (``I``).
        """
        own = [e.timing for e in self.units if e.unit.section == key and _whole_section(e.unit)]
        if own:
            return own[0]  # the section's own unit, whatever position it holds in the list
        for entry in self.units:
            unit = entry.unit
            in_chapter = unit.chapter is not None and unit.chapter == chapter and not unit.heading
            reached = entry.part is None or _part_name(entry.part) in parts or entry.part in parts
            if in_chapter and reached:
                return entry.timing
        return self.default

    def ledd_timings(self, key: str) -> dict[int, Timing]:
        """Ledd of the section dated apart from the section itself."""
        return {
            e.unit.ledd: e.timing
            for e in self.units
            if e.unit.section == key and e.unit.ledd is not None and _only_ledd(e.unit)
        }

    def finer_than_ledd(self, key: str) -> bool:
        """A punktum, bokstav or heading of the section is dated apart: not modelled, refuse."""
        return any(
            e.unit.section == key and not _whole_section(e.unit) and not _only_ledd(e.unit)
            for e in self.units
        )


def _whole_section(unit: Unit) -> bool:
    return (
        unit.section is not None
        and unit.ledd is None
        and unit.punktum is None
        and unit.bokstav is None
        and not unit.heading
    )


def _only_ledd(unit: Unit) -> bool:
    return unit.punktum is None and unit.bokstav is None and not unit.heading


def _roman(numeral: str) -> int:
    total = 0
    for index, char in enumerate(numeral):
        value = _ROMAN_VALUES[char]
        bigger_follows = index + 1 < len(numeral) and _ROMAN_VALUES[numeral[index + 1]] > value
        total += -value if bigger_follows else value
    return total


def _part_name(roman: str) -> str:
    """``I`` → ``del1``, the data-name form an enacting act's part section carries."""
    return f"del{_roman(roman.upper())}"


class _Statement(Frozen):
    """A commencement statement about the act written by another instrument (Decision 5d)."""

    when: date | None
    known_from: date | None
    published_at: datetime | None
    proof: Proof
    problem: str | None = None


def _payload_sentence(event: Event) -> str | None:
    op = event.operation
    if op.kind in {"replace_ledd", "insert_ledd"} and op.ledd:
        return split_sentences(op.ledd[0].text)[0]
    if op.kind == "replace_section" and op.new_provision is not None and op.new_provision.ledd:
        return split_sentences(op.new_provision.ledd[0].text)[0]
    return None


def _member_sha(event: Event) -> str | None:
    return next(
        (
            p.sha256
            for p in event.timing.proofs
            if p.refid == event.law and p.member == event.member
        ),
        None,
    )


def _statement_proof(event: Event, sentence: str) -> Proof | None:
    digest = _member_sha(event)
    if digest is None:
        return None
    return Proof(
        refid=event.law, member=event.member, sha256=digest,
        quote_sha256=sha256(sentence.encode("utf-8")).hexdigest(),
    )  # fmt: skip


def _statement(event: Event, sentence: str) -> _Statement | None:
    """The statement an operation's payload makes about the act's commencement, if any."""
    if _DEFAULT_CLAUSE.match(sentence) is None or _RESERVATION.search(sentence):
        return None
    if _PENDING_WORDS.search(sentence):
        return None  # a delegation written into the act dates nothing: the act stays as it was
    proof = _statement_proof(event, sentence)
    if proof is None:
        return None
    known = event.published_at.date() if event.published_at else None
    explicit = parse_date(_CITED.sub("", sentence))
    when = event.timing.effective if _STRAKS.search(sentence) and explicit is None else explicit
    problem = None if when is not None else "commencement statement without a readable date"
    if event.timing.status != "dated":
        when, problem = None, INSTRUMENT_UNDATED
    return _Statement(
        when=when, known_from=known, published_at=event.published_at, proof=proof, problem=problem
    )


def instrument_statements(act: str, events: list[Event]) -> list[_Statement]:
    """Every default-shaped commencement sentence an instrument writes into the act's text."""
    found: list[_Statement] = []
    for event in events:
        if event.operation.act != act:
            continue
        sentence = _payload_sentence(event)
        statement = _statement(event, sentence) if sentence is not None else None
        if statement is not None:
            found.append(statement)
    return found


def _unresolved_by(current: Timing, reason: str, proof: Proof) -> Timing:
    return current.model_copy(
        update={"status": "unresolved", "effective": None, "retroactive": False,
                "reason": reason, "proofs": (*current.proofs, proof)}
    )  # fmt: skip


def _fold_statement(current: Timing, statement: _Statement) -> Timing:
    """Fold an instrument's statement like a full decision: cite, date, or conflict."""
    if current.status == "unresolved":
        return current
    if statement.problem is not None or statement.when is None:
        reason = statement.problem or "commencement statement without a readable date"
        return _unresolved_by(current, reason, statement.proof)
    if current.status == "pending":
        known = statement.known_from
        return Timing(
            status="dated", effective=statement.when, known_from=known,
            retroactive=known is not None and statement.when < known,
            proofs=(*current.proofs[1:], statement.proof),
        )  # fmt: skip
    if current.effective == statement.when:
        return current.model_copy(update={"proofs": (*current.proofs, statement.proof)})
    return _unresolved_by(current, CONFLICT, statement.proof)


def _part_only(scope: Scope) -> bool:
    """``Loven del I trer i kraft …``, ``Endringsloven del I § 2 …``: a part, with or without a
    section of that part, and no unit of the act. Whose part — the act's own or the amending
    law's — the sentence does not say, so the fold cannot place it on any unit."""
    return scope.part is not None and not (
        scope.whole or scope.remainder or scope.own_sections or scope.units
    )


class _Fold:
    """The act's default and unit timings, folded instrument by instrument."""

    def __init__(self, act: Document, chapter_of: dict[str, str]) -> None:
        self.act = act
        self.chapter_of = {**chapter_of, **{p.key: p.chapter for p in act.provisions if p.chapter}}
        self.chapters = set(chapter_of.values()) | {
            p.chapter for p in act.provisions if p.chapter is not None
        }
        self.sections = set(chapter_of) | {p.key for p in act.provisions}
        self.default: Timing = header_timing(act)
        self.units: dict[tuple[Unit, str | None], Timing] = {}
        self.excluded: set[tuple[Unit, str | None]] = set()

    def result(self) -> Commencement:
        units = [
            UnitTiming(unit=unit, part=part, timing=self._reconciled(key, timing))
            for key, timing in self.units.items()
            for unit, part in (key,)
        ]
        return Commencement(default=self.default, units=tuple(units))

    def _reconciled(self, key: tuple[Unit, str | None], timing: Timing) -> Timing:
        """A unit every wider statement also reaches must agree with each (Decision 6).

        Wider statements are the act-wide default and, for a section, a dated chapter unit
        that holds the section; an excepted unit is reached by none of them.
        """
        if key in self.excluded or timing.status != "dated":
            return timing
        wider = [self.default] if self.default.status == "dated" else []
        chapter = self.chapter_of.get(key[0].section or "")
        wider.extend(
            t for (unit, _), t in self.units.items()
            if unit.chapter is not None and unit.chapter == chapter and t.status == "dated"
        )  # fmt: skip
        disagreeing = [t for t in wider if t.effective != timing.effective]
        if not disagreeing:
            return timing
        proofs = tuple(p for t in disagreeing for p in t.proofs)
        return timing.model_copy(
            update={"status": "unresolved", "effective": None, "retroactive": False,
                    "reason": CONFLICT, "proofs": (*timing.proofs, *proofs)}
        )  # fmt: skip

    def unsettle_all(self, decision: Document, reason: str) -> None:
        self.default = _unsettled(self.default, decision, reason)
        for key, timing in self.units.items():
            self.units[key] = _unsettled(timing, decision, reason)

    def decision(self, read: _Decision) -> None:
        if read.partial:
            self._partial(read)
        elif read.unreadable is not None:
            self.unsettle_all(read.document, read.unreadable)
        elif read.conditional:
            self.default = _conditional(self.default, read.document)
        else:
            self.default = _settle(self.default, read.full_date, read.document, read.title)

    def _partial(self, read: _Decision) -> None:
        scopes = read.scopes or ()
        if read.unreadable is not None and read.unreadable != NAMES_NOTHING:
            return self.unsettle_all(read.document, read.unreadable)
        if any(_part_only(scope) for scope in scopes):
            return self.unsettle_all(read.document, PART_UNPLACED)
        if not any(self._names_act(scope) for scope in scopes):  # no scope at all names nothing
            return self.unsettle_all(read.document, NAMES_NOTHING)
        for scope in scopes:
            self._scope(read.document, scope)
        return None

    def _names_act(self, scope: Scope) -> bool:
        if scope.whole or scope.remainder:
            return True
        if scope.own_sections:
            return any(key in self.sections for key in scope.own_sections)
        return any(
            (u.section is not None and u.section in self.sections)
            or (u.chapter is not None and u.chapter in self.chapters)
            for u in scope.units
        )

    def _scope(self, decision: Document, scope: Scope) -> None:
        if scope.whole or scope.remainder:
            self.default = _settle(self.default, scope.when, decision, scope.quote)
            for unit in scope.excluded:
                self.excluded.add((unit, None))
                self.units.setdefault((unit, None), self._excepted(decision, scope.quote))
            return
        named = [Unit(section=key) for key in sorted(scope.own_sections)] or list(scope.units)
        for unit in named:
            key = (unit, scope.part)
            base = self.units.get(key, self.default)
            self.units[key] = _settle(base, scope.when, decision, scope.quote)

    def _excepted(self, decision: Document, quote: str) -> Timing:
        """An excepted unit keeps the header's state (pending), with the exception as proof."""
        proof = Proof(
            refid=decision.header.refid, member=decision.member, sha256=decision.sha256,
            quote_sha256=sha256(quote.encode("utf-8")).hexdigest(),
        )  # fmt: skip
        header = header_timing(self.act)
        if header.status == "pending":
            return header.model_copy(update={"proofs": (*header.proofs, proof)})
        return Timing(
            status="unresolved", reason="excepted from the act's commencement",
            known_from=header.known_from, proofs=(*header.proofs, proof),
        )  # fmt: skip

    def statement(self, statement: _Statement) -> None:
        self.default = _fold_statement(self.default, statement)


def _ordered(reads: list[_Decision], statements: list[_Statement]) -> list[_Decision | _Statement]:
    def stamp(item: _Decision | _Statement) -> tuple[datetime, str]:
        if isinstance(item, _Decision):
            return (item.document.header.published_at or datetime.min, item.document.member)
        return (item.published_at or datetime.min, item.proof.member)

    return sorted([*reads, *statements], key=stamp)


def baseline_commencement(
    act: Document, decisions: DecisionsLike, events: list[Event]
) -> Commencement:
    """The act's commencement per unit, from its header, its decisions and other instruments.

    A dated or unreadable header is the whole answer, as for ``baseline_timing``; a
    delegated header (``Kongen bestemmer``) is folded with every instrument in reach.
    """
    context: Decisions = _decisions(decisions)
    fold = _Fold(act, context.chapter_of)
    if fold.default.status != "pending":
        return fold.result()
    statements = instrument_statements(act.header.refid, events)
    for item in _ordered(_citing(act, decisions), statements):
        if fold.default.status == "unresolved":  # type: ignore[comparison-overlap]
            break
        if isinstance(item, _Decision):
            fold.decision(item)
        else:
            fold.statement(item)
    return fold.result()


# ----------------------------------------------------------- the state of a unit at D


class UnitState(Frozen):
    """What the commencement evidence says about one section at D."""

    in_force: InForce
    effective: date | None
    flagged: tuple[int, ...]  # ledd not in force at D
    problems: tuple[str, ...]


def _in_force(timing: Timing, at: date) -> tuple[InForce, date | None]:
    if timing.status == "dated" and timing.effective is not None:
        return ("in_force" if timing.effective <= at else "not_yet_in_force"), timing.effective
    return ("pending" if timing.status == "pending" else "unresolved"), None


def unit_state(
    commencement: Commencement, key: str, where: tuple[str | None, tuple[str, ...]], at: date
) -> UnitState:
    """The section's legal effectiveness at D, and its ledd dated apart not yet in force."""
    chapter, parts = where
    timing = commencement.section_timing(key, chapter, parts)
    in_force, effective = _in_force(timing, at)
    problems = [f"commencement {timing.reason}"] if in_force == "unresolved" else []
    if commencement.finer_than_ledd(key):
        in_force, problems = "unresolved", [*problems, f"commencement {BELOW_LEDD}"]
    flagged: list[int] = []
    for index, ledd_timing in sorted(commencement.ledd_timings(key).items()):
        state, _ = _in_force(ledd_timing, at)
        if state == "unresolved":
            problems.append(f"ledd {index} commencement {ledd_timing.reason}")
        elif state != "in_force":
            flagged.append(index)
    return UnitState(
        in_force=in_force, effective=effective, flagged=tuple(flagged), problems=tuple(problems)
    )


def addresses_flagged(op: Operation, flagged: tuple[int, ...], count: int) -> bool:
    """The operation names a ledd that exists and is not in force at D."""
    start = op.address.ledd
    if start is None:
        return False
    end = op.address.ledd_end or start
    return any(start <= index <= end for index in flagged if index <= count)


def restructures(op: Operation, flagged: tuple[int, ...], count: int) -> bool:
    """The operation would move a ledd that is flagged not in force out of its number."""
    present = [index for index in flagged if index <= count]
    if not present:
        return False
    start = op.address.ledd
    if op.kind in {"insert_ledd", "repeal_ledd", "move_ledd"}:
        return start is None or start <= max(present)
    if op.kind == "replace_section":
        return op.new_provision is None or len(op.new_provision.ledd) != count
    return False


def state_gate(unit: UnitState) -> str | None:
    """``withheld`` when the unit is not in force at D; its problems when unresolved; else None."""
    if unit.in_force == "unresolved":
        return "; ".join(unit.problems)
    return None if unit.in_force == "in_force" else "withheld"
