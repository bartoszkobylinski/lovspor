"""lovspor command-line interface."""

from pathlib import Path
from typing import Annotated

import typer

from lovspor import __version__
from lovspor.mcp import serve as _mcp_serve
from lovspor.settings import Settings
from lovspor.sync.orchestrator import run_sync

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

    Twelve read-only tools are served — see ``docs/mcp.md`` for the
    full list, sample inputs/outputs, and the Sprint 9 anti-
    hallucination flow (semantic_search → get_section + cross_references
    → verify_quote → validate_citation). ``OPENAI_API_KEY`` is optional;
    missing key disables only ``semantic_search``, the other eleven
    tools work normally.
    """
    _mcp_serve(corpus_path.resolve())
