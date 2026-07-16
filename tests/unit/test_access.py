"""Unit tests for lovspor.access.

Covers the credential store and the mcp TokenVerifier implementation. The
SDK owns bearer parsing, expiry enforcement and the 401/403 responses (see
docs/decisions.md Sprint 12); what is ours is the store, the hashing, and
the fail-closed reload behaviour.
"""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lovspor.access import (
    Credential,
    CredentialStore,
    Limits,
    LovsporAccessToken,
    generate_token,
    hash_token,
)
from lovspor.errors import ConfigError


def _write_store(path: Path, credentials: list[Credential]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "credentials": [json.loads(c.model_dump_json()) for c in credentials],
            },
        ),
        encoding="utf-8",
    )


def _credential(
    token: str,
    *,
    credential_id: str = "beta-001",
    revoked: bool = False,
    expires_at: datetime | None = None,
    limits: Limits | None = None,
) -> Credential:
    return Credential(
        credential_id=credential_id,
        label="beta tester",
        token_sha256=hash_token(token),
        revoked=revoked,
        expires_at=expires_at,
        limits=limits or Limits(),
    )


def _bump_mtime(path: Path) -> None:
    """Force an mtime move so the reload fires regardless of filesystem
    timestamp granularity."""
    bumped = path.stat().st_mtime_ns + 1_000_000_000
    os.utime(path, ns=(bumped, bumped))


def test_generated_tokens_are_prefixed_unique_and_high_entropy() -> None:
    tokens = {generate_token() for _ in range(50)}

    assert len(tokens) == 50
    for token in tokens:
        assert token.startswith("lsp_")
        # 32 CSPRNG bytes, urlsafe-base64 => >=43 chars after the prefix.
        assert len(token) - len("lsp_") >= 43


def test_hash_token_is_stable_and_hides_the_token() -> None:
    token = generate_token()

    digest = hash_token(token)

    assert digest == hash_token(token)
    assert len(digest) == 64
    assert token not in digest
    assert hash_token(generate_token()) != digest


def test_verify_token_admits_a_valid_credential_and_carries_its_limits(
    tmp_path: Path,
) -> None:
    """The limits ride on the AccessToken so the quota middleware can read them
    via get_access_token() without a second store lookup."""
    token = generate_token()
    limits = Limits(max_in_flight=2, rate_per_minute=30, rate_burst=5, daily_quota=100)
    store_path = tmp_path / "credentials.json"
    _write_store(store_path, [_credential(token, limits=limits)])
    store = CredentialStore(store_path)

    access = asyncio.run(store.verify_token(token))

    assert isinstance(access, LovsporAccessToken)
    assert access.client_id == "beta-001"
    assert access.limits == limits


def test_verify_token_rejects_an_unknown_token(tmp_path: Path) -> None:
    store_path = tmp_path / "credentials.json"
    _write_store(store_path, [_credential(generate_token())])
    store = CredentialStore(store_path)

    assert asyncio.run(store.verify_token(generate_token())) is None


def test_verify_token_rejects_a_revoked_credential(tmp_path: Path) -> None:
    token = generate_token()
    store_path = tmp_path / "credentials.json"
    _write_store(store_path, [_credential(token, revoked=True)])
    store = CredentialStore(store_path)

    assert asyncio.run(store.verify_token(token)) is None


def test_revoking_takes_effect_without_a_restart(tmp_path: Path) -> None:
    """The server is long-lived; a revocation that needed a restart would mean
    a leaked token stays live until the next deploy."""
    token = generate_token()
    store_path = tmp_path / "credentials.json"
    _write_store(store_path, [_credential(token)])
    store = CredentialStore(store_path)
    assert asyncio.run(store.verify_token(token)) is not None

    _write_store(store_path, [_credential(token, revoked=True)])
    _bump_mtime(store_path)

    assert asyncio.run(store.verify_token(token)) is None


def test_expiry_is_passed_to_the_sdk_as_unix_seconds(tmp_path: Path) -> None:
    """The SDK enforces expiry itself (BearerAuthBackend), so we only have to
    hand it the right shape: an int, not a float or a datetime."""
    token = generate_token()
    expires = datetime.now(UTC) + timedelta(days=30)
    store_path = tmp_path / "credentials.json"
    _write_store(store_path, [_credential(token, expires_at=expires)])
    store = CredentialStore(store_path)

    access = asyncio.run(store.verify_token(token))

    assert access is not None
    assert access.expires_at == int(expires.timestamp())
    assert isinstance(access.expires_at, int)


def test_a_credential_without_an_expiry_never_expires(tmp_path: Path) -> None:
    """Pinning the SDK gotcha: expires_at=None means 'never', not 'expired'.
    The issuing CLI defaults to a real expiry so this stays a deliberate choice
    rather than an accident."""
    token = generate_token()
    store_path = tmp_path / "credentials.json"
    _write_store(store_path, [_credential(token, expires_at=None)])
    store = CredentialStore(store_path)

    access = asyncio.run(store.verify_token(token))

    assert access is not None
    assert access.expires_at is None


def test_store_refuses_to_start_without_a_credential_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no credential store"):
        CredentialStore(tmp_path / "missing.json")


def test_store_refuses_to_start_on_a_malformed_file(tmp_path: Path) -> None:
    store_path = tmp_path / "credentials.json"
    store_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ConfigError, match="unreadable credential store"):
        CredentialStore(store_path)


def test_a_file_that_breaks_while_running_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A botched edit must reject every token, not keep serving the last-good
    set. A typo is an outage we notice in minutes; honouring a credential the
    operator just tried to revoke is a breach we would never notice."""
    token = generate_token()
    store_path = tmp_path / "credentials.json"
    _write_store(store_path, [_credential(token)])
    store = CredentialStore(store_path)
    assert asyncio.run(store.verify_token(token)) is not None

    store_path.write_text("{ oops", encoding="utf-8")
    _bump_mtime(store_path)

    assert asyncio.run(store.verify_token(token)) is None
    assert "credential store" in capsys.readouterr().err


def test_a_deleted_file_while_running_fails_closed(tmp_path: Path) -> None:
    token = generate_token()
    store_path = tmp_path / "credentials.json"
    _write_store(store_path, [_credential(token)])
    store = CredentialStore(store_path)
    assert asyncio.run(store.verify_token(token)) is not None

    store_path.unlink()

    assert asyncio.run(store.verify_token(token)) is None


def test_the_store_file_never_holds_the_plaintext_token(tmp_path: Path) -> None:
    """If the file leaks, it must not be a set of working credentials."""
    token = generate_token()
    store_path = tmp_path / "credentials.json"
    _write_store(store_path, [_credential(token)])

    CredentialStore(store_path)

    assert token not in store_path.read_text(encoding="utf-8")


def test_limit_defaults_match_the_approved_beta_brakes() -> None:
    """Pinned so a silent default change cannot loosen the brakes."""
    limits = Limits()

    assert limits.max_in_flight == 4
    assert limits.rate_per_minute == 120
    assert limits.rate_burst == 30
    assert limits.daily_quota == 5000
