"""``lovspor ops forarbeider-release``: the private release preflight (#738, ADR-0018 PR3B-1).

The group takes the workspace and the PR3A ledger; its ``chapters`` subgroup
takes one proposition's staged files, because a command may take four
parameters and a release needs more. Each command only calls its ``_impl`` in
:mod:`lovspor.forarbeider.release_commands`.

Exit 1 is a typed refusal, with nothing written. Exit 2 is a written ``deny``
report. Exit 0 is an ``eligible_preview`` report, which still authorizes no
publication: no command here writes to ``lovverk`` or any Git checkout.
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.release_commands import (
    ChapterScope,
    Grant,
    ReleaseScope,
    Run,
    authorize_impl,
    dossier_impl,
    preflight_impl,
    revoke_impl,
    show_impl,
)
from lovspor.ops.commands import ops_app

DENY_EXIT = 2
#: Reason codes, roles, chapter ids and digests. A path has a separator and is never printed.
_SAFE_DETAIL = re.compile(r"[A-Za-z0-9_.,: -]*")

release_app = typer.Typer(help="Private, offline release preflight. Publishes nothing.")
chapters_app = typer.Typer(help="Commands on the staged chapters of one proposition.")
ops_app.add_typer(release_app, name="forarbeider-release")
release_app.add_typer(chapters_app, name="chapters")


def refusal_line(error: ForarbeiderRefusedError) -> str:
    """The refusal as printed; a detail that could be a path or a name is shown as ``input``."""
    detail = error.detail if _SAFE_DETAIL.fullmatch(error.detail) else "input"
    return f"refused: {error.reason.value}: {detail}"


def _echo(action: Callable[[], str]) -> None:
    try:
        typer.echo(action())
    except ForarbeiderRefusedError as error:
        typer.echo(refusal_line(error), err=True)
        raise typer.Exit(code=1) from error


def run_preflight(scope: ChapterScope, run: Run) -> None:
    """Print the verdict; a ``deny`` exits 2 after its report is written."""
    try:
        lines, preview = preflight_impl(scope, run)
    except ForarbeiderRefusedError as error:
        typer.echo(refusal_line(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(lines)
    if not preview:
        raise typer.Exit(code=DENY_EXIT)


_Actor = Annotated[str, typer.Option(help="HANDLE@ISO-8601-with-offset; a handle, never a name.")]
_Digest = Annotated[str, typer.Option(help="The sha256 a previous command printed.")]


@release_app.callback()
def release_scope(
    ctx: typer.Context,
    workspace: Annotated[
        Path, typer.Option("--workspace", help="Absolute owner-only directory outside Git.")
    ],
    ledger: Annotated[Path, typer.Option("--ledger", help="The PR3A review ledger.")],
) -> None:
    """Private release preflight for staged chapters (issue #738, ADR-0018 PR3B-1).

    Offline. Writes only its own workspace, never lovverk or a Git checkout.
    Even an eligible preview is not permission to publish.
    """
    ctx.obj = ReleaseScope(workspace=workspace, ledger=ledger)


@chapters_app.callback()
def chapter_scope(
    ctx: typer.Context,
    metadata: Annotated[Path, typer.Option("--metadata", help="The PR1 metadata record.")],
    observation: Annotated[
        list[Path], typer.Option("--observation", help="A PR2 chapter result; may be repeated.")
    ],
    page: Annotated[
        list[Path] | None,
        typer.Option("--page", help="A saved page those were read from; may be repeated."),
    ] = None,
) -> None:
    """The staged files of one proposition, all private and outside Git."""
    ctx.obj = ChapterScope(
        release=ctx.obj, metadata=metadata, observations=tuple(observation), pages=tuple(page or ())
    )


@chapters_app.command("dossier")
def dossier(
    ctx: typer.Context,
    evidence: Annotated[
        list[str],
        typer.Option(help="KIND@CHAPTER[,CHAPTER]=/absolute/private/file; may be repeated."),
    ],
    reviewed: _Actor,
) -> None:
    """Record the case-specific evidence dossier for the staged chapters."""
    _echo(lambda: dossier_impl(ctx.obj, evidence, reviewed))


@chapters_app.command("authorize")
def authorize(
    ctx: typer.Context,
    dossier: _Digest,
    decided: _Actor,
    valid: Annotated[str, typer.Option(help="FROM/UNTIL, ISO 8601 with offsets.")],
) -> None:
    """Record the owner's release authorization candidate at a terminal. Publishes nothing."""
    grant = Grant(dossier=dossier, decided=decided, valid=valid)
    _echo(lambda: authorize_impl(ctx.obj, grant))


@chapters_app.command("preflight")
def preflight(
    ctx: typer.Context,
    at: Annotated[str, typer.Option(help="The moment evaluated, ISO 8601 with offset.")],
    authorization: Annotated[str | None, typer.Option(help="The authorization sha256.")] = None,
    checkpoint: Annotated[
        list[str] | None,
        typer.Option(help="SUBJECT@REVISION:HEAD kept outside the ledger; may be repeated."),
    ] = None,
) -> None:
    """Evaluate the staged chapters and write a private deny or preview report."""
    run = Run(authorization=authorization, checkpoints=tuple(checkpoint or ()), at=at)
    run_preflight(ctx.obj, run)


@release_app.command("revoke")
def revoke(ctx: typer.Context, authorization: _Digest, decided: _Actor) -> None:
    """Withdraw an authorization. A later preflight denies it."""
    _echo(lambda: revoke_impl(ctx.obj, authorization, decided))


@release_app.command("show")
def show(
    ctx: typer.Context,
    kind: Annotated[str, typer.Argument(help="dossier, authorization or report.")],
    sha: Annotated[str, typer.Argument(help="The record's sha256.")],
) -> None:
    """Show a stored record's ids, digests and codes, never paths or names."""
    _echo(lambda: show_impl(ctx.obj, kind, sha))
