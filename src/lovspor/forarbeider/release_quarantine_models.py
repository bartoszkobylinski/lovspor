"""AR3 records: document quarantine, failure attribution and owner exceptions (#738; §9.4, §9.6).

**Findings.** A :class:`QuarantineFinding` is one *document-local* failure:
the document, the exact version (``None`` only when FC8b could select no
version), the scope (the whole document, one part, one span or one
attachment), a stable reason code and its category, the digests of the
evidence it rests on and the policy it was evaluated under. It carries no
source text, no URL and no personal data: spans and parts are named by their
FC5/FC7 ids, attachments by a digest.

A failure that cannot be proven to belong to one document is a
:class:`GlobalFailure`: it never quarantines anything, it halts the run
(:mod:`lovspor.forarbeider.release_attribution`).

**Records.** A :class:`QuarantineRecord` is keyed by the document, version,
scope and code (``quarantine_id``). It keeps the first time it was seen, the
evidence and policy of its latest observation, and an append-only history:

- ``observed``: the first observation;
- ``evidence_changed``: the same failure on other evidence or under another
  policy. It ends any clearance: changed evidence needs a new assessment;
- ``cleared``: a verified owner clearance matched the record exactly;
- ``reinstated``: the clearance no longer matches or is no longer offered.

``state`` follows from the history alone, so a stored ``cleared`` is a
consequence, never an input: the engine re-verifies the clearance on every
run (:mod:`lovspor.forarbeider.release_quarantine`). A record never expires
and is never removed, whatever later runs, versions or policies show.

**Ledger.** The :class:`QuarantineLedger` holds every record and every owner
exclusion. It is private (``published: false``), lives in the runner's
private workspace and is never part of a release. Document quarantine is not
the runner's STOP: a quarantine removes documents from batches and lets the
others proceed; a global failure halts the run and is for the kill switch
(AR5) to act on. Nothing here writes a stop or control record.

**Owner exceptions.** A :class:`QuarantineException` is signed by the owner in
its own namespace, ``lovspor-forarbeider-quarantine``, which a bot key can
never hold. It binds the exact policy (FC7b's :class:`ApprovalScope`), the
document, and the exact evidence digests it was decided on, and is either:

- a ``clearance`` of one finding (``finding``), one reason class of a version
  (``class``), or every finding of a version (``version``). It lifts only the
  matching quarantine restriction: FC7, privacy, completeness, integrity and
  every AR1 gate still apply in full;
- a permanent ``exclusion`` of a document, with an explicit reason code.
"""

from itertools import pairwise
from typing import Final, Literal, Self

from pydantic import Field, model_validator

from lovspor.forarbeider.policy_authorization_models import ApprovalScope, Instant
from lovspor.forarbeider.release_contract_models import _Evidence
from lovspor.forarbeider.version_models import Digest, Frozen, NonBlank
from lovspor.forarbeider.version_release_models import PolicyReference, digest_of

QUARANTINE_VERSION: Final = "quarantine/1"

ScopeKind = Literal["document", "part", "span", "attachment"]
LocalCategory = Literal[
    "extraction", "rendering", "integrity", "rights", "privacy", "attachment", "completeness"
]
GlobalCategory = Literal["trust", "policy", "source_wide", "repository", "authorization", "unknown"]
QuarantineState = Literal["active", "cleared"]
EventKind = Literal["observed", "evidence_changed", "cleared", "reinstated"]

_Code = Field(pattern=r"^[a-z0-9][a-z0-9_:.-]{0,127}$")
_Ref = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_DocumentId = Field(pattern=r"^regjeringen-[1-9]\d{0,11}$")
_Principal = Field(pattern=r"^[a-z0-9][a-z0-9._@-]{0,63}$")
_Id = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
_Reason = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")


def _sorted_once(values: tuple[str, ...], what: str) -> None:
    if list(values) != sorted(set(values)):
        raise ValueError(f"{what} are sorted, once each")


class QuarantineScope(Frozen):
    """What a finding covers: the whole document, or one part, span or attachment."""

    kind: ScopeKind
    ref: str | None = _Ref

    @model_validator(mode="after")
    def _named(self) -> Self:
        if (self.kind == "document") != (self.ref is None):
            raise ValueError("a part, span or attachment is named; the document is not")
        return self


