"""Unit tests for the per-credential quota brakes."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.access import (
    Credential,
    CredentialStore,
    Limits,
    ServiceLimits,
    generate_token,
    hash_token,
    write_credential_file,
)
from lovspor.quota import QuotaEnforcer, QuotaExceededError


class _Clock:
    """Hand-cranked clocks so the brakes are tested on exact time, not sleeps."""

    def __init__(self) -> None:
        self.mono = 1000.0
        self.now = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.mono

    def utc_now(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.mono += seconds


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


def _write(path: Path, limits: Limits, credential_id: str = "beta-001") -> None:
    write_credential_file(
        path,
        [
            Credential(
                credential_id=credential_id,
                label="test",
                token_sha256=hash_token(generate_token()),
                limits=limits,
            )
        ],
    )


def _force_reload(path: Path) -> None:
    bumped = path.stat().st_mtime_ns + 1_000_000_000
    os.utime(path, ns=(bumped, bumped))


def _enforcer(tmp_path: Path, limits: Limits, clock: _Clock) -> QuotaEnforcer:
    path = tmp_path / "credentials.json"
    _write(path, limits)
    return QuotaEnforcer(CredentialStore(path), clock.monotonic, clock.utc_now)


def _refused(enforcer: QuotaEnforcer, credential_id: str = "beta-001") -> QuotaExceededError:
    """Attempt one call and return the refusal, failing if it was admitted."""
    with pytest.raises(QuotaExceededError) as excinfo, enforcer.guard(credential_id):
        pass
    return excinfo.value


def test_admits_a_call_within_every_limit(tmp_path: Path, clock: _Clock) -> None:
    enforcer = _enforcer(tmp_path, Limits(), clock)
    with enforcer.guard("beta-001"):
        pass  # no raise


def test_rejects_beyond_max_in_flight(tmp_path: Path, clock: _Clock) -> None:
    enforcer = _enforcer(tmp_path, Limits(max_in_flight=2), clock)

    with enforcer.guard("beta-001"), enforcer.guard("beta-001"):
        error = _refused(enforcer)
    assert str(error) == "2 calls already in flight for this credential"
    assert error.retry_after_seconds == 1


def test_in_flight_slot_frees_on_exit(tmp_path: Path, clock: _Clock) -> None:
    enforcer = _enforcer(tmp_path, Limits(max_in_flight=1), clock)
    with enforcer.guard("beta-001"):
        pass
    with enforcer.guard("beta-001"):
        assert "in flight" in str(_refused(enforcer))


def test_in_flight_slot_frees_when_the_tool_body_raises(tmp_path: Path, clock: _Clock) -> None:
    """A failing tool must not leak its in-flight slot, or a credential that
    hits N errors is locked out until restart."""
    enforcer = _enforcer(tmp_path, Limits(max_in_flight=1), clock)
    with pytest.raises(ValueError, match="boom"), enforcer.guard("beta-001"):
        raise ValueError("boom")
    with enforcer.guard("beta-001"):
        pass


def test_burst_is_allowed_then_the_rate_brake_bites(tmp_path: Path, clock: _Clock) -> None:
    limits = Limits(rate_burst=3, rate_per_minute=60, max_in_flight=100)
    enforcer = _enforcer(tmp_path, limits, clock)

    for _ in range(3):
        with enforcer.guard("beta-001"):
            pass
    assert str(_refused(enforcer)) == "rate limit of 60/min exceeded"


def test_bucket_refills_over_time(tmp_path: Path, clock: _Clock) -> None:
    limits = Limits(rate_burst=1, rate_per_minute=60, max_in_flight=100)
    enforcer = _enforcer(tmp_path, limits, clock)

    with enforcer.guard("beta-001"):
        pass
    with pytest.raises(QuotaExceededError), enforcer.guard("beta-001"):
        pass

    clock.advance(1.0)  # 60/min == one token per second
    with enforcer.guard("beta-001"):
        pass


def test_fractional_refill_reports_exact_wait_then_admits_at_boundary(
    tmp_path: Path,
    clock: _Clock,
) -> None:
    limits = Limits(
        daily_quota=10,
        max_in_flight=10,
        rate_burst=1,
        rate_per_minute=30,
    )
    enforcer = _enforcer(tmp_path, limits, clock)

    with enforcer.guard("beta-001"):
        pass
    clock.advance(1.0)

    error = _refused(enforcer)

    assert error.retry_after_seconds == 1
    assert enforcer.daily_used("beta-001") == 1

    clock.advance(1.0)
    with enforcer.guard("beta-001"):
        pass
    assert enforcer.daily_used("beta-001") == 2


def test_rate_retry_reports_full_time_until_next_token(
    tmp_path: Path,
    clock: _Clock,
) -> None:
    limits = Limits(max_in_flight=10, rate_burst=1, rate_per_minute=6)
    enforcer = _enforcer(tmp_path, limits, clock)

    with enforcer.guard("beta-001"):
        pass

    assert _refused(enforcer).retry_after_seconds == 10


def test_repeated_rate_refusals_cannot_reopen_a_fresh_bucket(
    tmp_path: Path,
    clock: _Clock,
) -> None:
    limits = Limits(max_in_flight=10, rate_burst=1, rate_per_minute=60)
    enforcer = _enforcer(tmp_path, limits, clock)

    with enforcer.guard("beta-001"):
        pass

    assert "rate limit" in str(_refused(enforcer))
    assert "rate limit" in str(_refused(enforcer))


def test_bucket_does_not_refill_past_burst(tmp_path: Path, clock: _Clock) -> None:
    limits = Limits(rate_burst=2, rate_per_minute=60, max_in_flight=100)
    enforcer = _enforcer(tmp_path, limits, clock)

    clock.advance(3600)  # an idle hour must not bank an hour of calls
    for _ in range(2):
        with enforcer.guard("beta-001"):
            pass
    with pytest.raises(QuotaExceededError), enforcer.guard("beta-001"):
        pass


def test_lowered_burst_clamps_tokens_already_banked(
    tmp_path: Path,
    clock: _Clock,
) -> None:
    path = tmp_path / "credentials.json"
    _write(
        path,
        Limits(max_in_flight=10, rate_burst=5, rate_per_minute=60),
    )
    enforcer = QuotaEnforcer(CredentialStore(path), clock.monotonic, clock.utc_now)

    with enforcer.guard("beta-001"):
        pass
    clock.advance(60.0)
    _write(
        path,
        Limits(max_in_flight=10, rate_burst=1, rate_per_minute=60),
    )
    _force_reload(path)

    with enforcer.guard("beta-001"):
        pass
    assert "rate limit" in str(_refused(enforcer))


def test_daily_quota_is_exhaustible(tmp_path: Path, clock: _Clock) -> None:
    limits = Limits(daily_quota=2, max_in_flight=100, rate_burst=100)
    enforcer = _enforcer(tmp_path, limits, clock)

    for _ in range(2):
        with enforcer.guard("beta-001"):
            pass
    assert str(_refused(enforcer)) == "daily quota of 2 calls is exhausted"


def test_daily_quota_resets_on_the_utc_day_boundary(tmp_path: Path, clock: _Clock) -> None:
    limits = Limits(daily_quota=1, max_in_flight=100, rate_burst=100)
    enforcer = _enforcer(tmp_path, limits, clock)

    with enforcer.guard("beta-001"):
        pass
    with pytest.raises(QuotaExceededError), enforcer.guard("beta-001"):
        pass

    clock.now = datetime(2026, 7, 18, 0, 0, 1, tzinfo=UTC)
    with enforcer.guard("beta-001"):
        pass


def test_daily_quota_rolls_at_exact_utc_midnight(
    tmp_path: Path,
    clock: _Clock,
) -> None:
    clock.now = datetime(2026, 7, 17, 23, 59, 59, tzinfo=UTC)
    limits = Limits(daily_quota=1, max_in_flight=10, rate_burst=10)
    enforcer = _enforcer(tmp_path, limits, clock)

    with enforcer.guard("beta-001"):
        pass
    assert _refused(enforcer).retry_after_seconds == 1

    clock.now = datetime(2026, 7, 18, 0, 0, 0, tzinfo=UTC)
    with enforcer.guard("beta-001"):
        pass
    assert enforcer.daily_used("beta-001") == 1


def test_daily_refusal_does_not_spend_a_rate_token(
    tmp_path: Path,
    clock: _Clock,
) -> None:
    limits = Limits(
        daily_quota=1,
        max_in_flight=10,
        rate_burst=2,
        rate_per_minute=1,
    )
    enforcer = _enforcer(tmp_path, limits, clock)

    with enforcer.guard("beta-001"):
        pass
    assert "daily quota" in str(_refused(enforcer))

    clock.now = datetime(2026, 7, 18, 0, 0, 0, tzinfo=UTC)
    with enforcer.guard("beta-001"):
        pass


def test_rejected_call_does_not_spend_the_daily_quota(tmp_path: Path, clock: _Clock) -> None:
    """A call refused by one brake must not bill against another, or a client
    stuck in a retry loop burns its whole day on rejections."""
    limits = Limits(daily_quota=10, max_in_flight=1, rate_burst=100)
    enforcer = _enforcer(tmp_path, limits, clock)

    with enforcer.guard("beta-001"):
        for _ in range(5):
            with pytest.raises(QuotaExceededError), enforcer.guard("beta-001"):
                pass
    assert enforcer.daily_used("beta-001") == 1

    with enforcer.guard("beta-001"):
        pass
    assert enforcer.daily_used("beta-001") == 2


def test_credentials_do_not_share_state(tmp_path: Path, clock: _Clock) -> None:
    path = tmp_path / "credentials.json"
    write_credential_file(
        path,
        [
            Credential(
                credential_id=cid,
                label=cid,
                token_sha256=hash_token(generate_token()),
                limits=Limits(max_in_flight=1),
            )
            for cid in ("beta-001", "beta-002")
        ],
    )
    enforcer = QuotaEnforcer(CredentialStore(path), clock.monotonic, clock.utc_now)

    with enforcer.guard("beta-001"), enforcer.guard("beta-002"):
        pass  # beta-001 saturated must not touch beta-002


def test_exhausted_daily_and_rate_counters_are_isolated_per_credential(
    tmp_path: Path,
    clock: _Clock,
) -> None:
    path = tmp_path / "credentials.json"
    limits = Limits(
        daily_quota=1,
        max_in_flight=10,
        rate_burst=1,
        rate_per_minute=1,
    )
    write_credential_file(
        path,
        [
            Credential(
                credential_id=credential_id,
                label=credential_id,
                token_sha256=hash_token(generate_token()),
                limits=limits,
            )
            for credential_id in ("beta-001", "beta-002")
        ],
    )
    enforcer = QuotaEnforcer(CredentialStore(path), clock.monotonic, clock.utc_now)

    with enforcer.guard("beta-001"):
        pass
    assert "daily quota" in str(_refused(enforcer, "beta-001"))

    with enforcer.guard("beta-002"):
        pass
    assert enforcer.daily_used("beta-001") == 1
    assert enforcer.daily_used("beta-002") == 1


def test_limits_are_read_fresh_from_the_store(tmp_path: Path, clock: _Clock) -> None:
    """The token handed to a tool body carries limits PINNED to the session-
    creating request (probed on mcp 1.28.1), so the enforcer must re-read the
    store instead. Otherwise tightening a quota is silently ignored for the
    life of an open session.
    """
    path = tmp_path / "credentials.json"
    token_hash = hash_token(generate_token())

    def write(daily_quota: int) -> None:
        write_credential_file(
            path,
            [
                Credential(
                    credential_id="beta-001",
                    label="test",
                    token_sha256=token_hash,
                    limits=Limits(daily_quota=daily_quota, max_in_flight=100, rate_burst=100),
                )
            ],
        )

    write(daily_quota=5)
    enforcer = QuotaEnforcer(CredentialStore(path), clock.monotonic, clock.utc_now)
    with enforcer.guard("beta-001"):
        pass

    write(daily_quota=1)  # operator tightens the quota mid-flight
    with pytest.raises(QuotaExceededError, match="daily"), enforcer.guard("beta-001"):
        pass


def test_unknown_credential_fails_closed(tmp_path: Path, clock: _Clock) -> None:
    """A credential deleted from the store between auth and the tool body has
    no limits to enforce; admitting it unlimited would invert the brake."""
    enforcer = _enforcer(tmp_path, Limits(), clock)
    error = _refused(enforcer, "beta-nonexistent")

    assert str(error) == "unknown credential beta-nonexistent"
    assert error.retry_after_seconds == 1
    assert enforcer.daily_used("beta-nonexistent") == 0


def test_revoked_credential_fails_closed_without_billing_a_call(
    tmp_path: Path,
    clock: _Clock,
) -> None:
    path = tmp_path / "credentials.json"
    token_hash = hash_token(generate_token())
    write_credential_file(
        path,
        [
            Credential(
                credential_id="beta-001",
                label="tester",
                token_sha256=token_hash,
                limits=Limits(rate_burst=10),
            )
        ],
    )
    enforcer = QuotaEnforcer(CredentialStore(path), clock.monotonic, clock.utc_now)
    with enforcer.guard("beta-001"):
        pass

    write_credential_file(
        path,
        [
            Credential(
                credential_id="beta-001",
                label="tester",
                token_sha256=token_hash,
                revoked=True,
                limits=Limits(rate_burst=10),
            )
        ],
    )
    _force_reload(path)

    assert "unknown credential" in str(_refused(enforcer))
    assert enforcer.daily_used("beta-001") == 1


def test_broken_store_fails_closed_without_billing_a_call(
    tmp_path: Path,
    clock: _Clock,
) -> None:
    path = tmp_path / "credentials.json"
    _write(path, Limits(rate_burst=10))
    enforcer = QuotaEnforcer(CredentialStore(path), clock.monotonic, clock.utc_now)
    with enforcer.guard("beta-001"):
        pass

    path.write_text("{broken", encoding="utf-8")
    _force_reload(path)

    assert "unknown credential" in str(_refused(enforcer))
    assert enforcer.daily_used("beta-001") == 1


def test_daily_retry_after_points_past_utc_midnight(tmp_path: Path, clock: _Clock) -> None:
    limits = Limits(daily_quota=1, max_in_flight=100, rate_burst=100)
    enforcer = _enforcer(tmp_path, limits, clock)

    with enforcer.guard("beta-001"):
        pass
    with pytest.raises(QuotaExceededError) as excinfo, enforcer.guard("beta-001"):
        pass
    # 12:00:00 UTC -> midnight is 12h out.
    assert excinfo.value.retry_after_seconds == 12 * 3600


def test_daily_retry_after_never_rounds_down_to_zero(tmp_path: Path, clock: _Clock) -> None:
    """In the last fraction of the UTC day, int() truncation returned a
    Retry-After of 0 — sending the client straight back into a quota that has
    not reset. The hint must round up to at least 1."""
    clock.now = datetime(2026, 7, 17, 23, 59, 59, 999999, tzinfo=UTC)
    enforcer = _enforcer(tmp_path, Limits(daily_quota=1, max_in_flight=10, rate_burst=10), clock)

    with enforcer.guard("beta-001"):
        pass

    assert _refused(enforcer).retry_after_seconds >= 1


def test_daily_used_reports_zero_after_the_day_turns_without_a_guard(
    tmp_path: Path,
    clock: _Clock,
) -> None:
    """daily_used claims to report 'today'; across UTC midnight it must not keep
    returning yesterday's count until the next guard() happens to roll it."""
    enforcer = _enforcer(tmp_path, Limits(daily_quota=5, max_in_flight=10, rate_burst=10), clock)

    with enforcer.guard("beta-001"):
        pass
    assert enforcer.daily_used("beta-001") == 1

    clock.now = datetime(2026, 7, 18, 0, 0, 1, tzinfo=UTC)
    assert enforcer.daily_used("beta-001") == 0  # rolled without needing a guard()


