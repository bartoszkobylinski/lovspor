"""Access control for the hosted MCP transport.

The hosted server (``lovspor mcp-http``) issues opaque bearer tokens by hand
for a bounded beta. mcp's ``TokenVerifier`` protocol is the whole integration
point: return an ``AccessToken`` to admit a call, ``None`` to reject it. The
SDK owns everything around that — bearer parsing, expiry enforcement, scope
checks, and the spec-compliant 401/403 with ``WWW-Authenticate``. Revocation
is therefore just "stop returning a token"; there is no session to invalidate.

Credentials live in a JSON file rather than a database. The beta is small and
hand-issued, so keeping the server free of write-state buys us no schema, no
backups, and no thread-safe database access on a hot path that now runs on
worker threads. The file is re-read when its mtime moves, so revoking a
credential lands without a restart — the same signal ``CorpusReader`` uses for
the corpus manifest.

This lives outside ``mcp.py`` deliberately: mutmut cannot complete a run on
that module (docs/decisions.md §9a), and security logic is exactly what we
want mutation coverage on.
"""

import hashlib
import json
import os
import secrets
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mcp.server.auth.provider import AccessToken
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from lovspor.errors import ConfigError

# Not a secret: an identifying prefix on the token so a leaked string is
# recognisable as a lovspor credential (and greppable in logs/secret scanners).
_TOKEN_PREFIX = "lsp_"  # noqa: S105
_TOKEN_BYTES = 32

# The on-disk store shape this build understands. One source of truth: the
# writer stamps it and the reader refuses anything else, so the two cannot
# drift apart the way a hardcoded literal on each side would.
CREDENTIALS_SCHEMA_VERSION = 1


class Limits(BaseModel):
    """Per-credential safety brakes.

    These are a runaway-loop brake, not a business limit: generous enough that
    a real research session never notices, tight enough that one client cannot
    take the box down. Defaults approved 2026-07-16 and expected to be wrong —
    they are placeholders until real beta traffic replaces guesswork. Every
    field is overridable per credential so a specific tester can be raised by
    editing one line, without a deploy.
    """

    # The shared asyncio.to_thread pool is min(32, cpu + 4). A rate limit does
    # not protect it: 50 calls fired in one instant satisfy any per-minute cap
    # while occupying every worker. This is the brake that actually matters.
    max_in_flight: int = 4
    # search_body measured ~0.57 s warm on the production corpus, so 120/min of
    # the heaviest tool is roughly one worker thread held continuously.
    rate_per_minute: int = 120
    # An AI client fans out ~15-20 calls answering one legal question
    # (semantic_search -> get_section xN -> verify_quote -> validate_citation),
    # so a burst allowance below that would break normal use.
    rate_burst: int = 30
    # ~5x a heavy researcher's day (20 calls x 50 questions). Approximate by
    # construction: counters live in memory and reset on restart.
    daily_quota: int = 5000


class Credential(BaseModel):
    """One issued beta credential, as stored on disk.

    Holds the token's SHA-256, never the token itself: if this file leaks it
    must not be a set of working credentials.
    """

    credential_id: str
    label: str
    token_sha256: str
    scopes: list[str] = Field(default_factory=list)
    revoked: bool = False
    expires_at: datetime | None = None
    limits: Limits = Field(default_factory=Limits)


class CredentialFile(BaseModel):
    """On-disk shape of the credential store."""

    # validate_default so a wrong default is caught here rather than shipped:
    # the version is the one field whose in-code default is itself a claim.
    model_config = ConfigDict(validate_default=True)

    schema_version: int = CREDENTIALS_SCHEMA_VERSION
    credentials: list[Credential] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def _reject_unknown_version(cls, value: int) -> int:
        """Refuse a store this build does not understand instead of reading it
        with today's rules.

        A *newer* file is the dangerous direction. v2 may add a field that
        withdraws access — a second revocation flag, a narrower scope, an
        allow-list — and v1 code, not knowing to look for it, would admit a
        credential the operator believes is shut off. That is the silent
        failure this module exists to avoid, so an unknown version fails the
        parse: the constructor turns that into a startup refusal and the
        reloader into fail-closed, exactly as for a corrupt file.

        Deliberately not widened to "accept anything older". Whoever adds v2
        has to write the migration and decide what a v1 file means to it,
        rather than inherit a guess made here before v2 existed.
        """
        if value != CREDENTIALS_SCHEMA_VERSION:
            raise ValueError(
                f"credential store schema_version is {value}, but this build only "
                f"understands {CREDENTIALS_SCHEMA_VERSION}. Upgrade lovspor, or point "
                "--credentials at a store this build wrote.",
            )
        return value


