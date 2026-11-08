"""The remote-first embedding-cache store on R2 (#802, design §8.1, ADR-0022).

One test per row of the §8.1 write protocol, lookup table and failure matrix.
The bucket is :class:`tests.unit.r2_fakes.FakeBucket` behind ``pytest-httpx``;
no request leaves the process.

The remote object is the authority: the first value committed under a key is
its only value. A writer that loses adopts the winner. Every invalid or
divergent case stops with a typed refusal, and nothing is overwritten.
"""

import errno
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import httpx
import numpy as np
import pytest
from pytest_httpx import HTTPXMock

from lovspor.embeddings import cache_store
from lovspor.embeddings.cache_format import CachedVectors, EntryKey, encode_entry
from lovspor.embeddings.cache_remote import RemoteEntryStore, remote_key, same_value
from lovspor.embeddings.cache_store import LocalEntryStore, entry_location
from lovspor.embeddings.r2_client import R2Client
from lovspor.errors import (
    EmbeddingCacheConflictError,
    EmbeddingCacheError,
    EmbeddingCacheExpiredError,
    EmbeddingCacheStorageError,
)
from tests.unit import r2_fakes
from tests.unit.test_embedding_cache_admin import ESI, NOW, POLICY, _doc, _hash, _unit

Sleeps = list[float]


@pytest.fixture
def bucket(httpx_mock: HTTPXMock) -> r2_fakes.FakeBucket:
    fake = r2_fakes.FakeBucket()
    httpx_mock.add_callback(fake, is_reusable=True)
    return fake


@pytest.fixture
def sleeps() -> Sleeps:
    return []


@pytest.fixture
def remote(tmp_path: Path, sleeps: Sleeps) -> Callable[[str], RemoteEntryStore]:
    def make(name: str = "runner-1") -> RemoteEntryStore:
        local = LocalEntryStore.open(tmp_path / name, (), POLICY)
        client = R2Client(r2_fakes.credentials(), httpx.Client())
        return RemoteEntryStore(local, client, sleep=sleeps.append)

    return make


def _other_value(entry: CachedVectors) -> CachedVectors:
    """The same key, a different valid value: what another writer could have been paid."""
    vectors = entry.vectors.copy()
    vectors.flat[0] = vectors.flat[0] + 1 if entry.scale is not None else vectors.flat[0] / 2
    return CachedVectors(key=entry.key, vectors=vectors, scale=entry.scale, created_at=NOW)


def _local_bytes(store: RemoteEntryStore, key: EntryKey) -> bytes | None:
    parts, name = entry_location(key)
    path = store.local.root.joinpath(*parts, name)
    return path.read_bytes() if path.exists() else None


# --- keys ------------------------------------------------------------------


def test_remote_keys_mirror_the_local_layout_under_one_prefix() -> None:
    document, unit = _doc("a").key, _unit("a").key
    assert remote_key(document) == (
        f"lovspor-embedding-cache/schema-1/{ESI}/documents/{document.input_hash}.lsec"
    )
    assert remote_key(unit) == (
        f"lovspor-embedding-cache/schema-1/{ESI}/units/{unit.input_hash}/"
        f"0-2-{unit.unit.input_hash}.lsec"  # type: ignore[union-attr]
    )


def test_same_value_ignores_created_at_and_nothing_else() -> None:
    entry = _doc("a")
    later = CachedVectors(entry.key, entry.vectors, entry.scale, NOW + timedelta(hours=1))
    assert same_value(entry, later)
    assert not same_value(entry, _other_value(entry))
    rescaled = CachedVectors(entry.key, entry.vectors, 0.25, NOW)
    assert not same_value(entry, rescaled)


# --- write protocol ----------------------------------------------------------


def test_put_200_publishes_the_same_bytes_locally_and_returns_its_own_value(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore]
) -> None:
    store, entry = remote("runner-1"), _unit("a")
    stored = store.put(entry)
    assert same_value(stored, entry)
    assert bucket.objects[remote_key(entry.key)] == encode_entry(entry)
    assert _local_bytes(store, entry.key) == encode_entry(entry)
    assert bucket.calls == [("PUT", remote_key(entry.key))]


