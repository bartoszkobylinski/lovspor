"""``lovspor publication-seal``: compose and verify the signed publication seal.

``compose`` writes the unsigned seal tag object for the corpus HEAD and never
touches a key; a separate workflow job signs it (``scripts/seal/sign-and-tag.sh``),
running no engine code. ``verify`` applies the
consumer rules of ``docs/publication-seal.md`` with git's own ``verify-tag``
against a pinned allowed-signers file. Any failure exits 1 with one line on
stderr; a usage error exits 2.
"""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from lovspor.errors import LovsporError
from lovspor.publication_seal import AlreadySealed, SealRequest, compose_seal, verify_seal

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
_SignersOption = Annotated[Path, typer.Option("--allowed-signers", help=_SIGNERS_HELP)]


@publication_seal_app.command(name="compose")
def compose(
    corpus_path: _CorpusPath,
    tag_name: Annotated[str, typer.Option("--tag-name", help="sync/seal-<run>-<attempt>.")],
    allowed_signers: _SignersOption,
    out: Annotated[Path, typer.Option("--out", help="File the unsigned tag is written to.")],
) -> None:
    """Write the unsigned seal tag object for HEAD, dated now, or report the seal.

    Prints ``already-sealed <tag>`` and writes no file when any ``sync/*`` tag
    on HEAD verifies against ``--allowed-signers``: a sealed head is never
    sealed again."""
    try:
        request = SealRequest(
            tag_name=tag_name,
            tagged_at=int(time.time()),
            allowed_signers=allowed_signers,
        )
    except ValidationError as exc:
        typer.echo(
            f"error: --tag-name must look like sync/seal-<run>-<attempt>: {tag_name}", err=True
        )
        raise typer.Exit(code=2) from exc
    result = _guard(lambda: compose_seal(corpus_path.expanduser(), request))
    if isinstance(result, AlreadySealed):
        typer.echo(f"already-sealed {result.tag}")
        return
    out.expanduser().write_bytes(result)
    typer.echo(f"composed {out}")


@publication_seal_app.command(name="verify")
def verify(
    corpus_path: _CorpusPath,
    allowed_signers: _SignersOption,
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
