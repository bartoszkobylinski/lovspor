"""The promotion decision log (ADR-0016 4b-4d): append-only, beside the archive.

``promotions.jsonl`` lives in the observatory root next to
``observations.jsonl`` — outside the engine repository and outside
``lovverk`` (ADR-0010 §5) — and is opened only for append. It holds three
record kinds, discriminated by ``kind``:

* ``decision`` — a human act on one artifact: ``approve``, ``reject`` or
  ``hold``, with who decided, in what role, when and why. ``decided_by`` is a
  person; a machine name is refused, because a decision the log attributes to
  a human must have been one (ADR-0016 4c). An ``approve`` names the content
  hash and extractor version of the text the reviewer read, so a later run
  cannot promote a different text under it.
* ``promoted`` — what a promotion run wrote, with its audit record (4d).

The reviewer's name lives here and nowhere else. The audit record published
into ``lovverk`` names the reviewer by ``reviewed_by_role`` — ``lovverk`` is
public and its history is never rewritten (ADR-0003), so a personal name
written there could never be taken back (owner decision on PR #517,
2026-10-03). Every field that is published — the role, the reason and the
classifier evidence — is refused when it carries the reviewer's name.
* ``held`` — a promotion run that stopped at a hold, with the stage and the
  reason (4h). Holds are recorded, never dropped.
* ``migrated`` — a migration run (4e, ``lovspor promote migrate``) that moved
  one promoted document's version metadata to the running extractor, the
  text being byte-identical; it names the approval it rests on.
* ``carried`` — a migration run that carried the owner's standing approval of
  one version to the running extractor because the extractor bump left its
  published rendering byte-identical (owner decision, 2026-10-08). It is not a
  human act and does not read as one: it names the approval it rests on (when
  it was given, at which extractor, in which role) and never a person, so
  nothing in the log or the corpus says anyone reviewed the text at the new
  extractor. Any byte difference still needs a fresh ``decision``.
* ``withdrawal`` — a human act on one promoted document (4f): its id, the
  closed-set ``removed_reason``, and every archived artifact it was promoted
  from, so no later run promotes the document again — under its id or from
  any of those artifacts, in any checkout. Signed like a ``decision``.

A run that would append an outcome identical to the artifact's last one
(apart from ``recorded_at``) appends nothing, so a rerun leaves the log as it
found it.
"""

from __future__ import annotations

import fcntl
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    ValidationError,
    model_serializer,
    model_validator,
)

from lovspor.errors import DecisionLogError, StorageBoundaryError
from lovspor.observatory.fields import TrimmedNonBlankStr
from lovspor.observatory.storage import ObservatoryRoot
from lovspor.promotion.models import IdScheme, PersonalDataHit, RemovedReason
from lovspor.promotion.personal_data import screen_personal_data

DECISIONS_FILENAME = "promotions.jsonl"

#: Names a pipeline might sign with. A decision under one of them is not a
#: human's, and the log must not say it was.
MACHINE_NAMES = frozenset({"classifier", "lovspor", "promotion", "auto", "automatic", "system"})

_SHA256 = r"^[0-9a-f]{64}$"
_AUTHORITY_ID = r"^(?:\d{2}|\d{4})$"

#: The one basis on which a migration carries an approval (owner decision, 2026-10-08).
CarryBasis = Literal["byte-identical rendering"]
CARRY_BASIS: CarryBasis = "byte-identical rendering"


def _a_person(value: str) -> str:
    if value.casefold() in MACHINE_NAMES:
        msg = f"decided_by must name a person, not {value!r} (ADR-0016 4c)"
        raise ValueError(msg)
    return value


PersonName = Annotated[TrimmedNonBlankStr, AfterValidator(_a_person)]

ROLE_REQUIRED = (
    "reviewer_role is required: the published audit names the reviewer by role, never by name"
)
_WORD = re.compile(r"\w{2,}")