@pytest.mark.parametrize("make", [_doc, _unit], ids=["document", "unit"])
def test_put_412_adopts_a_different_valid_winner(
    bucket: r2_fakes.FakeBucket,
    remote: Callable[[str], RemoteEntryStore],
    make: Callable[[str], CachedVectors],
) -> None:
    mine = make("a")
    winner = _other_value(mine)
    bucket.objects[remote_key(mine.key)] = encode_entry(winner)
    store = remote("runner-1")

    stored = store.put(mine)

    assert same_value(stored, winner)
    assert not same_value(stored, mine)
    assert _local_bytes(store, mine.key) == encode_entry(winner)
    assert bucket.calls == [("PUT", remote_key(mine.key)), ("GET", remote_key(mine.key))]
    assert bucket.objects[remote_key(mine.key)] == encode_entry(winner), "never overwritten"


def test_put_412_with_an_equal_winner_is_adopted(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore]
) -> None:
    mine = _doc("a")
    earlier = CachedVectors(mine.key, mine.vectors, mine.scale, NOW - timedelta(hours=2))
    bucket.objects[remote_key(mine.key)] = encode_entry(earlier)
    stored = remote("runner-1").put(mine)
    assert stored.created_at == earlier.created_at, "the winner's created_at is kept"


def test_put_409_is_handled_like_412_never_as_success(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore]
) -> None:
    mine = _doc("a")
    winner = _other_value(mine)
    bucket.objects[remote_key(mine.key)] = encode_entry(winner)
    bucket.fail("PUT", 409)
    assert same_value(remote("runner-1").put(mine), winner)


def test_a_timeout_after_a_committed_put_then_412_adopts_its_own_bytes(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore], sleeps: Sleeps
) -> None:
    bucket.fail("PUT", r2_fakes.COMMIT_THEN_TIMEOUT)
    store, mine = remote("runner-1"), _unit("a")
    stored = store.put(mine)
    assert same_value(stored, mine)
    assert [method for method, _key in bucket.calls] == ["PUT", "PUT", "GET"]
    assert len(sleeps) == 1


def test_a_timeout_then_another_writers_commit_adopts_that_writer(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore]
) -> None:
    mine = _unit("a")
    winner = _other_value(mine)
    bucket.fail("PUT", r2_fakes.TIMEOUT)

    def other_writer_commits(key: str) -> None:
        bucket.objects.setdefault(key, encode_entry(winner))

    bucket.before_put = other_writer_commits
    assert same_value(remote("runner-1").put(mine), winner)


def _corrupt(entry: CachedVectors) -> bytes:
    data = bytearray(encode_entry(entry))
    data[-40] ^= 0x01
    return bytes(data)


def _torn(entry: CachedVectors) -> bytes:
    return encode_entry(entry)[:-7]


def _swapped(entry: CachedVectors) -> bytes:
    return encode_entry(_doc("another document"))


def _other_dimension(entry: CachedVectors) -> bytes:
    key = EntryKey(esi=ESI, input_hash=entry.key.input_hash, dim=8, rows=1)
    return encode_entry(CachedVectors(key, np.ones((1, 8), np.int8), 0.5, NOW))


def _expired(entry: CachedVectors) -> bytes:
    old = CachedVectors(entry.key, entry.vectors, entry.scale, NOW - timedelta(days=40))
    return encode_entry(old)


_INVALID: dict[str, Callable[[CachedVectors], bytes]] = {
    "corrupt": _corrupt,
    "torn": _torn,
    "swapped-identity": _swapped,
    "other-dimension": _other_dimension,
    "expired": _expired,
}


@pytest.mark.parametrize("plant", _INVALID.values(), ids=_INVALID.keys())
def test_put_412_then_an_invalid_winner_refuses_and_writes_nothing(
    bucket: r2_fakes.FakeBucket,
    remote: Callable[[str], RemoteEntryStore],
    plant: Callable[[CachedVectors], bytes],
) -> None:
    mine = _doc("a")
    evidence = plant(mine)
    bucket.objects[remote_key(mine.key)] = evidence
    store = remote("runner-1")

    with pytest.raises(EmbeddingCacheError, match="remote entry refused") as refused:
        store.put(mine)

    assert not isinstance(refused.value, EmbeddingCacheStorageError)
    assert _local_bytes(store, mine.key) is None
    assert bucket.objects[remote_key(mine.key)] == evidence, "kept as evidence"
    assert bucket.puts() == 1, "no PUT that could overwrite"


