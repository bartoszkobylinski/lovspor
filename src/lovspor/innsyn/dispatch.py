"""Which letters of a wave go out, and sending them (issue #509, point 6).

Every letter of the manifest is judged before anything is sent, in manifest
order:

* **skipped** — the ledger already holds a ``sent`` for the authority with this
  letter's SHA-256: it went out from this manifest, and never goes twice;
* **refused** — the authority has another open request; the directory's
  recipient is no longer the manifest's; the letter file is missing, changed
  since the manifest (SHA-256), or not a letter; each with its reason;
* **ready** — everything else.

The first ``limit`` ready letters are sent, a pause between each. Each one
is recorded the moment the server accepts it, through the event ``record-sent``
writes, with the acceptance time and the Message-ID. The first letter the
server does not accept stops the run: nothing is recorded for it, and the
letters after it are not tried.
"""

import hashlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from lovspor.errors import InnsynError, InnsynSendError, StorageUnavailableError
from lovspor.innsyn.directory import Contact, contacts, directory_journal
from lovspor.innsyn.ledger import RequestState, Sent, record_events, requests
from lovspor.innsyn.mailer import MailMessage, Transport, message_id
from lovspor.innsyn.sender import Sender
from lovspor.innsyn.sent import sent_event
from lovspor.innsyn.waves import ManifestEntry, read_manifest
from lovspor.innsyn.workspace import InnsynWorkspace


class Ready(BaseModel):
    """A letter that may go: its subject and body as the file holds them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry: ManifestEntry
    subject: str
    body: str
    digest: str


class Refusal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    authority_id: str
    reason: str


class Prepared(BaseModel):
    """The manifest judged against the ledger, the directory and the letter files."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ready: tuple[Ready, ...]
    skipped: tuple[str, ...]
    refused: tuple[Refusal, ...]


