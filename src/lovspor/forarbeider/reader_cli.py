"""``lovspor ops forarbeider-read``: the opt-in, read-only forarbeider reader (#738, PR3B-3).

Every call names its sandbox (``--sandbox``, required): there is no default
path and no environment switch, so nothing finds forarbeider by itself. A
document is named exactly by its government id, session and type. The reader
prints one versioned JSON record and writes nothing. Exit 1 is a typed
refusal on stderr that shows a reason code, never a path or document text.

Each command only calls its ``_impl`` here, which mutation testing reaches.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import click
import typer

from lovspor.forarbeider.corpus_reader import Lookup, Shelf, find, open_shelf, released_chapter
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.reader_views import (
    chapter_record,
    document_record,
    list_record,
    provenance_record,
    text_of,
)
from lovspor.forarbeider.release_cli import refusal_line
from lovspor.forarbeider.release_commands import forbidden_trees
from lovspor.ops.commands import ops_app

read_app = typer.Typer(
    help="Read-only reader of a synthetic sandbox's forarbeider dataset. Writes nothing."
)
ops_app.add_typer(read_app, name="forarbeider-read")

_Document = Annotated[
    str, typer.Option("--document", help="The government id, e.g. regjeringen-9000001.")
]
_Session = Annotated[str, typer.Option("--session", help="The session, e.g. 2099-2100.")]
_Type = Annotated[str, typer.Option("--type", help="prop-l or prop-ls.")]


def _shelf(sandbox: Path) -> Shelf:
    return open_shelf(sandbox, forbidden_trees())


def list_impl(sandbox: Path) -> str:
    return text_of(list_record(_shelf(sandbox)))


def show_impl(sandbox: Path, lookup: Lookup) -> str:
    shelf = _shelf(sandbox)
    return text_of(document_record(shelf, find(shelf, lookup)))


def chapter_impl(sandbox: Path, lookup: Lookup, number: int) -> str:
    member = find(_shelf(sandbox), lookup)
    return text_of(chapter_record(member, released_chapter(member, number)))


def provenance_impl(sandbox: Path, lookup: Lookup) -> str:
    shelf = _shelf(sandbox)
    return text_of(provenance_record(shelf, find(shelf, lookup)))


def _sandbox() -> Path:
    """The sandbox the group was given: every command reads only that one."""
    sandbox: Path = click.get_current_context().obj
    return sandbox


def _echo(action: Callable[[], str]) -> None:
    try:
        typer.echo(action())
    except ForarbeiderRefusedError as error:
        typer.echo(refusal_line(error), err=True)
        raise typer.Exit(code=1) from error


@read_app.callback()
def read_scope(
    ctx: typer.Context,
    sandbox: Annotated[
        Path, typer.Option("--sandbox", help="A sandbox made by sandbox-create; required.")
    ],
) -> None:
    """Read released synthetic forarbeider from one sandbox (issue #738, ADR-0018 PR3B-3).

    Offline and read-only. Preparatory material, not enacted law. Reading a
    synthetic sandbox is not approval to publish or host real text.
    """
    ctx.obj = sandbox


@read_app.command("list")
def list_documents() -> None:
    """List the released propositions."""
    _echo(lambda: list_impl(_sandbox()))


@read_app.command("show")
def show(document: _Document, session: _Session, kind: _Type) -> None:
    """Show one released proposition: labels, time axes and chapter receipts."""
    _echo(lambda: show_impl(_sandbox(), Lookup(document, session, kind)))


@read_app.command("chapter")
def chapter(
    document: _Document,
    session: _Session,
    kind: _Type,
    number: Annotated[int, typer.Option("--chapter", min=1, help="A released chapter's number.")],
) -> None:
    """Print one released chapter's synthetic text, annotated as preparatory material."""
    _echo(lambda: chapter_impl(_sandbox(), Lookup(document, session, kind), number))


@read_app.command("provenance")
def provenance(document: _Document, session: _Session, kind: _Type) -> None:
    """Show one released proposition's source receipts and the digests the reader verified."""
    _echo(lambda: provenance_impl(_sandbox(), Lookup(document, session, kind)))
