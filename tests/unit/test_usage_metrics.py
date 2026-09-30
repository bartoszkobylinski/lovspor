"""The hourly usage aggregate for the hosted MCP (issue #479).

The privacy page promises aggregate counts only: these tests pin what one
emitted line holds, and that no credential identifier ever reaches it.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta

from lovspor.usage_metrics import METRICS_TAG, HourlyUsage, ToolCall, UsageRecorder


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 30, 13, 5, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _recorder() -> tuple[UsageRecorder, list[str], _Clock]:
    lines: list[str] = []
    clock = _Clock()
    return UsageRecorder(emit=lines.append, utc_now=clock), lines, clock


def _ok(tool: str = "get_law", ms: float = 10.0, who: str | None = "beta-001") -> ToolCall:
    return ToolCall(tool=tool, outcome="ok", duration_ms=ms, credential_id=who)


def _payload(line: str) -> dict[str, object]:
    tag, _, body = line.partition(" ")
    assert tag == METRICS_TAG
    decoded = json.loads(body)
    assert isinstance(decoded, dict)
    return decoded


def test_nothing_is_emitted_within_the_hour() -> None:
    recorder, lines, clock = _recorder()
    recorder.record(_ok())
    clock.now += timedelta(minutes=50)
    recorder.record(_ok())
    recorder.tick()
    assert lines == []


def test_the_first_call_of_a_new_hour_emits_the_previous_one() -> None:
    recorder, lines, clock = _recorder()
    recorder.record(_ok())
    clock.now += timedelta(hours=1)
    recorder.record(_ok())
    assert len(lines) == 1
    assert _payload(lines[0])["hour"] == "2026-09-30T13:00:00Z"
    assert _payload(lines[0])["calls"] == 1


def test_tick_emits_a_finished_hour_without_waiting_for_traffic() -> None:
    recorder, lines, clock = _recorder()
    recorder.record(_ok())
    clock.now += timedelta(hours=1)
    recorder.tick()
    recorder.tick()
    assert len(lines) == 1


def test_flush_emits_the_open_hour_once_and_an_empty_one_never() -> None:
    recorder, lines, _ = _recorder()
    recorder.flush()
    recorder.record(_ok())
    recorder.flush()
    recorder.flush()
    assert len(lines) == 1


def test_one_line_holds_the_whole_aggregate() -> None:
    recorder, lines, _ = _recorder()
    for ms in (10.0, 20.0, 30.0, 40.0):
        recorder.record(_ok(ms=ms))
    recorder.record(ToolCall("semantic_search", "error", 500.0, "beta-002"))
    recorder.record(ToolCall("get_law", "refused", 0.1, "beta-003", "service_capacity"))
    recorder.record(ToolCall("get_law", "refused", 0.1, "beta-003", "rate"))
    recorder.record(ToolCall("get_law", "refused", 0.1, None, "unidentified"))
    recorder.flush()
    assert _payload(lines[0]) == {
        "hour": "2026-09-30T13:00:00Z",
        "calls": 8,
        "ok": 4,
        "errors": 1,
        "refused": {"rate": 1, "service_capacity": 1, "unidentified": 1},
        # Latency is over admitted calls only: a refusal returns before any
        # work, and counting it would pull the percentiles toward zero.
        "p50_ms": 30.0,
        "p95_ms": 500.0,
        "by_tool": {"get_law": 7, "semantic_search": 1},
        "active_credentials": 3,
    }


def test_an_hour_of_refusals_only_has_no_latency() -> None:
    recorder, lines, _ = _recorder()
    recorder.record(ToolCall("get_law", "refused", 0.1, "beta-001", "daily"))
    recorder.flush()
    payload = _payload(lines[0])
    assert payload["p50_ms"] is None
    assert payload["p95_ms"] is None


def test_distinct_credentials_are_counted_per_hour_and_never_emitted() -> None:
    recorder, lines, clock = _recorder()
    recorder.record(_ok(who="beta-001"))
    recorder.record(_ok(who="beta-001"))
    recorder.record(_ok(who="workos:user_01SECRET"))
    clock.now += timedelta(hours=1)
    recorder.record(_ok(who="beta-001"))
    recorder.flush()
    assert [_payload(line)["active_credentials"] for line in lines] == [2, 1]
    assert not any("beta-001" in line or "user_01SECRET" in line for line in lines)


def test_percentiles_are_nearest_rank() -> None:
    recorder, lines, _ = _recorder()
    for ms in range(1, 101):
        recorder.record(_ok(ms=float(ms)))
    recorder.flush()
    assert (_payload(lines[0])["p50_ms"], _payload(lines[0])["p95_ms"]) == (50.0, 95.0)


def test_an_emitted_line_parses_back_into_the_model() -> None:
    recorder, lines, _ = _recorder()
    recorder.record(_ok())
    recorder.flush()
    usage = HourlyUsage.model_validate_json(lines[0].partition(" ")[2])
    assert usage.hour == datetime(2026, 9, 30, 13, tzinfo=UTC)
    assert usage.by_tool == {"get_law": 1}


def test_the_hourly_ticker_emits_a_turned_hour_and_flushes_on_exit() -> None:
    recorder, lines, clock = _recorder()
    emitted = threading.Event()

    def emit(line: str) -> None:
        lines.append(line)
        emitted.set()

    recorder = UsageRecorder(emit=emit, utc_now=clock)
    with recorder.hourly_ticker(interval_seconds=0.01):
        recorder.record(_ok())
        clock.now += timedelta(hours=1)
        assert emitted.wait(timeout=5)
        recorder.record(_ok(tool="list_sections"))
    assert [_payload(line)["by_tool"] for line in lines] == [
        {"get_law": 1},
        {"list_sections": 1},
    ]
