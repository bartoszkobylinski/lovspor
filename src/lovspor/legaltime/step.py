"""One instrument step on one act's label ledger (ADR-0023 R4, R5; Phase B1, #861).

A step is one member's section-level operations on one act with one effective date,
already attributed and typed by the caller. ``apply_step`` decides which operations
are accepted, in R4's two phases, and then builds ``Q`` from ``P`` with the accepted
ones only:

1. **Intrinsic.** Each operation is checked alone against ``P``: an undated step, a
   partial or no-op rename, a missing source address, a replacement not headed by the
   rename's target. A refused operation claims no label, vacates none and joins no
   component.
2. **Relational.** Over the survivors: conflicting renames, the one-fate rule, label
   collisions (R4(e)) and the ambiguity rule. An operation in a relational conflict is
   refused with every rename and insertion of its R5 component, and the checks run
   again over what remains, until nothing more is refused. A refused operation is never
   reconsidered.

The accepted renames, repeals and insertions then move, close and mint identities all
at once (R4(b)). Replacements of whole sections and finer (ledd, punktum, bokstav,
heading) operations take part only in acceptance: their text effects are the replay's
(ADR-0020 D4, D7), not this module's.

**Structure (Phase B3).** Given the structure map ``S_P``
(``lovspor.legaltime.structure``), the intrinsic phase also checks a rename's placement
clause and an insertion's stated pair against ``S_P`` and the outline before the step; a
refusal there refuses the whole operation and creates no edge. Two placements reaching
one identity, and two insertions stating one new chapter under two parts, are relational
conflicts. ``S_Q`` is built beside ``Q`` from the same accepted operations. Without
``S_P`` a rename carrying a placement clause is refused, never accepted unplaced. The step
order (R6, R8) and the replay wiring are not here.
"""

from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date
from typing import Literal, Self

from pydantic import Field, model_validator

from lovspor.legaltime.identity import (
    _ACT,
    _DIGEST,
    _LABEL,
    _MEMBER,
    EndedBy,
    EndProof,
    Identity,
    IdentityError,
    LabelInterval,
    LabelProof,
    Ledger,
    _ledger,
    _Validated,
    inserted_id,
)
from lovspor.legaltime.structure import (
    NO_STRUCTURE,
    Moves,
    Pair,
    Structure,
    advanced,
    inserted_pair,
    outline_conflicts,
    paired,
    placed_pair,
)

FinerKind = Literal["ledd", "punktum", "bokstav", "heading"]
Phase = Literal["intrinsic", "relational"]


class Replacement(_Validated):
    """The text a rename carries (R3): validated here, applied by the replay's text layer.

    ``headed`` is the section key the payload is headed by, or None when no heading
    parsed. A replacement headed by anything but the rename's target refuses the rename.
    """

    headed: str | None
    payload_sha256: str = Field(pattern=_DIGEST)


class Rename(_Validated):
    """``§ source blir § target`` (R3), addressed by label in ``P``."""

    source: str = Field(pattern=_LABEL.pattern)
    target: str = Field(pattern=_LABEL.pattern)
    proof: LabelProof
    replacement: Replacement | None = None
    partial: bool = False  # "De delene av § X … som ikke allerede er satt i kraft"
    placement: str | None = None  # the clause as the source states it (R3)


class Repeal(_Validated):
    label: str = Field(pattern=_LABEL.pattern)
    proof: LabelProof


class Replace(_Validated):
    """A replacement of a whole section: a whole-section operation with no label effect."""

    label: str = Field(pattern=_LABEL.pattern)
    proof: LabelProof
    payload_sha256: str = Field(pattern=_DIGEST)


class Finer(_Validated):
    """A ledd, punktum, bokstav or heading operation on one section (D7 keeps its order)."""

    label: str = Field(pattern=_LABEL.pattern)
    kind: FinerKind
    proof: LabelProof


class Insertion(_Validated):
    """Mints a new identity under ``label`` once the step accepts it (R1, R4(c)).

    ``placement`` is the pair the inserting structure stamps (``instructions._placed``);
    None when it stamps none, which leaves the new identity's pair unresolved.
    """

    label: str = Field(pattern=_LABEL.pattern)
    proof: LabelProof
    placement: Pair | None = None


Operation = Rename | Repeal | Replace | Finer | Insertion


