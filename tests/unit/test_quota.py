"""Unit tests for the per-credential quota brakes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.access import (
    Credential,
    CredentialStore,
    Limits,
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
    assert "in flight" in str(error)
    assert error.retry_after_seconds >= 1


def test_in_flight_slot_frees_on_exit(tmp_path: Path, clock: _Clock) -> None:
    enforcer = _enforcer(tmp_path, Limits(max_in_flight=1), clock)
    with enforcer.guard("beta-001"):
        pass
    with enforcer.guard("beta-001"):
        pass  # the first call released its slot


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
    with pytest.raises(QuotaExceededError, match="rate"), enforcer.guard("beta-001"):
        pass


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


def test_bucket_does_not_refill_past_burst(tmp_path: Path, clock: _Clock) -> None:
    limits = Limits(rate_burst=2, rate_per_minute=60, max_in_flight=100)
    enforcer = _enforcer(tmp_path, limits, clock)

    clock.advance(3600)  # an idle hour must not bank an hour of calls
    for _ in range(2):
        with enforcer.guard("beta-001"):
            pass
    with pytest.raises(QuotaExceededError), enforcer.guard("beta-001"):
        pass


def test_daily_quota_is_exhaustible(tmp_path: Path, clock: _Clock) -> None:
    limits = Limits(daily_quota=2, max_in_flight=100, rate_burst=100)
    enforcer = _enforcer(tmp_path, limits, clock)

    for _ in range(2):
        with enforcer.guard("beta-001"):
            pass
    with pytest.raises(QuotaExceededError, match="daily"), enforcer.guard("beta-001"):
        pass


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
    with (
        pytest.raises(QuotaExceededError, match="unknown credential"),
        enforcer.guard("beta-nonexistent"),
    ):
        pass


def test_daily_retry_after_points_past_utc_midnight(tmp_path: Path, clock: _Clock) -> None:
    limits = Limits(daily_quota=1, max_in_flight=100, rate_burst=100)
    enforcer = _enforcer(tmp_path, limits, clock)

    with enforcer.guard("beta-001"):
        pass
    with pytest.raises(QuotaExceededError) as excinfo, enforcer.guard("beta-001"):
        pass
    # 12:00:00 UTC -> midnight is 12h out.
    assert excinfo.value.retry_after_seconds == 12 * 3600
