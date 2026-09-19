"""The ``observatory survey`` command: recon that leaves a record.

Registration needs evidence about a site, and until now gathering it happened in
scripts. The 2026-08-20 sweep over all 358 municipalities is the cost of that: it
produced the figures still quoted in :mod:`lovspor.observatory.commands` and
persisted nothing, so the population it measured cannot be re-derived (issue
#349). This command exists so the next such pass is answerable a month later.

It lives in its own module rather than in :mod:`lovspor.observatory.commands`
because that file is at its size ratchet, and a command is a reasonable seam.
:mod:`lovspor.cli` imports it for the registration side effect.

The survey log is written beside the observation log but is **not** part of it.
An observation records what an activated source served; a survey row records
whether a host could be activated at all. Keeping them apart preserves the
invariant that every authority id in the observation log is a registered one.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer

from lovspor.observatory.commands import _root, observatory_app
from lovspor.observatory.storage import ObservatoryRoot
from lovspor.observatory.survey import SiteShape
from lovspor.observatory.survey_probe import DEFAULT_DELAY_SECONDS, ProbeSettings, SiteProbe

SURVEY_DIRNAME = "survey"


def _domains(named: list[str] | None, listing: Path | None) -> list[str]:
    """Every host to probe, in the order given, with duplicates dropped.

    A list file may carry comments and blank lines, because the useful version
    of it is one a human maintains next to their notes.
    """
    collected = list(named or ())
    if listing is not None:
        collected.extend(
            line.strip()
            for line in listing.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    return list(dict.fromkeys(collected))


def _survey_path(root: ObservatoryRoot, run_id: str) -> Path:
    directory = root.path / SURVEY_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{run_id}.jsonl"


def _write(path: Path, shapes: list[SiteShape]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for shape in shapes:
            handle.write(json.dumps(shape.model_dump(mode="json"), ensure_ascii=False) + "\n")


def _tally(shapes: list[SiteShape]) -> None:
    counts: dict[str, int] = {}
    for shape in shapes:
        counts[shape.entry] = counts.get(shape.entry, 0) + 1
    for entry, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])):
        typer.echo(f"  {entry}: {count}")


def _refuse(message: str) -> None:
    typer.echo(message, err=True)
    raise typer.Exit(2)


@observatory_app.command("survey")
def survey(
    domain: Annotated[
        list[str] | None,
        typer.Option("--domain", help="A host to probe, without scheme. Repeatable."),
    ] = None,
    from_file: Annotated[
        Path | None,
        typer.Option("--from", help="A file of hosts, one per line; # comments allowed."),
    ] = None,
    run_id: Annotated[
        str | None, typer.Option("--run-id", help="Names the log file. Defaults to a UTC stamp.")
    ] = None,
    delay: Annotated[
        float, typer.Option("--delay", help="Seconds between requests to one host.")
    ] = DEFAULT_DELAY_SECONDS,
) -> None:
    """Ask hosts what they offer a crawler, before anything registers them.

    Three requests per host at most, and none at all past a policy that refuses.
    Nothing is captured and nothing enters the observation log: the question is
    whether a source could be registered, not what it says.
    """
    if from_file is not None and not from_file.exists():
        _refuse(f"Refused: no such file: {from_file}")
    hosts = _domains(domain, from_file)
    if not hosts:
        _refuse("Refused: no domains to survey; pass --domain or --from.")
    root = _root()
    shapes: list[SiteShape] = []
    with httpx.Client() as client:
        probe = SiteProbe(client, ProbeSettings(delay_seconds=delay))
        for host in hosts:
            shape = probe.read(host)
            typer.echo(f"{shape.entry:22} {host}")
            shapes.append(shape)
    path = _survey_path(root, run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    _write(path, shapes)
    typer.echo(f"\n{len(shapes)} host(s) surveyed, written to {path}")
    _tally(shapes)
