"""WorkOS-issued JWT verification for the hosted MCP resource server.

lovspor stays a **resource server**: WorkOS (AuthKit) is the OAuth 2.1
authorization server that runs login + consent + dynamic client registration and
issues the access-token JWTs. This module verifies those JWTs against WorkOS's
JWKS and adapts them to mcp's ``TokenVerifier`` protocol — the same seam
:class:`lovspor.access.CredentialStore` fills for the hand-issued opaque-token
path used by developer clients. See ``analysis/oauth-scout.md`` and
``analysis/auth-provider-workos.md``.

The two paths coexist. A chat-app connector (ChatGPT / Claude.ai) arrives with a
WorkOS JWT; a developer client (Claude Code / Cursor) still pastes an opaque
``lsp_`` token. :class:`CompositeVerifier` routes by token shape and, for the
quota layer, resolves a default :class:`~lovspor.access.Limits` for any
WorkOS-authenticated user (self-service users have no hand-issued credential).
"""

from __future__ import annotations

import anyio.to_thread
import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError, PyJWTError
from mcp.server.auth.provider import AccessToken

from lovspor.access import SELF_SERVICE_LIMITS, CredentialStore, Limits

# A WorkOS identity is namespaced so the quota layer can tell it apart from a
# hand-issued ``credential_id`` without ambiguity — a revoked credential and an
# unknown WorkOS subject must never resolve to the same limits bucket.
WORKOS_CLIENT_ID_PREFIX = "workos:"

# WorkOS access tokens are RS256 JWTs. Pinning the algorithm list is the standard
# defence against the alg-confusion / "alg: none" family — never let the token's
# own header pick a symmetric or empty algorithm.
_ALGORITHMS = ["RS256"]
_REQUIRED_CLAIMS = ["exp", "iat", "sub", "aud", "iss"]

# A JWT is header.payload.signature — two dots. An opaque developer token
# (``lsp_…``) has none, so the dot count routes a bearer to the right verifier.
_JWT_DOT_COUNT = 2

# Self-service WorkOS users have no hand-issued credential, so they share one set
# of default limits — their own counters, keyed by ``workos:<sub>``, but the same
# numbers. Those numbers are deliberately NOT the hand-issued defaults: a named
# beta tester and an anonymous sign-up are different risk, and at the hand-issued
# ``max_in_flight`` of 4 one self-service user could hold four of the production
# droplet's five worker threads. See ``access.SELF_SERVICE_LIMITS``; the operator
# overrides them from the environment without a release.
DEFAULT_WORKOS_LIMITS = SELF_SERVICE_LIMITS


class WorkOSTokenVerifier:
    """Verify WorkOS AuthKit access-token JWTs (RS256, audience-bound per RFC 8707).

    Satisfies mcp's ``TokenVerifier`` protocol structurally (an async
    ``verify_token``). The JWKS fetch + signature check is a blocking library
    call, so it runs on a worker thread to keep the event loop free — the same
    posture as the tool-body offload in ``mcp.py``. ``PyJWKClient`` caches keys,
    so the network fetch is a rare per-rotation event, not a per-request cost.
    """

    def __init__(
        self,
        *,
        authkit_domain: str,
        resource_url: str,
        jwks_client: PyJWKClient | None = None,
    ) -> None:
        # issuer_url the tokens must carry (`iss`) and the JWKS host.
        self._issuer = authkit_domain.rstrip("/")
        # The MCP server's resource identifier the token must be bound to (`aud`,
        # RFC 8707) — a token minted for another resource must not open lovspor.
        self._audience = resource_url
        self._jwks = jwks_client or PyJWKClient(f"{self._issuer}/oauth2/jwks")

    def _decode(self, token: str) -> dict[str, object]:
        signing_key = self._jwks.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=_ALGORITHMS,
            audience=self._audience,
            issuer=self._issuer,
            options={"require": _REQUIRED_CLAIMS},
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return an :class:`AccessToken` for a valid WorkOS JWT, else ``None``.

        ``None`` fails closed — a bad signature, wrong issuer/audience, expired
        token, missing required claim, or an unreachable JWKS all reject rather
        than raise, which the SDK turns into a 401.
        """
        try:
            claims = await anyio.to_thread.run_sync(self._decode, token)
        except (PyJWTError, PyJWKClientError):
            return None
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            return None
        raw_scope = claims.get("scope", "")
        scopes = raw_scope.split() if isinstance(raw_scope, str) else []
        expires = claims.get("exp")
        return AccessToken(
            token=token,
            client_id=f"{WORKOS_CLIENT_ID_PREFIX}{subject}",
            scopes=scopes,
            expires_at=int(expires) if isinstance(expires, int | float) else None,
        )


class CompositeVerifier:
    """Route a bearer token to the right verifier by shape, and serve limits.

    A WorkOS access token is a JWT (three ``.``-separated segments); a developer
    token is an opaque ``lsp_…`` string. Trying the wrong verifier merely returns
    ``None``, so the shape check is a cheap dispatch, not a security boundary —
    each verifier fully validates its own tokens. Implements the same
    ``verify_token`` + ``limits_for`` surface the quota layer already consumes
    from :class:`~lovspor.access.CredentialStore`.
    """

    def __init__(
        self,
        *,
        credentials: CredentialStore,
        workos: WorkOSTokenVerifier,
        workos_default_limits: Limits,
    ) -> None:
        self._credentials = credentials
        self._workos = workos
        self._workos_default_limits = workos_default_limits

    async def verify_token(self, token: str) -> AccessToken | None:
        if token.count(".") == _JWT_DOT_COUNT:
            return await self._workos.verify_token(token)
        return await self._credentials.verify_token(token)

    def limits_for(self, client_id: str) -> Limits | None:
        """Quota for an authenticated identity. WorkOS self-service users share a
        default bucket (no hand-issued credential exists); developer credentials
        keep their own per-credential limits, read live from the store."""
        if client_id.startswith(WORKOS_CLIENT_ID_PREFIX):
            return self._workos_default_limits
        return self._credentials.limits_for(client_id)
