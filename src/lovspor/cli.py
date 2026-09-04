"""lovspor command-line interface."""

import subprocess
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from lovspor import __version__
from lovspor.access import (
    Credential,
    default_credentials_path,
    issue_credential,
    load_credentials,
    revoke_credential,
    write_credential_file,
)
from lovspor.corpus_audit import AuditFinding, AuditReport, audit_corpus
from lovspor.corpus_fetch import default_corpus_path, fetch_corpus, is_corpus
from lovspor.errors import ConfigError
from lovspor.github_output import append_step_summary, set_output
from lovspor.mcp import HttpConfig
from lovspor.mcp import serve as _mcp_serve
from lovspor.mcp import serve_http as _mcp_serve_http
from lovspor.observatory.commands import observatory_app
from lovspor.publish.emit import emit_site
from lovspor.publish.inventory import PublishError
from lovspor.rendering.markdown_renderer import RENDERER_VERSION
from lovspor.settings import Settings, load_env
from lovspor.storage.manifest import read_manifest
from lovspor.sync.input_annotation import annotate_embedding_input_identity
from lovspor.sync.lspe_cutover import migrate_lspe_v2
from lovspor.sync.orchestrator import mark_undersized_embeddings_stale, run_sync

app = typer.Typer(
    name="lovspor",
    help="Norwegian law change tracker. Engine for the lovverk corpus.",
    add_completion=False,
    no_args_is_help=True,
)


tokens_app = typer.Typer(
    name="tokens",
    help="Issue, list and revoke hosted-MCP beta credentials.",
    no_args_is_help=True,
)
app.add_typer(tokens_app)
app.add_typer(observatory_app)

_CredentialsOption = Annotated[
    Path | None,
    typer.Option(
        "--credentials",
        help="Path to the credential store (default: ~/.config/lovspor/credentials.json).",
        envvar="LOVSPOR_CREDENTIALS",
    ),
]


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"lovspor {__version__}")
        raise typer.Exit


@tokens_app.command("issue")
def tokens_issue(
    label: Annotated[str, typer.Option("--label", help="Who this credential is for.")],
    expires_in_days: Annotated[
        int,
        typer.Option("--expires-in-days", help="Lifetime in days. 0 means never expires."),
    ] = 90,
    credentials_path: _CredentialsOption = None,
) -> None:
    """Mint a beta credential and print its token once.

    The token is never stored — only its SHA-256 — so it cannot be recovered
    later, only reissued.
    """
    path = (credentials_path or default_credentials_path()).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_credentials(path)
    credential, token = issue_credential(existing, label, expires_in_days or None)
    write_credential_file(path, [*existing, credential])
    typer.echo(f"Issued {credential.credential_id} ({label}) -> {path}")
    expiry = credential.expires_at.date().isoformat() if credential.expires_at else "never"
    typer.echo(f"Expires: {expiry}")
    typer.echo("\nToken (shown once, store it now):\n")
    typer.echo(f"  {token}\n")
    typer.echo("The server re-reads the store on change, so it is live immediately.")


@tokens_app.command("list")
def tokens_list(credentials_path: _CredentialsOption = None) -> None:
    """List issued credentials. Never prints tokens — they are not stored."""
    path = (credentials_path or default_credentials_path()).expanduser()
    credentials = load_credentials(path)
    if not credentials:
        typer.echo(f"No credentials in {path}.")
        return
    for credential in credentials:
        typer.echo(f"{credential.credential_id}  {_describe(credential)}  {credential.label}")


def _describe(credential: Credential) -> str:
    if credential.revoked:
        return "revoked"
    if credential.expires_at is None:
        return "active (no expiry)"
    return f"active until {credential.expires_at.date().isoformat()}"


