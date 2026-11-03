"""``lovspor ops crosswalk ...``: the crosswalk producer's commands (#610).

On the ``ops`` group because :mod:`lovspor.cli` sits at its size ratchet (the
same reason as ``forarbeider``). Each decorated command only calls its
``_impl``, so the bodies stay inside the mutation gate (#292). Nothing here
publishes, tags, pushes, or writes into ``lovverk``.
"""

import os
import subprocess
from pathlib import Path
from typing import Annotated, BinaryIO

import typer
from pydantic import ValidationError

from lovspor.crosswalk.build import BuildOptions, build
from lovspor.crosswalk.capture import REAL_SOURCES, Sources, capture
from lovspor.crosswalk.check import check
from lovspor.crosswalk.errors import CrosswalkContractError, CrosswalkError
from lovspor.crosswalk.paths import writable_target
from lovspor.crosswalk.release import Approval, promote
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


def read_capped(stream: BinaryIO, limit: int) -> bytes:
    """At most ``limit + 1`` bytes: one byte past the limit proves the input too large.

    Never stat and never read to EOF: a pipe reports size 0 and may never close.
    """
    return stream.read(limit + 1)


def read_bounded(path: Path, limit: int, what: str) -> bytes:
    try:
        with path.open("rb") as stream:
            data = read_capped(stream, limit)
    except OSError as exc:
        raise CrosswalkError(f"{what} unreadable: {path}") from exc
    if len(data) > limit:
        raise CrosswalkError(f"{what} too large: {path}")
    return data


def read_law_ids(path: Path) -> list[str]:
    """One Lovdata law id per line; blank lines and `#` comments are skipped."""
    data = read_bounded(path, MAX_LAW_LIST_BYTES, "law list")
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


_Cache = Annotated[
    Path,
    typer.Option("--cache", help="A new, empty directory outside every Git checkout and Lovverk."),
]


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
    return read_bounded(path, MAX_PREVIOUS_BYTES, "previous release")


def _claim_staging(staging: Path, path: Path) -> None:
    """Create the staging file exclusively: an existing one, or a symlink, is someone else's."""
    try:
        staging.touch(exist_ok=False)
    except FileExistsError as exc:
        raise CrosswalkError(f"refusing to reuse the staging file {staging}") from exc
    except OSError as exc:
        raise CrosswalkError(f"could not write {path}: {exc}") from exc


def write_once(path: Path, data: bytes) -> None:
    """Write a new file whole or not at all; an existing file is never replaced."""
    if os.path.lexists(path):
        raise CrosswalkError(f"refusing to overwrite {path}")
    writable_target(path, "crosswalk output")
    staging = path.with_name(f".{path.name}.partial")
    _claim_staging(staging, path)
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


MAX_APPROVAL_BYTES = 64 * 1024


def read_approval(path: Path) -> Approval:
    try:
        return Approval.model_validate_json(read_bounded(path, MAX_APPROVAL_BYTES, "approval"))
    except ValidationError as exc:
        # Approval is flat, so each error's location is one top-level field.
        fields = sorted({str(e["loc"][0]) for e in exc.errors()})
        raise CrosswalkError(f"approval refused: invalid {', '.join(fields)}") from exc


def check_impl(data_path: Path, previous: Path | None, cache: Path | None) -> str:
    """One line per problem, then a verdict; raises when anything failed."""
    data = read_bounded(data_path, MAX_PREVIOUS_BYTES, "crosswalk file")
    report = check(data, read_previous(previous), cache)
    if not report.passed:
        raise CrosswalkContractError(report.problems)
    unverified = f", {len(report.unverified)} not reproduced from a capture" if cache else ""
    return f"ok: {len(report.records)} records{unverified}"


def release_candidate_impl(data_path: Path, approval: Path, cache: Path, output: Path) -> str:
    data = read_bounded(data_path, MAX_PREVIOUS_BYTES, "crosswalk file")
    promoted, counts = promote(data, read_approval(approval), cache)
    write_once(output, promoted)
    return "; ".join(f"{reason}: {count}" for reason, count in sorted(counts.items()))


_Input = Annotated[Path, typer.Option("--input", help="A crosswalk JSONL file.")]


@crosswalk_app.command("check")
def check_command(
    data: _Input,
    previous: Annotated[Path | None, typer.Option("--previous", help="Previous release.")] = None,
    cache: Annotated[Path | None, typer.Option("--cache", help="Capture to verify.")] = None,
) -> None:
    """Fail closed: canonical form, contract, append-only history, provenance."""
    try:
        typer.echo(check_impl(data, previous, cache))
    except CrosswalkContractError as error:
        for problem in error.problems:
            typer.echo(f"problem: {problem}", err=True)
        raise typer.Exit(code=1) from error
    except LovsporError as error:
        typer.echo(f"refused: {error}", err=True)
        raise typer.Exit(code=1) from error


@crosswalk_app.command("release-candidate")
def release_candidate_command(
    data: _Input,
    approval: Annotated[Path, typer.Option("--approval", help="The owner's approval file.")],
    cache: _Cache,
    output: Annotated[Path, typer.Option("--output", help="New JSONL file; never overwritten.")],
) -> None:
    """Explicit gate: promote eligible records under the owner's approval of these bytes."""
    try:
        typer.echo(release_candidate_impl(data, approval, cache, output))
    except LovsporError as error:
        typer.echo(f"refused: {error}", err=True)
        raise typer.Exit(code=1) from error