class Step(_Validated):
    """One member's operations on one act with one effective date (R4).

    An undated step is carried so that each operation is refused with its provenance;
    it never applies (I7).
    """

    act: str = Field(pattern=_ACT.pattern)
    member: str = Field(pattern=_MEMBER.pattern)
    effective: date | None
    renames: tuple[Rename, ...] = ()
    repeals: tuple[Repeal, ...] = ()
    replaces: tuple[Replace, ...] = ()
    finer: tuple[Finer, ...] = ()
    insertions: tuple[Insertion, ...] = ()

    @model_validator(mode="after")
    def _one_member_one_date(self) -> Self:
        for op in _operations(self):
            if op.proof.member != self.member:
                raise ValueError(f"a proof from another member: {op.proof.member}")
            if self.effective is not None and op.proof.effective != self.effective:
                raise ValueError(f"a proof dated {op.proof.effective}, not {self.effective}")
        return self


class Refusal(_Validated):
    """An operation the step did not accept, with its provenance and reason (R1, R5)."""

    op_id: str
    kind: str
    member: str
    sentence_sha256: str
    address: str | None  # the label the operation reads in ``P``
    asserted_label: str | None  # the label it would write in ``Q``
    phase: Phase
    reason: str


class StepResult(_Validated):
    ledger: Ledger
    structure: Structure | None = None  # ``S_Q``, when the step was given ``S_P``
    accepted: tuple[str, ...] = ()  # op ids, sorted
    refusals: tuple[Refusal, ...] = ()


def _operations(step: Step) -> list[Operation]:
    return [*step.renames, *step.repeals, *step.replaces, *step.finer, *step.insertions]


def _kind(op: Operation) -> str:
    return op.kind if isinstance(op, Finer) else type(op).__name__.lower()


def _address(op: Operation) -> str | None:
    """The label an operation reads in ``P``; an insertion reads none."""
    if isinstance(op, Rename):
        return op.source
    return None if isinstance(op, Insertion) else op.label


def _asserted(op: Operation) -> str | None:
    """The label an operation writes in ``Q``: a rename's target or an insertion's label."""
    if isinstance(op, Rename):
        return op.target
    return op.label if isinstance(op, Insertion) else None


def _refusal(op: Operation, phase: Phase, reason: str) -> Refusal:
    return Refusal(
        op_id=op.proof.op_id, kind=_kind(op), member=op.proof.member,
        sentence_sha256=op.proof.sentence_sha256, address=_address(op),
        asserted_label=_asserted(op), phase=phase, reason=reason,
    )  # fmt: skip


# ------------------------------------------------------------------ phase 1: intrinsic


def _intrinsic(op: Operation, held: dict[str, str], structure: Structure | None) -> str | None:
    """R4 phase 1: what is wrong with ``op`` alone, against ``P`` and ``S_P``; None if nothing."""
    if isinstance(op, Rename):
        return _rename_flaw(op, held) or _placement_flaw(op, held, structure)
    if isinstance(op, Insertion) and structure is not None:
        stated = inserted_pair(structure, op.placement)
        return stated if isinstance(stated, str) else None
    address = _address(op)
    return None if address is None or address in held else "target provision missing"


def _placement_flaw(
    rename: Rename, held: dict[str, str], structure: Structure | None
) -> str | None:
    """R3: a placement clause must give a complete pair in ``S_P``, or the rename is refused."""
    if rename.placement is None:
        return None
    if structure is None:
        return NO_STRUCTURE
    pair = placed_pair(structure, held[rename.source], rename.placement)
    return pair if isinstance(pair, str) else None


def _rename_flaw(rename: Rename, held: dict[str, str]) -> str | None:
    if rename.partial:
        return "partial rename: extent not in the source"
    if rename.source == rename.target:
        return "rename to the same label"
    if rename.source not in held:
        return "rename source missing"
    replacement = rename.replacement
    if replacement is not None and replacement.headed != rename.target:
        return "replacement not headed by the target label"
    return None


# ---------------------------------------------------------------- phase 2: relational

Check = Callable[[list[Operation], dict[str, str]], dict[int, str]]


def _vacated(ops: list[Operation]) -> set[str]:
    """``V``: the labels the surviving renames and repeals vacate."""
    return {op.source if isinstance(op, Rename) else op.label for op in ops if _vacates(op)}


def _vacates(op: Operation) -> bool:
    return isinstance(op, Rename | Repeal)


def _fates(ops: list[Operation], held: dict[str, str]) -> dict[int, str]:
    """One fate per pre-step identity, and two renames of one source (R5)."""
    reaching: defaultdict[str, list[int]] = defaultdict(list)
    for index, op in enumerate(ops):
        address = _address(op)
        if address is not None:
            reaching[held[address]].append(index)
    found: dict[int, str] = {}
    for group in reaching.values():
        whole = [i for i in group if not isinstance(ops[i], Finer)]
        if len(whole) > 1 or (whole and len(group) > len(whole)):
            found.update(dict.fromkeys(group, _CONFLICT[_renames_only(ops, group)]))
    return found


