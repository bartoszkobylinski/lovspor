"""The LF ledger commands, and the step the nightly runs after its sweep (issue #509).

`lf-ledger` brings the first-seen ledger up to date with the log; `lf-first-seen`
lists what it learned on given days, which is the list the next-day request
under offentleglova is made from. Their own module because
:mod:`lovspor.observatory.commands` is at its size ratchet; that module imports
:func:`refresh_after_sweep` from here, and the entrypoint imports this module
for its registrations.
"""

from datetime import date
from typing import Annotated

import typer

from lovspor.errors import LogIntegrityError
from lovspor.observatory.app import observatory_app
from lovspor.observatory.lf_ledger import (
    LEDGER_DAY_ZONE,
    LedgerEntry,
    LedgerUpdate,
    first_seen_between,
    read_ledger,
    update_ledger,
)
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.registry_io import _root
from lovspor.observatory.storage import ObservatoryRoot


def _summary(update: LedgerUpdate, held: list[LedgerEntry]) -> str:
    authorities = len({entry.authority_id for entry in held})
    return (
        f"lf-ledger: read {update.html_records} html records from byte "
        f"{update.started_at_offset} ({update.blobs_read} blobs, "
        f"{update.blobs_missing} missing); {len(update.appended)} new; "
        f"ledger holds {len(held)} entries across {authorities} authorities"
    )


@observatory_app.command("lf-ledger")
def lf_ledger(
    rebuild: Annotated[
        bool,
        typer.Option("--rebuild", help="Read the whole log, not only what the cursor has not."),
    ] = False,
) -> None:
    """Bring the first-seen ledger of linked Lovdata local-regulation ids up to date.

    Reads only the observation-log records appended since the last update, so
    running it again is cheap and appends nothing it already holds.
    """
    log = ObservationLog(_root())
    try:
        update = update_ledger(log, rebuild=rebuild)
        held = read_ledger(log)
    except LogIntegrityError as exc:
        typer.echo(f"Refused: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(_summary(update, held))


def refresh_after_sweep(root: ObservatoryRoot) -> None:
    """The nightly's post-sweep step: loud on failure, never fatal.

    The sweep is the point of the night and its exit code is about the sweep.
    A ledger that could not be updated is said on stderr and caught up by the
    next update, which reads from where this one would have started.
    """
    log = ObservationLog(root)
    try:
        update = update_ledger(log)
        held = read_ledger(log)
    except (LogIntegrityError, OSError) as exc:
        typer.echo(f"lf-ledger not updated: {exc}", err=True)
        return
    typer.echo(_summary(update, held))


def _days(day: str | None, since: str | None) -> tuple[date, date]:
    """The inclusive range asked for: one day, or a day onwards."""
    if (day is None) == (since is None):
        raise typer.BadParameter("give exactly one of --day or --since")
    raw = str(day if day is not None else since)
    try:
        first = date.fromisoformat(raw)
    except ValueError as exc:
        raise typer.BadParameter(f"not an ISO date (YYYY-MM-DD): {raw}") from exc
    return first, first if day is not None else date.max


def _row(entry: LedgerEntry) -> str:
    local_day = entry.first_seen.astimezone(LEDGER_DAY_ZONE).date().isoformat()
    first_seen = entry.first_seen.isoformat()
    fields = (local_day, first_seen, entry.authority_id, entry.kind, entry.lf_id, entry.url)
    return "\t".join(fields)


@observatory_app.command("lf-first-seen")
def lf_first_seen(
    day: Annotated[
        str | None, typer.Option("--day", help="One Oslo calendar day, YYYY-MM-DD.")
    ] = None,
    since: Annotated[
        str | None, typer.Option("--since", help="This Oslo calendar day and every later one.")
    ] = None,
) -> None:
    """List the ledger entries first seen on a day, tab-separated, oldest first.

    Columns: Oslo day, first ObservedAt (UTC), authority, kind, id, page. The
    count goes to stderr so the rows stay a clean table on stdout.
    """
    first, last = _days(day, since)
    try:
        entries = read_ledger(ObservationLog(_root()))
    except LogIntegrityError as exc:
        typer.echo(f"Refused: {exc}", err=True)
        raise typer.Exit(1) from exc
    chosen = sorted(first_seen_between(entries, first, last), key=lambda e: (e.first_seen, e.key))
    for entry in chosen:
        typer.echo(_row(entry))
    typer.echo(f"{len(chosen)} entries first seen {first} .. {last}", err=True)
