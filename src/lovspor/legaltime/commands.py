"""``lovspor ops legal-time ...``: the #744 replay pilot's operator commands.

On the ``ops`` group because :mod:`lovspor.cli` sits at its size ratchet.
``fetch`` pins the publicData inputs into a cache directory; ``replay``
reconstructs one act at one or more legal dates from those inputs and writes
a JSON report of identities, dates, verdicts and counts. Neither command
writes into ``lovverk``, publishes anything, or touches the recorded-time
tools.
"""

import re
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError

from lovspor.errors import LovsporError
from lovspor.legaltime.archive import collect, dump_json, load_oracle, pins
from lovspor.legaltime.document import Frozen
from lovspor.legaltime.errors import LegalTimeInputError
from lovspor.legaltime.run import build_report
from lovspor.ops.commands import ops_app
from lovspor.sources.lovdata import LovdataClient

legal_time_app = typer.Typer(
    name="legal-time",
    help="#744 pilot: replay Lovtidend avd. I announcements into an act's text at a legal date.",
    no_args_is_help=True,
)
ops_app.add_typer(legal_time_app)

PILOT_ARCHIVES = re.compile(r"^(?:lovtidend-avd1-(?:2001-2025|\d{4})|gjeldende-lover)\.tar\.bz2$")
_ACT = re.compile(r"^lov/\d{4}-\d{2}-\d{2}(?:-\d+)?$")


class ReplayRequest(Frozen):
    act: str
    archives: tuple[Path, ...]
    dates: tuple[date, ...]
    oracle: Path | None


def _parse_dates(raw: list[str]) -> tuple[date, ...]:
    try:
        return tuple(date.fromisoformat(value) for value in raw)
    except ValueError as exc:
        raise typer.BadParameter(f"--at takes ISO dates: {exc}") from exc


def replay_impl(request: ReplayRequest) -> tuple[str, str]:
    """(report JSON, one-line-per-date summary). Indexes are cached beside each archive."""
    if not _ACT.match(request.act):
        raise typer.BadParameter(
            f"--act must be a refid such as lov/2019-06-14-21, not {request.act!r}"
        )
    if not request.dates:
        raise typer.BadParameter("at least one --at date is required; no clock is consulted")
    try:
        evidence = collect(request.act, list(request.archives))
        oracle = load_oracle(request.oracle, request.act) if request.oracle else None
        report = build_report(evidence, list(request.dates), oracle, list(request.archives))
    except LovsporError as exc:
        typer.echo(f"legal-time replay refused: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    return dump_json(report) + "\n", _summary(report)


def _summary(report: dict[str, Any]) -> str:
    lines = []
    for state in report["states"]:
        lines.append(
            f"{state['at']}: {state['status']}, applied {len(state['applied'])}, "
            f"future {len(state['future'])}, pending {len(state['pending'])}, "
            f"unresolved {len(state['unresolved'])}"
        )
    for verification in report.get("verification", []):
        lines.append(f"{verification['at']}: {verification['counts']}")
    return "\n".join(lines)


_Archive = Annotated[
    list[Path], typer.Option("--archive", help="A lovtidend-avd1 archive; repeatable.")
]
_At = Annotated[
    list[str], typer.Option("--at", help="Legal date (ISO) to reconstruct; repeatable.")
]
_Oracle = Annotated[
    Path | None,
    typer.Option("--oracle", help="gjeldende-lover archive to verify the end state against."),
]


@legal_time_app.command(name="replay")
def replay_command(
    act: Annotated[
        str, typer.Option("--act", help="Lovdata refid of the act, e.g. lov/2019-06-14-21.")
    ],
    archive: _Archive,
    at: _At,
    oracle: _Oracle = None,
) -> None:
    """Reconstruct one act at legal dates; JSON report on stdout, summary on stderr."""
    try:
        request = ReplayRequest(
            act=act, archives=tuple(archive), dates=_parse_dates(at), oracle=oracle
        )
    except ValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc
    report, summary = replay_impl(request)
    typer.echo(report, nl=False)
    typer.echo(summary, err=True)


def fetch_impl(dest: Path, client: LovdataClient) -> str:
    """Download the pilot's archives from publicData and pin them in ``dest/pins.json``."""
    dest.mkdir(parents=True, exist_ok=True)
    chosen = [a for a in client.list_datasets() if PILOT_ARCHIVES.match(a.filename)]
    if not chosen:
        raise LegalTimeInputError("publicData /list names no pilot archive")
    paths = [client.download(entry, dest).path for entry in chosen]
    record: dict[str, Any] = {
        "source": "https://api.lovdata.no/v1/publicData/",
        "archives": pins(paths),
    }
    (dest / "pins.json").write_text(dump_json(record) + "\n", encoding="utf-8")
    return "\n".join(f"{p['filename']} sha256={p['sha256']}" for p in record["archives"])


@legal_time_app.command(name="fetch")
def fetch_command(
    dest: Annotated[Path, typer.Option("--dest", help="Cache directory for the pinned archives.")],
) -> None:
    """Download and pin the pilot's publicData archives (never committed)."""
    try:
        with LovdataClient() as client:
            typer.echo(fetch_impl(dest, client))
    except LovsporError as exc:
        typer.echo(f"legal-time fetch refused: {exc}", err=True)
        raise typer.Exit(code=1) from exc
