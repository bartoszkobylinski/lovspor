"""Aggregate usage of the hosted MCP, for the operator only (issue #479).

The owner needs to know when the 1-vCPU droplet should be resized: calls per
hour, how many were refused and by which brake, tool latency, and how many
distinct callers were active. This module keeps those numbers per UTC hour and
writes each finished hour as one line to the service's journal::

    lovspor.metrics {"hour": "2026-09-30T13:00:00Z", "calls": 812, ...}

``lovspor ops usage`` (:mod:`lovspor.usage_report`) reads the lines back.

**Aggregate only, by construction.** The privacy page promises it, so the shape
here is what keeps the promise rather than a convention on top of it: a
:class:`ToolCall` carries no arguments, no query text and no address, and
:class:`HourlyUsage` has no field that could hold an identifier. The caller's
credential id is used for one thing — counting distinct callers — and is kept
only as a digest in a set that is dropped when the hour is emitted.

**Why a line on stderr, not the logging module.** FastMCP installs its own log
handler, which formats and wraps records for a terminal; a JSON payload wrapped
at a column is a payload the reader cannot parse. A single flushed ``print`` is
one journald record, whatever else configures logging in the process.
"""

from __future__ import annotations

import hashlib
import math
import sys
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

METRICS_TAG = "lovspor.metrics"

Outcome = Literal["ok", "error", "refused"]

# How often the ticker looks at the clock. Emission happens at most once per
# hour; this only bounds how late after the hour turns the line is written.
_TICK_SECONDS = 60.0


@dataclass(frozen=True)
class ToolCall:
    """One finished tool call, as much of it as the aggregate may know."""

    tool: str
    outcome: Outcome
    duration_ms: float
    credential_id: str | None
    refusal_reason: str | None = None


class HourlyUsage(BaseModel):
    """One UTC hour of hosted-MCP usage: the payload of one journal line."""

    model_config = ConfigDict(frozen=True)

    hour: datetime
    calls: int
    ok: int
    errors: int
    refused: dict[str, int]
    p50_ms: float | None
    p95_ms: float | None
    by_tool: dict[str, int]
    active_credentials: int


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _to_stderr(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _hour_of(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _percentile(samples: list[float], share: float) -> float | None:
    """Nearest-rank percentile: always a latency some call really had."""
    if not samples:
        return None
    ordered = sorted(samples)
    return round(ordered[max(1, math.ceil(share * len(ordered))) - 1], 1)


def format_line(usage: HourlyUsage) -> str:
    """The journal line for one hour: the tag, a space, the JSON payload."""
    return f"{METRICS_TAG} {usage.model_dump_json()}"


class _Hour:
    """One hour's counters. Lives only until the hour is emitted."""

    def __init__(self, hour: datetime) -> None:
        self.hour = hour
        self.outcomes: Counter[str] = Counter()
        self.refused: Counter[str] = Counter()
        self.by_tool: Counter[str] = Counter()
        self.durations: list[float] = []
        self.credentials: set[bytes] = set()

    def add(self, call: ToolCall) -> None:
        self.outcomes[call.outcome] += 1
        self.by_tool[call.tool] += 1
        if call.outcome == "refused":
            self.refused[call.refusal_reason or "unidentified"] += 1
        else:
            # A refusal returns before any work; timing it would pull the
            # percentiles toward zero exactly when the box is busiest.
            self.durations.append(call.duration_ms)
        if call.credential_id is not None:
            self.credentials.add(hashlib.sha256(call.credential_id.encode()).digest())

    def usage(self) -> HourlyUsage:
        return HourlyUsage(
            hour=self.hour,
            calls=self.outcomes.total(),
            ok=self.outcomes["ok"],
            errors=self.outcomes["error"],
            refused=dict(sorted(self.refused.items())),
            p50_ms=_percentile(self.durations, 0.50),
            p95_ms=_percentile(self.durations, 0.95),
            by_tool=dict(sorted(self.by_tool.items())),
            active_credentials=len(self.credentials),
        )


class UsageRecorder:
    """Counts tool calls per UTC hour and emits each finished hour once.

    In memory and per process, like the quota counters: a restart emits the
    open hour on the way down (:meth:`flush`) and starts a new one, so an hour
    can appear as two lines and the reader sums them. An hour with no calls
    emits nothing.
    """

    def __init__(
        self,
        emit: Callable[[str], None] = _to_stderr,
        utc_now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._emit = emit
        self._utc_now = utc_now
        self._open: _Hour | None = None
        # Calls finish on the event loop, the ticker runs on its own thread.
        self._lock = threading.Lock()

    def record(self, call: ToolCall) -> None:
        hour = _hour_of(self._utc_now())
        with self._lock:
            self._close_if_turned(hour)
            if self._open is None:
                self._open = _Hour(hour)
            self._open.add(call)

    def tick(self) -> None:
        """Emit the open hour if the clock has left it."""
        hour = _hour_of(self._utc_now())
        with self._lock:
            self._close_if_turned(hour)

    def flush(self) -> None:
        """Emit the open hour now, finished or not. For shutdown."""
        with self._lock:
            self._close()

    @contextmanager
    def hourly_ticker(self, interval_seconds: float = _TICK_SECONDS) -> Iterator[None]:
        """Tick on a daemon thread for the duration, then flush.

        Without it a quiet hour's line would wait for the next call, and the
        last hour before a lull would be invisible until traffic returned.
        """
        stop = threading.Event()
        ticker = threading.Thread(target=self._tick_until, args=(stop, interval_seconds))
        ticker.daemon = True
        ticker.start()
        try:
            yield
        finally:
            stop.set()
            ticker.join()
            self.flush()

    def _tick_until(self, stop: threading.Event, interval_seconds: float) -> None:
        while not stop.wait(interval_seconds):
            self.tick()

    def _close_if_turned(self, hour: datetime) -> None:
        if self._open is not None and self._open.hour != hour:
            self._close()

    def _close(self) -> None:
        closing, self._open = self._open, None
        if closing is not None:
            self._emit(format_line(closing.usage()))