# --- instance-wide ceiling (self-service sign-up) ---------------------------


def _write_many(path: Path, ids: tuple[str, ...], limits: Limits) -> None:
    write_credential_file(
        path,
        [
            Credential(
                credential_id=cid,
                label=cid,
                token_sha256=hash_token(generate_token()),
                limits=limits,
            )
            for cid in ids
        ],
    )


def test_service_daily_ceiling_refuses_a_user_still_inside_their_own_quota(
    tmp_path: Path, clock: _Clock
) -> None:
    """The point of the ceiling: per-user limits do not bound N users."""
    path = tmp_path / "credentials.json"
    _write_many(path, ("a", "b"), Limits(daily_quota=100))
    enforcer = QuotaEnforcer(
        CredentialStore(path),
        clock.monotonic,
        clock.utc_now,
        service_limits=ServiceLimits(daily_quota=2),
    )

    with enforcer.guard("a"):
        pass
    with enforcer.guard("b"):
        pass

    with pytest.raises(QuotaExceededError) as excinfo, enforcer.guard("a"):
        pass  # pragma: no cover - guard raises before the body
    assert "daily ceiling" in str(excinfo.value)
    assert enforcer.daily_used("a") == 1  # own quota untouched by the refusal


def test_service_in_flight_ceiling_counts_across_different_users(
    tmp_path: Path, clock: _Clock
) -> None:
    path = tmp_path / "credentials.json"
    _write_many(path, ("a", "b", "c"), Limits(max_in_flight=4))
    enforcer = QuotaEnforcer(
        CredentialStore(path),
        clock.monotonic,
        clock.utc_now,
        service_limits=ServiceLimits(max_in_flight=2),
    )

    with enforcer.guard("a"), enforcer.guard("b"):
        with pytest.raises(QuotaExceededError) as excinfo, enforcer.guard("c"):
            pass  # pragma: no cover - guard raises before the body
        assert "at capacity" in str(excinfo.value)

    with enforcer.guard("c"):
        pass  # slots freed on exit, so the instance recovers