class _FindingKey(Frozen):
    document_id: NonBlank
    version_id: Digest | None
    scope: QuarantineScope
    code: NonBlank


class QuarantineFinding(Frozen):
    """One document-local failure, by stable code, on exact evidence; never text."""

    document_id: str = _DocumentId
    version_id: Digest | None
    scope: QuarantineScope
    code: str = _Code
    category: LocalCategory
    evidence_sha256s: tuple[Digest, ...] = Field(min_length=1)
    policy: PolicyReference | None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _sorted_once(self.evidence_sha256s, "evidence digests")
        return self

    @property
    def quarantine_id(self) -> str:
        return quarantine_id_of(self.document_id, self.version_id, self.scope, self.code)


class GlobalFailure(Frozen):
    """A failure no document can be proven to own: it halts the run, it quarantines nothing."""

    code: str = _Code
    category: GlobalCategory
    document_id: str | None = None


class QuarantineEvent(Frozen):
    """One entry of a record's append-only history."""

    sequence: int = Field(ge=1)
    at: Instant
    event: EventKind
    evidence_sha256s: tuple[Digest, ...] = Field(min_length=1)
    policy: PolicyReference | None
    plan_id: Digest | None = None
    exception_sha256: Digest | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _sorted_once(self.evidence_sha256s, "evidence digests")
        if (self.event == "cleared") != (self.exception_sha256 is not None):
            raise ValueError("a clearance, and only a clearance, names its exception")
        return self


class QuarantineRecord(Frozen):
    """One finding's persistent quarantine: first seen, latest evidence, full history."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_quarantine_record"] = "forarbeider_quarantine_record"
    quarantine_id: Digest
    document_id: str = _DocumentId
    version_id: Digest | None
    scope: QuarantineScope
    code: str = _Code
    category: LocalCategory
    evidence_sha256s: tuple[Digest, ...] = Field(min_length=1)
    policy: PolicyReference | None
    first_seen: Instant
    state: QuarantineState
    history: tuple[QuarantineEvent, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        expected = quarantine_id_of(self.document_id, self.version_id, self.scope, self.code)
        if self.quarantine_id != expected:
            raise ValueError("quarantine_id is the digest of document, version, scope and code")
        _check_history(self)
        return self


class ExclusionEntry(Frozen):
    """A document the owner excluded permanently, by a verified signed exception."""

    document_id: str = _DocumentId
    reason_code: str = _Reason
    exception_sha256: Digest
    excluded_at: Instant


class QuarantineLedger(Frozen):
    """Every quarantine record and owner exclusion; private, never published, never expired."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_quarantine_ledger"] = "forarbeider_quarantine_ledger"
    domain: Literal["lovspor.forarbeider.quarantine.v1"] = "lovspor.forarbeider.quarantine.v1"
    quarantine_version: Literal["quarantine/1"] = QUARANTINE_VERSION
    records: tuple[QuarantineRecord, ...] = ()
    exclusions: tuple[ExclusionEntry, ...] = ()
    private: Literal[True] = True
    published: Literal[False] = False

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _sorted_once(tuple(item.quarantine_id for item in self.records), "records")
        _sorted_once(tuple(item.document_id for item in self.exclusions), "exclusions")
        return self


