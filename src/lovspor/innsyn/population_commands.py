"""``innsyn fetch-population`` and ``innsyn population`` (issue #509, point 1)."""

from datetime import date
from typing import Annotated

import typer

from lovspor.errors import NetworkError, ParseError
from lovspor.innsyn.app import guarded, innsyn_app, now, refuse, workspace
from lovspor.innsyn.population import (
    PopulationSnapshot,
    fetch_population,
    klass_client,
    latest_snapshot,
    write_snapshot,
)
from lovspor.innsyn.workdays import OSLO


def _valid_at(raw: str | None) -> date:
    if raw is None:
        return now().astimezone(OSLO).date()
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise typer.BadParameter(f"--date: not an ISO date (YYYY-MM-DD): {raw}") from exc


def _counts(snapshot: PopulationSnapshot) -> str:
    kinds = [authority.kind for authority in snapshot.authorities]
    return (
        f"{kinds.count('kommune')} kommuner + {kinds.count('fylkeskommune')} fylkeskommuner "
        f"valid at {snapshot.valid_at}, read {snapshot.read_at.isoformat()} "
        f"({len(snapshot.excluded)} codes excluded)"
    )


@innsyn_app.command("fetch-population")
def fetch_population_command(
    valid_at: Annotated[
        str | None,
        typer.Option("--date", help="The date the division is read at (default: today, Oslo)."),
    ] = None,
) -> None:
    """Read every kommune and fylkeskommune from SSB KLASS into a new snapshot file."""
    target = workspace()
    day = _valid_at(valid_at)
    try:
        with klass_client() as client:
            snapshot = fetch_population(client, day, now())
    except (NetworkError, ParseError) as exc:
        raise refuse(exc) from exc
    path = guarded(lambda: write_snapshot(target, snapshot))
    typer.echo(f"population: {_counts(snapshot)} -> {path}")


@innsyn_app.command("population")
def population_command() -> None:
    """List the latest snapshot, tab-separated: id, kind, KLASS code, name.

    The sources it was read from and the excluded codes go to stderr.
    """
    snapshot = guarded(lambda: latest_snapshot(workspace()))
    for authority in snapshot.authorities:
        row = (authority.authority_id, authority.kind, authority.klass_code, authority.name)
        typer.echo("\t".join(row))
    for source in snapshot.sources:
        typer.echo(f"source: KLASS {source.classification_id} {source.url}", err=True)
    for code in snapshot.excluded:
        typer.echo(f"excluded: {code.code} {code.name}: {code.reason}", err=True)
    typer.echo(f"population: {_counts(snapshot)}", err=True)
