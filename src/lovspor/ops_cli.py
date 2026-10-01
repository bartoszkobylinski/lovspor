"""``lovspor ops``: operator tools for the hosted service.

Its own module because :mod:`lovspor.cli` sits at its size ratchet. The
decorated command only calls :func:`usage_impl`, so the body stays inside the
mutation gate (#292: mutmut skips decorated functions).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from lovspor.usage_report import (
    UsageReportError,
    parse_usage,
    render_report,
    resolve_since,
    summarize,
)

ops_app = typer.Typer(
    name="ops",
    help="Operator tools for the hosted MCP service.",
    no_args_is_help=True,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def usage_impl(source: Path | None, since: str, utc_now: Callable[[], datetime]) -> str:
    """The usage report for journal text in ``source``, or on stdin when ``None``."""
    since_day = resolve_since(since, utc_now().date())
    if source is None:
        text = typer.get_text_stream("stdin").read()
    else:
        text = source.read_text(encoding="utf-8")
    parsed = parse_usage(text.splitlines())
    return render_report(summarize(parsed.hours, since_day), parsed.malformed)


@ops_app.command("usage")
def usage(
    source: Annotated[
        Path | None,
        typer.Argument(help="Journal text to read (default: stdin).", exists=True, dir_okay=False),
    ] = None,
    since: Annotated[
        str,
        typer.Option("--since", help="'today', 'all', or a UTC date like 2026-09-30."),
    ] = "all",
) -> None:
    """Summarize the hosted MCP's hourly usage lines (issue #479).

    Aggregate numbers only — calls, refusals by brake, latency and a count of
    distinct callers per hour; the lines carry no identifiers. On the droplet:
    journalctl -u lovspor-mcp -o cat | lovspor ops usage --since today
    """
    try:
        typer.echo(usage_impl(source, since, _utc_now))
    except UsageReportError as error:
        raise typer.BadParameter(str(error), param_hint="--since") from error
