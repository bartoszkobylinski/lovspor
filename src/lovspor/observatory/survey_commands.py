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
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import httpx
import typer

from lovspor.observatory.commands import _root, observatory_app
from lovspor.observatory.storage import ObservatoryRoot
from lovspor.observatory.survey import SiteShape
from lovspor.observatory.survey_probe import DEFAULT_DELAY_SECONDS, ProbeSettings, SiteProbe

SURVEY_DIRNAME = "survey"

#: A run id is a file name. Separators, a leading dot and anything else that
#: could make it behave like a path are refused rather than rewritten.
_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


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


def _run_name(value: str | None) -> str:
    """The log's file name: what was asked for, or a UTC stamp.

    A run id names a file; it is not a path, and it may not act like one.
    Interpolating it straight into one let ``--run-id ../escaped`` write outside
    ``survey/``, and a longer climb outside the archive altogether — the
    ADR-0010 §5 boundary, reached through an operator's argument rather than
    through the env var the boundary type guards.

    Refused by pattern rather than sanitised: quietly rewriting the name that
    was asked for means the file the operator goes looking for later is not the
    file that was written. Checked before any host is probed, so a bad argument
    costs no requests.
    """
    if value is None:
        return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if not _RUN_ID_PATTERN.fullmatch(value):
        _refuse(
            f"Refused: --run-id must be a plain file name, got {value!r}. "
            "Letters, digits, dot, dash and underscore, starting with a letter or digit."
        )
    return value


def _survey_path(root: ObservatoryRoot, run_name: str) -> Path:
    directory = root.path / SURVEY_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{run_name}.jsonl"


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


def _hosts_only(hosts: list[str]) -> None:
    """Refuse a URL where a host was asked for, before any request is made.

    The probe builds ``https://{host}/`` itself, so a scheme or path here
    yields ``https://https://x/`` — three requests to nothing, an exit code
    of 0, and a log row that says the host was reached and answered nothing.
    """
    for host in hosts:
        if "/" in host:
            _refuse(f"Refused: a host without scheme or path was expected, got: {host}")


def _refuse(message: str) -> NoReturn:
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
    _hosts_only(hosts)
    root = _root()
    run_name = _run_name(run_id)
    shapes: list[SiteShape] = []
    with httpx.Client() as client:
        probe = SiteProbe(client, ProbeSettings(delay_seconds=delay))
        for host in hosts:
            shape = probe.read(host)
            typer.echo(f"{shape.entry:22} {host}")
            shapes.append(shape)
    path = _survey_path(root, run_name)
    _write(path, shapes)
    typer.echo(f"\n{len(shapes)} host(s) surveyed, written to {path}")
    _tally(shapes)
