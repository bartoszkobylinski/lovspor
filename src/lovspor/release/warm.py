"""The release probe waits out the MCP's warm window (#584).

``systemctl restart lovspor-mcp`` leaves the process unbound for about
thirty seconds while it warms its indices: loopback refuses and Caddy
answers ``502``. A release built in that window published
``hosted_state: unavailable`` until the next release (seen 2026-10-07,
16:34Z). The probe observes one instant and never retries (ADR-0014
Decision 4); which instant a release records is the release's choice.
While the observation says the subject did not answer at all, the release
observes again, for a bounded time, and records the last observation
whatever it says — an answer of the subject's own (a ``503`` from
``/readyz``, a ``401``) is recorded at once.
"""

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

from lovspor.site.capabilities import CapabilityDocument, Checkout, Observation

WARM_WINDOW_SECONDS = 90.0
RETRY_INTERVAL_SECONDS = 5.0

_NO_ANSWER = frozenset({"network", "timeout"})
_NO_UPSTREAM = frozenset({502, 503, 504})

Observe = Callable[[Checkout], CapabilityDocument]


def _stderr(line: str) -> None:
    print(line, file=sys.stderr)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


@dataclass(frozen=True)
class WarmWindow:
    """How long a release waits for the MCP to answer, and the boundaries it waits by."""

    budget_seconds: float = WARM_WINDOW_SECONDS
    interval_seconds: float = RETRY_INTERVAL_SECONDS
    sleep: Callable[[float], None] = _sleep
    clock: Callable[[], float] = time.monotonic
    say: Callable[[str], None] = _stderr


def in_warm_window(observation: Observation) -> bool:
    """Nothing answered: loopback refused or timed out, or Caddy had no upstream."""
    process, transport = observation.process, observation.transport
    if process.status == "unobserved" and process.reason in _NO_ANSWER:
        return True
    if transport.status == "unobserved":
        return True
    step = transport.unauthenticated
    return step is not None and step.status_code in _NO_UPSTREAM


def settled(observe: Observe, checkout: Checkout, window: WarmWindow) -> CapabilityDocument:
    """Observe until something answers or the budget would be exceeded; the last one counts."""
    deadline = window.clock() + window.budget_seconds
    document = observe(checkout)
    while in_warm_window(document.observation):
        if window.clock() + window.interval_seconds > deadline:
            break
        window.say(
            f"release probe: hosted MCP not answering (warm window?); "
            f"observing again in {window.interval_seconds:g} s"
        )
        window.sleep(window.interval_seconds)
        document = observe(checkout)
    return document


def warm_observer(observe: Observe, window: WarmWindow | None = None) -> Observe:
    """``observe``, waiting out the warm window before its observation is recorded."""
    chosen = window or WarmWindow()
    return lambda checkout: settled(observe, checkout, chosen)
