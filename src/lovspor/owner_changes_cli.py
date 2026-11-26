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

from lovspor.errors import LovsporError
from lovspor.ops.commands import ops_app
from lovspor.owner_changes import PIN_FILE, PinFileError, load_pin_file
from lovspor.owner_changes_run import RunContext, apply_pins, check_applied, write_report

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


_Corpus = Annotated[Path, typer.Option("--corpus-path", help="The lovverk clone.")]
_Report = Annotated[Path, typer.Option("--report", help="JSON report of the apply pass.")]


@owner_changes_app.command(name="apply")
def apply(
    corpus_path: _Corpus,
    report: _Report,
    file: Annotated[Path, typer.Option("--file", help="The pin file.")] = PIN_FILE,
    run: Annotated[str, typer.Option("--run", help="<lovspor sha>:<run id>.")] = "local:0",
) -> None:
    """Apply every pin to the clone; a refused pin is reported, never fatal."""
    lovspor_sha, _, run_id = run.partition(":")
    try:
        outcome = apply_pins(corpus_path, file, RunContext(lovspor_sha, run_id))
    except LovsporError as exc:
        typer.echo(f"error: {' '.join(str(exc).split())}", err=True)
        raise typer.Exit(code=1) from exc
    write_report(outcome, report)
    for problem in outcome.file_problems:
        typer.echo(f"::warning::pin file refused, no pin applied: {problem}")
    for entry in outcome.refused:
        typer.echo(f"::warning::pinned owner change not applied: {entry['detail']}")
    for entry in outcome.applied:
        typer.echo(f"applied owner change {entry['summary']}")
    if not (outcome.file_problems or outcome.refused or outcome.applied or outcome.skipped):
        typer.echo("no pinned owner change")


@owner_changes_app.command(name="check-applied")
def check_applied_command(corpus_path: _Corpus, report: _Report) -> None:
    """Fail if the sync changed any file of a pin applied in this run."""
    changed = check_applied(corpus_path, report)
    for path in changed:
        typer.echo(f"error: pinned file changed after the merge: {path}", err=True)
    if changed:
        raise typer.Exit(code=1)
    typer.echo("pinned owner changes are unchanged")
