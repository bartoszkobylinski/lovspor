"""Replay-internal provision identity and label history (ADR-0023 Phase A, #861).

A ``provision_id`` names the slot that holds a section label over time (R1). It is a
label-layer identity, never an identity of text existence (ADR-0020 D3a) and never a
norm identity (D13). Each identity carries an immutable list of label intervals with a
start proof and, once ended, an end proof (R2); ``former_keys`` is derived from the
closed intervals. A ``Ledger`` is one act's identities with its live label map; every
ledger holds I1 (one live holder per label), I2 (an id is minted once) and I3 (every
interval sourced).

This module is the identity domain only. One instrument step on a ledger (R4, R5)
is ``lovspor.legaltime.step``. Neither is wired into the replay, the report or any
surface, so replay output and ``REPLAY_VERSION`` do not change.
"""

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from lovspor.legaltime.document import Frozen
from lovspor.legaltime.errors import LegalTimeError

_ACT = re.compile(r"lov/\d{4}-\d{2}-\d{2}(?:-\d+)?")
_MEMBER = re.compile(r"lti/\d{4}/(?:nl|sf)-\d{8}-\d{3,4}\.xml")
# The engine's section key, exactly what ``text.section_key`` returns: ``§101``,
# ``§15-7``, ``§99a``, ``§6A``, ``§9A-1``.
_LABEL = re.compile(r"§\d+[A-Z]?(?:-\d+)?[a-hj-zæøå]?")
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


class _Validated(Frozen):
    """A frozen model whose ``model_copy`` re-validates: no update can skip an invariant.

    Pydantic's own ``model_copy`` copies without validation, which would let an update
    build an identity or ledger that breaks I1-I3.
    """

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        del deep  # every copy is rebuilt from validated data, so depth does not matter
        fields = {name: getattr(self, name) for name in type(self).model_fields}
        return type(self).model_validate({**fields, **(update or {})})


class LabelProof(_Validated):
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


class FormerKey(_Validated):
    """A label the identity no longer holds, with the proof of what ended it (R2)."""

    label: str
    ended_by: EndedBy
    instrument: str
    op_id: str
    member_sha256: str
    sentence_sha256: str
    effective: date
    known_from: date | None


class LabelInterval(_Validated):
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


class Identity(_Validated):
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
        first = self.intervals[0]
        if first.start.member != self.text_member:
            raise ValueError("the minting proof names another member than the text's")
        if self.provision_id != _minting_id(self.act, self.text_member, first):
            raise ValueError(f"not the R1 id of its own minting: {self.provision_id}")
        return self


def _minting_id(act: str, member: str, first: "LabelInterval") -> str:
    """R1, derived from the minting interval itself, so an id can only be its own."""
    try:
        if first.start.effective is None:
            return enacted_id(act, member, first.label)
        return inserted_id(act, member, first.start.effective, first.label)
    except IdentityError as error:
        raise ValueError(str(error)) from error


def _check_history(intervals: tuple[LabelInterval, ...]) -> None:
    """I3: a gap-free, sourced chain: open only at its end, each move on its own proof."""
    for index, interval in enumerate(intervals):
        last = index == len(intervals) - 1
        if interval.end is None and not last:
            raise ValueError("only the last label interval may be open")
        if interval.end is not None and interval.end.ended_by == "repeal" and not last:
            raise ValueError("a repeal ends the identity: nothing follows it")
        ended = interval.end.effective if interval.end is not None else None
        if ended is not None and ended < _start_date(interval):
            raise ValueError("an interval ends before it starts")
        if index > 0:
            _check_link(intervals[index - 1], interval)


def _start_date(interval: LabelInterval) -> date:
    """The start date R10 uses: ``effective``, or ``label_minted_at`` for step 0."""
    start = interval.start
    placed = start.effective if start.effective is not None else start.label_minted_at
    if placed is None:
        # Unreachable: LabelProof proves exactly one date. The pragma must sit on
        # the statement's own line: mutmut 3.8.0 reads it from a single-line
        # statement's trailing comment only (a multi-line call's is ignored).
        raise ValueError("a start proof carries no date")  # pragma: no mutate
    return placed


def _check_link(previous: LabelInterval, interval: LabelInterval) -> None:
    """A later interval starts on the very rename proof that ended the one before (R2)."""
    end = previous.end
    if interval.start.effective is None:
        raise ValueError("only the minting interval may carry a step-0 proof")
    if end is None or end.ended_by != "rename":
        # Unreachable: _check_history refused an open or repeal-ended previous
        # interval at its own index. The branch narrows ``end`` for the checks below.
        raise ValueError("a label interval follows only a rename")  # pragma: no mutate
    if end.model_dump(exclude={"ended_by"}) != interval.start.model_dump():
        raise ValueError("a renamed interval starts on another proof than its rename's")


class Ledger(_Validated):
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
        if (proof.member, proof.instrument, proof.known_from) != (member, act, published):
            raise IdentityError("step 0's proof names another member, act or publication date")
        interval = LabelInterval(label=label, start=proof)
        pid = enacted_id(act, member, label)
        identities.append(Identity(provision_id=pid, act=act, text_member=member,
                                   intervals=(interval,)))  # fmt: skip
    return _ledger(act, identities)
