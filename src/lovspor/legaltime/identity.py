"""Replay-internal provision identity and label history (ADR-0023 Phase A, #861).

A ``provision_id`` names the slot that holds a section label over time (R1). It is a
label-layer identity, never an identity of text existence (ADR-0020 D3a) and never a
norm identity (D13). Each identity carries an immutable list of label intervals with a
start proof and, once ended, an end proof (R2); ``former_keys`` is derived from the
closed intervals. A ``Ledger`` is one act's identities with its live label map; every
ledger holds I1 (one live holder per label), I2 (an id is minted once) and I3 (every
interval sourced).

Phase A builds the domain only. It is not wired into the replay, the report or any
surface, so replay output and ``REPLAY_VERSION`` do not change. ``apply_step`` performs
R4(b)'s simultaneous assignment for operations a caller has already accepted; R4's
two-phase acceptance (intrinsic and relational refusals, components, the one-fate and
ambiguity rules), R6-R9 ordering and resolution, placements, commencement binding and
R10's dated and knowledge-limited queries are Phase B and C. Here any structural
violation refuses the whole step, a stricter answer than R4's per-component refusal.
"""

import json
import re
from collections import Counter
from collections.abc import Iterable
from datetime import date
from typing import Literal, Self

from pydantic import Field, model_validator

from lovspor.legaltime.document import Frozen
from lovspor.legaltime.errors import LegalTimeError

_ACT = re.compile(r"lov/\d{4}-\d{2}-\d{2}(?:-\d+)?")
_MEMBER = re.compile(r"lti/\d{4}/(?:nl|sf)-\d{8}-\d{3,4}\.xml")
_LABEL = re.compile(r"§\d[0-9a-zæøå-]*")  # the engine's section key: ``§101``, ``§15-7``, ``§99a``
_DIGEST = r"^[0-9a-f]{64}$"

EndedBy = Literal["rename", "repeal"]


class IdentityError(LegalTimeError):
    """A label-layer invariant the replay must never cross (I1, I2, I3): a hard failure."""


def _checked(pattern: re.Pattern[str], value: str, what: str) -> str:
    if not pattern.fullmatch(value):
        raise IdentityError(f"not a {what}: {value!r}")
    return value


def enacted_id(act: str, member: str, label: str) -> str:
    """R1: ``<act refid>|enacted:<enacting member path>|<label at enactment>``."""
    parts = (_checked(_ACT, act, "act refid"), _checked(_MEMBER, member, "member path"))
    return f"{parts[0]}|enacted:{parts[1]}|{_checked(_LABEL, label, 'section label')}"


def inserted_id(act: str, member: str, effective: date, label: str) -> str:
    """R1: ``<act refid>|<inserting member path>|<step effective date>|<asserted label>``."""
    parts = (_checked(_ACT, act, "act refid"), _checked(_MEMBER, member, "member path"))
    label = _checked(_LABEL, label, "section label")
    return f"{parts[0]}|{parts[1]}|{effective.isoformat()}|{label}"


class LabelProof(Frozen):
    """The evidence that starts a label interval (R2).

    ``sentence_sha256`` is the engine's digest of the sentence (ADR-0020 D9, the
    ``quote_sha256`` of a ``Proof``), never the evidence file's ``quoted_sentence_sha256``.
    A step-0 proof has ``effective=None`` and ``label_minted_at`` = the enacting member's
    publication date, a label-layer placement and not a commencement; every later proof
    has a proven ``effective`` and no ``label_minted_at``.
    """

    instrument: str
    op_id: str = Field(min_length=1)
    member: str = Field(pattern=_MEMBER.pattern)
    member_sha256: str = Field(pattern=_DIGEST)
    sentence_sha256: str = Field(pattern=_DIGEST)
    effective: date | None
    known_from: date | None
    label_minted_at: date | None = None

    @model_validator(mode="after")
    def _one_placement(self) -> Self:
        if (self.effective is None) == (self.label_minted_at is None):
            raise ValueError("a proof is dated by exactly one of effective or label_minted_at")
        return self


class EndProof(LabelProof):
    """The rename or repeal that ends an interval; always dated (I3)."""

    ended_by: EndedBy

    @model_validator(mode="after")
    def _dated(self) -> Self:
        if self.effective is None:
            raise ValueError("an interval ends only by a dated rename or repeal")
        return self


class FormerKey(Frozen):
    """A label the identity no longer holds, with the proof of what ended it (R2)."""

    label: str
    ended_by: EndedBy
    instrument: str
    op_id: str
    member_sha256: str
    sentence_sha256: str
    effective: date
    known_from: date | None


class LabelInterval(Frozen):
    label: str = Field(pattern=_LABEL.pattern)
    start: LabelProof
    end: EndProof | None = None

    def former_key(self) -> FormerKey | None:
        end = self.end
        if end is None or end.effective is None:
            return None
        return FormerKey(
            label=self.label, ended_by=end.ended_by, instrument=end.instrument,
            op_id=end.op_id, member_sha256=end.member_sha256,
            sentence_sha256=end.sentence_sha256, effective=end.effective,
            known_from=end.known_from,
        )  # fmt: skip


