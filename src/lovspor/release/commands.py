"""``lovspor release``: the operator's route to the envelope (ADR-0014 Decision 6).

``publish-release.sh`` is a thin wrapper over these: ``live`` (root,
the reconcile identity) establishes the reconciled live release;
``build`` (the build user) runs the probe and the seven steps, printing
the finalized id alone on stdout; ``commit`` (root) runs the
transaction; ``prune`` (root) removes what no record names.
``reconcile`` and ``rollback`` are the operator's own.

Exit codes: 0 done (``commit`` of the live release included — it is
already live); 1 refused, unreconciled or failed, one line on stderr;
2 usage; 3 the precondition *Caddy admin reachable* is unmet, with D
and M printed. The only clock in the release package is read here, for
the probe, and passed in.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

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


def _plane(releases: Path, caddyfile: Path, fragment: Path, admin: str) -> ControlPlane:
    return ControlPlane(releases, caddyfile, fragment, SubprocessRunner(), HttpxAdminClient(admin))


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
) -> None:
    """Name the host's state — R, D and M — and resolve it per the crash table."""
    if complete and abandon:
        raise typer.BadParameter("--complete and --abandon exclude each other")
    action: ReconcileAction = "complete" if complete else "abandon" if abandon else "report"
    plane = _plane(releases, caddyfile, fragment, admin)
    with _refusals():
        report = reconcile(plane, action)
    typer.echo(f"{report.situation.value}: {report.triple}; action {report.action}")
    typer.echo(f"live: {report.live or NOTHING_LIVE}")


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