def _a_role(value: str) -> str:
    if value.casefold() in MACHINE_NAMES:
        msg = f"reviewer_role must name a human role, not {value!r} (ADR-0016 4c)"
        raise ValueError(msg)
    if screen_personal_data(value):
        msg = "reviewer_role carries personal data; it is published with the audit record"
        raise ValueError(msg)
    return value


ReviewerRole = Annotated[TrimmedNonBlankStr, AfterValidator(_a_role)]


def _refuse_the_name(decided_by: str, published: dict[str, str]) -> None:
    """Refuse a published field sharing a word with the reviewer's name."""
    name = set(_WORD.findall(decided_by.casefold()))
    for field, text in published.items():
        if name & set(_WORD.findall(text.casefold())):
            msg = f"{field} carries the reviewer's name; the corpus is given the role, not the name"
            raise ValueError(msg)


def utc_text(moment: datetime) -> str:
    """An aware instant as ``YYYY-MM-DDTHH:MM:SSZ`` — the one spelling every file uses."""
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    HOLD = "hold"


class ArtifactKey(BaseModel):
    """One archived artifact: the bytes, where they were served, and by whom."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority_id: str = Field(pattern=_AUTHORITY_ID)
    sha256: str = Field(pattern=_SHA256)
    source_url: str = Field(pattern=r"^https?://\S+$")


class ClassifierEvidence(BaseModel):
    """What a classifier said about the artifact, as the reviewer supplied it.

    Optional: the classifier is a parallel workstream (ADR-0016 Out of
    Scope), and an approval made without its output says so by omitting this.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    classifier_version: TrimmedNonBlankStr
    class_name: TrimmedNonBlankStr
    evidence: tuple[TrimmedNonBlankStr, ...] = ()

    def published(self) -> dict[str, str]:
        """Each field as the audit record publishes it, by its path in the decision."""
        fields = {
            "classifier.classifier_version": self.classifier_version,
            "classifier.class_name": self.class_name,
        }
        return fields | {f"classifier.evidence[{i}]": e for i, e in enumerate(self.evidence)}


def _published(role: str, reason: str, classifier: ClassifierEvidence | None) -> dict[str, str]:
    fields = {"reviewer_role": role, "reason": reason}
    return fields | (classifier.published() if classifier is not None else {})


class DecisionDocument(BaseModel):
    """The reviewer's decision as the operator hands it over: a JSON file.

    A document rather than flags, as with the access-policy check: a
    conclusion typed into a shell leaves nothing to re-read.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Decision
    decided_by: PersonName
    reviewer_role: ReviewerRole
    reason: TrimmedNonBlankStr
    classifier: ClassifierEvidence | None = None

    @model_validator(mode="before")
    @classmethod
    def _names_a_role(cls, data: object) -> object:
        if isinstance(data, dict) and "reviewer_role" not in data:
            raise ValueError(ROLE_REQUIRED)
        return data

    @model_validator(mode="after")
    def _keeps_the_name_private(self) -> DecisionDocument:
        _refuse_the_name(self.decided_by, self.published())
        return self

    def published(self) -> dict[str, str]:
        """The fields the audit record publishes into ``lovverk``, by their path here."""
        return _published(self.reviewer_role, self.reason, self.classifier)


class HumanDecision(BaseModel):
    """A recorded human decision on one artifact (ADR-0016 4c)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["decision"] = "decision"
    artifact: ArtifactKey
    decision: Decision
    decided_by: PersonName
    reviewer_role: ReviewerRole
    decided_at: AwareDatetime
    reason: TrimmedNonBlankStr
    content_hash: str | None = Field(default=None, pattern=_SHA256)
    extractor_version: int | None = None
    classifier: ClassifierEvidence | None = None

    @model_validator(mode="after")
    def _keeps_the_name_private(self) -> HumanDecision:
        published = _published(self.reviewer_role, self.reason, self.classifier)
        _refuse_the_name(self.decided_by, published)
        return self

    @model_validator(mode="after")
    def _approval_names_the_text(self) -> HumanDecision:
        if self.decision is Decision.APPROVE and (
            self.content_hash is None or self.extractor_version is None
        ):
            msg = "an approval must name the content_hash and extractor_version it read"
            raise ValueError(msg)
        return self