@tokens_app.command("revoke")
def tokens_revoke(
    credential_id: Annotated[str, typer.Argument(help="Credential id, e.g. beta-001.")],
    credentials_path: _CredentialsOption = None,
) -> None:
    """Revoke a credential. Takes effect on the running server without a restart."""
    path = (credentials_path or default_credentials_path()).expanduser()
    write_credential_file(path, revoke_credential(load_credentials(path), credential_id))
    typer.echo(f"Revoked {credential_id}. The running server picks this up on its next call.")


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


@app.command(name="publish-site")
def publish_site(
    corpus: Annotated[
        Path,
        typer.Option(help="Path to a lovverk checkout (any state; reads via git)."),
    ],
    out: Annotated[
        Path,
        typer.Option(help="Directory to write the site tree into."),
    ],
    ref: Annotated[
        str | None,
        typer.Option(
            help="Corpus commit to build from; defaults to the checkout's HEAD, "
            "resolved to a full SHA so the build input is always recorded exactly.",
        ),
    ] = None,
) -> None:
    """Build the ADR-0013 static site from one pinned corpus commit."""
    try:
        resolved = subprocess.run(  # noqa: S603
            ["git", "rev-parse", ref or "HEAD"],  # noqa: S607
            cwd=corpus,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError) as error:
        raise typer.BadParameter(
            f"{corpus} is not a readable git corpus checkout",
        ) from error
    try:
        emit_site(corpus, resolved, out)
    except PublishError as error:
        typer.echo(f"publish refused: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"site built from corpus commit {resolved[:12]} into {out}")


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
    allow_mass_reembed: bool = typer.Option(
        False,
        "--allow-mass-reembed",
        help=(
            "Explicitly authorize an embedding-input repair larger than the "
            "ADR-0006 mass-re-embed guard allows. Default is fail-closed: a "
            "keyed sync whose input-identity repair selection exceeds the "
            "configured document-fraction or token-workload threshold aborts "
            "BEFORE any provider call. The scheduled workflow never passes "
            "this flag; it is a deliberate operator action for intended "
            "large repairs (unannotated corpus, deliberate pipeline change)."
        ),
    ),
) -> None:
    """Incremental sync against the current Lovdata public-data tarballs.

    Typically invoked by the scheduled workflow. Reads the existing
    manifest, downloads current tarballs, classifies each document, and
    commits only the changed ones.
    """
    settings = Settings.from_env()
    report = run_sync(
        settings,
        force_rerender=force_rerender,
        allow_mass_reembed=allow_mass_reembed,
    )
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

    Diagnostic/recovery tooling. Since ADR-0006, transformation drift is
    detected automatically by the ``embedding_input_hash`` staleness
    condition on every keyed sync — this count heuristic is retained during
    the rollout as an independent safety net, not as the normal mechanism.
    It catches under-counted sidecars only (e.g. flat acts whose H2 sections
    once produced zero vectors); same-count drift is the input hash's job.
    Clears each affected record's ``embedding_hash`` and commits the
    manifest; run ``lovspor sync`` afterwards (with ``OPENAI_API_KEY`` set)
    to rebuild the vectors via the backfill. A no-op — no commit — when
    every embedding is already current.
    """
    settings = Settings.from_env()
    count = mark_undersized_embeddings_stale(settings)
    typer.echo(
        f"Flagged {count} document(s) for re-embed. "
        "Run `lovspor sync` with OPENAI_API_KEY set to rebuild their vectors.",
    )


@app.command(name="annotate-input-identity")
def annotate_input_identity() -> None:
    """Run the ADR-0006 metadata-only embedding-input-identity migration.

    Stamps ``embedding_input_hash`` on every current manifest record by
    reconstructing each document's embedding inputs through the production
    pipeline and digesting them. Manifest-only: no Markdown, history,
    sidecar or ESI field changes, and no provider credential needed. The
    ADR-0006 drift invariant is enforced — the corpus basis is re-verified
    immediately before the manifest is written, and any drift aborts with
    nothing written. Commits locally; publication is a separate,
    deliberately manual step.
    """
    settings = Settings.from_env()
    report = annotate_embedding_input_identity(settings)
    typer.echo(
        f"Annotated {report.annotated} record(s) "
        f"({report.already_annotated} already current, "
        f"{report.tombstones_skipped} tombstone(s) untouched, "
        f"{report.empty_input_documents} sectionless) "
        f"at corpus {report.corpus_head[:12]} with engine {report.engine_version}.",
    )


@app.command(name="migrate-lspe-v2")
def migrate_lspe_v2_command() -> None:
    """Run the ADR-0005 Stage 2 coordinated LSPE version-2 cutover.

    Rewrites every current sidecar to format version 2, embedding each
    record's manifest ESI in the file header, with vectors preserved
    bit-for-bit and every written file re-read and verified. Keyless and
    derived-artifact-only: Markdown, manifest and history are untouched.
    One commit for the whole corpus — never a mixed-version state — and the
    binding ADR-0005 §3 ordering applies: run this only after the
    dual-reader release has propagated, and switch the writer to version 2
    only after this cutover has landed. Commits locally; publication is a
    separate, deliberately manual step.
    """
    settings = Settings.from_env()
    report = migrate_lspe_v2(settings)
    typer.echo(
        f"Rewrote {report.rewritten} sidecar(s) to LSPE v2 "
        f"({report.already_v2} already v2, "
        f"{report.tombstones_skipped} tombstone(s) untouched, "
        f"{report.header_only} header-only) "
        f"at corpus {report.corpus_head[:12]} with engine {report.engine_version}.",
    )


class AuditFailOn(StrEnum):
    """What makes `lovspor audit` exit non-zero."""

    integrity = "integrity"
    all = "all"


def _echo_audit(report: AuditReport) -> None:
    """Print findings grouped by severity, then by kind, most-common kind first."""
    if report.clean:
        typer.echo(f"Corpus is clean — {report.documents_checked} current document(s), no drift.")
        return
    typer.echo(
        f"Corpus drift: {len(report.findings)} finding(s) "
        f"across {report.documents_checked} current document(s).\n",
    )
    _echo_findings("INTEGRITY (blocking)", report.integrity_findings)
    _echo_findings("ADVISORY (registered follow-up work, non-blocking)", report.advisory_findings)


def _echo_findings(label: str, findings: tuple[AuditFinding, ...]) -> None:
    if not findings:
        return
    typer.echo(f"{label}:\n")
    by_kind: dict[str, list[str]] = {}
    for finding in findings:
        by_kind.setdefault(finding.kind, []).append(finding.path)
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
    fail_on: Annotated[
        AuditFailOn,
        typer.Option(
            "--fail-on",
            help="Exit non-zero on integrity findings only (default), or on all findings.",
        ),
    ] = AuditFailOn.integrity,
) -> None:
    """Reconcile the corpus on disk against the manifest, and report the drift.

    Every change-detection path in the engine compares *upstream* against the
    *manifest*; nothing compares the *manifest* against *disk*. A file that has
    fallen out of the manifest is therefore invisible to all of them and can
    never self-heal — which is how 48 repealed regulations sat in `lovverk` for
    seven weeks. This is the check that catches that class of drift.

    INTEGRITY findings — the corpus contradicting its own manifest: documents
    on disk that no record claims (`orphan_document`), tombstoned records whose
    file was never deleted (`tombstoned_but_present`), current records with no
    file (`missing_document`), embedding sidecars no current record owns
    (`orphan_embedding`), documents left behind by a renderer bump
    (`stale_render`), one markdown path claimed by more than one record or one
    current record claiming two paths (`duplicate_path_ownership`), and files
    whose frontmatter id contradicts their owning record (`identity_mismatch`).

    ADVISORY findings are registered follow-up work, not corruption — today
    that is `unparsed_section_heading` (18 pre-existing findings). They are
    printed and labeled, but do not fail the command by default: a CI gate
    that is permanently red is one nobody reads. Pass `--fail-on all` to
    treat them as blocking too.

    Read-only — it never writes or deletes. Exits non-zero on integrity
    findings (or on any finding with `--fail-on all`), so it can gate CI.
    """
    root = corpus_path or Settings.from_env().lovverk_repo_path
    report = audit_corpus(root, read_manifest(root / "manifest.json"), RENDERER_VERSION)
    _echo_audit(report)
    blocking = report.findings if fail_on is AuditFailOn.all else report.integrity_findings
    if blocking:
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
    full_history: Annotated[
        bool,
        typer.Option(
            "--full-history",
            help=(
                "Clone the complete git history (deepens an existing shallow "
                "clone in place). Required for full time-machine reach; the "
                "default shallow clone limits get_law_at/diff_law_versions "
                "to dates after the clone was made."
            ),
        ),
    ] = False,
) -> None:
    """Clone or update the local lovverk corpus that ``lovspor mcp`` reads.

    First run shallow-clones the public corpus to ``--dest`` (or the default
    cache); later runs fast-forward it. With no ``--dest`` and no
    ``LOVVERK_CORPUS_PATH``, ``lovspor mcp`` then finds it automatically — so
    the whole consumer flow is ``lovspor fetch-corpus`` then ``lovspor mcp``.
    Pass ``--full-history`` when the deployment exposes the time-machine
    tools; a shallow clone serves them only back to its own creation date.
    """
    result = fetch_corpus(dest or default_corpus_path(), full_history=full_history)
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


@app.command(name="mcp-http")
def mcp_http(
    host: Annotated[
        str,
        typer.Option("--host", help="Interface to bind. Keep on localhost behind a proxy."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", help="TCP port for the Streamable HTTP server."),
    ] = 8000,
    corpus_path: Annotated[
        Path | None,
        typer.Option(
            "--corpus-path",
            help="Path to a local lovverk clone (default: the fetch-corpus cache).",
            envvar="LOVVERK_CORPUS_PATH",
        ),
    ] = None,
    credentials_path: _CredentialsOption = None,
    insecure_no_auth: Annotated[
        bool,
        typer.Option(
            "--insecure-no-auth",
            help="Serve with NO authentication. Development only.",
        ),
    ] = False,
    authkit_domain: Annotated[
        str | None,
        typer.Option(
            "--authkit-domain",
            help="WorkOS AuthKit issuer URL. With --public-url, enables self-service "
            "OAuth connectors (ChatGPT/Claude) alongside hand-issued tokens.",
            envvar="LOVSPOR_AUTHKIT_DOMAIN",
        ),
    ] = None,
    public_url: Annotated[
        str | None,
        typer.Option(
            "--public-url",
            help="This server's public /mcp URL (the OAuth resource identifier the "
            "token is bound to).",
            envvar="LOVSPOR_PUBLIC_URL",
        ),
    ] = None,
) -> None:
    """Serve the lovverk corpus over the MCP Streamable HTTP transport.

    Exposes the same sixteen read-only tools as ``mcp`` (stdio) to remote
    clients, authenticated with bearer credentials from the store (see
    ``lovspor tokens issue``). Tool bodies run on worker threads so one slow
    call cannot block other clients.

    TLS is terminated upstream: run this behind a reverse proxy.
    """
    target = (corpus_path or default_corpus_path()).expanduser()
    if not is_corpus(target):
        raise ConfigError(
            f"No lovverk corpus at {target}. Run `lovspor fetch-corpus` first, "
            "or pass --corpus-path / set LOVVERK_CORPUS_PATH.",
        )
    store = None if insecure_no_auth else (credentials_path or default_credentials_path())
    _mcp_serve_http(
        target.resolve(),
        HttpConfig(
            host=host,
            port=port,
            credentials_path=store.expanduser() if store else None,
            allow_insecure=insecure_no_auth,
            authkit_domain=authkit_domain,
            public_url=public_url,
        ),
    )
