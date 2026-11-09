"""``lovspor ops forarbeider-fetch``: fetch an owner-named list of pages (#738).

The decorated command only calls :func:`fetch_impl`, so the body stays inside
the mutation gate (#292: mutmut skips decorated functions).
"""

from dataclasses import replace
from pathlib import Path
from typing import Annotated

import httpx
import typer

from lovspor.errors import RegjeringenFetchError
from lovspor.ops.commands import ops_app
from lovspor.regjeringen_fetch.fetch import (
    MAX_URLS,
    FetchSettings,
    prepare_out,
    read_url_list,
    run_fetch,
)
from lovspor.regjeringen_fetch.models import (
    FetchRecord,
    FetchRun,
    SourceEvidence,
    all_fetched,
    evidence_file_name,
    run_file_name,
)

DEFAULT_LIMIT = 50


def fetch_impl(
    urls: Path, out: Path, limit: int, settings: FetchSettings | None = None
) -> tuple[str, bool]:
    """Run the fetch and return the report and whether every URL was stored.

    Raises:
        RegjeringenFetchError: the list, the directory or robots.txt was refused.
    """
    listed = read_url_list(urls, limit)
    target = prepare_out(out)
    with httpx.Client() as client:
        run = run_fetch(client, listed, target, settings or FetchSettings())
    return _report(run, target), all_fetched(run)


def _report(run: FetchRun, out: Path) -> str:
    sources = [run.robots] if run.terms is None else [run.robots, run.terms]
    lines = [_source_line(evidence, out) for evidence in sources]
    lines.extend(_line(record, out) for record in run.records)
    fetched = sum(record.sha256 is not None for record in run.records)
    lines.append(
        f"fetched {fetched} of {len(run.records)}; robots {_digest(run.robots)}; "
        f"terms {'-' if run.terms is None else _digest(run.terms)}; "
        f"spacing {run.delay_seconds:g}s; run {out / run_file_name(run.started_at)}"
    )
    return "\n".join(lines)


def _digest(evidence: SourceEvidence) -> str:
    return evidence.state.value if evidence.sha256 is None else evidence.sha256[:12]


def _source_line(evidence: SourceEvidence, out: Path) -> str:
    moment = evidence.retrieved_at.isoformat(timespec="seconds")
    status = "-" if evidence.status is None else str(evidence.status)
    line = f"{evidence.kind} {evidence.state} {evidence.requested_url} status {status} at {moment}"
    name = evidence_file_name(evidence)
    return line if name is None else f"{line} file {out / name}"


def _line(record: FetchRecord, out: Path) -> str:
    moment = record.retrieved_at.isoformat(timespec="seconds")
    if record.sha256 is None:
        status = "-" if record.status is None else str(record.status)
        return f"{record.outcome} {record.url} status {status} at {moment}"
    return (
        f"{record.outcome} {record.url} retrieved_at {moment} file {out / f'{record.sha256}.html'}"
    )


@ops_app.command("forarbeider-fetch")
def forarbeider_fetch(
    urls: Annotated[
        Path,
        typer.Option(
            "--urls", help="A text file: one Prop. L / LS page URL per line.", readable=False
        ),
    ],
    out: Annotated[
        Path,
        typer.Option("--out", help="Absolute owner-only directory outside every Git repository."),
    ],
    limit: Annotated[
        int,
        typer.Option("--max", min=1, max=MAX_URLS, help="Refuse a list longer than this."),
    ] = DEFAULT_LIMIT,
    terms_url: Annotated[
        str | None,
        typer.Option(
            "--terms-url",
            help="One www.regjeringen.no page holding the website terms, read once per run.",
        ),
    ] = None,
) -> None:
    """Fetch the listed regjeringen.no Prop. L / LS pages into private staging (issue #738).

    Only the URLs listed, robots.txt read live, at least 5 s between requests,
    a 403 or 429 stops the run, no page redirect is followed. robots.txt and the
    terms page are stored as the current source proof. Exit 1 is a refusal with
    nothing fetched; exit 2 means some URLs were not stored.
    """
    settings = replace(FetchSettings(), terms_url=terms_url)
    try:
        report, complete = fetch_impl(urls, out, limit, settings)
    except RegjeringenFetchError as error:
        typer.echo(f"refused: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(report)
    if not complete:
        raise typer.Exit(code=2)