class IdentityAudit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scheme: IdScheme
    doc_id: str
    ref_id: str | None
    candidates: tuple[str, ...]


class ObservationUsed(BaseModel):
    """One archive record the promoted version was read from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observed_at: str
    url: str
    sha256: str


class ApprovalCarried(BaseModel):
    """In a published audit: the approval was given at one extractor and carried to another.

    ``decided_at`` and ``reviewed_by_role`` of the audit stay the human
    approval's; this says the review happened at ``approved_at_extractor`` and
    a migration carried it to ``carried_to_extractor`` at ``carried_at``,
    because the rendering stayed byte-identical (owner decision, 2026-10-08).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    approved_at_extractor: int
    carried_to_extractor: int
    carried_at: str
    basis: CarryBasis = CARRY_BASIS


class PromotionAudit(BaseModel):
    """Why a version was promoted (ADR-0016 4d): the versions, the evidence, the human's role.

    Published into ``lovverk``, so it names the reviewer by role only; the
    name stays in the decision log's ``decision`` record. ``extractor_version``
    is the extractor the published bytes are current for: the one that read
    the text at promotion, or the one a migration moved it to. Whether a human
    approved the text at that extractor is told by ``approval_carried``: absent,
    the approval was given there; present, it was given at
    ``approved_at_extractor`` and carried. It is written only when present, so
    an audit without it keeps its bytes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Decision
    reviewed_by_role: str
    decided_at: str
    reason: str
    reviewed_in_sample: bool
    classifier: ClassifierEvidence | None
    extractor_version: int
    renderer_version: int
    source_form: str
    identity: IdentityAudit
    observations_through: str
    observations: tuple[ObservationUsed, ...]
    approval_carried: ApprovalCarried | None = None

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        payload: dict[str, Any] = handler(self)
        if self.approval_carried is None:
            payload.pop("approval_carried", None)
        return payload


class PromotedRecord(BaseModel):
    """A promotion run that wrote one version into a corpus checkout."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["promoted"] = "promoted"
    artifact: ArtifactKey
    recorded_at: AwareDatetime
    doc_id: str
    version: int
    markdown_path: str
    content_hash: str
    commit_subject: str
    audit: PromotionAudit


class HeldRecord(BaseModel):
    """A promotion run that stopped at a hold; nothing was written to the corpus."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["held"] = "held"
    artifact: ArtifactKey
    recorded_at: AwareDatetime
    stage: Literal["extraction", "identity"]
    reason: str
    detail: str
    personal_data: tuple[PersonalDataHit, ...] = ()


class MigratedRecord(BaseModel):
    """A migration run that moved one document to the running extractor (ADR-0016 4e)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["migrated"] = "migrated"
    artifact: ArtifactKey
    recorded_at: AwareDatetime
    doc_id: str
    version: int
    content_hash: str
    from_extractor_version: int
    to_extractor_version: int
    approved_at: AwareDatetime
    written: tuple[str, ...]
    commit_subject: str