class Identity(Frozen):
    """One provision slot: its id, its text-existence member (D3a) and its label history."""

    provision_id: str
    act: str
    text_member: str
    intervals: tuple[LabelInterval, ...] = Field(min_length=1)

    @property
    def live(self) -> bool:
        return self.intervals[-1].end is None

    @property
    def label(self) -> str | None:
        """The label held now; None once repealed. Never a former label."""
        return self.intervals[-1].label if self.live else None

    @property
    def former_keys(self) -> tuple[FormerKey, ...]:
        """Every closed interval, the repeal-ended last one included (R2)."""
        return tuple(k for k in (i.former_key() for i in self.intervals) if k is not None)

    @model_validator(mode="after")
    def _sourced(self) -> Self:
        _check_history(self.intervals)
        return self


def _check_history(intervals: tuple[LabelInterval, ...]) -> None:
    """I3: only the last interval may be open; a repeal ends history; step 0 starts it."""
    for index, interval in enumerate(intervals):
        last = index == len(intervals) - 1
        if interval.end is None and not last:
            raise ValueError("only the last label interval may be open")
        if interval.end is not None and interval.end.ended_by == "repeal" and not last:
            raise ValueError("a repeal ends the identity: nothing follows it")
        if index > 0 and interval.start.effective is None:
            raise ValueError("only the minting interval may carry a step-0 proof")


class Refusal(Frozen):
    """An operation the label layer did not apply, with its provenance (R1, R5)."""

    op_id: str
    member: str
    sentence_sha256: str
    asserted_label: str | None
    reason: str


class Ledger(Frozen):
    """One act's identities, sorted by id, with I1 and I2 checked on construction."""

    act: str = Field(pattern=_ACT.pattern)
    identities: tuple[Identity, ...] = ()

    @model_validator(mode="after")
    def _invariants(self) -> Self:
        ids = [i.provision_id for i in self.identities]
        if len(set(ids)) != len(ids):
            raise ValueError("I2: a provision_id is held twice")
        if ids != sorted(ids):
            raise ValueError("identities are kept in provision_id order")
        labels = Counter(i.label for i in self.identities if i.live)
        if any(count > 1 for count in labels.values()):
            raise ValueError("I1: a label is held by two live identities")
        if any(i.act != self.act for i in self.identities):
            raise ValueError("an identity of another act")
        return self

    def get(self, provision_id: str) -> Identity | None:
        return next((i for i in self.identities if i.provision_id == provision_id), None)

    def holder(self, label: str) -> Identity | None:
        """The live identity holding ``label`` now; never resolved through former_keys (R9)."""
        return next((i for i in self.identities if i.live and i.label == label), None)

    def live_map(self) -> dict[str, str]:
        """``P``: label → live provision_id."""
        return {str(i.label): i.provision_id for i in self.identities if i.live}

    def canonical_json(self) -> str:
        """Byte-stable serialization for tests and evidence; not a report schema."""
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )


def _ledger(act: str, identities: Iterable[Identity]) -> Ledger:
    """Build a ledger, turning an invariant breach into the replay's hard failure."""
    try:
        return Ledger(act=act, identities=tuple(sorted(identities, key=lambda i: i.provision_id)))
    except ValueError as error:
        raise IdentityError(str(error)) from error


def mint_enacted(
    act: str, member: str, published: date, labels: Iterable[tuple[str, LabelProof]]
) -> Ledger:
    """Step 0 (R1): every enacted provision, minted once, placed at the publication date."""
    identities = []
    for label, proof in labels:
        if proof.effective is not None or proof.label_minted_at != published:
            raise IdentityError(f"step 0 proves no effective date and sits at {published}")
        if proof.member != member:
            raise IdentityError(f"step 0's proof names another member: {proof.member}")
        interval = LabelInterval(label=label, start=proof)
        pid = enacted_id(act, member, label)
        identities.append(Identity(provision_id=pid, act=act, text_member=member,
                                   intervals=(interval,)))  # fmt: skip
    return _ledger(act, identities)


class Rename(Frozen):
    """An accepted rename: the identity ``source`` (resolved in ``P``) takes ``target``."""

    source: str
    target: str = Field(pattern=_LABEL.pattern)
    proof: LabelProof


class Repeal(Frozen):
    label: str = Field(pattern=_LABEL.pattern)
    proof: LabelProof


class Insertion(Frozen):
    """An accepted insertion: mints a new identity under ``label`` (R1, R4(c))."""

    label: str = Field(pattern=_LABEL.pattern)
    proof: LabelProof