class LovsporAccessToken(AccessToken):
    """``AccessToken`` carrying the credential's limits to the quota layer.

    mcp blesses subclassing for server-side fields (``provider.py``: FastMCP
    never renders these into a response), which lets the quota middleware read
    the limits straight off ``get_access_token()`` instead of doing its own
    store lookup on every request.
    """

    limits: Limits


def generate_token() -> str:
    """Mint a new opaque bearer token. Shown once, never stored."""
    return _TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """SHA-256 hex of a token.

    A plain hash, not a password KDF. These tokens are 256 bits of CSPRNG
    output, so there is no dictionary to attack and no rainbow table to salt
    against; bcrypt/argon2 would only add latency to every single request. The
    reason KDFs exist — low-entropy, human-chosen secrets — does not apply.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def default_credentials_path() -> Path:
    """Default credential-store location.

    ``$XDG_CONFIG_HOME/lovspor/credentials.json`` when the XDG variable is set,
    else ``~/.config/lovspor/credentials.json``. Config rather than cache: a
    cache is something you can delete, and deleting this locks every beta user
    out.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return (base / "lovspor" / "credentials.json").expanduser()


def load_credentials(path: Path) -> list[Credential]:
    """Read the store for the issuing CLI. Missing file reads as empty so the
    first ``tokens issue`` can create it."""
    if not path.is_file():
        return []
    try:
        return list(_load_credential_file(path).credentials)
    except (OSError, ValueError, ValidationError) as exc:
        raise ConfigError(f"unreadable credential store at {path}: {exc}") from exc


def issue_credential(
    credentials: list[Credential],
    label: str,
    expires_in_days: int | None,
) -> tuple[Credential, str]:
    """Mint a credential, returning it alongside the plaintext token.

    The token is returned once and never stored — only its hash goes to disk,
    so a lost token is reissued, not recovered.

    ``expires_in_days`` of ``None`` means the credential never expires, which
    the SDK honours literally. The CLI defaults to a real expiry so that stays
    a deliberate choice rather than an accident of a forgotten beta token.
    """
    existing = {c.credential_id for c in credentials}
    index = 1
    while f"beta-{index:03d}" in existing:
        index += 1
    token = generate_token()
    credential = Credential(
        credential_id=f"beta-{index:03d}",
        label=label,
        token_sha256=hash_token(token),
        expires_at=(
            datetime.now(UTC) + timedelta(days=expires_in_days)
            if expires_in_days is not None
            else None
        ),
    )
    return credential, token


def revoke_credential(credentials: list[Credential], credential_id: str) -> list[Credential]:
    """Mark a credential revoked.

    Kept as a tombstone rather than deleted: the id stays reserved and the
    label survives, so the store still answers "who did we give access to?"
    after the fact.
    """
    if not any(c.credential_id == credential_id for c in credentials):
        raise ConfigError(f"no credential {credential_id!r} in the store")
    return [
        c.model_copy(update={"revoked": True}) if c.credential_id == credential_id else c
        for c in credentials
    ]


def _load_credential_file(path: Path) -> CredentialFile:
    return CredentialFile.model_validate_json(path.read_text(encoding="utf-8"))


