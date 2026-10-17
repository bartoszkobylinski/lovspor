"""The request ledger (issue #509, point 4): every innsynskrav and what became of it.

``requests.jsonl`` is append-only; each line is one transition of one
request, recorded by its own command:

* ``sent`` — the letter went out: authority, recipient, ``sent_at``, the LF
  ids of part (a), whether the general clause of part (b) was included, and
  the SHA-256 of the letter as sent, and, for a letter ``send`` delivered (or
  one recorded with ``--message-id``), the Message-ID it went out with. A
  request is identified by authority and ``sent_at`` (``4646-20261005T074425Z``).
* ``acknowledged`` — the authority's receipt, automatic or not.
* ``case_number`` — the authority's saksnummer, quoted in every later mail.
* ``outcome`` — one of :data:`OUTCOMES`, with the time it arrived and, for a
  refusal or partial answer, the stated basis. An interim reply without a
  date, or a recorded ``no_answer``, is not an answer.
* ``klage`` — a complaint was sent.
* ``document_received`` — a document the authority sent, by SHA-256 and media
  type, with when it arrived; the file itself is held by
  :mod:`lovspor.innsyn.received`, and its name is not kept.
* ``document_classified`` — what the operator read the document to be, and
  which requested LF id it answers, if any. A later classification replaces
  an earlier one; a document never classified is not yet evidence of anything.

The § 32 andre ledd mark is computed, never stored: five working days after
the Oslo day of ``sent_at``. Free-text notes are not kept: they name case
officers, and personopplysningsloven asks for the minimum.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from lovspor.errors import InnsynRefusedError
from lovspor.innsyn.directory import MAILBOX_PATTERN
from lovspor.innsyn.journal import Journal
from lovspor.innsyn.population import AUTHORITY_ID_PATTERN
from lovspor.innsyn.workdays import klage_mark
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.observatory.fields import TrimmedNonBlankStr
from lovspor.observatory.lf_refs import LF_ID_PATTERN
from lovspor.observatory.model import require_utc

OutcomeClass = Literal[
    "referred_to_lovdata",
    "interim_reply_no_date",
    "partial",
    "full",
    "refused",
    "no_answer",
]
OUTCOMES: tuple[OutcomeClass, ...] = (
    "referred_to_lovdata",
    "interim_reply_no_date",
    "partial",
    "full",
    "refused",
    "no_answer",
)
#: An outcome that is an answer: its time is the request's ``answered_at``.
ANSWERS = frozenset({"referred_to_lovdata", "partial", "full", "refused"})
#: An outcome after which the request is closed; any other leaves it open.
CLOSING = frozenset({"full", "refused"})
_WITH_BASIS = frozenset({"partial", "refused"})

DocumentKind = Literal["regulation_text", "vedtak", "minutes", "cover_letter", "other"]
DOCUMENT_KINDS: tuple[DocumentKind, ...] = (
    "regulation_text",
    "vedtak",
    "minutes",
    "cover_letter",
    "other",
)
#: A kind that can be the requested regulation, or the vedtak adopting it.
_ANSWERING = frozenset({"regulation_text", "vedtak"})
#: The formats the promotion extractor reads (:mod:`lovspor.promotion.source_text`).
ReceivedMediaType = Literal[
    "application/pdf",
    "text/html",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]

REQUEST_ID_PATTERN = r"^(?:\d{2}|\d{4})-\d{8}T\d{6}Z$"
MESSAGE_ID_PATTERN = r"^<[^<>@\s]+@[^<>@\s]+>$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_LfId = Annotated[str, Field(pattern=LF_ID_PATTERN)]
Source = Literal["cli", "pilot-import"]


def request_id(authority_id: str, sent_at: datetime) -> str:
    return f"{authority_id}-{sent_at.strftime('%Y%m%dT%H%M%SZ')}"


def _utc_or_none(value: datetime | None) -> datetime | None:
    return None if value is None else require_utc(value)


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(pattern=REQUEST_ID_PATTERN)
    recorded_at: datetime
    source: Source = "cli"

    @field_validator("recorded_at")
    @classmethod
    def _utc_recorded(cls, value: datetime) -> datetime:
        return require_utc(value)


class Sent(_Event):
    event: Literal["sent"] = "sent"
    authority_id: str = Field(pattern=AUTHORITY_ID_PATTERN)
    recipient: str = Field(pattern=MAILBOX_PATTERN)
    sent_at: datetime
    lf_ids: tuple[_LfId, ...]
    general_clause: bool
    letter_sha256: str = Field(pattern=_SHA256_PATTERN)
    message_id: str | None = Field(default=None, pattern=MESSAGE_ID_PATTERN)

    @field_validator("sent_at")
    @classmethod
    def _utc_sent(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _consistent(self) -> "Sent":
        if self.request_id != request_id(self.authority_id, self.sent_at):
            raise ValueError("request_id must be the authority and sent_at")
        if not self.lf_ids and not self.general_clause:
            raise ValueError("a request asks for known LF ids, the general clause, or both")
        if list(self.lf_ids) != sorted(set(self.lf_ids)):
            raise ValueError("lf_ids must be sorted and distinct")
        return self


class Acknowledged(_Event):
    event: Literal["acknowledged"] = "acknowledged"
    acknowledged_at: datetime

    @field_validator("acknowledged_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class CaseNumber(_Event):
    event: Literal["case_number"] = "case_number"
    saksnummer: TrimmedNonBlankStr


class Outcome(_Event):
    """``at`` is None only for a pilot-register row that gave no time."""

    event: Literal["outcome"] = "outcome"
    outcome: OutcomeClass
    at: datetime | None
    refusal_basis: TrimmedNonBlankStr | None = None

    @field_validator("at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return _utc_or_none(value)

    @model_validator(mode="after")
    def _basis_and_time(self) -> "Outcome":
        if self.refusal_basis is not None and self.outcome not in _WITH_BASIS:
            raise ValueError("a refusal basis belongs to a refused or partial outcome")
        if self.at is None and self.source != "pilot-import":
            raise ValueError("an outcome recorded now says when it arrived")
        return self


class KlageSent(_Event):
    event: Literal["klage"] = "klage"
    klage_sent_at: datetime

    @field_validator("klage_sent_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class DocumentReceived(_Event):
    event: Literal["document_received"] = "document_received"
    received_at: datetime
    sha256: str = Field(pattern=_SHA256_PATTERN)
    content_type: ReceivedMediaType

    @field_validator("received_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class DocumentClassified(_Event):
    event: Literal["document_classified"] = "document_classified"
    sha256: str = Field(pattern=_SHA256_PATTERN)
    kind: DocumentKind
    answers: _LfId | None = None

    @model_validator(mode="after")
    def _answering_kind(self) -> "DocumentClassified":
        if self.answers is not None and self.kind not in _ANSWERING:
            raise ValueError("only a regulation text or vedtak answers a requested id")
        return self


class ReceivedDocument(BaseModel):
    """One received document as its receipt and latest classification leave it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sha256: str
    content_type: ReceivedMediaType
    received_at: datetime
    kind: DocumentKind | None = None
    answers: str | None = None


