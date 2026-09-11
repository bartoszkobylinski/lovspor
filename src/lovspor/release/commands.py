"""``lovspor release``: the operator's route to the envelope (ADR-0014 Decision 6).

``publish-release.sh`` is a thin wrapper over these: ``live`` (root,
the reconcile identity) establishes the reconciled live release;
``build`` (the build user) runs the probe and the seven steps, printing
the finalized id alone on stdout; ``commit`` (root) runs the
transaction; ``prune`` (root) removes what no record names.
``reconcile`` and ``rollback`` are the operator's own, and so is
``migrate`` — the first envelope cutover (ADR-0014 Migration), whose
``--rollback`` and ``--retire`` are separate runs, never phases of it.

Exit codes: 0 done (``commit`` of the live release included — it is
already live); 1 refused, unreconciled or failed, one line on stderr;
2 usage; 3 the precondition *Caddy admin reachable* is unmet, with D
and M printed. The only clock in the release package is read here, for
the probe, and passed in.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import httpx
import typer

from lovspor.publish.inventory import PublishError
from lovspor.release.build import BuildOutcome, BuildRequest, build_release
from lovspor.release.caddy import (
    DEFAULT_ADMIN,
    DEFAULT_CADDYFILE,
    DEFAULT_FRAGMENT,
    HttpxAdminClient,
    SubprocessRunner,
)
from lovspor.release.control import ControlPlane, commit_release, live_release, rollback
from lovspor.release.envelope import is_release_id, read_marker
from lovspor.release.errors import ReleaseError, UnobservableError, UnreconciledError
from lovspor.release.migrate import (
    DEFAULT_CADDYFILE_SOURCE,
    DEFAULT_CURRENT_SYMLINK,
    DEFAULT_DROP_IN,
    DEFAULT_RELEASE_GROUP,
    DEFAULT_RUNTIME_DIR,
    DEFAULT_SITE_ROOT,
    DEFAULT_TCP_ADMIN,
    MigrationHost,
    MigrationReport,
    RetireReport,
    RollbackReport,
    first_migration,
    offline_rollback,
    preflight,
    retire_pre_envelope,
    retire_preview,
    rollback_first_migration,
)
from lovspor.release.reconcile import ReconcileAction, prune, reconcile
from lovspor.site.build import discover_checkout
from lovspor.site.capabilities import CapabilityDocument, Checkout
from lovspor.site.errors import SiteBuildError
from lovspor.site.probe import CANONICAL_MCP_URL, DEFAULT_READINESS_URL, ProbeSettings, probe
from lovspor.site.probe_credential import load_probe_token

DEFAULT_RELEASES = Path("/var/www/lovspor-releases")
EXIT_REFUSED = 1
EXIT_UNOBSERVABLE = 3
NOTHING_LIVE = "none"

release_app = typer.Typer(
    name="release",
    help="Build, commit, reconcile, roll back and prune release envelopes (ADR-0014).",
    no_args_is_help=True,
)

_ReleasesOption = Annotated[
    Path,
    typer.Option("--releases", envvar="LOVSPOR_RELEASES_ROOT", help="The releases root."),
]
_CaddyfileOption = Annotated[
    Path,
    typer.Option("--caddyfile", envvar="LOVSPOR_CADDYFILE", help="The composed Caddyfile."),
]
_FragmentOption = Annotated[
    Path,
    typer.Option(
        "--fragment",
        envvar="LOVSPOR_RELEASE_FRAGMENT",
        help="The active release fragment the Caddyfile imports.",
    ),
]
_AdminOption = Annotated[
    str,
    typer.Option(
        "--admin",
        envvar="LOVSPOR_CADDY_ADMIN",
        help="Caddy's admin endpoint: unix/<socket path>, or host:port during the migration.",
    ),
]


_TcpAdminOption = Annotated[
    str,
    typer.Option(
        "--tcp-admin",
        envvar="LOVSPOR_CADDY_ADMIN_TCP",
        help="The pre-envelope admin address, Caddy's default; the migration reloads through it.",
    ),
]
_CaddyfileSourceOption = Annotated[
    Path,
    typer.Option(
        "--caddyfile-source",
        envvar="LOVSPOR_CADDYFILE_SOURCE",
        help="The new Caddyfile the first migration installs, from the deployed checkout.",
    ),
]
_DropInOption = Annotated[
    Path, typer.Option("--drop-in", envvar="LOVSPOR_CADDY_DROP_IN", help="caddy.service drop-in.")
]
_RuntimeDirOption = Annotated[
    Path,
    typer.Option(
        "--runtime-dir", envvar="LOVSPOR_CADDY_RUNTIME_DIR", help="The admin socket's directory."
    ),
]
_ReleaseGroupOption = Annotated[
    str,
    typer.Option(
        "--release-group",
        envvar="LOVSPOR_RELEASE_GROUP",
        help="The group that may open the admin socket; root is in it, lovspor is not.",
    ),
]


@dataclass(frozen=True)
class HostOptions:
    """The first migration's host, as the options name it."""

    tcp_admin: str = DEFAULT_TCP_ADMIN
    caddyfile_source: Path = DEFAULT_CADDYFILE_SOURCE
    drop_in: Path = DEFAULT_DROP_IN
    runtime_dir: Path = DEFAULT_RUNTIME_DIR
    release_group: str = DEFAULT_RELEASE_GROUP
    site_root: Path = DEFAULT_SITE_ROOT
    current_symlink: Path = DEFAULT_CURRENT_SYMLINK


