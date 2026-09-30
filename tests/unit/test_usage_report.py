"""``lovspor ops usage``: reading the hourly metrics back (issue #479).

The input is what the owner pipes in from the droplet, ``journalctl -o cat``
text. The fixture below is that shape: metric lines among the service's other
output, including a line cut short and an hour split by a restart.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.ops_cli import usage_impl
from lovspor.usage_report import (
    UsageReportError,
    parse_usage,
    render_report,
    resolve_since,
    summarize,
)

_JOURNAL = """\
INFO:     Started server process [4121]
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
lovspor.metrics {"hour":"2026-09-29T23:00:00Z","calls":5,"ok":5,"errors":0,"refused":{},"p50_ms":12.0,"p95_ms":40.0,"by_tool":{"get_law":5},"active_credentials":1}
INFO:     127.0.0.1:52100 - "POST /mcp HTTP/1.1" 200 OK
lovspor.metrics {"hour":"2026-09-30T13:00:00Z","calls":8,"ok":4,"errors":1,"refused":{"rate":1,"service_capacity":2},"p50_ms":30.0,"p95_ms":500.0,"by_tool":{"get_law":7,"semantic_search":1},"active_credentials":3}
lovspor.metrics {"hour":"2026-09-30T13:00:00Z","calls":2,"ok":1,"errors":0,"refused":{"rate":1},"p50_ms":8.0,"p95_ms":8.0,"by_tool":{"get_law":2},"active_credentials":2}
lovspor.metrics {"hour":"2026-09-30T14:00:00Z","calls":
lovspor.metrics {"hour":"2026-09-30T14:00:00Z","calls":1,"ok":0,"errors":0,"refused":{"daily":1},"p50_ms":null,"p95_ms":null,"by_tool":{"list_sections":1},"active_credentials":1}
"""  # noqa: E501


def test_parse_keeps_metric_lines_and_counts_the_malformed() -> None:
    parsed = parse_usage(_JOURNAL.splitlines())
    assert [usage.calls for usage in parsed.hours] == [5, 8, 2, 1]
    assert parsed.malformed == 1


def test_parse_finds_the_tag_behind_a_journal_prefix() -> None:
    line = "Sep 30 13:00:01 lovspor lovspor[4121]: " + _JOURNAL.splitlines()[2]
    assert parse_usage([line]).hours[0].calls == 5


def test_since_is_today_all_or_an_iso_date() -> None:
    today = date(2026, 9, 30)
    assert resolve_since("today", today) == date(2026, 9, 30)
    assert resolve_since("all", today) is None
    assert resolve_since("2026-09-01", today) == date(2026, 9, 1)
    with pytest.raises(UsageReportError, match="--since"):
        resolve_since("yesterday-ish", today)


def test_summary_sums_counts_and_keeps_peaks_that_cannot_be_summed() -> None:
    hours = parse_usage(_JOURNAL.splitlines()).hours
    summary = summarize(hours, since=date(2026, 9, 30))
    assert (summary.calls, summary.ok, summary.errors) == (11, 5, 1)
    assert summary.refused == {"daily": 1, "rate": 2, "service_capacity": 2}
    assert summary.by_tool == {"get_law": 9, "list_sections": 1, "semantic_search": 1}
    # Distinct callers are counted within an hour only; the report says the
    # peak rather than a sum that would count one caller many times.
    assert summary.peak_active_credentials == 3
    assert summary.worst_p95_ms == 500.0
    assert [usage.hour.hour for usage in summary.hours] == [13, 13, 14]


def test_summary_without_since_keeps_every_hour() -> None:
    hours = parse_usage(_JOURNAL.splitlines()).hours
    assert summarize(hours, since=None).calls == 16


def test_render_is_a_table_and_the_totals() -> None:
    parsed = parse_usage(_JOURNAL.splitlines())
    text = render_report(summarize(parsed.hours, since=date(2026, 9, 30)), parsed.malformed)
    assert "2026-09-30 13:00      8    4       1        3    30.0   500.0       3" in text
    assert "2026-09-30 14:00      1    0       0        1       -       -       1" in text
    assert "total: 11 calls, 5 ok, 1 errors, 5 refused" in text
    assert "refused by reason: daily 1, rate 2, service_capacity 2" in text
    assert "by tool: get_law 9, list_sections 1, semantic_search 1" in text
    assert "peak distinct credentials in one hour: 3" in text
    assert "worst hourly p95: 500.0 ms" in text
    assert "skipped 1 malformed lovspor.metrics line(s)" in text


def test_render_says_so_when_there_is_nothing() -> None:
    text = render_report(summarize([], since=None), malformed=0)
    assert text == "no lovspor.metrics hours to report"


def test_impl_reads_a_file_and_filters_by_the_injected_day(tmp_path: Path) -> None:
    journal = tmp_path / "journal.txt"
    journal.write_text(_JOURNAL, encoding="utf-8")
    text = usage_impl(journal, "today", lambda: datetime(2026, 9, 30, 10, 0, tzinfo=UTC))
    assert "total: 11 calls, 5 ok, 1 errors, 5 refused" in text


def test_the_command_reads_stdin_through_the_real_cli() -> None:
    result = CliRunner().invoke(app, ["ops", "usage", "--since", "2026-09-30"], input=_JOURNAL)
    assert result.exit_code == 0, result.output
    assert "total: 11 calls, 5 ok, 1 errors, 5 refused" in result.output


def test_the_command_refuses_a_bad_since_with_a_usage_error() -> None:
    result = CliRunner().invoke(app, ["ops", "usage", "--since", "last week"], input="")
    assert result.exit_code == 2
    assert "--since" in result.output
