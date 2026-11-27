"""``lovspor ops identity-exceptions-digest`` and ``identity-exceptions-approve`` (#921).

The supported way to read and to sign the reviewed owner-exception list
(``owner_exceptions``, ADR-0026 draft). ``identity-exceptions-digest`` prints the
digest the owner signs, with the list's state and counts. ``identity-exceptions-
approve`` is the owner's sign-off: it writes the approval only when ``--digest``
is the list's own digest and the list is still pending, so the owner signs the
exact content they reviewed. Agents never run it. Registered on the ``ops``
group by :mod:`lovspor.ops_cli`; the decorated commands only call the
``*_impl`` functions, so the bodies stay inside the mutation gate.

Exit codes: 0 done; 2 refused or unreadable, nothing written.
"""

import json
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from lovspor.errors import IdentitySchemaError
from lovspor.identity.owner_exceptions import (
    REASSIGNMENTS_PATH,
    approval_state,
    approve_list,
    list_digest,
    read_list,
)
from lovspor.ops.commands import ops_app

EXIT_REFUSED = 2
_PATH_HELP = "The reviewed list; defaults to the packaged owner_reassignments.json."


def digest_impl(path: Path) -> str:
    """The list's digest, approval state and counts, as one JSON line."""
    listed = read_list(path)
    digest = list_digest(listed)
    approved, state = approval_state(listed, digest)
    summary = {
        "approved": approved,
        "digest": digest,
        "reassignments": len(listed.reassignments),
        "reviewed_base": listed.corpus.reviewed_base,
        "state": state,
        "threshold_exceptions": len(listed.threshold_exceptions),
        "u1_threshold": listed.corpus.u1_threshold,
    }
    return json.dumps(summary, sort_keys=True, ensure_ascii=False)


def approve_impl(path: Path, digest: str, approved_by: str, approved_on: str) -> str:
    """Sign the list; refuses an unparsable date as it refuses any other bad input."""
    try:
        day = date.fromisoformat(approved_on)
    except ValueError as exc:
        raise IdentitySchemaError(f"--on {approved_on!r} is not a YYYY-MM-DD date") from exc
    signed = approve_list(path, digest, approved_by, day)
    return f"approved {path} by {approved_by} on {day.isoformat()}, digest {signed}"


@ops_app.command(name="identity-exceptions-digest")
def identity_exceptions_digest(
    path: Annotated[Path, typer.Option(help=_PATH_HELP)] = REASSIGNMENTS_PATH,
) -> None:
    """Print the digest the owner signs for the reviewed owner-exception list."""
    try:
        typer.echo(digest_impl(path))
    except IdentitySchemaError as error:
        typer.echo(f"identity-exceptions-digest: {error}", err=True)
        raise typer.Exit(EXIT_REFUSED) from error


@ops_app.command(name="identity-exceptions-approve")
def identity_exceptions_approve(
    digest: Annotated[str, typer.Option(help="The digest reviewed, as printed by -digest.")],
    by: Annotated[str, typer.Option(help="Who approves.")],
    on: Annotated[str, typer.Option(help="Approval date, YYYY-MM-DD.")],
    path: Annotated[Path, typer.Option(help=_PATH_HELP)] = REASSIGNMENTS_PATH,
) -> None:
    """Owner only: approve the reviewed owner-exception list by its digest."""
    try:
        typer.echo(approve_impl(path, digest, by, on))
    except IdentitySchemaError as error:
        typer.echo(f"identity-exceptions-approve: {error}", err=True)
        raise typer.Exit(EXIT_REFUSED) from error
