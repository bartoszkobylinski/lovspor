"""lovspor command-line interface."""

from pathlib import Path
from typing import Annotated

import typer

from lovspor import __version__
from lovspor.corpus_audit import AuditReport, audit_corpus
from lovspor.corpus_fetch import default_corpus_path, fetch_corpus, is_corpus
from lovspor.errors import ConfigError
from lovspor.github_output import append_step_summary, set_output
from lovspor.mcp import serve as _mcp_serve
from lovspor.rendering.markdown_renderer import RENDERER_VERSION
from lovspor.settings import Settings, load_env
from lovspor.storage.manifest import read_manifest
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
def sync(
    force_rerender: bool = typer.Option(
        False,
        "--force-rerender",
        help=(
            "Re-render every document even when its XML is unchanged, so a "
            "renderer fix reaches files the change detector would skip forever. "
            "Byte-identical re-renders are skipped, so only genuinely different "
            "output is written, embedded, and committed."
        ),
    ),
) -> None:
    """Incremental sync against the current Lovdata public-data tarballs.

    Typically invoked by the scheduled workflow. Reads the existing
    manifest, downloads current tarballs, classifies each document, and
    commits only the changed ones.
    """
    settings = Settings.from_env()
    report = run_sync(settings, force_rerender=force_rerender)
    _warn_schema_drift(report.unknown_archive_fields)
    typer.echo(
        f"Sync complete at {settings.lovverk_repo_path}: "
        f"{report.new_count} new, "
        f"{report.changed_count} changed, "
        f"{report.removed_count} removed, "
        f"{report.unchanged_count} unchanged.",
    )


def _warn_schema_drift(fields: tuple[str, ...]) -> None:
    """Report unknown upstream fields to the user and the CI runner.

    Non-fatal: the sync already ran. Echoes a warning, and — under GitHub
    Actions — sets a step output (``schema_drift_fields``) and a job-summary
    note so the workflow can open an issue. No-op when the schema matched.
    """
    if not fields:
        return
    joined = ", ".join(fields)
    typer.echo(
        f"WARNING: Lovdata /list returned unknown field(s): {joined}. "
        "Tolerated; update LovdataArchive to model them.",
        err=True,
    )
    set_output("schema_drift_fields", ",".join(fields))
    append_step_summary(
        f"### Lovdata schema drift\nUnknown `/list` field(s): `{joined}`. "
        "Tolerated by the sync; update `LovdataArchive`.",
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


def _echo_audit(report: AuditReport) -> None:
    """Print findings grouped by kind, most-common kind first."""
    if report.clean:
        typer.echo(f"Corpus is clean — {report.documents_checked} current document(s), no drift.")
        return
    by_kind: dict[str, list[str]] = {}
    for finding in report.findings:
        by_kind.setdefault(finding.kind, []).append(finding.path)
    typer.echo(
        f"Corpus drift: {len(report.findings)} finding(s) "
        f"across {report.documents_checked} current document(s).\n",
    )
    for kind, paths in sorted(by_kind.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        typer.echo(f"{kind} ({len(paths)}):")
        for path in paths:
            typer.echo(f"  {path}")
        typer.echo("")


@app.command()
def audit(
    corpus_path: Annotated[
        Path | None,
        typer.Option("--corpus-path", help="Corpus to audit (default: the configured lovverk)."),
    ] = None,
) -> None:
    """Reconcile the corpus on disk against the manifest, and report the drift.

    Every change-detection path in the engine compares *upstream* against the
    *manifest*; nothing compares the *manifest* against *disk*. A file that has
    fallen out of the manifest is therefore invisible to all of them and can
    never self-heal — which is how 48 repealed regulations sat in `lovverk` for
    seven weeks. This is the check that catches that class of drift.

    Reports: documents on disk that no record claims (`orphan_document`),
    tombstoned records whose file was never deleted (`tombstoned_but_present`),
    current records with no file (`missing_document`), embedding sidecars no
    record owns (`orphan_embedding`), and documents left behind by a renderer
    bump (`stale_render`).

    Read-only — it never writes or deletes. Exits non-zero when drift is found,
    so it can gate CI.
    """
    root = corpus_path or Settings.from_env().lovverk_repo_path
    report = audit_corpus(root, read_manifest(root / "manifest.json"), RENDERER_VERSION)
    _echo_audit(report)
    if not report.clean:
        raise typer.Exit(code=1)


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
    if not is_corpus(target):
        raise ConfigError(
            f"No lovverk corpus at {target}. Run `lovspor fetch-corpus` first, "
            "or pass --corpus-path / set LOVVERK_CORPUS_PATH.",
        )
    _mcp_serve(target.resolve())
