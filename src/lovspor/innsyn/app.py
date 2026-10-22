"""The ``observatory innsyn`` Typer app, and what every innsyn command shares.

Separate from the command modules for the reason :mod:`lovspor.observatory.app`
is: several modules register commands on one app without importing each other.
The workspace is the group's option (``--dir`` / ``LOVSPOR_INNSYN_DIR``), read
by every command through :func:`workspace`, so no command spends one of its
four parameters on it.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import click
import typer
from pydantic import ValidationError
from pydantic_core import ErrorDetails

from lovspor.errors import ConfigError, InnsynError, StorageUnavailableError
from lovspor.innsyn.workspace import ENV_INNSYN_DIR, InnsynWorkspace, innsyn_workspace

innsyn_app = typer.Typer(
    name="innsyn",
    help="Innsyn requests under offentleglova to every kommune and fylkeskommune (#509).",
    no_args_is_help=True,
)

_DIR_KEY = "lovspor.innsyn.dir"


@innsyn_app.callback()
def _workspace_option(
    ctx: typer.Context,
    directory: Annotated[
        Path | None,
        typer.Option(
            "--dir",
            envvar=ENV_INNSYN_DIR,
            help="The innsyn workspace: population, directory and request ledger.",
        ),
    ] = None,
) -> None:
    # Resolved by each command, not here: a command's own --help must work unset.
    ctx.meta[_DIR_KEY] = directory


def workspace() -> InnsynWorkspace:
    """The validated workspace, or the operator's message and exit 1."""
    raw = click.get_current_context().meta.get(_DIR_KEY)
    try:
        return innsyn_workspace(raw)
    except ConfigError as exc:
        typer.echo(f"Cannot use the innsyn directory: {exc}", err=True)
        raise typer.Exit(1) from exc


def refuse(exc: Exception, under: str | None = None) -> typer.Exit:
    """Say why on stderr; the caller raises the returned exit from ``exc``.

    A ``ValidationError`` is one line of its reasons, each under the field it
    names, never pydantic's union type names or its documentation URL (#628).
    ``under`` is the tag the value was validated under in a tagged union; it
    heads every location and the operator already typed it, so it is dropped.
    """
    reason = _reasons(exc, under) if isinstance(exc, ValidationError) else str(exc)
    typer.echo(f"Refused: {reason}", err=True)
    return typer.Exit(1)


def _reasons(exc: ValidationError, under: str | None) -> str:
    return "; ".join(_reason(error, under) for error in exc.errors())


def _reason(error: ErrorDetails, under: str | None) -> str:
    message = error["msg"].removeprefix("Value error, ")
    location = [str(part) for part in error["loc"]]
    if under is not None and location[:1] == [under]:
        location = location[1:]
    return f"{'.'.join(location)}: {message}" if location else message


def guarded[ResultT](action: Callable[[], ResultT]) -> ResultT:
    """Run ``action``; an innsyn refusal or a missing volume is a message, not a traceback."""
    try:
        return action()
    except (InnsynError, StorageUnavailableError) as exc:
        raise refuse(exc) from exc


def instant(raw: str, option: str) -> datetime:
    """An ISO-8601 instant with an explicit offset, as UTC.

    Raises:
        typer.BadParameter: naive or not ISO-8601; a naive time invents a zone.
    """
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise typer.BadParameter(f"{option}: not an ISO-8601 instant: {raw}") from exc
    if parsed.tzinfo is None:
        raise typer.BadParameter(f"{option}: give an offset or Z, e.g. 2026-10-05T07:43:43Z")
    return parsed.astimezone(UTC)


def now() -> datetime:
    return datetime.now(UTC)
