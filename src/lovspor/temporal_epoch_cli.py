"""``lovspor temporal-epoch``: the supported writers of the gate epoch.

ADR-0012 Amendment 1 point 1e: the epoch record is written through an
engine command, never a hand-made note. ``record-sync-run`` is the sync
workflow's mechanical write at run start; ``backfill`` is the operator's
record of an epoch that predates the mechanism (parser version 2).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import click
import typer
from pydantic import ValidationError

from lovspor.corpus_fetch import is_corpus
from lovspor.temporal_attestation import AttestationError
from lovspor.temporal_gate import (
    BackfillRequest,
    EpochReport,
    SyncRunRequest,
    backfill_epoch,
    is_repository_root,
    record_sync_run_epoch,
)

temporal_epoch_app = typer.Typer(
    name="temporal-epoch",
    help="Record the temporal gate epoch (ADR-0012 Amendment 1).",
    no_args_is_help=True,
)

_SyncRun = Annotated[
    str,
    typer.Option("--sync-run", help="The lovspor sync workflow run id that defines the epoch."),
]


@temporal_epoch_app.callback()
def _corpus(
    ctx: typer.Context,
    corpus_path: Annotated[
        Path,
        typer.Option("--corpus-path", help="A lovverk clone whose origin takes the push."),
    ],
) -> None:
    repo = corpus_path.expanduser()
    # Both, because a wrong path is a write to some other repository's
    # notes and a push to its origin: the top of a git clone that also
    # carries the corpus manifest.
    if not (is_repository_root(repo) and is_corpus(repo)):
        raise typer.BadParameter(
            f"{repo} is not the top level of a lovverk corpus clone (git + manifest.json)",
            param_hint="--corpus-path",
        )
    ctx.obj = repo


@temporal_epoch_app.command(name="record-sync-run")
def record_sync_run(sync_run: _SyncRun) -> None:
    """Record and push the epoch at sync-run start, before the gate.

    Writes only when neither a record nor an attestation exists under
    the engine's parser version; with attestations but no record it
    warns and writes nothing."""
    try:
        request = SyncRunRequest.model_validate({"sync_run": sync_run})
    except ValidationError as exc:
        _usage_error(exc)
    _run(lambda repo: record_sync_run_epoch(repo, request, datetime.now(UTC)))


@temporal_epoch_app.command(name="backfill")
def backfill(
    epoch_at: Annotated[
        str, typer.Option("--epoch-at", help="UTC instant, e.g. 2026-09-04T08:45:36Z.")
    ],
    boundary_commit: Annotated[
        str, typer.Option("--boundary-commit", help="Last pre-epoch state.")
    ],
    sync_run: _SyncRun,
    apply: Annotated[
        bool, typer.Option("--apply", help="Write and push; default is a dry run.")
    ] = False,
) -> None:
    """Backfill the epoch for the engine's temporal parser version.

    Dry run by default: fetches the registry refs from origin, validates
    the record exactly as the write would, and writes nothing. ``--apply``
    writes the note and pushes refs/notes/temporal-attestations-epoch."""
    try:
        request = BackfillRequest.model_validate(
            {
                "epoch_at": epoch_at,
                "boundary_commit": boundary_commit,
                "sync_run": sync_run,
                "apply": apply,
            },
        )
    except ValidationError as exc:
        _usage_error(exc)
    _run(lambda repo: backfill_epoch(repo, request, datetime.now(UTC)))


def _usage_error(exc: ValidationError) -> NoReturn:
    """Malformed input: exit 2, the usage-error code, before any git work."""
    typer.echo(f"error: {exc}", err=True)
    raise typer.Exit(code=2) from exc


def _run(action: Callable[[Path], EpochReport]) -> None:
    repo: Path = click.get_current_context().obj
    try:
        report = action(repo)
    except AttestationError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"{'warning: ' if report.warning else ''}{report.message}", err=report.warning)
    if report.record is not None:
        typer.echo(report.record.model_dump_json(indent=2))