def _renames_only(ops: list[Operation], group: list[int]) -> bool:
    """Two plain renames of one source; two placements of one identity are not (R3)."""
    renames = [op for i in group if isinstance(op := ops[i], Rename)]
    placed = [r for r in renames if r.placement is not None]
    return len(renames) == len(group) and len(placed) <= 1


_CONFLICT = {True: "conflicting renames", False: "conflicting operations within instrument"}


def _collisions(ops: list[Operation], held: dict[str, str]) -> dict[int, str]:
    """R4(e): a label written twice in ``Q``, or over a holder the step keeps."""
    kept = set(held) - _vacated(ops)
    writes = [(index, _asserted(op)) for index, op in enumerate(ops)]
    count = Counter(label for _, label in writes if label is not None)
    return {
        index: "label collision"
        for index, label in writes
        if label is not None and (count[label] > 1 or label in kept)
    }


def _ambiguous(ops: list[Operation], held: dict[str, str]) -> dict[int, str]:
    """A text or refill-less operation on a label the step renames from or to (R4).

    Exempt: a rename itself, an insertion that fills a vacated label, and a repeal. A
    repeal reads ``P`` and vacates, as a rename-out does, so "§ 19 oppheves" beside a
    rename into § 19 is R5's valid repeal-and-refill; a repeal of a rename's source is
    already the one-fate conflict.
    """
    del held
    renamed = {label for op in ops if isinstance(op, Rename) for label in (op.source, op.target)}
    vacated = _vacated(ops)
    found: dict[int, str] = {}
    for index, op in enumerate(ops):
        if isinstance(op, Rename | Repeal):
            continue
        refill = isinstance(op, Insertion) and op.label in vacated
        if op.label in renamed and not refill:
            found[index] = "label ambiguous within instrument"
    return found


def _outlines(ops: list[Operation], held: dict[str, str]) -> dict[int, str]:
    """Insertions of the step stating one new chapter under two parts (B3)."""
    del held
    stated = [(i, op.placement) for i, op in enumerate(ops) if isinstance(op, Insertion)]
    hit = outline_conflicts((i, pair) for i, pair in stated if pair is not None)
    return dict.fromkeys(sorted(hit), "conflicting placements within instrument")


_CHECKS: tuple[Check, ...] = (_fates, _collisions, _ambiguous, _outlines)


def _relational(ops: list[Operation], held: dict[str, str]) -> dict[int, str]:
    """One round of R4 phase 2: each survivor in a conflict, with its first reason."""
    found: dict[int, str] = {}
    for check in _CHECKS:
        for index, reason in sorted(check(ops, held).items()):
            found.setdefault(index, reason)
    return found


def _roots(ops: list[Operation]) -> dict[int, str]:
    """R5's components: each rename and insertion keyed by its component's least label.

    The graph is on labels, with an edge ``s—t`` per rename. An insertion is the node of
    its own label, so a refill of a rename source joins that rename's component.
    """
    parent: dict[str, str] = {}
    for op in ops:
        if isinstance(op, Rename):
            a, b = _find(parent, op.source), _find(parent, op.target)
            parent[max(a, b)] = min(a, b)
    return {i: _find(parent, label) for i, op in enumerate(ops) if (label := _node(op))}


def _node(op: Operation) -> str | None:
    """The label node a rename (its source) or an insertion (its label) sits on."""
    if isinstance(op, Rename):
        return op.source
    return op.label if isinstance(op, Insertion) else None


def _find(parent: dict[str, str], label: str) -> str:
    while parent.get(label, label) != label:
        label = parent[label]
    return label


def _spread(ops: list[Operation], found: dict[int, str]) -> dict[int, str]:
    """A relational refusal refuses every rename and insertion of its component (R5)."""
    roots = _roots(ops)
    hit = {roots[index] for index in found if index in roots}
    spread = {index: "refused with its component" for index, root in roots.items() if root in hit}
    return {**spread, **found}


def _accept(
    step: Step, held: dict[str, str], structure: Structure | None
) -> tuple[list[Operation], list[Refusal]]:
    """R4's two phases: the accepted operations and every refusal, order-free."""
    if step.effective is None:
        return [], [_refusal(op, "intrinsic", "undated step") for op in _operations(step)]
    survivors: list[Operation] = []
    refusals: list[Refusal] = []
    for op in _operations(step):
        reason = _intrinsic(op, held, structure)
        if reason is None:
            survivors.append(op)
        else:
            refusals.append(_refusal(op, "intrinsic", reason))
    while found := _spread(survivors, _relational(survivors, held)):
        refusals += [_refusal(survivors[i], "relational", r) for i, r in found.items()]
        survivors = [op for index, op in enumerate(survivors) if index not in found]
    return survivors, refusals


