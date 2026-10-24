"""What became of a letter on its way: it never arrived (#644), or it was chased (#625).

A bounce and a mailbox closed to e-mail are two commands rather than one with
a ``--reason``, because only a bounce carries a DSN status. Either takes the
bounce or auto-reply as saved from the mailbox, and records its SHA-256, not
its text.
"""

import hashlib
from pathlib import Path
from typing import Annotated

import typer

from lovspor.innsyn.app import guarded, innsyn_app, instant
from lovspor.innsyn.ledger_commands import Request, record_event
from lovspor.innsyn.received import read_document

_Evidence = Annotated[
    Path,
    typer.Option(
        "--evidence", help="The bounce or auto-reply as saved (.eml); its text is not kept."
    ),
]
_Arrived = Annotated[str, typer.Option("--at", help="When the bounce or auto-reply arrived (UTC).")]


def _digest(path: Path) -> str:
    return hashlib.sha256(guarded(lambda: read_document(path))).hexdigest()


@innsyn_app.command("record-bounce")
def record_bounce(
    request: Request,
    at: _Arrived,
    evidence: _Evidence,
    status: Annotated[
        str, typer.Option("--status", help="The DSN's permanent-failure status, e.g. 5.4.1.")
    ],
) -> None:
    """Record that the letter bounced: it never reached the authority."""
    fields = {"reason": "bounced", "at": instant(at, "--at"), "dsn_status": status}
    record_event(request, "undelivered", {**fields, "evidence_sha256": _digest(evidence)})


@innsyn_app.command("record-mailbox-closed")
def record_mailbox_closed(request: Request, at: _Arrived, evidence: _Evidence) -> None:
    """Record that the mailbox answered it takes no e-mail: the letter was not accepted."""
    fields = {"reason": "mailbox_closed", "at": instant(at, "--at")}
    record_event(request, "undelivered", {**fields, "evidence_sha256": _digest(evidence)})


@innsyn_app.command("record-reminder")
def record_reminder(
    request: Request,
    at: Annotated[str, typer.Option("--at", help="When the reminder was sent (UTC).")],
    letter: Annotated[Path, typer.Option("--letter", help="The reminder as sent.")],
) -> None:
    """Record a reminder (offentleglova § 29 første ledd); the request stays due."""
    fields = {"reminder_sent_at": instant(at, "--at"), "letter_sha256": _digest(letter)}
    record_event(request, "reminder", fields)
