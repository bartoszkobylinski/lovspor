"""Unit tests for WorkOS JWT verification and the composite verifier.

Signs real RS256 JWTs with a throwaway key and stubs the JWKS lookup to return
its public half, so verification exercises the true pyjwt path (signature,
issuer, audience, expiry, required claims) without a network call.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import PyJWKClientError
from mcp.server.auth.provider import AccessToken

from lovspor.access import SELF_SERVICE_LIMITS, Limits
from lovspor.workos_auth import (
    DEFAULT_WORKOS_LIMITS,
    CompositeVerifier,
    WorkOSTokenVerifier,
)

ISSUER = "https://vigilant-beacon-78-staging.authkit.app"
RESOURCE = "https://lovspor.bartoszkobylinski.com/mcp"


@pytest.fixture(scope="module")
def keypair() -> tuple[Any, Any]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


class _StubJWKS:
    """Return a fixed public key for any token — the network JWKS fetch, faked."""

    def __init__(self, public_key: Any) -> None:
        self._key = public_key

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        return SimpleNamespace(key=self._key)


class _UnavailableJWKS:
    """Model an AuthKit JWKS endpoint that cannot be reached."""

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        raise PyJWKClientError("JWKS unavailable")


class _FakeCreds:
    """Minimal CredentialStore stand-in: records calls, serves one Limits."""

    def __init__(self, limits: Limits | None = None) -> None:
        self.verify_called = False
        self._limits = limits or Limits()

    async def verify_token(self, token: str) -> AccessToken | None:
        self.verify_called = True
        return AccessToken(token=token, client_id="beta-001", scopes=[], expires_at=None)

    def limits_for(self, credential_id: str) -> Limits | None:
        return self._limits


def _verifier(public_key: Any) -> WorkOSTokenVerifier:
    return WorkOSTokenVerifier(
        authkit_domain=ISSUER, resource_url=RESOURCE, jwks_client=_StubJWKS(public_key)
    )


def _sign(private: Any, **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": RESOURCE,
        "sub": "user_123",
        "iat": now,
        "exp": now + 3600,
        "scope": "openid profile email",
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}  # None drops a claim
    return jwt.encode(claims, private, algorithm="RS256")


def test_default_workos_limits_are_the_self_service_bucket_not_the_hand_issued_one() -> None:
    """Self-service users stopped inheriting the hand-issued defaults when the
    endpoint opened to public sign-up (2026-09-06). The assertion is inverted on
    purpose: an anonymous sign-up must not be handed a named tester's allowance,
    and at the hand-issued in-flight default of 4 one of them could hold four of
    the production droplet's five worker threads."""
    assert DEFAULT_WORKOS_LIMITS == SELF_SERVICE_LIMITS
    assert Limits() != DEFAULT_WORKOS_LIMITS
    assert DEFAULT_WORKOS_LIMITS.max_in_flight < Limits().max_in_flight


def test_default_jwks_client_uses_the_authkit_oauth2_endpoint() -> None:
    verifier = WorkOSTokenVerifier(authkit_domain=f"{ISSUER}/", resource_url=RESOURCE)
    assert verifier._jwks.uri == f"{ISSUER}/oauth2/jwks"


def test_valid_jwt_returns_access_token(keypair: tuple[Any, Any]) -> None:
    private, public = keypair
    result = asyncio.run(_verifier(public).verify_token(_sign(private)))
    assert isinstance(result, AccessToken)
    # The literal is deliberate, do not rewrite it as f"{WORKOS_CLIENT_ID_PREFIX}…":
    # `workos:` is the quota-key contract shared with limits_for and the metering
    # store, so an expectation built from the constant would follow it anywhere.
    assert result.client_id == "workos:user_123"
    assert "openid" in result.scopes
    assert result.expires_at is not None


def test_wrong_audience_rejected(keypair: tuple[Any, Any]) -> None:
    private, public = keypair
    token = _sign(private, aud="https://evil.example.com/mcp")
    assert asyncio.run(_verifier(public).verify_token(token)) is None


def test_wrong_issuer_rejected(keypair: tuple[Any, Any]) -> None:
    private, public = keypair
    token = _sign(private, iss="https://other-project.authkit.app")
    assert asyncio.run(_verifier(public).verify_token(token)) is None


