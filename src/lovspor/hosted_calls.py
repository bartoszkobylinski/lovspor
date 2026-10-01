"""Tool-call wrappers for the hosted (Streamable HTTP) MCP server.

:func:`offload_to_thread` moved here from :mod:`lovspor.mcp`, which sits at its
file-size ratchet; :mod:`lovspor.mcp` still re-exports it as
``_offload_to_thread``. :func:`record_usage` counts each call for the operator's
usage metrics (issue #479, :mod:`lovspor.usage_metrics`).
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token

from lovspor.quota import QuotaExceededError
from lovspor.usage_metrics import Outcome, ToolCall, UsageRecorder


def offload_to_thread(fn: Callable[..., Any]) -> Callable[..., Awaitable[Any]]:
    """Wrap a synchronous tool body as an async tool run on a worker thread.

    mcp 1.27.0 calls a synchronous tool handler inline on the single event-
    loop thread, so one blocking call — a cold-cache ``search_body``, a
    ``semantic_search`` embedding round-trip, a ``git`` subprocess — would
    stall every other client on an HTTP server. Offloading to a thread lets
    the loop serve other requests while the body runs. ``functools.wraps``
    preserves ``__wrapped__`` so FastMCP still derives the tool's argument
    schema from the original signature; the wrapper is ``async`` so FastMCP
    awaits it instead of calling it inline.
    """

    @functools.wraps(fn)
    async def wrapper(**kwargs: Any) -> Any:
        return await asyncio.to_thread(fn, **kwargs)

    return wrapper


def _caller_id() -> str | None:
    token = get_access_token()
    return None if token is None else token.client_id


def record_usage(
    fn: Callable[..., Awaitable[Any]],
    usage: UsageRecorder,
    monotonic: Callable[[], float] = time.monotonic,
) -> Callable[..., Awaitable[Any]]:
    """Count every call to ``fn`` — admitted, failed or refused — in ``usage``.

    Wrap it outermost, around the quota guard, or a refused call is never seen:
    the refusals by brake are what tell the operator the box is too small. The
    caller's id is handed to the recorder only to count distinct callers; the
    call's arguments never leave this function.
    """
    tool = fn.__name__

    @functools.wraps(fn)
    async def wrapper(**kwargs: Any) -> Any:
        started = monotonic()
        outcome: Outcome = "error"
        reason: str | None = None
        try:
            result = await fn(**kwargs)
        except QuotaExceededError as refusal:
            outcome, reason = "refused", refusal.reason
            raise
        else:
            outcome = "ok"
            return result
        finally:
            elapsed_ms = (monotonic() - started) * 1000
            usage.record(ToolCall(tool, outcome, elapsed_ms, _caller_id(), reason))

    return wrapper
