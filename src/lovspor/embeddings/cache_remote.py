"""The remote-first embedding-cache store on Cloudflare R2 (#802, design §8.1, ADR-0022).

:class:`RemoteEntryStore` has the local store's two operations, ``get`` and
``put``. :class:`~lovspor.embeddings.cache.CachingEmbedder` therefore uses it
unchanged, at the same seam: after a paid request returns and passes
``_check_response``, and before the next request is sent.

**The remote object is the authority.** The first value committed under a key
in the bucket is that key's only value. ESI never promised bit-identical
vectors (ADR-0005 Amendment 1), so a ``412`` proves only that *an* object
exists, not that it holds this writer's bytes. Hence:

* ``put`` writes remote first, with ``If-None-Match: *``.
  * ``200``: the same bytes are published locally.
  * ``412`` (or ``409``): the object is fetched and validated, and **its**
    bytes and value are adopted.
  * Transient failures and timeouts retry the same conditional ``PUT``. A
    timed-out ``PUT`` may have committed, and the next ``412`` settles it.
* ``get`` reconciles the bucket with the local copy, per the §8.1 lookup table.
  A difference is a conflict, never an overwrite.

Every downloaded entry is untrusted. It is decoded against the key the caller
derived, exactly like a local one. Every invalid, divergent or unreachable case
raises a typed :class:`~lovspor.errors.EmbeddingCacheError` (D2). None of them
is a miss, so none of them is paid for.
"""

import os
import time
from collections.abc import Callable

import httpx

from lovspor.embeddings.cache_format import CachedVectors, EntryKey, decode_entry, encode_entry
from lovspor.embeddings.cache_store import LocalEntryStore, entry_location
from lovspor.embeddings.r2_client import R2Client, R2Credentials, Reply
from lovspor.errors import (
    EmbeddingCacheConflictError,
    EmbeddingCacheError,
    EmbeddingCacheStorageError,
)

REMOTE_PREFIX = "lovspor-embedding-cache"
REMOTE_VARIABLE = "LOVSPOR_EMBEDDING_CACHE_REMOTE"
BUCKET_VARIABLE = "LOVSPOR_EMBEDDING_CACHE_R2_BUCKET"
ACCOUNT_VARIABLE = "LOVSPOR_EMBEDDING_CACHE_R2_ACCOUNT_ID"
KEY_ID_VARIABLE = "LOVSPOR_EMBEDDING_CACHE_R2_ACCESS_KEY_ID"
SECRET_VARIABLE = "LOVSPOR_EMBEDDING_CACHE_R2_SECRET_ACCESS_KEY"  # noqa: S105 - a name
_CREDENTIAL_VARIABLES = (BUCKET_VARIABLE, ACCOUNT_VARIABLE, KEY_ID_VARIABLE, SECRET_VARIABLE)

_ATTEMPTS = 3
_BACKOFF_SECONDS = (1.0, 2.0)
_CREATED = 200
_FOUND = 200
_ABSENT = 404
_HELD = frozenset({409, 412})
_TOO_MANY = 429
_SERVER_ERROR = 500
# 412, then 404 (removed in between): one more conditional PUT, then stop.
_PUT_ROUNDS = 2


def remote_key(key: EntryKey) -> str:
    """The object key of ``key``: the local layout under one prefix, identity only."""
    parts, name = entry_location(key)
    return "/".join((REMOTE_PREFIX, *parts, name))


def same_value(left: CachedVectors, right: CachedVectors) -> bool:
    """Same identity, vector bytes and scale. ``created_at`` differs between writers."""
    return (
        left.key == right.key
        and left.vectors.dtype == right.vectors.dtype
        and left.vectors.shape == right.vectors.shape
        and left.vectors.tobytes() == right.vectors.tobytes()
        and left.scale == right.scale
    )


