"""``lovspor tokens``: issue, list and revoke hosted-MCP beta credentials.

Its own module because :mod:`lovspor.cli` sits at its size ratchet; moved
here unchanged when ``lovspor publication-seal`` was registered.
"""

from pathlib import Path
from typing import Annotated

import typer

from lovspor.access import (
    default_credentials_path,
    describe_credential,
    issue_credential,
    load_credentials,
    revoke_credential,
    write_credential_file,
)

tokens_app = typer.Typer(
    name="tokens",
    help="Issue, list and revoke hosted-MCP beta credentials.",
    no_args_is_help=True,
)

CredentialsOption = Annotated[
    Path | None,
    typer.Option(
        "--credentials",
        help="Path to the credential store (default: ~/.config/lovspor/credentials.json).",
        envvar="LOVSPOR_CREDENTIALS",
    ),
]


@tokens_app.command("issue")
def tokens_issue(
    label: Annotated[str, typer.Option("--label", help="Who this credential is for.")],
    expires_in_days: Annotated[
        int,
        typer.Option("--expires-in-days", help="Lifetime in days. 0 means never expires."),
    ] = 90,
    credentials_path: CredentialsOption = None,
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
def tokens_list(credentials_path: CredentialsOption = None) -> None:
    """List issued credentials. Never prints tokens — they are not stored."""
    path = (credentials_path or default_credentials_path()).expanduser()
    credentials = load_credentials(path)
    if not credentials:
        typer.echo(f"No credentials in {path}.")
        return
    for credential in credentials:
        state = describe_credential(credential)
        typer.echo(f"{credential.credential_id}  {state}  {credential.label}")


@tokens_app.command("revoke")
def tokens_revoke(
    credential_id: Annotated[str, typer.Argument(help="Credential id, e.g. beta-001.")],
    credentials_path: CredentialsOption = None,
) -> None:
    """Revoke a credential. Takes effect on the running server without a restart."""
    path = (credentials_path or default_credentials_path()).expanduser()
    write_credential_file(path, revoke_credential(load_credentials(path), credential_id))
    typer.echo(f"Revoked {credential_id}. The running server picks this up on its next call.")