class Step(Frozen):
    """One member's operations on one act with one effective date (R4).

    The operations are the ones Phase B's acceptance has already accepted. An undated
    step is carried so that it can be refused with its provenance; it never applies (I7).
    """

    act: str = Field(pattern=_ACT.pattern)
    member: str = Field(pattern=_MEMBER.pattern)
    effective: date | None
    renames: tuple[Rename, ...] = ()
    repeals: tuple[Repeal, ...] = ()
    insertions: tuple[Insertion, ...] = ()

    @model_validator(mode="after")
    def _one_member_one_date(self) -> Self:
        for proof in _proofs(self):
            if proof.member != self.member:
                raise ValueError(f"a proof from another member: {proof.member}")
            if self.effective is not None and proof.effective != self.effective:
                raise ValueError(f"a proof dated {proof.effective}, not {self.effective}")
        return self


class StepResult(Frozen):
    ledger: Ledger
    refusals: tuple[Refusal, ...] = ()


Operation = Rename | Repeal | Insertion


def _proofs(step: Step) -> list[LabelProof]:
    return [op.proof for op in _operations(step)]


def _operations(step: Step) -> list[Operation]:
    return [*step.renames, *step.repeals, *step.insertions]


def _asserted(op: Operation) -> str:
    return op.target if isinstance(op, Rename) else op.label


def _refusal(op: Operation, reason: str) -> Refusal:
    return Refusal(
        op_id=op.proof.op_id, member=op.proof.member,
        sentence_sha256=op.proof.sentence_sha256, asserted_label=_asserted(op), reason=reason,
    )  # fmt: skip


def apply_step(ledger: Ledger, step: Step) -> StepResult:
    """R4(b): build ``Q`` from ``P`` for one step, all at once, or refuse the step whole.

    Every source is read in ``P`` and every target written in ``Q``, so chains and
    cycles need no order. A structural violation (missing source, conflicting renames,
    a label assigned twice in ``Q``, an undated step) refuses every offending operation
    and applies none; the ledger is returned unchanged (I4). Allocating an id that
    already exists is a hard failure (I2).
    """
    if step.act != ledger.act:
        raise IdentityError(f"a step on {step.act} applied to {ledger.act}")
    refusals = _structural(ledger, step)
    if refusals or step.effective is None:
        return StepResult(ledger=ledger, refusals=refusals)
    return StepResult(ledger=_assigned(ledger, step, step.effective))


def _structural(ledger: Ledger, step: Step) -> tuple[Refusal, ...]:
    if step.effective is None:
        return tuple(_refusal(op, "undated step") for op in _operations(step))
    found = [*_sources(ledger, step), *_fates(step)]
    if not found:
        found = _collisions(ledger, step)
    return tuple(sorted(found, key=lambda r: (r.op_id, r.reason)))


def _sources(ledger: Ledger, step: Step) -> list[Refusal]:
    """Every source address resolves in ``P`` (R4(a), R5)."""
    live = set(ledger.live_map().values())
    held = ledger.live_map()
    missing = [_refusal(r, "rename source missing") for r in step.renames if r.source not in live]
    return missing + [
        _refusal(x, "repeal target missing") for x in step.repeals if x.label not in held
    ]


def _fates(step: Step) -> list[Refusal]:
    """Two renames of one source, or a source both renamed and repealed (R5)."""
    sources = Counter(r.source for r in step.renames)
    repealed = Counter(x.label for x in step.repeals)
    found = [_refusal(r, "conflicting renames") for r in step.renames if sources[r.source] > 1]
    return found + [
        _refusal(x, "conflicting operations within instrument")
        for x in step.repeals
        if repealed[x.label] > 1
    ]


def _collisions(ledger: Ledger, step: Step) -> list[Refusal]:
    """R4(e): the labels of ``Q`` — kept, renamed into, inserted — repeat none."""
    held = ledger.live_map()
    moved = {r.source for r in step.renames}
    vacated = {label for label, pid in held.items() if pid in moved}
    vacated |= {x.label for x in step.repeals}
    if moved & {held[x.label] for x in step.repeals}:
        both = [x for x in step.repeals if held[x.label] in moved]
        return [_refusal(x, "conflicting operations within instrument") for x in both]
    kept = [label for label in held if label not in vacated]
    assigned = Counter(
        [*kept, *(r.target for r in step.renames), *(n.label for n in step.insertions)]
    )
    writers: list[Rename | Insertion] = [*step.renames, *step.insertions]
    return [_refusal(op, "label collision") for op in writers if assigned[_asserted(op)] > 1]


def _assigned(ledger: Ledger, step: Step, effective: date) -> Ledger:
    renamed = {r.source: r for r in step.renames}
    repealed = {ledger.live_map()[x.label]: x for x in step.repeals}
    identities = []
    for held in ledger.identities:
        rename, repeal = renamed.get(held.provision_id), repealed.get(held.provision_id)
        updated = held
        if rename is not None:
            updated = _renamed(held, rename)
        elif repeal is not None:
            updated = _closed(held, repeal.proof, "repeal")
        identities.append(updated)
    identities += [_minted(ledger, step.member, effective, n) for n in step.insertions]
    return _ledger(ledger.act, identities)


def _closed(identity: Identity, proof: LabelProof, ended_by: EndedBy) -> Identity:
    end = EndProof(**proof.model_dump(), ended_by=ended_by)
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