_Transition = (
    Acknowledged | CaseNumber | Outcome | KlageSent | DocumentReceived | DocumentClassified
)
LedgerEvent = Annotated[Sent | _Transition, Field(discriminator="event")]
_ADAPTER: TypeAdapter[LedgerEvent] = TypeAdapter(LedgerEvent)


class RequestState(BaseModel):
    """One request as its transitions leave it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sent: Sent
    saksnummer: str | None = None
    acknowledged_at: datetime | None = None
    answered_at: datetime | None = None
    outcome: OutcomeClass | None = None
    refusal_basis: str | None = None
    klage_sent_at: datetime | None = None
    documents: tuple[ReceivedDocument, ...] = ()

    @property
    def unanswered_ids(self) -> tuple[str, ...]:
        """Requested ids no received regulation text answers: silence is not "none exist"."""
        texts = {d.answers for d in self.documents if d.kind == "regulation_text"}
        return tuple(lf_id for lf_id in self.sent.lf_ids if lf_id not in texts)

    @property
    def klage_mark(self) -> date:
        return klage_mark(self.sent.sent_at)

    @property
    def is_open(self) -> bool:
        return self.outcome not in CLOSING

    @property
    def answered(self) -> bool:
        return self.answered_at is not None or self.outcome in ANSWERS

    def is_due(self, today: date) -> bool:
        """Past the § 32 mark with no answer and no klage: a klage candidate."""
        return today > self.klage_mark and not self.answered and self.klage_sent_at is None


def parse_event(data: dict[str, object]) -> LedgerEvent:
    """Raises: ValidationError: not a ledger event."""
    return _ADAPTER.validate_python(data)


def ledger_journal(workspace: InnsynWorkspace) -> Journal[LedgerEvent]:
    return Journal(workspace.requests_log, _ADAPTER)


def _answer(state: RequestState, event: Outcome) -> RequestState:
    answered = state.answered_at
    if answered is None and event.outcome in ANSWERS:
        answered = event.at
    update = {"outcome": event.outcome, "answered_at": answered}
    return state.model_copy(update={**update, "refusal_basis": event.refusal_basis})


def _receive(state: RequestState, event: DocumentReceived) -> RequestState:
    document = ReceivedDocument(
        sha256=event.sha256, content_type=event.content_type, received_at=event.received_at
    )
    return state.model_copy(update={"documents": (*state.documents, document)})


def _classify(state: RequestState, event: DocumentClassified) -> RequestState:
    reading = {"kind": event.kind, "answers": event.answers}
    documents = tuple(
        document.model_copy(update=reading) if document.sha256 == event.sha256 else document
        for document in state.documents
    )
    return state.model_copy(update={"documents": documents})


def _apply(state: RequestState, event: _Transition) -> RequestState:
    if isinstance(event, Outcome):
        return _answer(state, event)
    if isinstance(event, DocumentReceived):
        return _receive(state, event)
    if isinstance(event, DocumentClassified):
        return _classify(state, event)
    if isinstance(event, Acknowledged):
        return state.model_copy(update={"acknowledged_at": event.acknowledged_at})
    if isinstance(event, CaseNumber):
        return state.model_copy(update={"saksnummer": event.saksnummer})
    return state.model_copy(update={"klage_sent_at": event.klage_sent_at})


def _step(held: dict[str, RequestState], event: LedgerEvent) -> None:
    if isinstance(event, Sent):
        held[event.request_id] = RequestState(sent=event)
    else:
        held[event.request_id] = _apply(held[event.request_id], event)


def fold(events: list[LedgerEvent]) -> dict[str, RequestState]:
    """Every request by id, in the order sent."""
    held: dict[str, RequestState] = {}
    for event in events:
        _step(held, event)
    return held


def _moment(event: LedgerEvent) -> datetime | None:
    if isinstance(event, Acknowledged):
        return event.acknowledged_at
    if isinstance(event, KlageSent):
        return event.klage_sent_at
    if isinstance(event, DocumentReceived):
        return event.received_at
    return event.at if isinstance(event, Outcome) else None


def _check_document(state: RequestState, event: LedgerEvent) -> None:
    held = {document.sha256 for document in state.documents}
    if isinstance(event, DocumentReceived) and event.sha256 in held:
        raise InnsynRefusedError(f"{event.request_id}: document {event.sha256} already received")
    if not isinstance(event, DocumentClassified):
        return
    if event.sha256 not in held:
        raise InnsynRefusedError(f"{event.request_id}: document {event.sha256} not received")
    if event.answers is not None and event.answers not in state.sent.lf_ids:
        raise InnsynRefusedError(f"{event.request_id} did not ask for {event.answers}")


def check(held: dict[str, RequestState], event: LedgerEvent) -> None:
    """Refuse an event the ledger cannot take.

    Raises:
        InnsynRefusedError: a second ``sent`` for one request, a transition of
            a request never sent, or one dated before the request was sent; a
            document received twice on one request, or classified without
            having been received, or said to answer an id the request did not
            ask for.
    """
    state = held.get(event.request_id)
    if isinstance(event, Sent):
        if state is not None:
            raise InnsynRefusedError(f"{event.request_id} is already in the ledger")
        return
    if state is None:
        raise InnsynRefusedError(f"no request {event.request_id} in the ledger")
    moment = _moment(event)
    if moment is not None and moment < state.sent.sent_at:
        raise InnsynRefusedError(f"{event.request_id}: {moment} is before it was sent")
    _check_document(state, event)


def _check_all(held: dict[str, RequestState], events: list[LedgerEvent]) -> None:
    for event in events:
        check(held, event)
        _step(held, event)


def precheck(workspace: InnsynWorkspace, events: list[LedgerEvent]) -> None:
    """Refuse what :func:`record_events` would refuse, writing nothing."""
    _check_all(requests(workspace), events)


def record_events(workspace: InnsynWorkspace, events: list[LedgerEvent]) -> None:
    """Check every event against the ledger and what precedes it, then append all or none."""
    journal = ledger_journal(workspace)
    _check_all(fold(journal.records()), events)
    workspace.ensure()
    journal.append(events)


def requests(workspace: InnsynWorkspace) -> dict[str, RequestState]:
    return fold(ledger_journal(workspace).records())