def test_a_users_own_refusal_does_not_bill_the_instance(tmp_path: Path, clock: _Clock) -> None:
    """A call refused by the caller's own brake never happened, so it must not
    eat the shared daily ceiling that protects the embedding budget."""
    path = tmp_path / "credentials.json"
    _write_many(path, ("a",), Limits(daily_quota=1))
    enforcer = QuotaEnforcer(
        CredentialStore(path),
        clock.monotonic,
        clock.utc_now,
        service_limits=ServiceLimits(daily_quota=10),
    )

    with enforcer.guard("a"):
        pass
    with pytest.raises(QuotaExceededError), enforcer.guard("a"):
        pass  # pragma: no cover - guard raises before the body

    assert enforcer.service_daily_used() == 1


def test_without_a_ceiling_the_instance_is_not_metered(tmp_path: Path, clock: _Clock) -> None:
    """Opaque-token-only deploys keep the pre-self-service behaviour."""
    path = tmp_path / "credentials.json"
    _write_many(path, ("a",), Limits())
    enforcer = QuotaEnforcer(CredentialStore(path), clock.monotonic, clock.utc_now)

    with enforcer.guard("a"):
        pass

    assert enforcer.service_daily_used() == 0


# --- bounded state store ----------------------------------------------------


def _saturate(enforcer: QuotaEnforcer, ids: tuple[str, ...]) -> None:
    for cid in ids:
        with enforcer.guard(cid):
            pass


