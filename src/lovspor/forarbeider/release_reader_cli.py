"""``lovspor ops forarbeider-release-read``: the real-release reader (#738, B2).

Apart from the synthetic ``forarbeider-read`` by command, module and record
type. Every call names the owner's private workspace (``--workspace``, where
the release receipts are) and a lovverk release clone on a published ``main``
(``--checkout``): there is no default path and no environment switch. It
prints one versioned JSON record and writes nothing. Exit 1 is a typed refusal
on stderr that shows a reason code, never a path or document text.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, NamedTuple

import click
import typer

from lovspor.forarbeider.apply_preflight import existing_workspace
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.reader_views import text_of
from lovspor.forarbeider.release_cli import refusal_line
from lovspor.forarbeider.release_commands import ReleaseScope
from lovspor.forarbeider.release_reader import (
    ReleaseShelf,
    chapter_markdown,
    find_release,
    open_release_shelf,
)
from lovspor.forarbeider.release_reader_views import (
    release_chapter_record,
    release_document_record,
    release_list_record,
    release_provenance_record,
)
from lovspor.ops.commands import ops_app

release_read_app = typer.Typer(
    help="Read-only reader of released forarbeider in a published lovverk checkout."
)
ops_app.add_typer(release_read_app, name="forarbeider-release-read")

_Document = Annotated[
    str, typer.Option("--document", help="The released document's government id.")
]


class ReadScope(NamedTuple):
    """The owner's private workspace and the lovverk checkout to read."""

    workspace: Path
    checkout: Path


def _shelf(scope: ReadScope) -> ReleaseShelf:
    workspace = existing_workspace(ReleaseScope(workspace=scope.workspace, ledger=scope.workspace))
    return open_release_shelf(workspace, scope.checkout)


def list_impl(scope: ReadScope) -> str:
    return text_of(release_list_record(_shelf(scope)))


def show_impl(scope: ReadScope, document: str) -> str:
    shelf = _shelf(scope)
    return text_of(release_document_record(shelf, find_release(shelf, document)))


def chapter_impl(scope: ReadScope, document: str, number: int) -> str:
    release = find_release(_shelf(scope), document)
    receipt, markdown = chapter_markdown(release, number)
    return text_of(release_chapter_record(release, receipt, markdown))


def provenance_impl(scope: ReadScope, document: str) -> str:
    shelf = _shelf(scope)
    return text_of(release_provenance_record(shelf, find_release(shelf, document)))


def _scope() -> ReadScope:
    scope: ReadScope = click.get_current_context().obj
    return scope


def _echo(action: Callable[[], str]) -> None:
    try:
        typer.echo(action())
    except ForarbeiderRefusedError as error:
        typer.echo(refusal_line(error), err=True)
        raise typer.Exit(code=1) from error


@release_read_app.callback()
def read_scope(
    ctx: typer.Context,
    workspace: Annotated[
        Path,
        typer.Option("--workspace", help="The owner-only release workspace holding the receipts."),
    ],
    checkout: Annotated[
        Path,
        typer.Option("--checkout", help="A lovverk release clone on main, equal to origin/main."),
    ],
) -> None:
    """Read released forarbeider from a published lovverk main (issue #738, B2).

    Offline and read-only. Preparatory material, not enacted law. A document is
    served only when its bytes, the commit and the owner's release records agree.
    """
    ctx.obj = ReadScope(workspace, checkout)


@release_read_app.command("list")
def list_documents() -> None:
    """List the released propositions."""
    _echo(lambda: list_impl(_scope()))


@release_read_app.command("show")
def show(document: _Document) -> None:
    """Show one released proposition: labels, coverage, time axes and chapter receipts."""
    _echo(lambda: show_impl(_scope(), document))


@release_read_app.command("chapter")
def chapter(
    document: _Document,
    number: Annotated[int, typer.Option("--chapter", min=1, help="A released chapter's number.")],
) -> None:
    """Print one released chapter's text, annotated as preparatory material."""
    _echo(lambda: chapter_impl(_scope(), document, number))


@release_read_app.command("provenance")
def provenance(document: _Document) -> None:
    """Show one released proposition's clearances and the owner records the reader verified."""
    _echo(lambda: provenance_impl(_scope(), document))
