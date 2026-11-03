"""``lovspor ops crosswalk ...``: the crosswalk producer's commands (#610).

On the ``ops`` group because :mod:`lovspor.cli` sits at its size ratchet (the
same reason as ``forarbeider``). Each decorated command only calls its
``_impl``, so the bodies stay inside the mutation gate (#292). Nothing here
publishes, tags, pushes, or writes into ``lovverk``.
"""

from pathlib import Path
from typing import Annotated

import typer

from lovspor.crosswalk.capture import REAL_SOURCES, Sources, capture
from lovspor.crosswalk.errors import CrosswalkError
from lovspor.errors import LovsporError
from lovspor.ops.commands import ops_app

crosswalk_app = typer.Typer(
    name="crosswalk",
    help="ADR-0017 legislative crosswalk: capture, offline build, check, manifest.",
    no_args_is_help=True,
)
ops_app.add_typer(crosswalk_app)

MAX_LAW_LIST_BYTES = 1_000_000


def read_law_ids(path: Path) -> list[str]:
    """One Lovdata law id per line; blank lines and `#` comments are skipped."""
    try:
        if path.stat().st_size > MAX_LAW_LIST_BYTES:
            raise CrosswalkError(f"law list too large: {path}")
        data = path.read_bytes()
    except OSError as exc:
        raise CrosswalkError(f"law list unreadable: {path}") from exc
    try:
        text = data.decode()
    except UnicodeDecodeError as exc:
        raise CrosswalkError(f"law list is not UTF-8: {path}") from exc
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def capture_impl(
    cache: Path, laws: Path, lovverk_commit: str, sources: Sources = REAL_SOURCES
) -> str:
    manifest = capture(cache, read_law_ids(laws), lovverk_commit, sources)
    failed = sum(entry["error"] is not None for entry in manifest["stortinget"])
    archives = len(manifest["lovdata"])
    responses = len(manifest["stortinget"])
    return f"captured {archives} archives and {responses} Stortinget responses ({failed} failed)"


_Cache = Annotated[Path, typer.Option("--cache", help="A new, gitignored capture directory.")]


@crosswalk_app.command("capture")
def capture_command(
    cache: _Cache,
    laws: Annotated[Path, typer.Option("--laws", help="File of amending LOV ids, one a line.")],
    lovverk_commit: Annotated[
        str, typer.Option("--lovverk-commit", help="Lovverk commit the law ids were read from.")
    ],
) -> None:
    """Network: Lovdata publicData and data.stortinget.no eksport/* into a write-once cache."""
    try:
        typer.echo(capture_impl(cache, laws, lovverk_commit))
    except LovsporError as error:
        typer.echo(f"refused: {error}", err=True)
        raise typer.Exit(code=1) from error