def test_an_expired_winner_is_refused_as_expired(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore]
) -> None:
    mine = _doc("a")
    bucket.objects[remote_key(mine.key)] = _expired(mine)
    with pytest.raises(EmbeddingCacheExpiredError):
        remote("runner-1").put(mine)


def test_put_412_then_404_tries_the_conditional_put_once_more(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore]
) -> None:
    bucket.fail("PUT", 412)
    store, mine = remote("runner-1"), _doc("a")
    assert same_value(store.put(mine), mine)
    assert [method for method, _key in bucket.calls] == ["PUT", "GET", "PUT"]


def test_put_412_then_404_twice_is_a_storage_error(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore]
) -> None:
    bucket.fail("PUT", 412, 412)
    store, mine = remote("runner-1"), _doc("a")
    with pytest.raises(EmbeddingCacheStorageError, match="vanished"):
        store.put(mine)
    assert _local_bytes(store, mine.key) is None


@pytest.mark.parametrize(
    "faults", [(429,), (503,), (500, 502), (r2_fakes.TIMEOUT,), (r2_fakes.DROP,)]
)
def test_transient_put_failures_are_retried_with_backoff(
    bucket: r2_fakes.FakeBucket,
    remote: Callable[[str], RemoteEntryStore],
    sleeps: Sleeps,
    faults: tuple[int | str, ...],
) -> None:
    bucket.fail("PUT", *faults)
    store, mine = remote("runner-1"), _unit("a")
    assert same_value(store.put(mine), mine)
    assert bucket.puts() == len(faults) + 1
    assert len(sleeps) == len(faults)
    assert sleeps == sorted(sleeps), "backoff does not shrink"


def test_retries_exhausted_stop_with_nothing_stored_locally(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore]
) -> None:
    bucket.fail("PUT", 503, 429, r2_fakes.TIMEOUT)
    store, mine = remote("runner-1"), _unit("a")
    with pytest.raises(EmbeddingCacheStorageError, match="3 attempts"):
        store.put(mine)
    assert bucket.puts() == 3
    assert _local_bytes(store, mine.key) is None


@pytest.mark.parametrize("status", [400, 403, 404])
def test_a_refused_put_is_a_storage_error_without_retry(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore], status: int
) -> None:
    bucket.fail("PUT", status)
    store, mine = remote("runner-1"), _unit("a")
    with pytest.raises(EmbeddingCacheStorageError, match=f"status {status}"):
        store.put(mine)
    assert bucket.puts() == 1
    assert _local_bytes(store, mine.key) is None


def test_remote_success_then_local_failure_stops_and_a_fresh_runner_reads_the_bucket(
    bucket: r2_fakes.FakeBucket,
    remote: Callable[[str], RemoteEntryStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mine = _unit("a")

    def disk_full(directory: int, name: str, payload: bytes) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr(cache_store, "_publish", disk_full)
        with pytest.raises(EmbeddingCacheStorageError, match="No space left"):
            remote("runner-1").put(mine)
    assert remote_key(mine.key) in bucket.objects

    fresh = remote("runner-2")
    stored = fresh.get(mine.key)
    assert stored is not None
    assert same_value(stored, mine)
    assert _local_bytes(fresh, mine.key) == encode_entry(mine)
    assert bucket.puts() == 1


# --- lookup table ------------------------------------------------------------


def test_remote_valid_local_absent_publishes_the_remote_value(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore]
) -> None:
    entry = _doc("a")
    bucket.objects[remote_key(entry.key)] = encode_entry(entry)
    store = remote("runner-1")
    stored = store.get(entry.key)
    assert stored is not None
    assert same_value(stored, entry)
    assert _local_bytes(store, entry.key) == encode_entry(entry)
    assert bucket.puts() == 0


def test_remote_valid_local_same_value_is_used(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore]
) -> None:
    store, entry = remote("runner-1"), _doc("a")
    store.put(entry)
    stored = store.get(entry.key)
    assert stored is not None
    assert same_value(stored, entry)
    assert bucket.puts() == 1


