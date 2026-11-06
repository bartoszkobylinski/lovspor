"""Local durable store of the embedding cache (#802, design §5.1 and §5.2).

Immutable, idempotent entries outside both repositories; symlinks and
foreign permissions refused; writes are temp + fsync + link, so a second
writer can never replace the first.
"""

import errno
import os
import stat
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.cache_format import (
    CachedVectors,
    EntryKey,
    ReadPolicy,
    UnitKey,
    encode_entry,
)
from lovspor.embeddings.cache_store import LocalEntryStore
from lovspor.embeddings.inputs import EmbeddingInput, hash_embedding_inputs
from lovspor.errors import (
    EmbeddingCacheError,
    EmbeddingCacheExpiredError,
    EmbeddingCacheStorageError,
)

ESI = "0123456789abcdef0123456789abcdef"
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
INPUT_HASH = hash_embedding_inputs([EmbeddingInput("§ 1", "tekst")])
UNIT_HASH = hash_embedding_inputs([EmbeddingInput("§ 1", "del")])
POLICY = ReadPolicy(max_age=timedelta(days=30), clock=lambda: NOW)


def _key() -> EntryKey:
    return EntryKey(esi=ESI, input_hash=INPUT_HASH, dim=4, rows=1)


def _unit_key(index: int = 0) -> EntryKey:
    unit = UnitKey(index=index, count=2, input_hash=UNIT_HASH)
    return EntryKey(esi=ESI, input_hash=INPUT_HASH, dim=4, rows=1, unit=unit)


def _entry(value: int = 5) -> CachedVectors:
    vectors = np.array([[value, 0, -1, 127]], dtype=np.int8)
    return CachedVectors(key=_key(), vectors=vectors, scale=0.01, created_at=NOW)


def _unit(index: int = 0) -> CachedVectors:
    vectors = np.array([[0.5, 0.5, 0.5, 0.5]], dtype=np.float32)
    return CachedVectors(key=_unit_key(index), vectors=vectors, scale=None, created_at=NOW)


@pytest.fixture
def cache_root(tmp_path: Path) -> Path:
    root = tmp_path / "cache"
    root.mkdir(mode=0o700)
    return root


def _open(root: Path, forbidden: tuple[Path, ...] = ()) -> LocalEntryStore:
    return LocalEntryStore.open(root, forbidden, POLICY)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def test_absent_entry_is_a_miss(cache_root: Path) -> None:
    assert _open(cache_root).get(_key()) is None


def test_put_then_get_returns_the_entry(cache_root: Path) -> None:
    store = _open(cache_root)
    store.put(_entry())
    found = store.get(_key())
    assert found is not None
    assert np.array_equal(found.vectors, _entry().vectors)


def test_entry_survives_a_new_store_object(cache_root: Path) -> None:
    _open(cache_root).put(_entry())
    assert _open(cache_root).get(_key()) is not None


def test_files_are_owner_only(cache_root: Path) -> None:
    store = _open(cache_root)
    store.put(_entry())
    store.put(_unit())
    files = [p for p in cache_root.rglob("*") if p.is_file()]
    dirs = [p for p in cache_root.rglob("*") if p.is_dir()]
    assert files
    assert all(_mode(p) == 0o600 for p in files)
    assert all(_mode(p) == 0o700 for p in dirs)


def test_no_temporary_file_is_left_behind(cache_root: Path) -> None:
    store = _open(cache_root)
    store.put(_entry())
    assert not [p for p in cache_root.rglob("*") if ".tmp." in p.name]


def test_second_writer_never_replaces_the_first(cache_root: Path) -> None:
    """Provider output is not bit-reproducible; one key must mean one value."""
    store = _open(cache_root)
    store.put(_entry(value=5))
    returned = _open(cache_root).put(_entry(value=9))
    assert int(returned.vectors[0, 0]) == 5
    found = store.get(_key())
    assert found is not None
    assert int(found.vectors[0, 0]) == 5


def test_put_over_a_corrupt_entry_refuses_instead_of_overwriting(cache_root: Path) -> None:
    store = _open(cache_root)
    store.put(_entry())
    target = next(p for p in cache_root.rglob("*.lsec"))
    target.write_bytes(b"garbage")
    with pytest.raises(EmbeddingCacheError):
        store.put(_entry())
    assert target.read_bytes() == b"garbage"


def test_corrupt_entry_is_refused_on_get(cache_root: Path) -> None:
    store = _open(cache_root)
    store.put(_entry())
    target = next(p for p in cache_root.rglob("*.lsec"))
    data = bytearray(target.read_bytes())
    data[-40] ^= 0xFF
    target.write_bytes(bytes(data))
    with pytest.raises(EmbeddingCacheError, match="digest"):
        store.get(_key())


def test_symlinked_entry_is_refused(cache_root: Path, tmp_path: Path) -> None:
    store = _open(cache_root)
    store.put(_entry())
    target = next(p for p in cache_root.rglob("*.lsec"))
    elsewhere = tmp_path / "elsewhere.lsec"
    elsewhere.write_bytes(target.read_bytes())
    elsewhere.chmod(0o600)
    target.unlink()
    target.symlink_to(elsewhere)
    with pytest.raises(EmbeddingCacheError, match="symlink"):
        store.get(_key())