def test_expired_rejected(keypair: tuple[Any, Any]) -> None:
    private, public = keypair
    now = int(time.time())
    token = _sign(private, exp=now - 10, iat=now - 3600)
    assert asyncio.run(_verifier(public).verify_token(token)) is None


def test_missing_sub_rejected(keypair: tuple[Any, Any]) -> None:
    private, public = keypair
    token = _sign(private, sub=None)
    assert asyncio.run(_verifier(public).verify_token(token)) is None


@pytest.mark.parametrize("claim", ["exp", "iat", "aud", "iss"])
def test_other_required_claims_rejected(
    keypair: tuple[Any, Any],
    claim: str,
) -> None:
    private, public = keypair
    token = _sign(private, **{claim: None})
    assert asyncio.run(_verifier(public).verify_token(token)) is None


def test_empty_sub_rejected(keypair: tuple[Any, Any]) -> None:
    private, public = keypair
    token = _sign(private, sub="")
    assert asyncio.run(_verifier(public).verify_token(token)) is None


def test_unavailable_jwks_rejected() -> None:
    verifier = WorkOSTokenVerifier(
        authkit_domain=ISSUER,
        resource_url=RESOURCE,
        jwks_client=_UnavailableJWKS(),
    )
    assert asyncio.run(verifier.verify_token("header.payload.signature")) is None


def test_missing_scope_becomes_an_empty_scope_list(keypair: tuple[Any, Any]) -> None:
    private, public = keypair
    result = asyncio.run(_verifier(public).verify_token(_sign(private, scope=None)))
    assert result is not None
    assert result.scopes == []


def test_signature_from_unknown_key_rejected(keypair: tuple[Any, Any]) -> None:
    _, public = keypair
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _sign(other)  # signed by a key the JWKS does not return
    assert asyncio.run(_verifier(public).verify_token(token)) is None


def test_symmetric_algorithm_rejected(keypair: tuple[Any, Any]) -> None:
    """alg-confusion defence: only RS256 is accepted, so an HS256 token rejects."""
    _, public = keypair
    now = int(time.time())
    token = jwt.encode(
        {"iss": ISSUER, "aud": RESOURCE, "sub": "user_123", "iat": now, "exp": now + 3600},
        key="not-the-real-secret",
        algorithm="HS256",
    )
    assert asyncio.run(_verifier(public).verify_token(token)) is None


def test_composite_routes_jwt_to_workos(keypair: tuple[Any, Any]) -> None:
    private, public = keypair
    creds = _FakeCreds()
    composite = CompositeVerifier(
        credentials=creds, workos=_verifier(public), workos_default_limits=Limits()
    )
    result = asyncio.run(composite.verify_token(_sign(private)))
    assert result is not None
    assert result.client_id == "workos:user_123"
    assert creds.verify_called is False  # never reached the opaque path


def test_composite_routes_opaque_to_credentials(keypair: tuple[Any, Any]) -> None:
    _, public = keypair
    creds = _FakeCreds()
    composite = CompositeVerifier(
        credentials=creds, workos=_verifier(public), workos_default_limits=Limits()
    )
    result = asyncio.run(composite.verify_token("lsp_opaque_developer_token"))
    assert result is not None
    assert creds.verify_called is True


def test_composite_limits_for_workos_user_is_default(keypair: tuple[Any, Any]) -> None:
    """A `workos:`-namespaced id must resolve to the shared default bucket, not fall
    through to the credential store. Literal prefix on purpose — see the note in
    test_valid_jwt_returns_access_token; the store's limits differ so a prefix that
    stopped matching shows up as the wrong quota rather than passing silently."""
    _, public = keypair
    default = Limits(daily_quota=42)
    composite = CompositeVerifier(
        credentials=_FakeCreds(limits=Limits(daily_quota=7)),
        workos=_verifier(public),
        workos_default_limits=default,
    )
    assert composite.limits_for("workos:user_abc") == default


def test_composite_limits_for_credential_reads_store(keypair: tuple[Any, Any]) -> None:
    _, public = keypair
    creds = _FakeCreds(limits=Limits(daily_quota=7))
    composite = CompositeVerifier(
        credentials=creds, workos=_verifier(public), workos_default_limits=Limits()
    )
    resolved = composite.limits_for("beta-001")
    assert resolved is not None
    assert resolved.daily_quota == 7
