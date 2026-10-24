"""The request-ledger commands: one per transition, the listings, and ``due`` (#509 point 4).

Every field of the ledger is written by one of these commands (or by
``record-sent`` in :mod:`lovspor.innsyn.letter_commands`), never by hand.
"""

from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from lovspor.innsyn.app import guarded, innsyn_app, instant, now, refuse, workspace
from lovspor.innsyn.ledger import (
    OUTCOMES,
    LedgerEvent,
    RequestState,
    parse_event,
    record_events,
    requests,
)
from lovspor.innsyn.pilot_import import read_register
from lovspor.innsyn.workdays import OSLO

Request = Annotated[
    str, typer.Option("--request", help="The request id, e.g. 4646-20261005T074425Z.")
]
_At = Annotated[str, typer.Option("--at", help="When it happened (UTC, e.g. 2026-10-06T12:28Z).")]


def event_from(request: str, event: str, fields: dict[str, object]) -> LedgerEvent:
    """The event recorded now, or the operator's message and exit 1."""
    data = {"request_id": request, "recorded_at": now(), "event": event, **fields}
    try:
        return parse_event(data)
    except ValidationError as exc:
        raise refuse(exc, under=event) from exc


def record_event(request: str, event: str, fields: dict[str, object]) -> None:
    parsed = event_from(request, event, fields)
    target = workspace()
    guarded(lambda: record_events(target, [parsed]))
    typer.echo(f"{request}: {event} recorded")


@innsyn_app.command("record-ack")
def record_ack(request: Request, at: _At) -> None:
    """Record the authority's acknowledgement of receipt."""
    record_event(request, "acknowledged", {"acknowledged_at": instant(at, "--at")})


@innsyn_app.command("record-case-number")
def record_case_number(
    request: Request,
    number: Annotated[str, typer.Option("--number", help="The authority's saksnummer.")],
) -> None:
    """Record the authority's saksnummer for the request."""
    record_event(request, "case_number", {"saksnummer": number})


@innsyn_app.command("record-outcome")
def record_outcome(
    request: Request,
    outcome: Annotated[str, typer.Option("--outcome", help=" | ".join(OUTCOMES))],
    at: _At,
    refusal_basis: Annotated[
        str | None,
        typer.Option("--refusal-basis", help="The hjemmel given, for refused or partial."),
    ] = None,
) -> None:
    """Record what the authority answered (or that it has not), and when."""
    fields = {"outcome": outcome, "at": instant(at, "--at"), "refusal_basis": refusal_basis}
    record_event(request, "outcome", fields)


@innsyn_app.command("record-klage")
def record_klage(request: Request, at: _At) -> None:
    """Record that a klage was sent on the request."""
    record_event(request, "klage", {"klage_sent_at": instant(at, "--at")})


@innsyn_app.command("import-pilot")
def import_pilot(
    register: Annotated[Path, typer.Option("--register", help="The pilot's sent.csv.")],
    letters: Annotated[
        Path | None,
        typer.Option("--letters", help="Where innsyn-<id>.txt are (default: beside it)."),
    ] = None,
) -> None:
    """Import the pilot register and its letters as sent; rows already held are skipped."""
    target = workspace()
    rows = guarded(lambda: read_register(register, letters or register.parent, now()))
    held = guarded(lambda: requests(target))
    fresh = [row for row in rows if row.request_id not in held]
    events = [event for row in fresh for event in row.events]
    if events:
        guarded(lambda: record_events(target, events))
    typer.echo(f"import-pilot: {len(fresh)} requests imported, {len(rows) - len(fresh)} held")


def _text(value: object) -> str:
    if value is None:
        return ""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _row(state: RequestState) -> str:
    sent = state.sent
    fields = (
        sent.request_id,
        sent.authority_id,
        sent.recipient,
        _text(sent.sent_at),
        str(len(sent.lf_ids)),
        "general" if sent.general_clause else "",
        _text(state.saksnummer),
        _text(state.acknowledged_at),
        _text(state.answered_at),
        _text(state.outcome),
        _text(state.klage_mark),
        _text(state.klage_sent_at),
        _text(sent.message_id),
        _text(state.undelivered),
        _text(state.reminded_at),
        _text(sent.follows),
    )
    return "\t".join(fields)


@innsyn_app.command("requests")
def list_requests() -> None:
    """List every request, tab-separated.

    Columns: request, authority, recipient, sent_at, LF ids, general clause,
    saksnummer, acknowledged_at, answered_at, outcome, § 32 mark, klage_sent_at,
    Message-ID, undelivered (bounced or mailbox_closed), last reminder sent, the request a
    follow-up follows.
    """
    held = guarded(lambda: requests(workspace()))
    for state in held.values():
        typer.echo(_row(state))
    typer.echo(f"{len(held)} requests", err=True)


def _today(raw: str | None) -> date:
    if raw is None:
        return now().astimezone(OSLO).date()
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise typer.BadParameter(f"--today: not an ISO date (YYYY-MM-DD): {raw}") from exc


@innsyn_app.command("due")
def due(
    today: Annotated[
        str | None, typer.Option("--today", help="The Oslo day to judge by (default: today).")
    ] = None,
) -> None:
    """List the klage candidates: past the § 32 andre ledd mark, no answer, no klage.

    Same columns as ``requests``. An interim reply without a date is not an answer;
    a letter recorded undelivered started no clock.
    """
    day = _today(today)
    chosen = [s for s in guarded(lambda: requests(workspace())).values() if s.is_due(day)]
    for state in chosen:
        typer.echo(_row(state))
    typer.echo(f"{len(chosen)} requests past their § 32 mark without an answer on {day}", err=True)