def _plane(releases: Path, caddyfile: Path, fragment: Path, admin: str) -> ControlPlane:
    return ControlPlane(releases, caddyfile, fragment, SubprocessRunner(), HttpxAdminClient(admin))


def _host(caddyfile: Path, admin: str, options: HostOptions) -> MigrationHost:
    """The production host: the socket is the plane's admin address, everything else as given."""
    return MigrationHost(
        caddyfile=caddyfile,
        caddyfile_source=options.caddyfile_source,
        drop_in=options.drop_in,
        runtime_dir=options.runtime_dir,
        tcp_admin=options.tcp_admin,
        socket_admin=admin,
        release_group=options.release_group,
        site_root=options.site_root,
        current_symlink=options.current_symlink,
    )


@contextmanager
def _refusals() -> Iterator[None]:
    """One named line and the exit code for each refusal family."""
    try:
        yield
    except UnobservableError as error:
        typer.echo(f"release refused: precondition Caddy admin reachable unmet: {error}", err=True)
        raise typer.Exit(code=EXIT_UNOBSERVABLE) from error
    except (UnreconciledError, ReleaseError, SiteBuildError, PublishError) as error:
        typer.echo(f"release refused: {error}", err=True)
        raise typer.Exit(code=EXIT_REFUSED) from error


def _clock() -> datetime:
    return datetime.now(UTC)


def _observer(settings: ProbeSettings) -> Callable[[Checkout], CapabilityDocument]:
    def observe(checkout: Checkout) -> CapabilityDocument:
        with httpx.Client() as client:
            return probe(settings, client=client, checkout=checkout, clock=_clock)

    return observe


def _live_id(value: str) -> str | None:
    if value == NOTHING_LIVE:
        return None
    if not is_release_id(value):
        raise typer.BadParameter(f"--live must be a release_content_id or '{NOTHING_LIVE}'")
    return value


def _check_marker(releases: Path, live: str | None) -> None:
    """The caller established ``live`` from the reconciled triple; the marker must agree."""
    marker = read_marker(releases)
    marked = marker.active if marker else None
    if marked != live:
        raise ReleaseError(
            f"--live names {live or NOTHING_LIVE}, the marker names {marked or NOTHING_LIVE}; "
            "establish the live release with `lovspor release live` first"
        )


