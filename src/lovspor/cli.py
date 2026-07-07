"""lovspor command-line interface."""

from pathlib import Path
from typing import Annotated

import typer

from lovspor import __version__
from lovspor.mcp import serve as _mcp_serve
from lovspor.settings import Settings, load_env
from lovspor.sync.orchestrator import mark_undersized_embeddings_stale, run_sync

app = typer.Typer(
    name="lovspor",
    help="Norwegian law change tracker. Engine for the lovverk corpus.",
    add_completion=False,
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"lovspor {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Norwegian law change tracker."""
    # Load .env here, in the group callback, so it is applied BEFORE Typer
    # resolves any subcommand option's ``envvar=`` (e.g. the mcp command's
    # LOVVERK_CORPUS_PATH). Doing it inside the command body — or in
    # serve() — is too late: the option is resolved during arg parsing and
    # a value living only in .env would be missed, exiting with code 2.
    load_env()


@app.command()
def info() -> None:
    """Show project information."""
    typer.echo(f"lovspor {__version__}")
    typer.echo("Engine producing the lovverk Norwegian law corpus.")
    typer.echo("Repo:   https://github.com/bartoszkobylinski/lovspor")
    typer.echo("Corpus: https://github.com/bartoszkobylinski/lovverk")


@app.command()
def seed() -> None:
    """Initial population of the lovverk corpus from Lovdata public data.

    Intended for the first run against an empty corpus. Technically the
    same pipeline as ``sync`` — the change detector treats a missing
    manifest as 'everything is new', so on a fresh lovverk every upstream
    document classifies as new. Settings are resolved from the environment
    (see ``.env.example``).
    """
    settings = Settings.from_env()
    report = run_sync(settings)
    typer.echo(
        f"Seeded corpus at {settings.lovverk_repo_path}: {report.new_count} documents added.",
    )


@app.command()
def sync() -> None:
    """Incremental sync against the current Lovdata public-data tarballs.

    Typically invoked by the scheduled workflow. Reads the existing
    manifest, downloads current tarballs, classifies each document, and
    commits only the changed ones.
    """
    settings = Settings.from_env()
    report = run_sync(settings)
    typer.echo(
        f"Sync complete at {settings.lovverk_repo_path}: "
        f"{report.new_count} new, "
        f"{report.changed_count} changed, "
        f"{report.removed_count} removed, "
        f"{report.unchanged_count} unchanged.",
    )


@app.command(name="repair-embeddings")
def repair_embeddings() -> None:
    """Flag documents whose embeddings under-count their current sections.

    One-time repair for a corpus embedded before a section-parser fix — e.g.
    flat acts whose sections render at H2 (``## § N.``) produced zero vectors
    and are invisible to ``semantic_search``. Clears each affected record's
    ``embedding_hash`` and commits the manifest; run ``lovspor sync`` afterwards
    (with ``OPENAI_API_KEY`` set) to rebuild the vectors via the Sprint 9
    backfill. A no-op — no commit — when every embedding is already current.
    """
    settings = Settings.from_env()
    count = mark_undersized_embeddings_stale(settings)
    typer.echo(
        f"Flagged {count} document(s) for re-embed. "
        "Run `lovspor sync` with OPENAI_API_KEY set to rebuild their vectors.",
    )


@app.command()
def mcp(
    corpus_path: Annotated[
        Path,
        typer.Option(
            "--corpus-path",
            help="Path to a local clone of the lovverk corpus.",
            envvar="LOVVERK_CORPUS_PATH",
        ),
    ],
) -> None:
    """Start the stdio MCP server exposing the lovverk corpus to AI clients.

    Designed to be launched as a subprocess by an MCP client (Claude
    Desktop, Claude Code, ...). Reads the corpus from ``--corpus-path``;
    does not pull from GitHub or trigger an engine sync.

    Sixteen read-only tools are served — see ``docs/mcp.md`` for the
    full list, sample inputs/outputs, and the Sprint 9 anti-
    hallucination flow (semantic_search → get_section + cross_references
    → verify_quote → validate_citation). ``OPENAI_API_KEY`` is optional;
    missing key disables only ``semantic_search``, the other fifteen
    tools work normally.
    """
    _mcp_serve(corpus_path.resolve())