def test_remote_and_local_different_values_is_a_conflict_and_changes_neither(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore]
) -> None:
    store, mine = remote("runner-1"), _doc("a")
    store.local.put(mine)
    bucket.objects[remote_key(mine.key)] = encode_entry(_other_value(mine))
    before = (_local_bytes(store, mine.key), bucket.objects[remote_key(mine.key)])

    with pytest.raises(EmbeddingCacheConflictError, match=mine.key.label()):
        store.get(mine.key)

    assert (_local_bytes(store, mine.key), bucket.objects[remote_key(mine.key)]) == before


def test_remote_valid_local_invalid_is_the_local_refusal(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore]
) -> None:
    store, entry = remote("runner-1"), _doc("a")
    store.put(entry)
    parts, name = entry_location(entry.key)
    path = store.local.root.joinpath(*parts, name)
    path.chmod(0o600)
    path.write_bytes(_corrupt(entry))
    with pytest.raises(EmbeddingCacheError) as refused:
        store.get(entry.key)
    assert "remote" not in str(refused.value)
    assert path.read_bytes() == _corrupt(entry), "local evidence kept"


@pytest.mark.parametrize("plant", _INVALID.values(), ids=_INVALID.keys())
def test_remote_invalid_is_refused_whatever_the_local_copy(
    bucket: r2_fakes.FakeBucket,
    remote: Callable[[str], RemoteEntryStore],
    plant: Callable[[CachedVectors], bytes],
) -> None:
    store, entry = remote("runner-1"), _doc("a")
    store.local.put(entry)
    bucket.objects[remote_key(entry.key)] = plant(entry)
    with pytest.raises(EmbeddingCacheError, match="remote entry refused"):
        store.get(entry.key)
    assert bucket.objects[remote_key(entry.key)] == plant(entry)


def test_remote_absent_local_valid_uploads_without_a_provider(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore]
) -> None:
    store, entry = remote("runner-1"), _unit("a")
    store.local.put(entry)
    stored = store.get(entry.key)
    assert stored is not None
    assert same_value(stored, entry)
    assert remote_key(entry.key) in bucket.objects


def test_remote_absent_local_valid_but_another_writer_wins_the_upload_is_a_conflict(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore]
) -> None:
    store, mine = remote("runner-1"), _unit("a")
    store.local.put(mine)
    winner = _other_value(mine)

    def other_writer_commits(key: str) -> None:
        bucket.objects.setdefault(key, encode_entry(winner))

    bucket.before_put = other_writer_commits
    with pytest.raises(EmbeddingCacheConflictError):
        store.get(mine.key)
    assert _local_bytes(store, mine.key) == encode_entry(mine)


def test_remote_absent_local_absent_is_a_miss(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore]
) -> None:
    assert remote("runner-1").get(_doc("a").key) is None
    assert bucket.puts() == 0


def test_remote_absent_local_invalid_is_the_local_refusal(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore]
) -> None:
    store, entry = remote("runner-1"), _doc("a")
    store.local.put(_doc("a", created_at=NOW - timedelta(days=40)))
    with pytest.raises(EmbeddingCacheExpiredError):
        store.get(entry.key)
    assert bucket.puts() == 0


@pytest.mark.parametrize("faults", [(503, 503, 503), (429, r2_fakes.TIMEOUT, r2_fakes.DROP)])
def test_an_unreadable_bucket_is_a_storage_error_never_a_miss(
    bucket: r2_fakes.FakeBucket,
    remote: Callable[[str], RemoteEntryStore],
    faults: tuple[int | str, ...],
) -> None:
    bucket.fail("GET", *faults)
    with pytest.raises(EmbeddingCacheStorageError, match="3 attempts"):
        remote("runner-1").get(_doc("a").key)


def test_a_transient_get_failure_is_retried(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore]
) -> None:
    entry = _doc("a")
    bucket.objects[remote_key(entry.key)] = encode_entry(entry)
    bucket.fail("GET", 503)
    assert remote("runner-1").get(entry.key) is not None