@release_app.command(name="build")
def build_command(
    corpus: Annotated[Path, typer.Option(help="The lovverk clone to build from.")],
    live: Annotated[
        str,
        typer.Option(
            help="The reconciled live release_content_id from `lovspor release live`, or 'none'."
        ),
    ],
    releases: _ReleasesOption = DEFAULT_RELEASES,
    ref: Annotated[str, typer.Option(help="Corpus commit to build; defaults to HEAD.")] = "HEAD",
    readiness_url: Annotated[str, typer.Option("--readiness-url")] = DEFAULT_READINESS_URL,
    public_mcp_url: Annotated[str, typer.Option("--public-mcp-url")] = CANONICAL_MCP_URL,
    probe_token_file: Annotated[
        Path | None, typer.Option("--probe-token-file", envvar="LOVSPOR_PROBE_TOKEN_FILE")
    ] = None,
    timeout_seconds: Annotated[float, typer.Option("--timeout-seconds")] = 10.0,
) -> None:
    """Probe, then build and finalize one envelope; print its release_content_id on stdout.

    Runs as the build user from the clean checkout it is installed in. The
    probe's document is the site's input; the id-carrying files are written
    before the final check; the directory is renamed to its id in one step.
    Already live (same release_key as the live release) prints the live id.
    """
    live_id = _live_id(live)
    token, notice = load_probe_token(probe_token_file)
    if notice is not None:
        typer.echo(notice, err=True)
    settings = ProbeSettings(
        readiness_url=readiness_url,
        public_mcp_url=public_mcp_url,
        probe_token=token,
        timeout_seconds=timeout_seconds,
        observer="release-probe",
    )

    with _refusals():
        _check_marker(releases, live_id)
        request = BuildRequest(
            releases=releases, checkout=discover_checkout(), corpus=corpus, corpus_ref=ref
        )
        outcome = build_release(request, _observer(settings), live_id)
    typer.echo(f"release {outcome.release_content_id[:12]}: {_state(outcome)}", err=True)
    typer.echo(outcome.release_content_id)


def _state(outcome: BuildOutcome) -> str:
    if outcome.already_live:
        return "already live"
    return "reused, already on disk" if outcome.reused else "built and finalized"


@release_app.command(name="live")
def live_command(
    releases: _ReleasesOption = DEFAULT_RELEASES,
    caddyfile: _CaddyfileOption = DEFAULT_CADDYFILE,
    fragment: _FragmentOption = DEFAULT_FRAGMENT,
    admin: _AdminOption = DEFAULT_ADMIN,
) -> None:
    """Print the reconciled live release_content_id, or 'none'; refuse anything else."""
    plane = _plane(releases, caddyfile, fragment, admin)
    with _refusals():
        live = live_release(plane)
    typer.echo(live or NOTHING_LIVE)


@release_app.command(name="commit")
def commit_command(
    content_id: Annotated[str, typer.Argument(help="A finalized release_content_id.")],
    releases: _ReleasesOption = DEFAULT_RELEASES,
    caddyfile: _CaddyfileOption = DEFAULT_CADDYFILE,
    fragment: _FragmentOption = DEFAULT_FRAGMENT,
    admin: _AdminOption = DEFAULT_ADMIN,
) -> None:
    """Make a finalized envelope live: stage, validate, commit the fragment, reload, mark."""
    if not is_release_id(content_id):
        raise typer.BadParameter(f"not a release_content_id: {content_id}")
    plane = _plane(releases, caddyfile, fragment, admin)
    with _refusals():
        report = commit_release(plane, content_id)
    if report.already_live:
        typer.echo(f"release {content_id[:12]} is already live; nothing to commit")
    else:
        previous = report.previous[:12] if report.previous else NOTHING_LIVE
        typer.echo(f"live: {report.active} (previous {previous})")


@release_app.command(name="reconcile")
def reconcile_command(
    complete: Annotated[bool, typer.Option("--complete", help="D becomes the truth.")] = False,
    abandon: Annotated[bool, typer.Option("--abandon", help="M's release is restored.")] = False,
    releases: _ReleasesOption = DEFAULT_RELEASES,
    caddyfile: _CaddyfileOption = DEFAULT_CADDYFILE,
    fragment: _FragmentOption = DEFAULT_FRAGMENT,
    admin: _AdminOption = DEFAULT_ADMIN,
    tcp_admin: _TcpAdminOption = DEFAULT_TCP_ADMIN,
    caddyfile_source: _CaddyfileSourceOption = DEFAULT_CADDYFILE_SOURCE,
    drop_in: _DropInOption = DEFAULT_DROP_IN,
    runtime_dir: _RuntimeDirOption = DEFAULT_RUNTIME_DIR,
    release_group: _ReleaseGroupOption = DEFAULT_RELEASE_GROUP,
) -> None:
    """Name the host's state — R, D and M — and resolve it per the crash table.

    Without a marker the admin endpoint is reached on the socket first, then
    on --tcp-admin, and the first migration's own resolutions apply.
    """
    if complete and abandon:
        raise typer.BadParameter("--complete and --abandon exclude each other")
    action: ReconcileAction = "complete" if complete else "abandon" if abandon else "report"
    plane = _plane(releases, caddyfile, fragment, admin)
    options = HostOptions(tcp_admin, caddyfile_source, drop_in, runtime_dir, release_group)
    with _refusals():
        report = reconcile(plane, action, _host(caddyfile, admin, options))
    typer.echo(f"{report.situation.value}: {report.triple}; action {report.action}")
    typer.echo(f"admin: {report.admin or admin}")
    typer.echo(f"live: {report.live or NOTHING_LIVE}")


