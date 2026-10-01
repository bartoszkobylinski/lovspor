"""The usage-counting tool wrapper (issue #479)."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from lovspor.errors import LovsporError
from lovspor.hosted_calls import record_usage
from lovspor.quota import DailyQuotaError, QuotaExceededError
from lovspor.usage_metrics import ToolCall, UsageRecorder


class _Recorder(UsageRecorder):
    """The real recorder, keeping what it was handed for the assertions."""

    def __init__(self) -> None:
        super().__init__(emit=lambda _line: None, utc_now=lambda: datetime(2026, 9, 30, tzinfo=UTC))
        self.calls: list[ToolCall] = []

    def record(self, call: ToolCall) -> None:
        self.calls.append(call)
        super().record(call)


class _Ticks:
    def __init__(self, *readings: float) -> None:
        self._readings = list(readings)

    def __call__(self) -> float:
        return self._readings.pop(0)


def _run(tool: Callable[..., Awaitable[object]], caller: str | None, **kwargs: object) -> object:
    async def run() -> object:
        if caller is None:
            return await tool(**kwargs)
        user = AuthenticatedUser(AccessToken(token="t", client_id=caller, scopes=[]))
        reset = auth_context_var.set(user)
        try:
            return await tool(**kwargs)
        finally:
            auth_context_var.reset(reset)

    return asyncio.run(run())


async def get_law(slug: str) -> str:
    """Return a law."""
    if slug == "missing":
        raise LovsporError("no such law")
    if slug == "over":
        raise DailyQuotaError("daily quota of 1 calls is exhausted", 60)
    return f"law:{slug}"


def test_an_admitted_call_is_counted_ok_with_its_duration_and_caller() -> None:
    usage = _Recorder()
    tool = record_usage(get_law, usage, _Ticks(10.0, 10.25))
    assert _run(tool, "beta-001", slug="skatteloven") == "law:skatteloven"
    assert usage.calls == [ToolCall("get_law", "ok", 250.0, "beta-001")]


def test_a_failing_call_is_counted_as_an_error_and_still_raises() -> None:
    usage = _Recorder()
    tool = record_usage(get_law, usage, _Ticks(1.0, 1.5))
    with pytest.raises(LovsporError, match="no such law"):
        _run(tool, "beta-001", slug="missing")
    assert usage.calls == [ToolCall("get_law", "error", 500.0, "beta-001")]


def test_a_refusal_is_counted_with_the_brake_that_fired() -> None:
    usage = _Recorder()
    tool = record_usage(get_law, usage, _Ticks(0.0, 0.001))
    with pytest.raises(QuotaExceededError):
        _run(tool, "beta-001", slug="over")
    assert usage.calls == [ToolCall("get_law", "refused", 1.0, "beta-001", "daily")]


def test_an_unauthenticated_call_has_no_caller() -> None:
    usage = _Recorder()
    tool = record_usage(get_law, usage, _Ticks(0.0, 0.0))
    _run(tool, None, slug="x")
    assert usage.calls == [ToolCall("get_law", "ok", 0.0, None)]


def test_the_wrapper_keeps_the_tool_name_and_signature() -> None:
    tool = record_usage(get_law, _Recorder())
    assert tool.__name__ == "get_law"
    assert tool.__doc__ == "Return a law."
    assert inspect.signature(tool) == inspect.signature(get_law)
