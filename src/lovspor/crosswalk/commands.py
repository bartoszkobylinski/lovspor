"""``lovspor ops crosswalk ...``: the crosswalk producer's commands (#610).

On the ``ops`` group because :mod:`lovspor.cli` sits at its size ratchet (the
same reason as ``forarbeider``). Each decorated command only calls its
``_impl``, so the bodies stay inside the mutation gate (#292). Nothing here
publishes, tags, pushes, or writes into ``lovverk``.
"""

import os
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from lovspor.crosswalk.build import BuildOptions, build
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
MAX_PREVIOUS_BYTES = 512 * 1024 * 1024
_ENGINE_ROOT = Path(__file__).resolve().parents[3]


def read_law_ids(path: Path) -> list[str]:
    """One Lovdata law id per line; blank lines and `#` comments are skipped."""
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_LAW_LIST_BYTES + 1)
    except OSError as exc:
        raise CrosswalkError(f"law list unreadable: {path}") from exc
    if len(data) > MAX_LAW_LIST_BYTES:
        raise CrosswalkError(f"law list too large: {path}")
    try:
        text = data.decode("utf-8")
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


def engine_commit(root: Path = _ENGINE_ROOT) -> str:
    """The producer commit: HEAD of the engine checkout this code runs from."""
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "-C", str(root), "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CrosswalkError("producer commit unknown: pass --producer-commit") from exc
    return done.stdout.strip()


def read_previous(path: Path | None) -> bytes | None:
    if path is None:
        return None
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_PREVIOUS_BYTES + 1)
    except OSError as exc:
        raise CrosswalkError(f"previous release unreadable: {path}") from exc
    if len(data) > MAX_PREVIOUS_BYTES:
        raise CrosswalkError(f"previous release too large: {path}")
    return data


def write_once(path: Path, data: bytes) -> None:
    """Write a new file whole or not at all; an existing file is never replaced."""
    if path.exists():
        raise CrosswalkError(f"refusing to overwrite {path}")
    staging = path.with_name(f".{path.name}.partial")
    try:
        staging.write_bytes(data)
        os.link(staging, path)
    except OSError as exc:
        raise CrosswalkError(f"could not write {path}: {exc}") from exc
    finally:
        staging.unlink(missing_ok=True)


def build_impl(cache: Path, output: Path, options: BuildOptions) -> str:
    data = build(cache, options)
    write_once(output, data)
    return f"wrote {data.count(b'\n')} records to {output}"


@crosswalk_app.command("build")
def build_command(
    cache: _Cache,
    output: Annotated[Path, typer.Option("--output", help="New JSONL file; never overwritten.")],
    previous: Annotated[
        Path | None, typer.Option("--previous", help="The previous release's JSONL, if any.")
    ] = None,
    producer_commit: Annotated[
        str | None, typer.Option("--producer-commit", help="Defaults to this checkout's HEAD.")
    ] = None,
) -> None:
    """Offline: canonical JSONL from a capture; no network, never research_only promotion."""
    try:
        options = BuildOptions(
            producer_commit=producer_commit or engine_commit(), previous=read_previous(previous)
        )
        typer.echo(build_impl(cache, output, options))
    except LovsporError as error:
        typer.echo(f"refused: {error}", err=True)
        raise typer.Exit(code=1) from error