class QuarantinedDocument(Frozen):
    """A document with an active restriction, and every reason for it."""

    document_id: str = _DocumentId
    reasons: tuple[NonBlank, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _sorted_once(self.reasons, "reasons")
        return self


class QuarantineSet(Frozen):
    """What the planner must keep out of every batch, bound to the ledger it came from."""

    ledger_sha256: Digest
    documents: tuple[QuarantinedDocument, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _sorted_once(tuple(item.document_id for item in self.documents), "documents")
        return self

    def reasons_of(self, document_id: str) -> tuple[str, ...]:
        found = (item.reasons for item in self.documents if item.document_id == document_id)
        return next(found, ())


class QuarantineEvidence(_Evidence):
    """The active quarantine, as the engine computed it for one exact base plan."""

    quarantine: QuarantineSet | None = None
    quarantine_sha256: Digest | None = None
    base_plan_id: Digest | None = None
    inventory_sha256: Digest | None = None
    limits_sha256: Digest | None = None
    trust_root_sha256: Digest | None = None

    @model_validator(mode="after")
    def _complete(self) -> Self:
        self._require(self.quarantine)
        self._require(self.base_plan_id)
        return self


NO_QUARANTINE: Final = QuarantineEvidence(
    status="missing", source=None, reasons=("quarantine_not_assessed",)
)


class GapCount(Frozen):
    category: NonBlank
    count: int = Field(ge=1)


class QuarantineMetrics(Frozen):
    """Deterministic counters; quarantined documents stay in every denominator. No text."""

    documents_expected: int = Field(ge=0)
    documents_ingested: int = Field(ge=0)
    documents_source_complete: int = Field(ge=0)
    documents_quarantined: int = Field(ge=0)
    documents_excluded_by_owner: int = Field(ge=0)
    documents_planned: int = Field(ge=0)
    spans_rights_unresolved: int = Field(ge=0)
    spans_privacy_unresolved: int = Field(ge=0)
    attachments_unpublishable: int = Field(ge=0)
    release_gaps: tuple[GapCount, ...] = ()


ExceptionAction = Literal["clearance", "exclusion"]
ExceptionTarget = Literal["finding", "class", "version", "document"]


class QuarantineException(Frozen):
    """The owner's signed clearance or exclusion for one document, on exact evidence."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_quarantine_exception"] = "forarbeider_quarantine_exception"
    domain: Literal["lovspor.forarbeider.quarantine-exception.v1"] = (
        "lovspor.forarbeider.quarantine-exception.v1"
    )
    exception_id: str = _Id
    action: ExceptionAction
    target: ExceptionTarget
    scope: ApprovalScope
    document_id: str = _DocumentId
    version_id: Digest | None = None
    quarantine_id: Digest | None = None
    code: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_:.-]{0,127}$")
    evidence_sha256s: tuple[Digest, ...] = Field(min_length=1)
    reason_code: str = _Reason
    signer_principal: str = _Principal
    signed_at: Instant
    valid_from: Instant
    valid_until: Instant

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _sorted_once(self.evidence_sha256s, "evidence digests")
        if (self.action == "exclusion") != (self.target == "document"):
            raise ValueError("an exclusion, and only an exclusion, targets a whole document")
        if (self.target == "finding") != (self.quarantine_id is not None):
            raise ValueError("a finding clearance, and only it, names the quarantine id")
        if (self.target == "class") != (self.code is not None):
            raise ValueError("a class clearance, and only it, names the reason code")
        if self.action == "clearance" and self.version_id is None:
            raise ValueError("a clearance names the exact version it was decided on")
        if not self.valid_from < self.valid_until or self.signed_at >= self.valid_until:
            raise ValueError("the validity window is ordered and the record signed inside it")
        return self


def quarantine_id_of(
    document_id: str, version_id: str | None, scope: QuarantineScope, code: str
) -> str:
    key = _FindingKey(document_id=document_id, version_id=version_id, scope=scope, code=code)
    return digest_of(key)


def state_of(history: tuple[QuarantineEvent, ...]) -> QuarantineState:
    """Cleared exactly when the latest clearance event is not followed by a change."""
    return "cleared" if history[-1].event == "cleared" else "active"


def _check_history(record: QuarantineRecord) -> None:
    history = record.history
    if [item.sequence for item in history] != list(range(1, len(history) + 1)):
        raise ValueError("history is numbered 1, 2, 3... in order")
    if history[0].event != "observed" or history[0].at != record.first_seen:
        raise ValueError("history starts with the first observation, at first_seen")
    if any(item.event == "observed" for item in history[1:]):
        raise ValueError("a record is first observed once")
    if any(later.at < earlier.at for earlier, later in pairwise(history)):
        raise ValueError("history is in time order")
    if (history[-1].evidence_sha256s, history[-1].policy) != (
        record.evidence_sha256s,
        record.policy,
    ):
        raise ValueError("a record's evidence and policy are its latest event's")
    if record.state != state_of(history):
        raise ValueError("state follows from the history")
