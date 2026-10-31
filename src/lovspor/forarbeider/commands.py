"""``lovspor ops forarbeider-metadata``: one saved page to one staging record.

On the ``ops`` group because :mod:`lovspor.cli` sits at its size ratchet. The
decorated command only calls :func:`metadata_impl`, so the body stays inside
the mutation gate (#292). All I/O is here, and there is no clock: the retrieval
time is the operator's, because only the operator knows when the page was saved.
"""

from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from lovspor.corpus_fetch import default_corpus_path
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.models import write_metadata
from lovspor.forarbeider.page import build_record
from lovspor.forarbeider.staging import checked_output
from lovspor.ops.commands import ops_app

_ENGINE_ROOT = Path(__file__).resolve().parents[3]


def _aware(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, text) from error
    if moment.tzinfo is None:
        raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, text)
    return moment


def metadata_impl(html: Path, source_url: str, retrieved_at: str, output: Path) -> str:
    """Write the record of a saved page and return the line that reports it.

    Raises:
        ForarbeiderRefusedError: any check failed; nothing was written.
    """
    target = checked_output(output, (_ENGINE_ROOT, default_corpus_path()))
    moment = _aware(retrieved_at)
    record = build_record(html.read_bytes(), source_url, moment)
    write_metadata(record, target)
    return f"wrote {record.id}: {len(record.chapters)} chapters to {target}"


@ops_app.command("forarbeider-metadata")
def forarbeider_metadata(
    html: Annotated[
        Path,
        typer.Option("--html", help="A saved ?ch=N page.", exists=True, dir_okay=False),
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
