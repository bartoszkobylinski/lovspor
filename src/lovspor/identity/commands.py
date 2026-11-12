"""``lovspor ops identity-plan``: the #871 identity repair, dry run only.

Reads a lovverk checkout through git (``rev-parse``, ``log``, ``ls-tree``,
``cat-file``; nothing that writes) and writes the plan's report into an
output directory that must lie outside the checkout. There is no apply mode:
applying the repair to lovverk is a separate owner decision (migration plan
§7). Registered on the ``ops`` group by :mod:`lovspor.ops_cli`, because
:mod:`lovspor.cli` sits at its size ratchet; the decorated command only
calls :func:`identity_plan_impl`, so the body stays inside the mutation gate.

Exit codes: 0 the report is written; 3 the report is written but the plan is
blocked (a ``doc_id`` no manifest revision ever held, or a registry
conflict); 2 the input could not be read and nothing was written.
"""

import json
from pathlib import Path
from typing import Annotated

import typer

from lovspor.errors import IdentityMigrationError
from lovspor.identity.plan import plan_migration
from lovspor.identity.report import report_data, write_report
from lovspor.ops.commands import ops_app

EXIT_BLOCKED = 3
EXIT_UNREADABLE = 2


def _ensure_outside(repo: Path, out: Path) -> None:
    """Refuse an output directory inside the corpus checkout: the run never writes there."""
    resolved_repo = repo.resolve()
    resolved_out = out.resolve()
    if resolved_out == resolved_repo or resolved_repo in resolved_out.parents:
        raise IdentityMigrationError(f"--out {out} is inside the corpus checkout {repo}")


def identity_plan_impl(repo: Path, base: str, out: Path) -> tuple[int, str]:
    """Plan the repair and write the report; ``(exit code, summary line)``."""
    _ensure_outside(repo, out)
    plan = plan_migration(repo, base)
    out.mkdir(parents=True, exist_ok=True)
    sums = write_report(plan, out)
    data = report_data(plan)
    summary = {"base_commit": plan.base.commit, "counts": data["counts"], "sha256": sums}
    code = EXIT_BLOCKED if plan.blocked else 0
    return code, json.dumps(summary, sort_keys=True, ensure_ascii=False)


@ops_app.command(name="identity-plan")
def identity_plan(
    repo: Annotated[Path, typer.Option(help="A lovverk checkout; read through git only.")],
    base: Annotated[str, typer.Option(help="Base commit the repair would build on.")],
    out: Annotated[Path, typer.Option(help="Report directory, outside the checkout.")],
) -> None:
    """Dry-run the #871 identity repair: tombstones, slug registry, U1 renames."""
    try:
        code, summary = identity_plan_impl(repo, base, out)
    except IdentityMigrationError as error:
        typer.echo(f"identity-plan: {error}", err=True)
        raise typer.Exit(EXIT_UNREADABLE) from error
    typer.echo(summary)
    raise typer.Exit(code)