class ApprovalReference(BaseModel):
    """The human approval a carry rests on, named by role and time, never by the person."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Literal["approve"] = "approve"
    decided_at: AwareDatetime
    extractor_version: int
    reviewer_role: ReviewerRole

    @classmethod
    def of(cls, decision: HumanDecision) -> ApprovalReference:
        """The reference to ``decision``, which must be an approval naming its extractor."""
        if decision.decision is not Decision.APPROVE or decision.extractor_version is None:
            msg = "only an approval naming its extractor can be carried"
            raise ValueError(msg)
        return cls(
            decided_at=decision.decided_at,
            extractor_version=decision.extractor_version,
            reviewer_role=decision.reviewer_role,
        )


class CarriedRecord(BaseModel):
    """A standing approval carried to the running extractor by a migration run.

    Byte-identical rendering is the only basis (owner decision, 2026-10-08).
    ``carried_at`` is when the run carried it, not when anyone decided.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["carried"] = "carried"
    artifact: ArtifactKey
    carried_at: AwareDatetime
    doc_id: str
    version: int
    content_hash: str = Field(pattern=_SHA256)
    from_extractor_version: int
    to_extractor_version: int
    basis: CarryBasis = CARRY_BASIS
    approval: ApprovalReference

    def carries(self, decision: HumanDecision) -> bool:
        """True when this carry rests on ``decision``: its artifact, text, time and extractor."""
        return (
            self.artifact == decision.artifact
            and self.content_hash == decision.content_hash
            and self.approval.decided_at == decision.decided_at
            and self.approval.extractor_version == decision.extractor_version
        )


class StandingApproval(BaseModel):
    """The human approval a writer promotes on, and the carry it stands by, if any.

    A writer stamps ``approval_carried`` from ``carry``, so an approval given at
    an earlier extractor never reads as a review at the running one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: HumanDecision
    carry: CarriedRecord | None = None

    def approval_carried(self) -> ApprovalCarried | None:
        """The published ``approval_carried`` block, or ``None`` for an approval given there."""
        if self.carry is None:
            return None
        return ApprovalCarried(
            approved_at_extractor=self.carry.approval.extractor_version,
            carried_to_extractor=self.carry.to_extractor_version,
            carried_at=utc_text(self.carry.carried_at),
        )


class WithdrawalDocument(BaseModel):
    """The reviewer's withdrawal of one promoted document, as the operator hands it over."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Literal["withdraw"]
    removed_reason: RemovedReason
    decided_by: PersonName
    reviewer_role: ReviewerRole
    reason: TrimmedNonBlankStr

    @model_validator(mode="before")
    @classmethod
    def _names_a_role(cls, data: object) -> object:
        if isinstance(data, dict) and "reviewer_role" not in data:
            raise ValueError(ROLE_REQUIRED)
        return data

    @model_validator(mode="after")
    def _keeps_the_name_private(self) -> WithdrawalDocument:
        _refuse_the_name(self.decided_by, self.published())
        return self

    def published(self) -> dict[str, str]:
        """The fields the withdrawal publishes into ``lovverk``, by their path here."""
        return _published(self.reviewer_role, self.reason, None)