def test_symlinked_directory_inside_the_store_is_refused(cache_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (cache_root / "schema-1").symlink_to(outside)
    with pytest.raises(EmbeddingCacheError, match="symlink"):
        _open(cache_root).put(_entry())
    assert not any(outside.iterdir())


def test_symlinked_root_is_refused(cache_root: Path, tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(cache_root)
    with pytest.raises(EmbeddingCacheError, match="symlink"):
        _open(link)


def test_relative_root_is_refused() -> None:
    with pytest.raises(EmbeddingCacheError, match="absolute"):
        _open(Path("relative/cache"))


def test_root_inside_a_forbidden_tree_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "lovverk"
    root = repo / "data" / "cache"
    root.mkdir(parents=True, mode=0o700)
    with pytest.raises(EmbeddingCacheError, match="inside"):
        _open(root, forbidden=(repo,))


def test_root_equal_to_a_forbidden_tree_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "lovverk"
    repo.mkdir(mode=0o700)
    with pytest.raises(EmbeddingCacheError, match="inside"):
        _open(repo, forbidden=(repo,))


def test_forbidden_tree_reached_through_a_symlinked_parent_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "lovverk"
    (repo / "c").mkdir(parents=True, mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(repo)
    with pytest.raises(EmbeddingCacheError, match="inside"):
        _open(alias / "c", forbidden=(repo,))


def test_missing_root_is_created_owner_only(tmp_path: Path) -> None:
    root = tmp_path / "new-cache"
    _open(root).put(_entry())
    assert _mode(root) == 0o700


def test_group_readable_root_is_refused(cache_root: Path) -> None:
    cache_root.chmod(0o750)
    with pytest.raises(EmbeddingCacheError, match="permission"):
        _open(cache_root)


def test_group_readable_entry_is_refused(cache_root: Path) -> None:
    store = _open(cache_root)
    store.put(_entry())
    next(p for p in cache_root.rglob("*.lsec")).chmod(0o640)
    with pytest.raises(EmbeddingCacheError, match="permission"):
        store.get(_key())


def test_root_that_is_a_file_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "file"
    root.write_bytes(b"")
    with pytest.raises(EmbeddingCacheError, match="directory"):
        _open(root)


def test_unit_entries_are_kept_apart_per_unit(cache_root: Path) -> None:
    store = _open(cache_root)
    store.put(_unit(0))
    assert store.get(_unit_key(0)) is not None
    assert store.get(_unit_key(1)) is None


def test_document_and_unit_keys_never_share_a_file(cache_root: Path) -> None:
    store = _open(cache_root)
    store.put(_unit(0))
    assert store.get(_key()) is None


def test_entry_moved_under_another_key_is_refused(cache_root: Path) -> None:
    store = _open(cache_root)
    store.put(_unit(0))
    source = next(p for p in cache_root.rglob("*.lsec"))
    other = source.with_name(source.name.replace("0-2-", "1-2-", 1))
    os.link(source, other)
    with pytest.raises(EmbeddingCacheError, match="unit"):
        store.get(_unit_key(1))


def test_failed_write_is_a_typed_storage_error(cache_root: Path) -> None:
    store = _open(cache_root)
    store.put(_unit(0))
    unit_dir = next(p for p in cache_root.rglob("*.lsec")).parent
    unit_dir.chmod(0o500)
    try:
        with pytest.raises(EmbeddingCacheStorageError):
            store.put(_unit(1))
    finally:
        unit_dir.chmod(0o700)


def test_stray_temporary_file_is_never_read(cache_root: Path) -> None:
    store = _open(cache_root)
    store.put(_entry())
    target = next(p for p in cache_root.rglob("*.lsec"))
    target.rename(target.with_name(f".{target.name}.tmp.1.x"))
    assert store.get(_key()) is None


def test_written_bytes_are_the_canonical_encoding(cache_root: Path) -> None:
    _open(cache_root).put(_entry())
    target = next(p for p in cache_root.rglob("*.lsec"))
    assert target.read_bytes() == encode_entry(_entry())


def test_directory_at_an_entry_path_is_a_typed_refusal(cache_root: Path) -> None:
    """Linux opens a directory read-only; the refusal must still be typed (#825)."""
    store = _open(cache_root)
    store.put(_entry())
    target = next(cache_root.rglob("*.lsec"))
    target.unlink()
    target.mkdir(mode=0o700)
    with pytest.raises(EmbeddingCacheError, match="regular file"):
        store.get(_key())


def test_fifo_at_an_entry_path_is_refused_without_blocking(cache_root: Path) -> None:
    """A FIFO would block a plain read-only open forever."""
    store = _open(cache_root)
    store.put(_entry())
    target = next(cache_root.rglob("*.lsec"))
    target.unlink()
    os.mkfifo(target, 0o600)
    with pytest.raises(EmbeddingCacheError, match="regular file"):
        store.get(_key())


@pytest.mark.parametrize("operation", ["fsync", "link"])
def test_failed_publication_leaves_no_entry_and_can_be_retried(
    cache_root: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Design §5.2: a pre-publication failure cannot expose partial vectors."""
    store = _open(cache_root)

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "injected disk full")

    with monkeypatch.context() as patch:
        patch.setattr(os, operation, fail)
        with pytest.raises(EmbeddingCacheStorageError, match="disk full"):
            store.put(_entry())
    assert store.get(_key()) is None
    assert not list(cache_root.rglob("*.lsec"))
    assert not [path for path in cache_root.rglob("*") if ".tmp." in path.name]
    store.put(_entry())
    found = store.get(_key())
    assert found is not None
    assert np.array_equal(found.vectors, _entry().vectors)


def test_temporary_file_is_created_owner_only_before_any_chmod(
    cache_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design §5.1: the entry is 0600 from creation, not only after fchmod.

    With a permissive umask, a temporary file created with the default mode
    would be world-readable until the fchmod that follows.
    """
    created: list[int] = []
    real_fchmod = os.fchmod

    def record(descriptor: int, mode: int) -> None:
        created.append(stat.S_IMODE(os.fstat(descriptor).st_mode))
        real_fchmod(descriptor, mode)

    store = _open(cache_root)
    monkeypatch.setattr(os, "fchmod", record)
    previous = os.umask(0)
    try:
        store.put(_entry())
    finally:
        os.umask(previous)
    assert created == [0o600]


def test_temporary_swapped_for_a_symlink_is_never_published_as_its_target(
    cache_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design §5.1: linking into place never follows a symlink.

    If the temporary name is swapped for a symlink before the link, the
    published name is that symlink, which every read refuses, and never a
    hard link to the file it points at.
    """
    outside = tmp_path / "outside.lsec"
    outside.write_bytes(encode_entry(_entry(99)))
    outside.chmod(0o600)
    real_fsync = os.fsync
    swapped: list[Path] = []

    def swap_then_fsync(descriptor: int) -> None:
        real_fsync(descriptor)
        for staged in cache_root.rglob("*.tmp.*"):
            if not swapped:
                staged.unlink()
                staged.symlink_to(outside)
                swapped.append(staged)

    store = _open(cache_root)
    monkeypatch.setattr(os, "fsync", swap_then_fsync)
    store.put(_entry())
    monkeypatch.setattr(os, "fsync", real_fsync)
    assert swapped
    with pytest.raises(EmbeddingCacheError, match="symlink"):
        store.get(_key())


def test_temporary_already_removed_after_publishing_does_not_fail_the_write(
    cache_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design §5.2: a temporary removed by someone else (a concurrent prune)
    once the entry is linked in place is not an error: the entry is durable."""
    real_link = os.link

    def link_then_lose_temporary(src: Path, dst: Path, **kwargs: bool) -> None:
        real_link(src, dst, **kwargs)
        Path(src).unlink()

    store = _open(cache_root)
    monkeypatch.setattr(os, "link", link_then_lose_temporary)
    store.put(_entry())
    monkeypatch.setattr(os, "link", real_link)
    stored = store.get(_key())
    assert stored is not None
    np.testing.assert_array_equal(stored.vectors, _entry().vectors)


def test_directory_fsync_failure_reports_error_without_replacing_published_vectors(
    cache_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design §5.2: publication is immutable, and a durability failure stops the run."""
    store = _open(cache_root)
    real_fsync = os.fsync
    synced: list[str] = []

    def fail_directory_sync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            synced.append("directory")
            raise OSError(errno.EIO, "injected directory sync failure")
        synced.append("file")
        real_fsync(descriptor)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail_directory_sync)
        with pytest.raises(EmbeddingCacheStorageError, match="directory sync failure"):
            store.put(_entry(value=5))

    assert synced == ["file", "directory"]
    targets = list(cache_root.rglob("*.lsec"))
    assert len(targets) == 1
    assert targets[0].read_bytes() == encode_entry(_entry(value=5))
    assert not [path for path in cache_root.rglob("*") if ".tmp." in path.name]
    returned = _open(cache_root).put(_entry(value=9))
    np.testing.assert_array_equal(returned.vectors, _entry(value=5).vectors)
    assert targets[0].read_bytes() == encode_entry(_entry(value=5))


def test_second_writer_refuses_expired_entry_without_refreshing_it(cache_root: Path) -> None:
    """Design §5.3: an expired entry is a refusal, including a publication collision."""
    store = _open(cache_root)
    expired = replace(_entry(value=5), created_at=NOW - timedelta(days=31))
    store.put(expired)
    target = next(cache_root.rglob("*.lsec"))
    original = target.read_bytes()

    with pytest.raises(EmbeddingCacheExpiredError, match="expired"):
        _open(cache_root).put(_entry(value=9))

    assert target.read_bytes() == original
    assert not [path for path in cache_root.rglob("*") if ".tmp." in path.name]
    with pytest.raises(EmbeddingCacheExpiredError):
        store.get(_key())
