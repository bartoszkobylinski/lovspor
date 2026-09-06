"""Per-credential quota brakes for the hosted MCP surface.

Enforces the four :class:`~lovspor.access.Limits` a credential carries. The
brakes exist to stop one client taking the box down, not to price anything —
see the ``Limits`` docstring for what each number is supposed to mean.

**Where this runs, and why it is not ASGI middleware.** mcp 1.28.1's FastMCP
has no post-auth/pre-tool hook (the standalone ``fastmcp`` package's middleware
API is a different library). The seam we use instead is the tool wrapper
lovspor already installs for :func:`~lovspor.mcp._offload_to_thread`: probed on
1.28.1, ``get_access_token()`` resolves inside a tool body, because
``AuthContextMiddleware`` sets a contextvar that the session task inherits and
``asyncio.to_thread`` copies into the worker. That seam knows the caller *and*
the tool, which ASGI middleware would only learn by parsing the JSON-RPC body.

The cost of that choice: a tool body cannot set an HTTP status, so an exhausted
limit surfaces as an in-band MCP tool error rather than a 429 with
``Retry-After``. The retry hint travels in the message text instead. This is the
better trade anyway — ``max_in_flight`` guards the shared ``to_thread`` pool, so
it *has* to sit around the thread hop, where no HTTP response exists any more.

**Guards run before the thread hop.** :meth:`QuotaEnforcer.guard` is a plain
synchronous context manager wrapped around the ``await``, so a throttled call is
refused on the event loop without ever occupying a worker thread — which is the
whole point of ``max_in_flight``. It is sync by construction: there is no await
between reading a counter and writing it, so the critical section cannot be
interleaved and needs no lock against the loop.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from lovspor.access import Limits, ServiceLimits
from lovspor.errors import LovsporError


class LimitsSource(Protocol):
    """The quota layer's view of the token store: resolve live limits by id.

    :class:`~lovspor.access.CredentialStore` and the composite WorkOS verifier
    both satisfy this structurally — the enforcer only needs a caller's limits by
    identity, not to know which auth path (opaque token vs WorkOS JWT) issued it.
    """

    def limits_for(self, credential_id: str) -> Limits | None: ...


# An in-flight rejection cannot say when a slot frees — that depends on a call
# still running. One second is a hint, not a promise.
_IN_FLIGHT_RETRY_SECONDS = 1

# Sweep only once the map is big enough to be worth walking: below this the
# hot path should not pay for a scan that would free a few hundred bytes.
_EVICTION_THRESHOLD = 512

# How long a counter set must be untouched before it is a candidate. Well past
# any client's think-time, so an idle-but-returning user keeps their bucket.
_EVICTION_IDLE_SECONDS = 3600.0


class QuotaExceededError(LovsporError):
    """A credential hit one of its limits.

    ``retry_after_seconds`` is the ``Retry-After`` we cannot send as a header
    (see the module docstring); callers put it in the message the client reads.
    """

    def __init__(self, message: str, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _seconds_to_utc_midnight(now: datetime) -> int:
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    # Ceil, and never below 1: int() truncation returns 0 in the last second
    # before midnight, and a Retry-After of 0 sends the client straight back
    # into a quota that has not reset yet — a hot rejection loop. Round up so the
    # hint always points past the reset, matching the rate path's max(1, ...).
    return max(1, math.ceil((midnight - now).total_seconds()))


class _Bucket:
    """Token bucket over ``rate_per_minute`` / ``rate_burst``.

    Limits arrive per call rather than at construction because they are re-read
    from the store on every guard (see :class:`QuotaEnforcer`), so capacity and
    refill rate can change under a live bucket.
    """

    def __init__(self, monotonic: Callable[[], float]) -> None:
        self._monotonic = monotonic
        self._tokens: float | None = None
        self._last = monotonic()

    def _refilled(self, limits: Limits) -> float:
        """Tokens available now, advancing the clock."""
        now = self._monotonic()
        if self._tokens is None:
            tokens = float(limits.rate_burst)  # a fresh credential starts full
        else:
            earned = (now - self._last) * limits.rate_per_minute / 60.0
            # Clamp to burst: an idle hour must not bank an hour of calls.
            tokens = min(float(limits.rate_burst), self._tokens + earned)
        self._last = now
        return tokens

    def try_consume(self, limits: Limits) -> float | None:
        """Spend one token. ``None`` on success, else seconds until one exists."""
        tokens = self._refilled(limits)
        if tokens >= 1.0:
            self._tokens = tokens - 1.0
            return None
        self._tokens = tokens
        return (1.0 - tokens) / (limits.rate_per_minute / 60.0)


class _Daily:
    """Calls used today, UTC. In memory: a restart forgives the day, which the
    ``Limits.daily_quota`` docstring already declares as approximate."""

    def __init__(self, utc_now: Callable[[], datetime]) -> None:
        self._utc_now = utc_now
        self._day: date | None = None
        self.used = 0

    def roll(self) -> None:
        today = self._utc_now().date()
        if today != self._day:
            self._day = today
            self.used = 0


class _State:
    """One credential's live counters."""

    def __init__(self, monotonic: Callable[[], float], utc_now: Callable[[], datetime]) -> None:
        self.in_flight = 0
        self.bucket = _Bucket(monotonic)
        self.daily = _Daily(utc_now)
        # Only used to decide eviction; never to admit or refuse a call.
        self.last_seen = monotonic()