MigrateAction = Literal["migrate", "check", "rollback", "offline", "retire"]


@dataclass(frozen=True)
class MigrateFlags:
    """The migrate command's flags, so choosing the run is one argument, not five."""

    rollback: bool = False
    retire: bool = False
    check: bool = False
    offline: bool = False
    yes: bool = False


@dataclass(frozen=True)
class Run:
    """One run of ``migrate``: which one, on what, and whether it was confirmed."""

    action: MigrateAction
    content_id: str | None = None
    yes: bool = False


def _migrate_action(content_id: str | None, flags: MigrateFlags) -> MigrateAction:
    """Which of the runs the operator asked for; none of them are ever combined."""
    if flags.rollback and flags.retire:
        raise typer.BadParameter("--rollback and --retire exclude each other")
    if content_id is not None and (flags.rollback or flags.retire):
        raise typer.BadParameter("--rollback and --retire take no release_content_id")
    if flags.check and (flags.rollback or flags.retire):
        raise typer.BadParameter(
            "--check is the migration's preflight; it excludes --rollback and --retire"
        )
    if flags.offline and not flags.rollback:
        raise typer.BadParameter("--offline is the rollback's last resort; it needs --rollback")
    if flags.yes and not flags.retire:
        raise typer.BadParameter("--yes confirms --retire; no other run asks")
    if content_id is not None and not is_release_id(content_id):
        raise typer.BadParameter(f"not a release_content_id: {content_id}")
    if flags.rollback:
        return "offline" if flags.offline else "rollback"
    if flags.retire:
        return "retire"
    return "check" if flags.check else "migrate"


def _done(happened: bool) -> str:
    return "yes" if happened else "no"


def _migrated(report: MigrationReport) -> tuple[str, ...]:
    return (
        f"migrated: {report.active} (admin {report.admin})",
        f"running: {report.running}",
        f"previous Caddyfile: {report.previous_caddyfile}",
    )


def _rolled_back(report: RollbackReport) -> tuple[str, ...]:
    lines = (
        f"rolled back from {report.admin_before} to {report.admin}; "
        f"reloaded {_done(report.reloaded)}",
        f"marker removed {_done(report.marker_removed)}, "
        f"ExecReload pair removed {_done(report.exec_reload_removed)}",
    )
    if report.restarted is None:
        return lines
    return (*lines, f"restarted {report.restarted}")


def _retired(report: RetireReport) -> tuple[str, ...]:
    return (f"retired {len(report.removed)}: {', '.join(report.removed) or '-'}",)


def _retire(plane: ControlPlane, host: MigrationHost, confirmed: bool) -> tuple[str, ...]:
    """The paths first, then ``--yes``.

    The confirmation is a flag and never a prompt: this deletes
    production directories and the rollback's only sources, and a run with
    no terminal — the wrapper under systemd, an ssh one-liner — must fail
    closed rather than read a yes off a pipe that is not there.
    """
    if confirmed:
        return _retired(retire_pre_envelope(plane, host))
    listed = "\n".join(f"  {path}" for path in retire_preview(plane, host).removed)
    raise ReleaseError(
        "--retire permanently removes these paths, the last two of them the only way back to the "
        f"pre-envelope site:\n{listed or '  (nothing)'}\nre-run with --yes to confirm"
    )


