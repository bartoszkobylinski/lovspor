"""``lovspor ops forarbeider-metadata`` and ``forarbeider-chapter``: one saved page to staging.

On the ``ops`` group because :mod:`lovspor.cli` sits at its size ratchet. The
decorated commands only call their ``_impl``, so the bodies stay inside the
mutation gate (#292). All I/O is here, and there is no clock: the retrieval
time is the operator's, because only the operator knows when the page was saved.
"""

from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from lovspor.corpus_fetch import default_corpus_path
from lovspor.forarbeider.chapter import build_chapter_result
from lovspor.forarbeider.chapter_models import ChapterResult, result_text
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.models import ForarbeidRecord, write_metadata
from lovspor.forarbeider.page import build_record
from lovspor.forarbeider.staging import checked_output, write_observation
from lovspor.ops.commands import ops_app

#: Far above the largest sampled chapter page (166 KB), below what memory would notice.
MAX_HTML_BYTES = 16 * 1024 * 1024

_ENGINE_ROOT = Path(__file__).resolve().parents[3]


def _aware(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, text) from error
    if moment.tzinfo is None:
        raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, text)
    return moment


def _saved_page(html: Path) -> bytes:
    """The bytes of ``html``; one past the limit is read, so a larger file never loads whole."""
    if not html.is_file():
        raise ForarbeiderRefusedError(RefusalReason.INPUT_UNREADABLE, str(html))
    try:
        with html.open("rb") as stream:
            payload = stream.read(MAX_HTML_BYTES + 1)
    except OSError as error:
        raise ForarbeiderRefusedError(RefusalReason.INPUT_UNREADABLE, str(html)) from error
    if len(payload) > MAX_HTML_BYTES:
        raise ForarbeiderRefusedError(RefusalReason.INPUT_TOO_LARGE, str(html))
    return payload


def _write(record: ForarbeidRecord, target: Path) -> None:
    try:
        write_metadata(record, target)
    except OSError as error:
        raise ForarbeiderRefusedError(RefusalReason.OUTPUT_WRITE_FAILED, str(target)) from error


def metadata_impl(html: Path, source_url: str, retrieved_at: str, output: Path) -> str:
    """Write the record of a saved page and return the line that reports it.

    Raises:
        ForarbeiderRefusedError: any check failed; nothing was written.
    """
    target = checked_output(output, (_ENGINE_ROOT, default_corpus_path()))
    moment = _aware(retrieved_at)
    record = build_record(_saved_page(html), source_url, moment)
    _write(record, target)
    return f"wrote {record.id}: {len(record.chapters)} chapters to {target}"


def chapter_impl(html: Path, source_url: str, retrieved_at: str, output: Path) -> str:
    """Write the held result of a saved chapter page and return the line that reports it.

    The line names reasons and counts, never chapter text.

    Raises:
        ForarbeiderRefusedError: any check failed; nothing was written.
    """
    target = checked_output(output, (_ENGINE_ROOT, default_corpus_path()))
    moment = _aware(retrieved_at)
    result = build_chapter_result(_saved_page(html), source_url, moment)
    written = write_observation(target, result_text(result))
    return f"{'held' if written else 'unchanged'} {result.id}: {_summary(result)} at {target}"


def _summary(result: ChapterResult) -> str:
    body = "body withheld" if result.blocks is None else f"{len(result.blocks)} blocks"
    return f"{body}, reasons: {', '.join(result.hold_reasons)}"


@ops_app.command("forarbeider-metadata")
def forarbeider_metadata(
    html: Annotated[
        Path,
        typer.Option("--html", help="A saved ?ch=N page.", readable=False),
    ],
    source_url: Annotated[
        str, typer.Option("--source-url", help="The exact URL that page was saved from.")
    ],
    retrieved_at: Annotated[
        str, typer.Option("--retrieved-at", help="When it was saved, ISO 8601 with offset.")
    ],
    output: Annotated[
        Path, typer.Option("--output", help="Absolute .json path outside every Git repository.")
    ],
) -> None:
    """Read a saved regjeringen.no Prop. L / LS page into staging metadata (issue #738).

    Offline: no HTTP request, no publication. Exit 1 is a typed refusal, and
    nothing is written.
    """
    try:
        typer.echo(metadata_impl(html, source_url, retrieved_at, output))
    except ForarbeiderRefusedError as error:
        typer.echo(f"refused: {error}", err=True)
        raise typer.Exit(code=1) from error


@ops_app.command("forarbeider-chapter")
def forarbeider_chapter(
    html: Annotated[
        Path,
        typer.Option("--html", help="A saved ?ch=N page: the chapter to extract.", readable=False),
    ],
    source_url: Annotated[
        str, typer.Option("--source-url", help="The exact URL that page was saved from.")
    ],
    retrieved_at: Annotated[
        str, typer.Option("--retrieved-at", help="When it was saved, ISO 8601 with offset.")
    ],
    output: Annotated[
        Path, typer.Option("--output", help="Absolute .json path outside every Git repository.")
    ],
) -> None:
    """Extract one chapter of a saved Prop. L / LS page into held private staging (issue #738).

    Offline: no HTTP request, no publication, no clearance. Every result is
    held. Exit 1 is a typed refusal, and nothing is written.
    """
    try:
        typer.echo(chapter_impl(html, source_url, retrieved_at, output))
    except ForarbeiderRefusedError as error:
        typer.echo(f"refused: {error}", err=True)
        raise typer.Exit(code=1) from error