class WithdrawalRecord(BaseModel):
    """A recorded human withdrawal of one promoted document (ADR-0016 4c, 4f)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["withdrawal"] = "withdrawal"
    doc_id: TrimmedNonBlankStr
    authority_id: str = Field(pattern=_AUTHORITY_ID)
    slug: TrimmedNonBlankStr
    removed_reason: RemovedReason
    artifacts: tuple[ArtifactKey, ...]
    decided_by: PersonName
    reviewer_role: ReviewerRole
    decided_at: AwareDatetime
    reason: TrimmedNonBlankStr

    @model_validator(mode="after")
    def _keeps_the_name_private(self) -> WithdrawalRecord:
        _refuse_the_name(self.decided_by, _published(self.reviewer_role, self.reason, None))
        return self

    def withdraws(self, key: ArtifactKey | None, doc_id: str | None) -> bool:
        """True when this withdrawal covers ``key`` or the document ``doc_id``."""
        return key in self.artifacts or self.doc_id == doc_id


DecisionLogRecord = Annotated[
    HumanDecision | PromotedRecord | HeldRecord | MigratedRecord | CarriedRecord | WithdrawalRecord,
    Field(discriminator="kind"),
]
OutcomeRecord = PromotedRecord | HeldRecord | MigratedRecord
#: Each carried approval and the last extractor it was carried to (:meth:`DecisionLog.carried`).
Carried = Mapping[HumanDecision, int]
#: Each carried approval and the carry that took it there (:meth:`DecisionLog.carries`).
Carries = Mapping[HumanDecision, CarriedRecord]
_RECORD: TypeAdapter[DecisionLogRecord] = TypeAdapter(DecisionLogRecord)


class DecisionLog:
    """``promotions.jsonl`` under a validated observatory root; append is the only write."""

    def __init__(self, root: ObservatoryRoot) -> None:
        if not isinstance(root, ObservatoryRoot):
            msg = "DecisionLog requires an ObservatoryRoot; resolve it through observatory_root()"
            raise StorageBoundaryError(msg)
        self._path = root.path / DECISIONS_FILENAME

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: DecisionLogRecord) -> None:
        """Append one record durably, locked, flushed and fsynced like the observation log."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = record.model_dump_json() + "\n"
        with self._path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def records(self) -> tuple[DecisionLogRecord, ...]:
        """Every record in append order; a line that does not parse is refused, never skipped."""
        if not self._path.exists():
            return ()
        lines = self._path.read_text(encoding="utf-8").split("\n")
        if lines[-1]:
            msg = f"{self._path}: the last record is incomplete (no newline)"
            raise DecisionLogError(msg)
        return tuple(_parse(line, number) for number, line in enumerate(lines[:-1], start=1))

    def latest_decision(self, artifact: ArtifactKey) -> HumanDecision | None:
        """The last human decision on ``artifact`` — the one that stands."""
        found = [
            r for r in self.records() if isinstance(r, HumanDecision) and r.artifact == artifact
        ]
        return found[-1] if found else None

    def withdrawal_of(self, key: ArtifactKey | None, doc_id: str | None) -> WithdrawalRecord | None:
        """The first withdrawal covering ``key`` or ``doc_id``; a withdrawal is never undone."""
        for record in self.records():
            if isinstance(record, WithdrawalRecord) and record.withdraws(key, doc_id):
                return record
        return None

    def carried(self) -> dict[HumanDecision, int]:
        """Each carried approval, with the last extractor a migration carried it to."""
        return {decision: c.to_extractor_version for decision, c in self.carries().items()}

    def carries(self) -> dict[HumanDecision, CarriedRecord]:
        """Each carried approval, with the carry that took it to the latest extractor.

        Of carries to the same extractor, the last recorded one stands.
        """
        records = self.records()
        carries = [r for r in records if isinstance(r, CarriedRecord)]
        found: dict[HumanDecision, CarriedRecord] = {}
        for decision in (r for r in records if isinstance(r, HumanDecision)):
            reached = [c for c in carries if c.carries(decision)]
            if reached:
                found[decision] = max(reversed(reached), key=lambda c: c.to_extractor_version)
        return found

    def record_carry(self, record: CarriedRecord) -> bool:
        """Append ``record`` unless the same carry is already recorded; True if appended."""
        ignore = {"carried_at"}
        same = record.model_dump(exclude=ignore)
        for earlier in self.records():
            if isinstance(earlier, CarriedRecord) and earlier.model_dump(exclude=ignore) == same:
                return False
        self.append(record)
        return True

    def record_outcome(self, record: OutcomeRecord) -> bool:
        """Append ``record`` unless it repeats the artifact's last outcome; True if appended."""
        previous = [
            r
            for r in self.records()
            if isinstance(r, PromotedRecord | HeldRecord | MigratedRecord)
            and r.artifact == record.artifact
        ]
        if previous and _same_outcome(previous[-1], record):
            return False
        self.append(record)
        return True


def _same_outcome(left: OutcomeRecord, right: OutcomeRecord) -> bool:
    ignore = {"recorded_at"}
    return left.model_dump(exclude=ignore) == right.model_dump(exclude=ignore)


def _parse(line: str, number: int) -> DecisionLogRecord:
    try:
        return _RECORD.validate_json(line)
    except ValidationError as exc:
        msg = f"{DECISIONS_FILENAME} line {number} does not parse: {exc.error_count()} error(s)"
        raise DecisionLogError(msg) from exc