def write_credential_file(path: Path, credentials: list[Credential]) -> None:
    """Write the store atomically-ish and lock it down to the owner.

    The file is a set of live credentials; a world-readable one on a shared
    box would hand them out.
    """
    path.write_text(
        json.dumps(
            {
                "schema_version": CREDENTIALS_SCHEMA_VERSION,
                "credentials": [json.loads(c.model_dump_json()) for c in credentials],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


class CredentialStore:
    """File-backed ``TokenVerifier`` for the hosted transport.

    Satisfies mcp's ``TokenVerifier`` protocol structurally — there is no base
    class to inherit — so it can be handed straight to ``FastMCP``.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._by_hash: dict[str, Credential] = {}
        self._mtime_ns: int | None = None
        if not path.is_file():
            raise ConfigError(
                f"no credential store at {path}. Run `lovspor tokens issue` to create "
                "one, or pass --credentials / set LOVSPOR_CREDENTIALS.",
            )
        try:
            self._install(_load_credential_file(path), path.stat().st_mtime_ns)
        except (OSError, ValueError, ValidationError) as exc:
            raise ConfigError(f"unreadable credential store at {path}: {exc}") from exc

    def _install(self, parsed: CredentialFile, mtime_ns: int) -> None:
        with self._lock:
            self._install_locked(parsed, mtime_ns)

    def _install_locked(self, parsed: CredentialFile, mtime_ns: int) -> None:
        self._by_hash = {c.token_sha256: c for c in parsed.credentials}
        self._mtime_ns = mtime_ns

    def _warn(self, reason: str) -> None:
        print(
            f"access: credential store unusable ({reason}); rejecting all",
            file=sys.stderr,
            flush=True,
        )

    def _reload_if_stale(self) -> None:
        """Re-read the store when its mtime moves, so a revocation lands
        without a restart.

        A broken file fails **closed** — every token is rejected until it parses
        again. Keeping the last-good set instead would mean a botched edit
        silently keeps a just-revoked credential alive: a typo is an outage we
        notice in minutes, an unapplied revocation is a breach we would never
        notice.

        The whole read — stat, parse and swap — happens under the lock. That is
        safe here precisely because the file is tiny, unlike the corpus indices,
        which are read off-lock and need the epoch guard (docs/decisions.md
        Sprint 12). It is also load-bearing: publishing the new mtime before the
        parse finished would let every other thread conclude the store is
        already current, skip the reload, and keep authenticating against the
        pre-edit map for the width of the parse — serving a credential revoked
        moments ago, which is the one outcome this store exists to prevent.
        """
        with self._lock:
            reason = self._refresh_locked()
        if reason is not None:
            self._warn(reason)

    def _refresh_locked(self) -> str | None:
        """Reload the store if its mtime moved; caller holds the lock.

        Returns the failure reason when the store went unusable, else ``None``.
        Reporting it back instead of printing here keeps stderr I/O off the lock.
        """
        try:
            mtime = self._path.stat().st_mtime_ns
        except OSError:
            self._by_hash = {}
            self._mtime_ns = None
            return f"{self._path} is gone"
        if mtime == self._mtime_ns:
            return None
        try:
            parsed = _load_credential_file(self._path)
        except (OSError, ValueError, ValidationError) as exc:
            self._by_hash = {}
            # Claim the mtime even on failure: a file that is broken stays
            # rejected without us re-parsing it on every single request.
            self._mtime_ns = mtime
            return str(exc)
        self._install_locked(parsed, mtime)
        return None

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a bearer token. ``None`` rejects, which the SDK turns into a
        401 with ``WWW-Authenticate``."""
        self._reload_if_stale()
        with self._lock:
            credential = self._by_hash.get(hash_token(token))
        if credential is None or credential.revoked:
            return None
        return LovsporAccessToken(
            token=token,
            client_id=credential.credential_id,
            scopes=list(credential.scopes),
            # int, not float: the SDK compares against int(time.time()). None
            # means "never expires" — deliberate, and the issuing CLI defaults
            # to a real expiry so it cannot happen by accident.
            expires_at=(int(credential.expires_at.timestamp()) if credential.expires_at else None),
            limits=credential.limits,
        )
