"""``lovspor ops embedding-cache``: inspect and maintain the durable cache (#802).

Registered on the ``ops`` group from here, because :mod:`lovspor.cli` sits at
its size ratchet. Each decorated command only calls an undecorated ``*_impl``
(decisions.md §9i), so the bodies stay inside the mutation gate.

Exit codes: 0 done; 1 an entry was refused or expired (``verify``) or the store refused
to open; 2 ``LOVSPOR_EMBEDDING_CACHE_DIR`` is not set. No command calls a
provider, and output is counts and key prefixes only.
"""

import os
from pathlib import Path
from typing import Annotated

import typer

from lovspor.embeddings.cache import CACHE_DIR_VARIABLE, read_policy_from_env
from lovspor.embeddings.cache_admin import invalidate, prune, status, verify
from lovspor.embeddings.cache_store import LocalEntryStore
from lovspor.errors import EmbeddingCacheError
from lovspor.ops.commands import ops_app

EXIT_REFUSED = 1
EXIT_NOT_CONFIGURED = 2

cache_app = typer.Typer(
    name="embedding-cache",
    help="Inspect and maintain the durable embedding cache. Never calls a provider.",
    no_args_is_help=True,
)
ops_app.add_typer(cache_app)


def open_configured_store() -> LocalEntryStore:
    """The store ``LOVSPOR_EMBEDDING_CACHE_DIR`` names, or exit 2 when unset."""
    raw = os.environ.get(CACHE_DIR_VARIABLE)
    if not raw:
        typer.echo(f"{CACHE_DIR_VARIABLE} is not set; the embedding cache is off.", err=True)
        raise typer.Exit(EXIT_NOT_CONFIGURED)
    try:
        return LocalEntryStore.open(Path(raw), (), read_policy_from_env())
    except EmbeddingCacheError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(EXIT_REFUSED) from error


def status_impl() -> int:
    for line in status(open_configured_store()).lines():
        typer.echo(line)
    return 0


def verify_impl() -> int:
    report = verify(open_configured_store())
    for reason in (*report.refused, *report.expired_reasons):
        typer.echo(reason, err=True)
    typer.echo(report.line())
    return EXIT_REFUSED if report.failed else 0


def prune_impl() -> int:
    report = prune(open_configured_store())
    for reason in report.refused:
        typer.echo(reason, err=True)
    typer.echo(report.line())
    return 0


def invalidate_impl(esi: str, input_hash: str | None) -> int:
    try:
        removed = invalidate(open_configured_store(), esi, input_hash)
    except EmbeddingCacheError as error:
        typer.echo(str(error), err=True)
        return EXIT_REFUSED
    typer.echo(f"removed={removed}")
    return 0


@cache_app.command("status")
def status_command() -> None:
    """Entry counts and bytes per embedding space, and stray temporary files."""
    raise typer.Exit(status_impl())


@cache_app.command("verify")
def verify_command() -> None:
    """Validate every entry; exit 1 if any is refused."""
    raise typer.Exit(verify_impl())


@cache_app.command("prune")
def prune_command() -> None:
    """Remove expired entries, superseded unit checkpoints and old temporary files."""
    raise typer.Exit(prune_impl())


@cache_app.command("invalidate")
def invalidate_command(
    esi: Annotated[str, typer.Option("--esi", help="32-hex embedding space identity.")],
    input_hash: Annotated[
        str | None,
        typer.Option("--input-hash", help="64-hex ADR-0006 input hash; omit for the whole space."),
    ] = None,
) -> None:
    """Remove one document's entries, or every entry of one space."""
    raise typer.Exit(invalidate_impl(esi, input_hash))
