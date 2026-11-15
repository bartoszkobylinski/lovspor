"""``lovspor ops forarbeider-release``: the private release preflight (#738, ADR-0018 PR3B-1).

The group takes the workspace and the PR3A ledger; its ``chapters`` subgroup
takes one proposition's staged files, because a command may take four
parameters and a release needs more. Each command only calls its ``_impl`` in
:mod:`lovspor.forarbeider.release_commands`.

Exit 1 is a typed refusal, with nothing written. Exit 2 is a written ``deny``
report, a batch plan that includes no chapter, a plan or simulation refused
because the preflight now denies, or a batch plan that no longer reproduces.
Exit 0 is an ``eligible_preview`` report or a plan, which still authorize no
publication. The simulator writes only a disposable sandbox this package
made, from the bundled synthetic propositions (PR3B-2). ``batch apply``,
``batch withdraw`` and ``batch recover`` (B1b) write only ``forarbeider/`` in
the working tree of a guarded lovverk release clone, after a typed phrase
(apply, withdraw); none runs ``git add``, commit or push.
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, NamedTuple

import click
import typer

from lovspor.forarbeider.apply_preflight import ApplyPreflightRequest, apply_preflight_impl
from lovspor.forarbeider.batch_apply import BatchApplyRequest, batch_apply_impl
from lovspor.forarbeider.batch_commands import (
    BatchPlanRequest,
    batch_plan_impl,
    batch_show_impl,
)
from lovspor.forarbeider.batch_records import WithdrawalReason
from lovspor.forarbeider.batch_recover import RecoverRequest, batch_recover_impl
from lovspor.forarbeider.batch_withdraw import ReleaseWithdrawRequest, release_withdraw_impl
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
from lovspor.forarbeider.publication import AttestRequest, attest_impl
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
#: Refusals that mean "the evidence now denies", not "the input could not be read".
DENY_REASONS = frozenset(
    {
        RefusalReason.PROMOTION_DENIED,
        RefusalReason.PLAN_NOT_REPRODUCED,
        RefusalReason.CONFIRMATION_MISMATCH,
    }
)
#: Reason codes, roles, chapter ids and digests. A path has a separator and is never printed.
_SAFE_DETAIL = re.compile(r"[A-Za-z0-9_.,: -]*")

release_app = typer.Typer(help="Private, offline release preflight. Publishes nothing.")
chapters_app = typer.Typer(help="Commands on the staged chapters of one proposition.")
policy_app = typer.Typer(help="The standing release policy: signed once, revocable.")
publication_app = typer.Typer(
    help="The owner's attestation that a lovverk main commit is published. Unverified."
)
batch_app = typer.Typer(
    help="Private batch plans and their preflight authorize nothing;"
    " release-clone writes a batch only after the owner's typed confirmation."
)
ops_app.add_typer(release_app, name="forarbeider-release")
release_app.add_typer(chapters_app, name="chapters")
release_app.add_typer(policy_app, name="policy")
release_app.add_typer(publication_app, name="publication")
release_app.add_typer(batch_app, name="batch")


def refusal_line(error: ForarbeiderRefusedError) -> str:
    """The refusal as printed; a detail that could be a path or a name is shown as ``input``."""
    detail = error.detail if _SAFE_DETAIL.fullmatch(error.detail) else "input"
    return f"refused: {error.reason.value}: {detail}"


def _echo(action: Callable[[], str]) -> None:
    try:
        typer.echo(action())
    except ForarbeiderRefusedError as error:
        typer.echo(refusal_line(error), err=True)
        code = DENY_EXIT if error.reason in DENY_REASONS else 1
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
    evidence: Annotated[
        list[str],
        typer.Option(help="KIND=/absolute/private/file, once per kind; may be repeated."),
    ],
    terms_url: Annotated[
        str,
        typer.Option(help="The www.regjeringen.no page the terms evidence was fetched from."),
    ],
    decided: _Actor,
    valid: Annotated[str, typer.Option(help="FROM/UNTIL, ISO 8601 with offsets.")],
) -> None:
    """Sign the standing policy at a terminal. It clears unflagged chapters through a batch only."""
    grant = PolicyGrant(evidence=tuple(evidence), terms_url=terms_url, decided=decided, valid=valid)
    scope = click.get_current_context().obj
    _echo(lambda: policy_sign_impl(scope, grant))


@policy_app.command("revoke")
def policy_revoke(ctx: typer.Context, policy: _Digest, decided: _Actor) -> None:
    """Withdraw the standing policy. A later batch clears nothing under it."""
    _echo(lambda: policy_revoke_impl(ctx.obj, policy, decided))


@policy_app.command("show")
def policy_show(
    ctx: typer.Context,
    policy: Annotated[str, typer.Argument(help="The policy's sha256.")],
    at: Annotated[str, typer.Option(help="The moment evaluated, ISO 8601 with offset.")],
    source_proof: Annotated[
        Path | None,
        typer.Option(help="A forarbeider-fetch run record: the current robots.txt and terms."),
    ] = None,
) -> None:
    """Show the policy's scope, window, evidence digests and whether it clears at a moment.

    Without a current source proof no automatic clearance is allowed.
    """
    _echo(lambda: policy_show_impl(ctx.obj, policy, at, source_proof))


@batch_app.command("plan")
def batch_plan(
    request: Annotated[
        Path,
        typer.Option(
            help="The absolute, owner-only batch request file, outside every Git checkout."
        ),
    ],
    policy: Annotated[str, typer.Option(help="The standing policy's sha256.")],
    source_proof: Annotated[
        Path, typer.Option(help="The forarbeider-fetch run record: the current source proof.")
    ],
    at: Annotated[str, typer.Option(help="The moment evaluated, ISO 8601 with offset.")],
) -> None:
    """Plan a batch of at most 25 propositions privately. Publishes and authorizes nothing.

    Exit 0: a plan with included chapters; exit 2: a plan that includes none.
    """
    ask = BatchPlanRequest(request=request, policy=policy, source_proof=source_proof, at=at)
    scope = click.get_current_context().obj
    run_batch_plan(scope, ask)


def run_batch_plan(scope: ReleaseScope, ask: BatchPlanRequest) -> None:
    """Print the plan; one that includes no chapter exits 2 after it is written."""
    try:
        lines, included = batch_plan_impl(scope, ask)
    except ForarbeiderRefusedError as error:
        typer.echo(refusal_line(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(lines)
    if not included:
        raise typer.Exit(code=DENY_EXIT)


@batch_app.command("show")
def batch_show(
    ctx: typer.Context,
    sha: Annotated[str, typer.Argument(help="The batch plan's sha256.")],
) -> None:
    """Show a stored batch plan's ids, digests and codes, never paths, names or text."""
    _echo(lambda: batch_show_impl(ctx.obj, sha))


