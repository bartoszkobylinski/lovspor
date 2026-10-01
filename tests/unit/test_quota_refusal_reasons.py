"""Every quota refusal names its brake (issue #479).

The usage metrics count refusals by reason, so the reason must come from the
brake that fired, not be guessed from the message text afterwards.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.access import (
    Credential,
    CredentialStore,
    Limits,
    ServiceLimits,
    hash_token,
    write_credential_file,
)
from lovspor.quota import QuotaEnforcer, QuotaExceededError

_NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _enforcer(
    tmp_path: Path, limits: Limits, ceiling: ServiceLimits | None = None
) -> QuotaEnforcer:
    path = tmp_path / "credentials.json"
    write_credential_file(
        path,
        [
            Credential(credential_id=cid, label=cid, token_sha256=hash_token(cid), limits=limits)
            for cid in ("a", "b")
        ],
    )
    return QuotaEnforcer(CredentialStore(path), lambda: 0.0, lambda: _NOW, service_limits=ceiling)


def _reason(enforcer: QuotaEnforcer, credential_id: str = "a", *, paid: bool = False) -> str:
    with pytest.raises(QuotaExceededError) as excinfo, enforcer.guard(credential_id, paid=paid):
        pass
    return excinfo.value.reason


def test_a_bare_refusal_is_unidentified() -> None:
    assert QuotaExceededError("no credential", 1).reason == "unidentified"


def test_per_credential_in_flight(tmp_path: Path) -> None:
    enforcer = _enforcer(tmp_path, Limits(max_in_flight=1))
    with enforcer.guard("a"):
        assert _reason(enforcer) == "in_flight"


def test_rate(tmp_path: Path) -> None:
    enforcer = _enforcer(tmp_path, Limits(rate_burst=1, rate_per_minute=1))
    with enforcer.guard("a"):
        pass
    assert _reason(enforcer) == "rate"


def test_daily(tmp_path: Path) -> None:
    enforcer = _enforcer(tmp_path, Limits(daily_quota=1))
    with enforcer.guard("a"):
        pass
    assert _reason(enforcer) == "daily"


def test_paid_at_admission(tmp_path: Path) -> None:
    enforcer = _enforcer(tmp_path, Limits(paid_daily_quota=1))
    with enforcer.guard("a", paid=True):
        pass
    assert _reason(enforcer, paid=True) == "paid"


def test_paid_at_the_spend(tmp_path: Path) -> None:
    enforcer = _enforcer(tmp_path, Limits(paid_daily_quota=1))
    enforcer.charge_paid("a")
    with pytest.raises(QuotaExceededError) as excinfo:
        enforcer.charge_paid("a")
    assert excinfo.value.reason == "paid"


def test_unknown_credential(tmp_path: Path) -> None:
    enforcer = _enforcer(tmp_path, Limits())
    assert _reason(enforcer, "gone") == "unknown_credential"
    with pytest.raises(QuotaExceededError) as excinfo:
        enforcer.charge_paid("gone")
    assert excinfo.value.reason == "unknown_credential"


def test_service_capacity(tmp_path: Path) -> None:
    ceiling = ServiceLimits(max_in_flight=1)
    enforcer = _enforcer(tmp_path, Limits(), ceiling)
    with enforcer.guard("a"):
        assert _reason(enforcer, "b") == "service_capacity"


def test_service_daily(tmp_path: Path) -> None:
    enforcer = _enforcer(tmp_path, Limits(), ServiceLimits(daily_quota=1))
    with enforcer.guard("a"):
        pass
    assert _reason(enforcer, "b") == "service_daily"


def test_service_paid_at_admission_and_at_the_spend(tmp_path: Path) -> None:
    enforcer = _enforcer(tmp_path, Limits(), ServiceLimits(paid_daily_quota=1))
    with enforcer.guard("a", paid=True):
        pass
    assert _reason(enforcer, "b", paid=True) == "service_paid"
    with pytest.raises(QuotaExceededError) as excinfo:
        enforcer.charge_paid("b")
    assert excinfo.value.reason == "service_paid"