class RemoteEntryStore:
    """The bucket as the authority, the local store as this runner's copy of its winners."""

    def __init__(
        self,
        local: LocalEntryStore,
        client: R2Client,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._local = local
        self._client = client
        self._sleep = sleep

    @property
    def local(self) -> LocalEntryStore:
        return self._local

    @property
    def client(self) -> R2Client:
        return self._client

    def get(self, key: EntryKey) -> CachedVectors | None:
        """The key's value, reconciled between the bucket and the local copy (§8.1 table)."""
        data = self._fetch(key)
        if data is None:
            local = self._local.get(key)
            return None if local is None else self.put(local)
        remote = _decode_remote(data, key, self._local)
        local = self._local.get(key)
        if local is None:
            return self._adopt(key, data)
        return _agree(local, remote)

    def put(self, entry: CachedVectors) -> CachedVectors:
        """Commit ``entry`` remote first; return the key's winning value (§8.1 steps 1-7)."""
        payload = encode_entry(entry)
        for _round in range(_PUT_ROUNDS):
            if self._create(entry.key, payload):
                return self._adopt(entry.key, payload)
            winner = self._fetch(entry.key)
            if winner is not None:
                return self._adopt(entry.key, winner)
        raise EmbeddingCacheStorageError(
            f"remote embedding cache entry was held, then vanished, twice ({entry.key.label()}); "
            "nothing was stored locally and no further request was sent",
        )

    def _adopt(self, key: EntryKey, data: bytes) -> CachedVectors:
        """Publish the winning bytes locally; a different local value is a conflict."""
        winner = _decode_remote(data, key, self._local)
        return _agree(self._local.publish(key, data), winner)

    def _create(self, key: EntryKey, payload: bytes) -> bool:
        """``True`` if this ``PUT`` created the object, ``False`` if another holds it."""
        reply = self._attempt(lambda: self._client.put_if_absent(remote_key(key), payload), key)
        if reply.status == _CREATED:
            return True
        if reply.status in _HELD:
            return False
        raise _refused("PUT", reply.status, key)

    def _fetch(self, key: EntryKey) -> bytes | None:
        """The object's bytes, or ``None`` if the bucket holds none under ``key``."""
        reply = self._attempt(lambda: self._client.get(remote_key(key)), key)
        if reply.status == _FOUND:
            return reply.body
        if reply.status == _ABSENT:
            return None
        raise _refused("GET", reply.status, key)

    def _attempt(self, call: Callable[[], Reply], key: EntryKey) -> Reply:
        """Up to three attempts across ``429``, ``5xx``, transport errors and timeouts."""
        for attempt in range(_ATTEMPTS):
            if attempt:
                self._sleep(_BACKOFF_SECONDS[attempt - 1])
            try:
                reply = call()
            except httpx.TransportError:
                continue
            if not _transient(reply.status):
                return reply
        raise EmbeddingCacheStorageError(
            f"remote embedding cache unreachable after {_ATTEMPTS} attempts ({key.label()}); "
            "the run stops before paying again",
        ) from None


def _transient(status: int) -> bool:
    return status == _TOO_MANY or status >= _SERVER_ERROR


def _refused(operation: str, status: int, key: EntryKey) -> EmbeddingCacheStorageError:
    return EmbeddingCacheStorageError(
        f"remote embedding cache refused {operation} with status {status} ({key.label()}); "
        "the run stops before paying again",
    )


def _decode_remote(data: bytes, key: EntryKey, local: LocalEntryStore) -> CachedVectors:
    """Validate downloaded bytes like a local entry; the refusal keeps its type."""
    try:
        return decode_entry(data, key, local.policy)
    except EmbeddingCacheError as exc:
        raise type(exc)(f"remote entry refused, kept as evidence: {exc}") from None


def _agree(local: CachedVectors, remote: CachedVectors) -> CachedVectors:
    if not same_value(local, remote):
        raise EmbeddingCacheConflictError(
            f"local and remote embedding cache values differ ({local.key.label()}); "
            "both are kept: invalidate one copy to continue",
        )
    return local


def remote_requested() -> bool:
    """Whether the environment asks for a remote store at all."""
    return bool(os.environ.get(REMOTE_VARIABLE))


def with_remote(local: LocalEntryStore) -> LocalEntryStore | RemoteEntryStore:
    """``local``, or the remote store over it when ``LOVSPOR_EMBEDDING_CACHE_REMOTE=r2``."""
    backend = os.environ.get(REMOTE_VARIABLE, "")
    if not backend:
        return local
    if backend != "r2":
        raise EmbeddingCacheError(f"{REMOTE_VARIABLE} supports only 'r2', got {backend!r}")
    return RemoteEntryStore(local, R2Client(_credentials()))


def _credentials() -> R2Credentials:
    values = {name: os.environ.get(name, "") for name in _CREDENTIAL_VARIABLES}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise EmbeddingCacheError(f"the remote embedding cache needs {', '.join(missing)}")
    return R2Credentials(
        bucket=values[BUCKET_VARIABLE],
        account_id=values[ACCOUNT_VARIABLE],
        access_key_id=values[KEY_ID_VARIABLE],
        secret_access_key=values[SECRET_VARIABLE],
    )