@batch_app.command("preflight")
def batch_preflight(
    plan: Annotated[str, typer.Argument(help="The stored batch plan's sha256.")],
    checkout: Annotated[
        Path,
        typer.Option(
            help="The dedicated lovverk release clone, on the plan's branch; never written."
        ),
    ],
    request: Annotated[
        Path, typer.Option(help="The private batch request file the plan was made from.")
    ],
    source_proof: Annotated[
        Path, typer.Option(help="The forarbeider-fetch run record the plan was made from.")
    ],
) -> None:
    """Check a stored batch plan against a release clone, now. Read-only: writes, applies nothing.

    Runs only read-only Git and re-evaluates the plan at the machine clock.
    Exit 0: the plan reproduces exactly; exit 2: it no longer does; exit 1: refused.
    """
    ask = ApplyPreflightRequest(
        plan=plan, checkout=checkout, request=request, source_proof=source_proof
    )
    scope = click.get_current_context().obj
    _echo(lambda: apply_preflight_impl(scope, ask))


clone_app = typer.Typer(
    help="One dedicated lovverk release clone: apply a confirmed batch, withdraw, recover."
)
batch_app.add_typer(clone_app, name="release-clone")


class CloneScope(NamedTuple):
    """The release scope, the release clone and the branch the owner names for it."""

    release: ReleaseScope
    checkout: Path
    branch: str