def _migrate_lines(plane: ControlPlane, host: MigrationHost, run: Run) -> tuple[str, ...]:
    """One run, one report; ``--check`` is the only one that moves nothing."""
    if run.action == "check":
        return (f"preflight: {preflight(plane, host, run.content_id).describe()}",)
    if run.action == "rollback":
        return _rolled_back(rollback_first_migration(plane, host))
    if run.action == "offline":
        return _rolled_back(offline_rollback(plane, host))
    if run.action == "retire":
        return _retire(plane, host, run.yes)
    if run.content_id is None:
        raise typer.BadParameter("migrate needs a release_content_id, --rollback or --retire")
    return _migrated(first_migration(plane, host, run.content_id))


@release_app.command(name="migrate")
def migrate_command(
    content_id: Annotated[
        str | None, typer.Argument(help="The finalized release_content_id to cut over to.")
    ] = None,
    rollback: Annotated[
        bool, typer.Option("--rollback", help="Back to the pre-envelope host, from any point.")
    ] = False,
    retire: Annotated[
        bool, typer.Option("--retire", help="Remove the pre-envelope layout; no way back after.")
    ] = False,
    check: Annotated[
        bool, typer.Option("--check", help="The preflight alone; nothing on the host moves.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", help="Confirm --retire, having read the paths it lists.")
    ] = False,
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            help="With --rollback: restore the files and restart, dialling no admin endpoint.",
        ),
    ] = False,
    releases: _ReleasesOption = DEFAULT_RELEASES,
    caddyfile: _CaddyfileOption = DEFAULT_CADDYFILE,
    fragment: _FragmentOption = DEFAULT_FRAGMENT,
    admin: _AdminOption = DEFAULT_ADMIN,
    tcp_admin: _TcpAdminOption = DEFAULT_TCP_ADMIN,
    caddyfile_source: _CaddyfileSourceOption = DEFAULT_CADDYFILE_SOURCE,
    drop_in: _DropInOption = DEFAULT_DROP_IN,
    runtime_dir: _RuntimeDirOption = DEFAULT_RUNTIME_DIR,
    release_group: _ReleaseGroupOption = DEFAULT_RELEASE_GROUP,
) -> None:
    """The first envelope cutover, over the address transition a reload cannot make.

    ``--retire`` is never performed by a migration: it deletes the
    previous Caddyfile's world, the only way back, so the operator asks
    for it explicitly once the cutover is verified (ADR-0014 Migration) —
    and again with ``--yes``, having read the paths the first run lists.
    ``--rollback --offline`` is the last resort when Caddy answers on
    neither address: the files go back and the unit is restarted, with
    nothing read first.
    """
    flags = MigrateFlags(rollback, retire, check, offline, yes)
    run = Run(_migrate_action(content_id, flags), content_id, yes)
    plane = _plane(releases, caddyfile, fragment, admin)
    options = HostOptions(tcp_admin, caddyfile_source, drop_in, runtime_dir, release_group)
    with _refusals():
        lines = _migrate_lines(plane, _host(caddyfile, admin, options), run)
    for line in lines:
        typer.echo(line)


@release_app.command(name="rollback")
def rollback_command(
    releases: _ReleasesOption = DEFAULT_RELEASES,
    caddyfile: _CaddyfileOption = DEFAULT_CADDYFILE,
    fragment: _FragmentOption = DEFAULT_FRAGMENT,
    admin: _AdminOption = DEFAULT_ADMIN,
) -> None:
    """The marker's previous release through the same transaction; run twice to roll forward."""
    plane = _plane(releases, caddyfile, fragment, admin)
    with _refusals():
        report = rollback(plane)
    typer.echo(f"live: {report.active} (previous {report.previous})")


@release_app.command(name="prune")
def prune_command(
    releases: _ReleasesOption = DEFAULT_RELEASES,
    caddyfile: _CaddyfileOption = DEFAULT_CADDYFILE,
    fragment: _FragmentOption = DEFAULT_FRAGMENT,
    admin: _AdminOption = DEFAULT_ADMIN,
) -> None:
    """Remove what no record names — only on a reconciled host; never R, D, M or previous."""
    plane = _plane(releases, caddyfile, fragment, admin)
    with _refusals():
        report = prune(plane)
    typer.echo(
        f"pruned {len(report.removed)}: {', '.join(name[:12] for name in report.removed) or '-'}; "
        f"retained {', '.join(name[:12] for name in report.retained) or '-'}"
    )