@pytest.mark.parametrize("status", [400, 403])
def test_a_refused_get_is_a_storage_error(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore], status: int
) -> None:
    bucket.fail("GET", status)
    with pytest.raises(EmbeddingCacheStorageError, match=f"status {status}"):
        remote("runner-1").get(_doc("a").key)


def test_errors_never_carry_the_endpoint_or_credentials(
    bucket: r2_fakes.FakeBucket, remote: Callable[[str], RemoteEntryStore]
) -> None:
    bucket.fail("PUT", r2_fakes.TIMEOUT, r2_fakes.TIMEOUT, r2_fakes.TIMEOUT)
    with pytest.raises(EmbeddingCacheStorageError) as refused:
        remote("runner-1").put(_unit(_hash("x")))
    shown = f"{refused.value!s} {refused.value!r} {refused.value.__cause__!r}"
    for secret in (r2_fakes.ACCOUNT_ID, r2_fakes.KEY_ID, r2_fakes.SECRET, "cloudflarestorage"):
        assert secret not in shown
    assert refused.value.__cause__ is None


@pytest.mark.parametrize("operation", ["get", "put"])
def test_a_local_writer_racing_remote_adoption_is_a_conflict_without_overwrite(
    bucket: r2_fakes.FakeBucket,
    remote: Callable[[str], RemoteEntryStore],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """The no-overwrite contract also holds after the initial local lookup."""
    store, winner = remote("runner-1"), _unit("a")
    local_winner = _other_value(winner)
    payload = encode_entry(winner)
    bucket.objects[remote_key(winner.key)] = payload
    publish = store.local.publish

    def racing_publish(key: EntryKey, data: bytes) -> CachedVectors:
        store.local.put(local_winner)
        return publish(key, data)

    monkeypatch.setattr(store.local, "publish", racing_publish)
    with pytest.raises(EmbeddingCacheConflictError):
        if operation == "get":
            store.get(winner.key)
        else:
            store.put(winner)

    assert _local_bytes(store, winner.key) == encode_entry(local_winner)
    assert bucket.objects[remote_key(winner.key)] == payload
    assert bucket.calls == (
        [("GET", remote_key(winner.key))]
        if operation == "get"
        else [("PUT", remote_key(winner.key)), ("GET", remote_key(winner.key))]
    )


@pytest.mark.parametrize("plant", _INVALID.values(), ids=_INVALID.keys())
@pytest.mark.parametrize("existing", [False, True], ids=["absent", "existing"])
def test_local_publish_validates_downloaded_bytes_before_writing(
    remote: Callable[[str], RemoteEntryStore],
    plant: Callable[[CachedVectors], bytes],
    existing: bool,
) -> None:
    """publish() must validate even when a previously stored entry could be returned."""
    store, entry = remote("runner-1"), _doc("a")
    if existing:
        store.local.put(entry)
    before = _local_bytes(store, entry.key)

    with pytest.raises(EmbeddingCacheError):
        store.local.publish(entry.key, plant(entry))

    assert _local_bytes(store, entry.key) == before
    assert not list(store.local.root.rglob("*.tmp.*"))


@pytest.mark.parametrize("status", [403, 503])
def test_an_unreadable_remote_never_falls_back_to_a_valid_local_copy(
    bucket: r2_fakes.FakeBucket,
    remote: Callable[[str], RemoteEntryStore],
    sleeps: Sleeps,
    status: int,
) -> None:
    """An unreachable authority is a refusal even on a runner with warm local state."""
    store, entry = remote("runner-1"), _doc("a")
    store.local.put(entry)
    attempts = 1 if status == 403 else 3
    bucket.fail("GET", *([status] * attempts))

    with pytest.raises(EmbeddingCacheStorageError):
        store.get(entry.key)

    assert bucket.calls == [("GET", remote_key(entry.key))] * attempts
    assert sleeps == ([] if status == 403 else [1.0, 2.0])
    assert _local_bytes(store, entry.key) == encode_entry(entry)
    assert bucket.objects == {}
