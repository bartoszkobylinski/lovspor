"""Read the hosted MCP's hourly metric lines back into a report (issue #479).

The input is journal text — ``journalctl -u lovspor-mcp -o cat`` on the droplet,
piped in or saved to a file — so this module only parses; it never runs
``journalctl`` itself and needs no access to the box.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, ValidationError

from lovspor.errors import LovsporError
from lovspor.usage_metrics import METRICS_TAG, HourlyUsage

_HEADER = (
    f"{'hour (UTC)':16} {'calls':>6} {'ok':>4} {'errors':>7} {'refused':>8} "
    f"{'p50 ms':>7} {'p95 ms':>7} {'active':>7}"
)


class UsageReportError(LovsporError):
    """The report was asked for something it cannot answer."""


class ParsedUsage(BaseModel):
    """The metric hours found in the input, and how many tagged lines did not parse."""

    model_config = ConfigDict(frozen=True)

    hours: list[HourlyUsage]
    malformed: int


class UsageSummary(BaseModel):
    """Totals over a run of hours.

    Distinct credentials are counted within one hour only, and one caller
    active in every hour would be counted in each, so the summary carries the
    peak hour rather than a sum that would overstate the audience.
    """

    model_config = ConfigDict(frozen=True)

    hours: list[HourlyUsage]
    calls: int
    ok: int
    errors: int
    refused: dict[str, int]
    by_tool: dict[str, int]
    peak_active_credentials: int
    worst_p95_ms: float | None


def parse_usage(lines: Iterable[str]) -> ParsedUsage:
    """Keep every line carrying the metrics tag, wherever the journal put it.

    A tagged line that does not parse — cut short by a crash mid-write — is
    counted rather than fatal, so one torn line does not hide a day of data.
    """
    hours: list[HourlyUsage] = []
    malformed = 0
    for line in lines:
        _, tag, payload = line.partition(f"{METRICS_TAG} ")
        if not tag:
            continue
        try:
            hours.append(HourlyUsage.model_validate_json(payload))
        except ValidationError:
            malformed += 1
    return ParsedUsage(hours=hours, malformed=malformed)


def resolve_since(value: str, today: date) -> date | None:
    """``today``, ``all`` (no bound) or an ISO date, as the first UTC day to report."""
    if value == "today":
        return today
    if value == "all":
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise UsageReportError(
            f"--since takes 'today', 'all' or a date like 2026-09-30, not {value!r}"
        ) from error


def _merged(counters: Iterable[dict[str, int]]) -> dict[str, int]:
    total: Counter[str] = Counter()
    for counter in counters:
        total.update(counter)
    return dict(sorted(total.items()))


def summarize(hours: list[HourlyUsage], since: date | None) -> UsageSummary:
    """Totals over the hours from the start of ``since`` (UTC) on, or all of them."""
    start = None if since is None else datetime(since.year, since.month, since.day, tzinfo=UTC)
    kept = [usage for usage in hours if start is None or usage.hour >= start]
    p95s = [usage.p95_ms for usage in kept if usage.p95_ms is not None]
    return UsageSummary(
        hours=kept,
        calls=sum(usage.calls for usage in kept),
        ok=sum(usage.ok for usage in kept),
        errors=sum(usage.errors for usage in kept),
        refused=_merged(usage.refused for usage in kept),
        by_tool=_merged(usage.by_tool for usage in kept),
        peak_active_credentials=max((usage.active_credentials for usage in kept), default=0),
        worst_p95_ms=max(p95s, default=None),
    )


def _latency(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _row(usage: HourlyUsage) -> str:
    refused = sum(usage.refused.values())
    return (
        f"{usage.hour:%Y-%m-%d %H:%M} {usage.calls:>6} {usage.ok:>4} {usage.errors:>7} "
        f"{refused:>8} {_latency(usage.p50_ms):>7} {_latency(usage.p95_ms):>7} "
        f"{usage.active_credentials:>7}"
    )


def _pairs(counts: dict[str, int]) -> str:
    return ", ".join(f"{name} {count}" for name, count in counts.items()) or "none"


def _totals(summary: UsageSummary) -> list[str]:
    refused = sum(summary.refused.values())
    return [
        f"total: {summary.calls} calls, {summary.ok} ok, {summary.errors} errors, "
        f"{refused} refused",
        f"refused by reason: {_pairs(summary.refused)}",
        f"by tool: {_pairs(summary.by_tool)}",
        f"peak distinct credentials in one hour: {summary.peak_active_credentials}",
        f"worst hourly p95: {_latency(summary.worst_p95_ms)} ms",
    ]


def render_report(summary: UsageSummary, malformed: int) -> str:
    """The per-hour table, then the totals. An hour split by a restart shows twice."""
    lines = [_HEADER, *(_row(usage) for usage in summary.hours), "", *_totals(summary)]
    if not summary.hours:
        lines = [f"no {METRICS_TAG} hours to report"]
    if malformed:
        lines.append(f"skipped {malformed} malformed {METRICS_TAG} line(s)")
    return "\n".join(lines)
