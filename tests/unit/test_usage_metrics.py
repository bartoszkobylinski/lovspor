"""The hourly usage aggregate for the hosted MCP (issue #479).

The privacy page promises aggregate counts only: these tests pin what one
emitted line holds, and that no credential identifier ever reaches it.
"""

from __future__ import annotations

import io
import json
import sys
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from lovspor.usage_metrics import METRICS_TAG, HourlyUsage, ToolCall, UsageRecorder, _utc_now


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


class _Flushes(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1
        super().flush()


class _StopAfter(threading.Event):
    """A stop event that reports ``waits`` timeouts, then that it was set."""

    def __init__(self, waits: int) -> None:
        super().__init__()
        self.waits = waits
        self.timeouts: list[float | None] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.timeouts.append(timeout)
        return len(self.timeouts) > self.waits


@pytest.fixture
def half_hour_local_zone(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_the_default_emitter_writes_stderr_and_flushes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # journald reads a pipe: an unflushed line sits in the buffer until the
    # next one, and a crash loses it with the hour it describes.
    stream = _Flushes()
    monkeypatch.setattr(sys, "stderr", stream)
    recorder = UsageRecorder(utc_now=_Clock())
    recorder.record(_ok())
    recorder.flush()
    assert stream.getvalue().startswith(f"{METRICS_TAG} ")
    assert stream.flushes >= 1


def test_the_default_clock_is_utc() -> None:
    assert _utc_now().tzinfo is UTC


def test_the_hour_drops_seconds_as_well_as_minutes() -> None:
    recorder, lines, clock = _recorder()
    clock.now = datetime(2026, 9, 30, 13, 5, 42, 7, tzinfo=UTC)
    recorder.record(_ok())
    recorder.flush()
    assert _payload(lines[0])["hour"] == "2026-09-30T13:00:00Z"


@pytest.mark.usefixtures("half_hour_local_zone")
def test_the_hour_is_a_utc_hour_whatever_the_box_zone() -> None:
    recorder, lines, _ = _recorder()
    recorder.record(_ok())
    recorder.flush()
    assert _payload(lines[0])["hour"] == "2026-09-30T13:00:00Z"


def test_latency_is_rounded_to_a_tenth_of_a_millisecond() -> None:
    recorder, lines, _ = _recorder()
    recorder.record(_ok(ms=12.34))
    recorder.flush()
    assert (_payload(lines[0])["p50_ms"], _payload(lines[0])["p95_ms"]) == (12.3, 12.3)


def test_refusals_without_a_reason_accumulate_as_unidentified() -> None:
    recorder, lines, _ = _recorder()
    recorder.record(ToolCall("get_law", "refused", 0.1, "beta-001"))
    recorder.record(ToolCall("get_law", "refused", 0.1, "beta-001"))
    recorder.flush()
    assert _payload(lines[0])["refused"] == {"unidentified": 2}


def test_the_ticker_loop_ticks_between_waits_until_stopped() -> None:
    recorder, lines, clock = _recorder()
    recorder.record(_ok())
    clock.now += timedelta(hours=1)
    stop = _StopAfter(waits=2)
    recorder._tick_until(stop, 7.5)
    assert stop.timeouts == [7.5, 7.5, 7.5]
    assert len(lines) == 1
