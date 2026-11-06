"""Local durable store of the embedding cache (#802, design §5.1 and §5.2).

Immutable, idempotent entries outside both repositories; symlinks and
foreign permissions refused; writes are temp + fsync + link, so a second
writer can never replace the first.
"""

import os
import stat
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
from lovspor.errors import EmbeddingCacheError, EmbeddingCacheStorageError

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
