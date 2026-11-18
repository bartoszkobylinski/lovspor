"""``lovspor publication-seal``: compose and verify the signed publication seal.

``compose`` writes the seal message for the corpus HEAD and never touches a
key; the sync workflow signs it in a separate step. ``verify`` applies the
consumer rules of ``docs/publication-seal.md`` with git's own ``verify-tag``
against a pinned allowed-signers file. Any failure exits 1 with one line on
stderr; a usage error exits 2.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from lovspor.errors import LovsporError
from lovspor.publication_seal import AlreadySealed, compose_seal, verify_seal

publication_seal_app = typer.Typer(
    name="publication-seal",
    help="Compose and verify the signed publication seal of a corpus commit.",
    no_args_is_help=True,
)

_CorpusPath = Annotated[
    Path,
    typer.Option("--corpus-path", help="The lovverk clone whose commit is sealed or checked."),
]
_SIGNERS_HELP = "OpenSSH allowed_signers file to trust."
_SignersOption = Annotated[Path | None, typer.Option("--allowed-signers", help=_SIGNERS_HELP)]


@publication_seal_app.command(name="compose")
def compose(
    corpus_path: _CorpusPath,
    out: Annotated[Path, typer.Option("--out", help="File the seal message is written to.")],
    allowed_signers: _SignersOption = None,
) -> None:
    """Write the seal message for HEAD, or report the tag already sealing it.

    Prints ``already-sealed <tag>`` and writes no file when a ``sync/*`` tag
    already seals HEAD with the same attestation note (and, with
    ``--allowed-signers``, verifies against that file)."""
    result = _guard(lambda: compose_seal(corpus_path.expanduser(), allowed_signers))
    if isinstance(result, AlreadySealed):
        typer.echo(f"already-sealed {result.tag}")
        return
    out.expanduser().write_bytes(result)
    typer.echo(f"composed {out}")


@publication_seal_app.command(name="verify")
def verify(
    corpus_path: _CorpusPath,
    allowed_signers: Annotated[Path, typer.Option("--allowed-signers", help=_SIGNERS_HELP)],
    tag: Annotated[str | None, typer.Option("--tag", help="The sync/* tag to judge.")] = None,
    commit: Annotated[
        str | None,
        typer.Option("--commit", help="Commit to prove (default HEAD); searches sync/* tags."),
    ] = None,
) -> None:
    """Accept a commit only if a signed sync/* seal tag proves it."""
    if tag is None and commit is None:
        typer.echo("error: pass --tag, --commit, or both", err=True)
        raise typer.Exit(code=2)
    candidate = commit or "HEAD"
    verdict = _guard(
        lambda: verify_seal(corpus_path.expanduser(), candidate, allowed_signers, tag),
    )
    typer.echo(f"verified {verdict.tag} signer {verdict.signer}")


def _guard[T](action: Callable[[], T]) -> T:
    try:
        return action()
    except LovsporError as exc:
        typer.echo(f"error: {' '.join(str(exc).split())}", err=True)
        raise typer.Exit(code=1) from exc