def test_idle_unspent_counters_are_evicted(tmp_path: Path, clock: _Clock) -> None:
    """Self-service sign-up makes the key space unbounded; the dict must not be."""
    path = tmp_path / "credentials.json"
    _write_many(path, ("a", "b"), Limits(daily_quota=5))
    enforcer = QuotaEnforcer(
        CredentialStore(path),
        clock.monotonic,
        clock.utc_now,
        eviction_threshold=1,
        eviction_idle_seconds=60.0,
    )

    with enforcer.guard("a"):
        pass
    # A new day makes "a" unspent again, and an hour makes it idle.
    clock.now = clock.now.replace(day=clock.now.day + 1)
    clock.advance(120)

    with enforcer.guard("b"):
        pass

    assert enforcer.tracked_credentials() == 1


def test_eviction_never_forgives_a_quota_spent_today(tmp_path: Path, clock: _Clock) -> None:
    """The trap a naive LRU would fall into: dropping a state hands its daily
    quota back, which anyone could drive by pausing for the idle window."""
    path = tmp_path / "credentials.json"
    _write_many(path, ("a", "b"), Limits(daily_quota=1))
    enforcer = QuotaEnforcer(
        CredentialStore(path),
        clock.monotonic,
        clock.utc_now,
        eviction_threshold=1,
        eviction_idle_seconds=60.0,
    )

    with enforcer.guard("a"):
        pass
    clock.advance(3600)  # idle long past the window, but same UTC day
    with enforcer.guard("b"):
        pass

    assert enforcer.daily_used("a") == 1
    with pytest.raises(QuotaExceededError), enforcer.guard("a"):
        pass  # pragma: no cover - guard raises before the body


def test_a_call_in_flight_is_never_evicted(tmp_path: Path, clock: _Clock) -> None:
    path = tmp_path / "credentials.json"
    _write_many(path, ("a", "b"), Limits())
    enforcer = QuotaEnforcer(
        CredentialStore(path),
        clock.monotonic,
        clock.utc_now,
        eviction_threshold=1,
        eviction_idle_seconds=0.0,
    )

    with enforcer.guard("a"):
        with enforcer.guard("b"):
            pass
        # "a" holds a slot; dropping its state would lose the decrement and leak
        # an in-flight count that never returns.
        assert enforcer.tracked_credentials() == 2