# ------------------------------------------------------------------------- assignment


def apply_step(ledger: Ledger, step: Step, structure: Structure | None = None) -> StepResult:
    """R4: accept in two phases, then build ``Q`` (and ``S_Q``) with accepted operations only.

    Every source is read in ``P`` and every target written in ``Q``, so chains, cycles,
    rename-out/refill and repeal/refill need no order. A refused operation changes
    nothing: no label, no pair, no history. Minting an id that exists, or a ``Q`` that
    breaks I1, is a hard failure: it means acceptance let a conflict through, and nothing
    is repaired. ``structure`` is ``S_P``; without it no placement is ever accepted.
    """
    if step.act != ledger.act:
        raise IdentityError(f"a step on {step.act} applied to {ledger.act}")
    if structure is not None:
        paired(ledger, structure)
    accepted, refusals = _accept(step, ledger.live_map(), structure)
    ordered = tuple(sorted(refusals, key=lambda r: (r.op_id, r.kind, r.reason, str(r.address))))
    if step.effective is None or not accepted:
        return StepResult(ledger=ledger, structure=structure, refusals=ordered)
    return StepResult(
        ledger=_assigned(ledger, step, step.effective, accepted),
        structure=_restructured(ledger, structure, step, accepted),
        accepted=tuple(sorted(op.proof.op_id for op in accepted)),
        refusals=ordered,
    )


def _restructured(
    ledger: Ledger, structure: Structure | None, step: Step, accepted: list[Operation]
) -> Structure | None:
    """``S_Q`` from ``S_P`` and the very operations ``Q`` was built from (R3, R4)."""
    if structure is None or step.effective is None:
        return structure
    held = ledger.live_map()
    placed = [
        (held[op.source], placed_pair(structure, held[op.source], op.placement), op.placement,
         op.proof)
        for op in accepted if isinstance(op, Rename) and op.placement is not None
    ]  # fmt: skip
    removed = [
        (held[op.label], _end(op.proof, "repeal")) for op in accepted if isinstance(op, Repeal)
    ]
    minted = [
        (inserted_id(ledger.act, step.member, step.effective, op.label),
         inserted_pair(structure, op.placement), op.proof)
        for op in accepted if isinstance(op, Insertion)
    ]  # fmt: skip
    moves = Moves.model_validate({"placed": placed, "removed": removed, "minted": minted})
    return advanced(structure, moves)


def _assigned(ledger: Ledger, step: Step, effective: date, accepted: list[Operation]) -> Ledger:
    held = ledger.live_map()
    renamed = {held[op.source]: op for op in accepted if isinstance(op, Rename)}
    repealed = {held[op.label]: op for op in accepted if isinstance(op, Repeal)}
    identities = []
    for identity in ledger.identities:
        rename, repeal = renamed.get(identity.provision_id), repealed.get(identity.provision_id)
        updated = identity
        if rename is not None:
            updated = _renamed(identity, rename)
        elif repeal is not None:
            updated = _closed(identity, repeal.proof, "repeal")
        identities.append(updated)
    minted = [op for op in accepted if isinstance(op, Insertion)]
    identities += [_minted(ledger, step.member, effective, op) for op in minted]
    return _ledger(ledger.act, identities)


def _end(proof: LabelProof, ended_by: EndedBy) -> EndProof:
    return EndProof(**proof.model_dump(), ended_by=ended_by)


def _closed(identity: Identity, proof: LabelProof, ended_by: EndedBy) -> Identity:
    end = _end(proof, ended_by)
    closed = identity.intervals[-1].model_copy(update={"end": end})
    return identity.model_copy(update={"intervals": (*identity.intervals[:-1], closed)})


def _renamed(identity: Identity, rename: Rename) -> Identity:
    """Close the held label and open the target, both on the rename's own proof (R2)."""
    closed = _closed(identity, rename.proof, "rename")
    opened = LabelInterval(label=rename.target, start=rename.proof)
    return closed.model_copy(update={"intervals": (*closed.intervals, opened)})


def _minted(ledger: Ledger, member: str, effective: date, insertion: Insertion) -> Identity:
    """R1 allocation after the step is accepted; an id that exists is a hard failure (I2)."""
    pid = inserted_id(ledger.act, member, effective, insertion.label)
    if ledger.get(pid) is not None:
        raise IdentityError(f"I2: {pid} would be minted twice")
    interval = LabelInterval(label=insertion.label, start=insertion.proof)
    return Identity(provision_id=pid, act=ledger.act, text_member=member,
                    intervals=(interval,))  # fmt: skip
