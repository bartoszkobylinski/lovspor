"""lovspor command-line interface."""

from pathlib import Path
from typing import Annotated

import typer

from lovspor import __version__
from lovspor.corpus_fetch import default_corpus_path, fetch_corpus
from lovspor.errors import ConfigError
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


@app.command(name="fetch-corpus")
def fetch_corpus_command(
    dest: Annotated[
        Path | None,
        typer.Option(
            "--dest",
            help="Where to clone/update the corpus (default: ~/.cache/lovverk).",
            envvar="LOVVERK_CORPUS_PATH",
        ),
    ] = None,
) -> None:
    """Clone or update the local lovverk corpus that ``lovspor mcp`` reads.

    First run shallow-clones the public corpus to ``--dest`` (or the default
    cache); later runs fast-forward it. With no ``--dest`` and no
    ``LOVVERK_CORPUS_PATH``, ``lovspor mcp`` then finds it automatically — so
    the whole consumer flow is ``lovspor fetch-corpus`` then ``lovspor mcp``.
    """
    result = fetch_corpus(dest or default_corpus_path())
    typer.echo(f"Corpus {result.action} at {result.path}.")


@app.command()
def mcp(
    corpus_path: Annotated[
        Path | None,
        typer.Option(
            "--corpus-path",
            help="Path to a local lovverk clone (default: the fetch-corpus cache).",
            envvar="LOVVERK_CORPUS_PATH",
        ),
    ] = None,
) -> None:
    """Start the stdio MCP server exposing the lovverk corpus to AI clients.

    Designed to be launched as a subprocess by an MCP client (Claude
    Desktop, Claude Code, ...). Reads the corpus from ``--corpus-path`` (or
    ``LOVVERK_CORPUS_PATH``); with neither set it falls back to the
    ``fetch-corpus`` cache (``~/.cache/lovverk``). Does not pull from GitHub
    or trigger an engine sync.

    Sixteen read-only tools are served — see ``docs/mcp.md`` for the
    full list, sample inputs/outputs, and the Sprint 9 anti-
    hallucination flow (semantic_search → get_section + cross_references
    → verify_quote → validate_citation). ``OPENAI_API_KEY`` is optional;
    missing key disables only ``semantic_search``, the other fifteen
    tools work normally.
    """
    target = (corpus_path or default_corpus_path()).expanduser()
    if not target.exists():
        raise ConfigError(
            f"No lovverk corpus at {target}. Run `lovspor fetch-corpus` first, "
            "or pass --corpus-path / set LOVVERK_CORPUS_PATH.",
        )
    _mcp_serve(target.resolve())
