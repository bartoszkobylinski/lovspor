"""The promotion decision log (ADR-0016 4b-4d): append-only, beside the archive.

``promotions.jsonl`` lives in the observatory root next to
``observations.jsonl`` — outside the engine repository and outside
``lovverk`` (ADR-0010 §5) — and is opened only for append. It holds three
record kinds, discriminated by ``kind``:

* ``decision`` — a human act on one artifact: ``approve``, ``reject`` or
  ``hold``, with who decided, when and why. ``decided_by`` is a person; a
  machine name is refused, because a decision the log attributes to a human
  must have been one (ADR-0016 4c). An ``approve`` names the content hash and
  extractor version of the text the reviewer read, so a later run cannot
  promote a different text under it.
* ``promoted`` — what a promotion run wrote, with its audit record (4d).
* ``held`` — a promotion run that stopped at a hold, with the stage and the
  reason (4h). Holds are recorded, never dropped.

A run that would append an outcome identical to the artifact's last one
(apart from ``recorded_at``) appends nothing, so a rerun leaves the log as it
found it.
"""

from __future__ import annotations

import fcntl
import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from lovspor.errors import DecisionLogError, StorageBoundaryError
from lovspor.observatory.fields import TrimmedNonBlankStr
from lovspor.observatory.storage import ObservatoryRoot
from lovspor.promotion.models import IdScheme, PersonalDataHit

DECISIONS_FILENAME = "promotions.jsonl"

#: Names a pipeline might sign with. A decision under one of them is not a
#: human's, and the log must not say it was.
MACHINE_NAMES = frozenset({"classifier", "lovspor", "promotion", "auto", "automatic", "system"})

_SHA256 = r"^[0-9a-f]{64}$"
_AUTHORITY_ID = r"^(?:\d{2}|\d{4})$"


def _a_person(value: str) -> str:
    if value.casefold() in MACHINE_NAMES:
        msg = f"decided_by must name a person, not {value!r} (ADR-0016 4c)"
        raise ValueError(msg)
    return value


PersonName = Annotated[TrimmedNonBlankStr, AfterValidator(_a_person)]


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


class DecisionDocument(BaseModel):
    """The reviewer's decision as the operator hands it over: a JSON file.

    A document rather than flags, as with the access-policy check: a
    conclusion typed into a shell leaves nothing to re-read.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Decision
    decided_by: PersonName
    reason: TrimmedNonBlankStr
    classifier: ClassifierEvidence | None = None


class HumanDecision(BaseModel):
    """A recorded human decision on one artifact (ADR-0016 4c)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["decision"] = "decision"
    artifact: ArtifactKey
    decision: Decision
    decided_by: PersonName
    decided_at: AwareDatetime
    reason: TrimmedNonBlankStr
    content_hash: str | None = Field(default=None, pattern=_SHA256)
    extractor_version: int | None = None
    classifier: ClassifierEvidence | None = None

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


class PromotionAudit(BaseModel):
    """Why a version was promoted (ADR-0016 4d): the versions, the evidence, the human."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Decision
    decided_by: str
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


DecisionLogRecord = Annotated[
    HumanDecision | PromotedRecord | HeldRecord, Field(discriminator="kind")
]
OutcomeRecord = PromotedRecord | HeldRecord
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

    def record_outcome(self, record: OutcomeRecord) -> bool:
        """Append ``record`` unless it repeats the artifact's last outcome; True if appended."""
        previous = [
            r
            for r in self.records()
            if isinstance(r, PromotedRecord | HeldRecord) and r.artifact == record.artifact
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
