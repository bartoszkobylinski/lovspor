"""Unit tests for lovspor.access.

Covers the credential store and the mcp TokenVerifier implementation. The
SDK owns bearer parsing, expiry enforcement and the 401/403 responses (see
docs/decisions.md Sprint 12); what is ours is the store, the hashing, and
the fail-closed reload behaviour.
"""

import asyncio
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lovspor.access import (
    _TOKEN_PREFIX,
    Credential,
    CredentialStore,
    Limits,
    LovsporAccessToken,
    default_credentials_path,
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
        # Exactly 32 CSPRNG bytes, urlsafe-base64 => exactly 43 chars after the
        # prefix. Pinned rather than a >= floor: a floor silently accepts
        # _TOKEN_BYTES drifting to None (which falls back to the library
        # default) or to any larger value, so the constant would stop meaning
        # anything. Too few bytes is the security bug; an unpinned constant is
        # how you stop noticing which you have.
        assert len(token) - len(_TOKEN_PREFIX) == 43


def test_hash_token_is_stable_and_hides_the_token() -> None:
    token = generate_token()

    digest = hash_token(token)

    assert digest == hash_token(token)
    assert len(digest) == 64
    assert token not in digest
    assert hash_token(generate_token()) != digest


def test_default_credentials_path_uses_xdg_config_home_when_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert default_credentials_path() == tmp_path / "lovspor" / "credentials.json"


def test_default_credentials_path_falls_back_to_home_dot_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The fallback is the branch that ships: XDG_CONFIG_HOME is unset on a
    stock macOS box, so ~/.config is where a real operator's store lands."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert default_credentials_path() == tmp_path / ".config" / "lovspor" / "credentials.json"


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


def test_a_file_fixed_after_breakage_recovers_without_a_restart(tmp_path: Path) -> None:
    token = generate_token()
    store_path = tmp_path / "credentials.json"
    _write_store(store_path, [_credential(token)])
    store = CredentialStore(store_path)
    assert asyncio.run(store.verify_token(token)) is not None

    store_path.write_text("{ oops", encoding="utf-8")
    _bump_mtime(store_path)
    assert asyncio.run(store.verify_token(token)) is None

    _write_store(store_path, [_credential(token)])
    _bump_mtime(store_path)

    assert asyncio.run(store.verify_token(token)) is not None


def test_a_deleted_file_while_running_fails_closed(tmp_path: Path) -> None:
    token = generate_token()
    store_path = tmp_path / "credentials.json"
    _write_store(store_path, [_credential(token)])
    store = CredentialStore(store_path)
    assert asyncio.run(store.verify_token(token)) is not None

    store_path.unlink()

    assert asyncio.run(store.verify_token(token)) is None


def test_a_deleted_file_restored_later_recovers_without_a_restart(tmp_path: Path) -> None:
    token = generate_token()
    store_path = tmp_path / "credentials.json"
    _write_store(store_path, [_credential(token)])
    store = CredentialStore(store_path)
    assert asyncio.run(store.verify_token(token)) is not None

    store_path.unlink()
    assert asyncio.run(store.verify_token(token)) is None

    _write_store(store_path, [_credential(token)])
    _bump_mtime(store_path)

    assert asyncio.run(store.verify_token(token)) is not None


def _hammer(store: CredentialStore, token: str, *, workers: int = 16) -> list[object]:
    """Fire ``workers`` verify_token calls that all start together.

    The barrier matters: it lands every thread inside the reload window at
    once, which is exactly where a mtime published before the parse finished
    lets the losers read the pre-edit map.
    """
    barrier = threading.Barrier(workers)

    def hit(_: int) -> object:
        barrier.wait()
        return asyncio.run(store.verify_token(token))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(hit, range(workers)))


def test_concurrent_stale_reload_fails_closed_without_raising(tmp_path: Path) -> None:
    """The verifier runs on worker threads under Streamable HTTP, so one
    reload-triggering request must not race another into an exception — nor
    into a stale answer. A broken file rejects *every* concurrent caller, not
    merely the one that happened to notice."""
    token = generate_token()
    store_path = tmp_path / "credentials.json"
    _write_store(store_path, [_credential(token)])
    store = CredentialStore(store_path)
    assert asyncio.run(store.verify_token(token)) is not None

    store_path.write_text("{ oops", encoding="utf-8")
    _bump_mtime(store_path)

    assert _hammer(store, token) == [None] * 16
    assert asyncio.run(store.verify_token(token)) is None


def test_concurrent_broken_reload_warns_once_after_claiming_the_failed_mtime(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The first thread that sees a broken edit owns the failure for that
    mtime. Everyone else must reject against the failed-closed empty map,
    rather than re-parsing the same broken file and flooding stderr."""
    token = generate_token()
    store_path = tmp_path / "credentials.json"
    _write_store(store_path, [_credential(token)])
    store = CredentialStore(store_path)
    assert asyncio.run(store.verify_token(token)) is not None

    store_path.write_text("{ oops", encoding="utf-8")
    _bump_mtime(store_path)

    assert _hammer(store, token) == [None] * 16
    assert capsys.readouterr().err.count("credential store unusable") == 1


def test_a_revocation_lands_for_every_concurrent_caller_not_just_the_reloader(
    tmp_path: Path,
) -> None:
    """Revocation is the whole point of the store, and requests arrive in
    parallel on the HTTP transport. If the reloading thread publishes the new
    mtime before it has finished parsing, every other in-flight thread decides
    the store is already current and keeps authenticating the credential that
    was just revoked — the breach the fail-closed design exists to rule out.
    """
    token = generate_token()
    store_path = tmp_path / "credentials.json"
    _write_store(store_path, [_credential(token)])
    store = CredentialStore(store_path)
    assert asyncio.run(store.verify_token(token)) is not None

    _write_store(store_path, [_credential(token, revoked=True)])
    _bump_mtime(store_path)

    assert _hammer(store, token) == [None] * 16


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
