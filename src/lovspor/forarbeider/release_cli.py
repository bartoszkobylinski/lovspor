"""``lovspor ops forarbeider-release``: the private release preflight (#738, ADR-0018 PR3B-1).

The group takes the workspace and the PR3A ledger; its ``chapters`` subgroup
takes one proposition's staged files, because a command may take four
parameters and a release needs more. Each command only calls its ``_impl`` in
:mod:`lovspor.forarbeider.release_commands`.

Exit 1 is a typed refusal, with nothing written. Exit 2 is a written ``deny``
report, or a plan or simulation refused because the preflight now denies.
Exit 0 is an ``eligible_preview`` report or a plan, which still authorize no
publication: no command here writes to ``lovverk`` or any real Git checkout.
The simulator writes only a disposable sandbox this package made, from the
bundled synthetic propositions (PR3B-2).
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from lovspor.forarbeider.corpus_commands import (
    PlanRequest,
    SimulateRequest,
    WithdrawRequest,
    fixtures_impl,
    plan_impl,
    sandbox_create_impl,
    sandbox_withdraw_impl,
    simulate_impl,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.pilot_audit import pilot_audit_impl
from lovspor.forarbeider.policy_commands import (
    PolicyGrant,
    policy_revoke_impl,
    policy_show_impl,
    policy_sign_impl,
)
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
policy_app = typer.Typer(help="The standing release policy: signed once, revocable.")
ops_app.add_typer(release_app, name="forarbeider-release")
release_app.add_typer(chapters_app, name="chapters")
release_app.add_typer(policy_app, name="policy")


def refusal_line(error: ForarbeiderRefusedError) -> str:
    """The refusal as printed; a detail that could be a path or a name is shown as ``input``."""
    detail = error.detail if _SAFE_DETAIL.fullmatch(error.detail) else "input"
    return f"refused: {error.reason.value}: {detail}"


def _echo(action: Callable[[], str]) -> None:
    try:
        typer.echo(action())
    except ForarbeiderRefusedError as error:
        typer.echo(refusal_line(error), err=True)
        code = DENY_EXIT if error.reason is RefusalReason.PROMOTION_DENIED else 1
        raise typer.Exit(code=code) from error


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


@chapters_app.command("pilot-audit")
def pilot_audit(
    ctx: typer.Context,
    at: Annotated[str, typer.Option(help="The moment evaluated, ISO 8601 with offset.")],
    authorization: Annotated[str | None, typer.Option(help="The authorization sha256.")] = None,
    checkpoint: Annotated[
        list[str] | None,
        typer.Option(help="SUBJECT@REVISION:HEAD kept outside the ledger; may be repeated."),
    ] = None,
) -> None:
    """The preflight per chapter as PASS, HOLD or DENY. A PASS is not release clearance."""
    run = Run(authorization=authorization, checkpoints=tuple(checkpoint or ()), at=at)
    run_audit(ctx.obj, run)


def run_audit(scope: ChapterScope, run: Run) -> None:
    """Print the audit; anything short of every chapter PASS exits 2 after its report is written."""
    try:
        lines, passed = pilot_audit_impl(scope, run)
    except ForarbeiderRefusedError as error:
        typer.echo(refusal_line(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(lines)
    if not passed:
        raise typer.Exit(code=DENY_EXIT)


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


@chapters_app.command("plan")
def plan(
    ctx: typer.Context,
    report: Annotated[str, typer.Option(help="The eligible preflight report's sha256.")],
    corpus: Annotated[Path, typer.Option(help="A corpus checkout, read and never written.")],
    checkpoint: Annotated[
        list[str] | None,
        typer.Option(help="SUBJECT@REVISION:HEAD, as at the preflight; may be repeated."),
    ] = None,
) -> None:
    """Re-verify an eligible preflight and keep the files a release would write, privately.

    Writes no checkout. A plan is not permission to publish.
    """
    request = PlanRequest(report=report, checkpoints=tuple(checkpoint or ()), corpus=corpus)
    _echo(lambda: plan_impl(ctx.obj, request))


@chapters_app.command("simulate")
def simulate(
    ctx: typer.Context,
    plan: Annotated[str, typer.Option(help="The plan's sha256.")],
    sandbox: Annotated[Path, typer.Option(help="A sandbox made by sandbox-create.")],
) -> None:
    """Apply a plan of synthetic inputs to a synthetic sandbox, re-validated now. Never lovverk."""
    request = SimulateRequest(plan=plan, sandbox=sandbox)
    _echo(lambda: simulate_impl(ctx.obj, request))


@release_app.command("sandbox-create")
def sandbox_create(
    ctx: typer.Context,
    path: Annotated[Path, typer.Option(help="A new absolute path outside every repository.")],
) -> None:
    """Make a disposable, empty Git checkout for the simulator: no remote, no commit."""
    _echo(lambda: sandbox_create_impl(ctx.obj, path))


@release_app.command("sandbox-withdraw")
def sandbox_withdraw(
    ctx: typer.Context,
    sandbox: Annotated[Path, typer.Option(help="A sandbox made by sandbox-create.")],
    document: Annotated[str, typer.Option(help="The released document's id.")],
    decided: _Actor,
) -> None:
    """Withdraw one document from the sandbox, forward only, at a terminal."""
    request = WithdrawRequest(sandbox=sandbox, document=document, decided=decided)
    _echo(lambda: sandbox_withdraw_impl(ctx.obj, request))


@release_app.command("synthetic-fixtures")
def synthetic_fixtures(
    ctx: typer.Context,
    out: Annotated[Path, typer.Option(help="An owner-only directory outside every repository.")],
) -> None:
    """Write the bundled synthetic Prop. L and Prop. LS staged files: invented text only."""
    _echo(lambda: fixtures_impl(ctx.obj, out))


@policy_app.command("sign")
def policy_sign(
    ctx: typer.Context,
    evidence: Annotated[
        list[str],
        typer.Option(help="KIND=/absolute/private/file, once per kind; may be repeated."),
    ],
    decided: _Actor,
    valid: Annotated[str, typer.Option(help="FROM/UNTIL, ISO 8601 with offsets.")],
) -> None:
    """Sign the standing policy at a terminal. It clears unflagged chapters through a batch only."""
    grant = PolicyGrant(evidence=tuple(evidence), decided=decided, valid=valid)
    _echo(lambda: policy_sign_impl(ctx.obj, grant))


@policy_app.command("revoke")
def policy_revoke(ctx: typer.Context, policy: _Digest, decided: _Actor) -> None:
    """Withdraw the standing policy. A later batch clears nothing under it."""
    _echo(lambda: policy_revoke_impl(ctx.obj, policy, decided))


@policy_app.command("show")
def policy_show(
    ctx: typer.Context,
    policy: Annotated[str, typer.Argument(help="The policy's sha256.")],
    at: Annotated[str, typer.Option(help="The moment evaluated, ISO 8601 with offset.")],
) -> None:
    """Show the policy's scope, window, evidence digests and whether it holds at a moment."""
    _echo(lambda: policy_show_impl(ctx.obj, policy, at))
