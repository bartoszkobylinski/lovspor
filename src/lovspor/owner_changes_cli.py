"""``lovspor ops owner-changes``: check the owner-change pin file.

Registered on the ``ops`` group (imported by :mod:`lovspor.ops_cli`), because
:mod:`lovspor.cli` sits at its size ratchet.

``check`` parses ``ops/lovverk-owner-changes`` and reports every grammar
problem. It reads one local file, runs no git command and touches no network,
so CI can run it on every pull request. Exit 0 on a valid file (an empty one
included), 1 on any problem, with one stderr line per problem.
"""

from pathlib import Path
from typing import Annotated

import typer

from lovspor.ops.commands import ops_app
from lovspor.owner_changes import PIN_FILE, PinFileError, load_pin_file

owner_changes_app = typer.Typer(
    name="owner-changes",
    help="Check the pin file of owner changes to lokale-forskrifter/.",
    no_args_is_help=True,
)
ops_app.add_typer(owner_changes_app)


@owner_changes_app.command(name="check")
def check(
    file: Annotated[Path, typer.Option("--file", help="The pin file to check.")] = PIN_FILE,
) -> None:
    """Accept the pin file only if every line follows the grammar."""
    try:
        pins = load_pin_file(file.expanduser())
    except PinFileError as exc:
        for problem in exc.problems:
            typer.echo(f"error: {file}: {problem}", err=True)
        raise typer.Exit(code=1) from exc
    noun = "pin" if len(pins) == 1 else "pins"
    typer.echo(f"ok {file}: {len(pins)} {noun}")
    for pin in pins:
        typer.echo(f"pin lovverk#{pin.pull_request} {pin.commit} attempt {pin.attempt}")