class _Context(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    folder: Path
    held: dict[str, RequestState]
    contacts: dict[str, Contact]


def _from_manifest(context: _Context, entry: ManifestEntry) -> bool:
    return any(
        state.sent.authority_id == entry.authority_id
        and state.sent.letter_sha256 == entry.letter_sha256
        for state in context.held.values()
    )


def _open_elsewhere(context: _Context, entry: ManifestEntry) -> str | None:
    for state in context.held.values():
        if state.sent.authority_id == entry.authority_id and state.is_open:
            return f"has an open request {state.sent.request_id} not from this manifest"
    return None


def _recipient_moved(context: _Context, entry: ManifestEntry) -> str | None:
    recorded = context.contacts.get(entry.authority_id, Contact()).recipient
    address = None if recorded is None else recorded.address
    if address == entry.recipient:
        return None
    return f"the directory's recipient is now {address}, the manifest's is {entry.recipient}"


def _parse(entry: ManifestEntry, payload: bytes) -> tuple[str, str]:
    """The subject and body of a letter file, or ValueError naming what is wrong."""
    head, separator, body = payload.decode().partition("\n\n")
    lines = head.split("\n")
    if not separator or len(lines) != 2 or lines[0] != f"Til: {entry.recipient}":  # noqa: PLR2004
        raise ValueError("not a letter: expected `Til:`, `Emne:`, a blank line, then the body")
    subject = lines[1].removeprefix("Emne: ").strip()
    if not lines[1].startswith("Emne: ") or not subject or "\r" in lines[1]:
        raise ValueError("not a letter: the second line is not a one-line `Emne:`")
    if not body.strip():
        raise ValueError("not a letter: the body is empty")
    return subject, body


def _letter(context: _Context, entry: ManifestEntry) -> Ready | str:
    path = context.folder / entry.letter_file
    try:
        payload = path.read_bytes()
    except OSError as exc:
        return f"{path}: cannot be read ({type(exc).__name__})"
    digest = hashlib.sha256(payload).hexdigest()
    if digest != entry.letter_sha256:
        return f"{path}: SHA-256 {digest} is not the manifest's; the letter changed"
    try:
        subject, body = _parse(entry, payload)
    except ValueError as exc:
        return f"{path}: {exc}"
    return Ready(entry=entry, subject=subject, body=body, digest=digest)


def _judge(context: _Context, entry: ManifestEntry) -> Ready | Refusal | None:
    """None: already sent from this manifest."""
    if _from_manifest(context, entry):
        return None
    reason = _open_elsewhere(context, entry) or _recipient_moved(context, entry)
    outcome = _letter(context, entry) if reason is None else reason
    if isinstance(outcome, str):
        return Refusal(authority_id=entry.authority_id, reason=outcome)
    return outcome


def prepare(workspace: InnsynWorkspace, manifest_path: Path) -> Prepared:
    """Judge every letter of the manifest, in its order; sends and writes nothing.

    Raises:
        InnsynRefusedError: no manifest there; InnsynRecordError: the ledger or
            directory does not read.
    """
    manifest = read_manifest(manifest_path)
    context = _Context(
        folder=manifest_path.parent,
        held=requests(workspace),
        contacts=contacts(directory_journal(workspace).records()),
    )
    ready, skipped, refused = [], [], []
    for entry in manifest.letters:
        judged = _judge(context, entry)
        if judged is None:
            skipped.append(entry.authority_id)
        elif isinstance(judged, Refusal):
            refused.append(judged)
        else:
            ready.append(judged)
    return Prepared(ready=tuple(ready), skipped=tuple(skipped), refused=tuple(refused))


def compose(ready: Ready, sender: Sender, at: datetime) -> MailMessage:
    """The message for ``ready``, dated ``at``, with a fresh Message-ID."""
    return MailMessage(
        sender=sender,
        to=ready.entry.recipient,
        subject=ready.subject,
        body=ready.body,
        date=at,
        message_id=message_id(sender),
    )


class SendPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sender: Sender
    limit: int = Field(ge=1)
    pause_seconds: float = Field(ge=0)


class Wire(BaseModel):
    """What a send touches outside the workspace: the server, the clock, the pause."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    transport: Transport
    clock: Callable[[], datetime]
    pause: Callable[[float], None]


class Failure(BaseModel):
    """The letter the run stopped at. ``accepted``: the server took it, the ledger did not."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority_id: str
    reason: str
    accepted: bool = False
    sent_at: datetime | None = None
    message_id: str | None = None


class SendReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sent: tuple[Sent, ...]
    failure: Failure | None
    remaining: int = Field(ge=0)


def _unrecorded(ready: Ready, event: Sent, exc: Exception) -> Failure:
    return Failure(
        authority_id=ready.entry.authority_id,
        reason=f"accepted by the server, but not recorded: {exc}",
        accepted=True,
        sent_at=event.sent_at,
        message_id=event.message_id,
    )


def _send_one(
    workspace: InnsynWorkspace, ready: Ready, plan: SendPlan, wire: Wire
) -> Sent | Failure:
    message = compose(ready, plan.sender, wire.clock())
    try:
        wire.transport.deliver(message.email())
    except InnsynSendError as exc:
        return Failure(authority_id=ready.entry.authority_id, reason=str(exc))
    event = sent_event(ready.entry, ready.digest, wire.clock(), message.message_id)
    try:
        record_events(workspace, [event])
    except (InnsynError, StorageUnavailableError, OSError) as exc:
        return _unrecorded(ready, event, exc)
    return event


def send_letters(
    workspace: InnsynWorkspace, prepared: Prepared, plan: SendPlan, wire: Wire
) -> SendReport:
    """Send the first ``plan.limit`` ready letters, recording each as it is accepted."""
    sent: list[Sent] = []
    failure = None
    for index, ready in enumerate(prepared.ready[: plan.limit]):
        if index:
            wire.pause(plan.pause_seconds)
        outcome = _send_one(workspace, ready, plan, wire)
        if isinstance(outcome, Failure):
            failure = outcome
            break
        sent.append(outcome)
    accepted = len(sent) + (1 if failure is not None and failure.accepted else 0)
    remaining = len(prepared.ready) - accepted
    return SendReport(sent=tuple(sent), failure=failure, remaining=remaining)