@clone_app.callback()
def clone_scope(
    ctx: typer.Context,
    checkout: Annotated[
        Path,
        typer.Option(
            "--checkout", help="The dedicated lovverk release clone (absolute, owner-only)."
        ),
    ],
    branch: Annotated[
        str, typer.Option("--branch", help="The forarbeider/ branch the clone is on.")
    ],
) -> None:
    """Commands that write only forarbeider/ in this clone's working tree (issue #738, B1b).

    None runs git add, commit or push: the owner commits, pushes and opens the
    lovverk PR, and merging it is the publication.
    """
    ctx.obj = CloneScope(ctx.obj, checkout, branch)


@clone_app.command("apply")
def batch_apply(
    plan: Annotated[str, typer.Argument(help="The stored batch plan's sha256.")],
    request: Annotated[
        Path, typer.Option(help="The private batch request file the plan was made from.")
    ],
    source_proof: Annotated[
        Path, typer.Option(help="The forarbeider-fetch run record the plan was made from.")
    ],
    decided: _Actor,
) -> None:
    """Write a confirmed batch to the clone's forarbeider/, working tree only.

    Re-runs the preflight under the clone's lock, asks the owner to type a phrase
    naming the plan and binding digests, the counts and the branch, then runs the
    preflight again and writes all or nothing. No git add, commit or push.
    Exit 0: applied or unchanged; exit 2: the plan or the binding moved; exit 1: refused.
    """
    clone: CloneScope = click.get_current_context().obj
    ask = BatchApplyRequest(
        plan=plan,
        checkout=clone.checkout,
        branch=clone.branch,
        request=request,
        source_proof=source_proof,
        decided=decided,
    )
    _echo(lambda: batch_apply_impl(clone.release, ask))


@clone_app.command("recover")
def batch_recover() -> None:
    """Settle an interrupted apply or withdrawal: a release is rolled back, a withdrawal finished.

    Takes over the clone's lock only when no process holds it. Publishes nothing new.
    """
    clone: CloneScope = click.get_current_context().obj
    ask = RecoverRequest(checkout=clone.checkout, branch=clone.branch)
    _echo(lambda: batch_recover_impl(clone.release, ask))


@clone_app.command("withdraw")
def batch_withdraw(
    document: Annotated[str, typer.Option(help="The released document's id.")],
    chapters: Annotated[
        str, typer.Option(help="The chapter numbers to take down, e.g. 2,3; the file goes whole.")
    ],
    reason: Annotated[WithdrawalReason, typer.Option(help="Why, as a code; never free text.")],
    decided: _Actor,
) -> None:
    """Withdraw one released document, forward only, at a terminal. History is not rewritten.

    Marks the member withdrawn, removes its file and rewrites the index and the
    manifest in the working tree. Earlier commits and copies keep the bytes.
    """
    clone: CloneScope = click.get_current_context().obj
    ask = ReleaseWithdrawRequest(
        checkout=clone.checkout,
        branch=clone.branch,
        document=document,
        chapters=_chapter_numbers(chapters),
        reason=reason,
        decided=decided,
    )
    _echo(lambda: release_withdraw_impl(clone.release, ask))


def _chapter_numbers(text: str) -> tuple[int, ...]:
    """``2,3``: a usage error unless every item is a number."""
    items = text.split(",")
    if not all(item.isdecimal() for item in items):
        raise typer.BadParameter("chapters are numbers separated by commas")
    return tuple(int(item) for item in items)


@publication_app.command("attest")
def publication_attest(
    checkout: Annotated[
        Path, typer.Option(help="The lovverk release clone, clean on main, at the commit.")
    ],
    commit: Annotated[
        str, typer.Option(help="The main commit you saw on lovverk's GitHub; must be HEAD.")
    ],
    decided: _Actor,
) -> None:
    """Attest, at a terminal, that this main commit is published. Writes only the workspace.

    Your word, not a verification: nothing here contacts the remote or authenticates you.
    The receipt-verified reader serves a commit only with this attestation.
    """
    ask = AttestRequest(checkout=checkout, commit=commit, decided=decided)
    scope = click.get_current_context().obj
    _echo(lambda: attest_impl(scope, ask))