class _ServiceState:
    """The instance's own counters: a daily total and a live in-flight count.

    No token bucket. A server-wide rate limit would refuse a caller because of
    *other* callers' timing, which is what ``max_in_flight`` already expresses
    honestly — the pool is the scarce thing, not the minute.
    """

    def __init__(self, utc_now: Callable[[], datetime]) -> None:
        self.in_flight = 0
        self.daily = _Daily(utc_now)


class QuotaEnforcer:
    """Admits or refuses one tool call per credential.

    Limits are re-read from ``store`` on every guard rather than taken from the
    caller's ``LovsporAccessToken.limits``. That is not a style preference: on
    mcp 1.28.1 the token a tool body sees is **pinned to the request that opened
    the session** (probed), and lovspor sets no ``session_idle_timeout``, so a
    session lives as long as its client. Trusting the token's copy would mean an
    operator who tightens a quota is silently ignored for hours — the same shape
    of unapplied-intent failure the credential store's live reload exists to
    prevent. A store lookup is a stat plus a dict hit; ``verify_token`` already
    pays it on every request.

    Counters are per-process and in memory — a restart forgives the day (the
    ``Limits`` docstring says as much). The hosted server runs single-process
    today; if it is ever run with N workers, each keeps its own counters and a
    credential's effective quota and in-flight allowance multiply by N. A
    multi-worker deploy would need shared counters (e.g. Redis), not just this.
    """

    def __init__(
        self,
        store: LimitsSource,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = _utc_now,
        service_limits: ServiceLimits | None = None,
        eviction_threshold: int = _EVICTION_THRESHOLD,
        eviction_idle_seconds: float = _EVICTION_IDLE_SECONDS,
    ) -> None:
        self._store = store
        self._monotonic = monotonic
        self._utc_now = utc_now
        self._states: dict[str, _State] = {}
        # None keeps the pre-self-service behaviour: per-credential brakes only,
        # which is correct when the operator controls how many credentials exist.
        self._service_limits = service_limits
        self._service = _ServiceState(utc_now)
        self._eviction_threshold = eviction_threshold
        self._eviction_idle_seconds = eviction_idle_seconds
        # Guards only _states' shape. Tool calls enter from the event loop
        # thread, but stdio and tests call in directly, and dict insertion
        # racing a read is cheap to rule out.
        self._lock = threading.Lock()

    def daily_used(self, credential_id: str) -> int:
        """Calls billed to ``credential_id`` today. For tests and diagnostics."""
        with self._lock:
            state = self._states.get(credential_id)
        if state is None:
            return 0
        # Roll first: without it this reports yesterday's total across the UTC
        # midnight boundary until the next guard() happens to roll the counter.
        # Enforcement itself always rolls before admitting, so only this readout
        # was stale — but a diagnostic that lies about the day is worse than one.
        state.daily.roll()
        return state.daily.used

    def service_daily_used(self) -> int:
        """Calls billed to the instance today. For tests and diagnostics."""
        self._service.daily.roll()
        return self._service.daily.used

    def tracked_credentials(self) -> int:
        """How many credentials currently hold counters. For tests and diagnostics."""
        with self._lock:
            return len(self._states)

    def _evict_idle(self) -> None:
        """Drop counter sets that can be forgotten without forgiving anything.

        A state is only removed when it is idle, holds no in-flight slot, and has
        spent nothing *today* — ``roll()`` first, so a state whose day has turned
        qualifies on its own. Evicting a state that has spent part of today's
        quota would hand that quota back, which under self-service sign-up is a
        bypass anyone could drive by pausing for the idle window.

        Called with the lock held.
        """
        now = self._monotonic()
        for credential_id, state in list(self._states.items()):
            if state.in_flight:
                continue
            if now - state.last_seen < self._eviction_idle_seconds:
                continue
            state.daily.roll()
            if state.daily.used == 0:
                del self._states[credential_id]

    def _state_for(self, credential_id: str) -> _State:
        with self._lock:
            state = self._states.get(credential_id)
            if state is None:
                if len(self._states) >= self._eviction_threshold:
                    self._evict_idle()
                state = _State(self._monotonic, self._utc_now)
                self._states[credential_id] = state
            state.last_seen = self._monotonic()
            return state

    def _admit_service(self) -> None:
        """Check the instance ceiling. Charges nothing; raises if it is reached."""
        ceiling = self._service_limits
        if ceiling is None:
            return
        self._service.daily.roll()
        if self._service.daily.used >= ceiling.daily_quota:
            raise QuotaExceededError(
                f"this server has reached its daily ceiling of {ceiling.daily_quota} "
                "calls across all users",
                _seconds_to_utc_midnight(self._utc_now()),
            )
        if self._service.in_flight >= ceiling.max_in_flight:
            raise QuotaExceededError(
                f"this server is at capacity ({ceiling.max_in_flight} calls in flight "
                "across all users)",
                _IN_FLIGHT_RETRY_SECONDS,
            )

    def _admit(self, state: _State, limits: Limits) -> None:
        """Charge one call against every brake, or raise having charged none."""
        state.daily.roll()
        if state.daily.used >= limits.daily_quota:
            raise QuotaExceededError(
                f"daily quota of {limits.daily_quota} calls is exhausted",
                _seconds_to_utc_midnight(self._utc_now()),
            )
        if state.in_flight >= limits.max_in_flight:
            raise QuotaExceededError(
                f"{limits.max_in_flight} calls already in flight for this credential",
                _IN_FLIGHT_RETRY_SECONDS,
            )
        # The caller's own brakes are checked first so a user over their own
        # limit is told that, rather than blaming a busy server for their loop.
        self._admit_service()
        # Consume last: the bucket mutates, and a call refused above must not
        # spend a token it never got to use.
        wait = state.bucket.try_consume(limits)
        if wait is not None:
            raise QuotaExceededError(
                f"rate limit of {limits.rate_per_minute}/min exceeded",
                max(1, math.ceil(wait)),
            )
        state.daily.used += 1
        state.in_flight += 1
        if self._service_limits is not None:
            self._service.daily.used += 1
            self._service.in_flight += 1

    @contextmanager
    def guard(self, credential_id: str) -> Iterator[None]:
        """Admit one call for ``credential_id``, or raise :class:`QuotaExceededError`.

        Wrap the tool body: the in-flight slot is held for the duration and
        released even if the body raises, so a credential that hits N errors is
        not locked out until restart.
        """
        limits = self._store.limits_for(credential_id)
        if limits is None:
            # Authenticated moments ago, gone from the store now (revoked or
            # deleted mid-call). No limits to enforce means no basis to admit.
            raise QuotaExceededError(
                f"unknown credential {credential_id}",
                _IN_FLIGHT_RETRY_SECONDS,
            )
        state = self._state_for(credential_id)
        self._admit(state, limits)
        try:
            yield
        finally:
            state.in_flight -= 1
            if self._service_limits is not None:
                self._service.in_flight -= 1
