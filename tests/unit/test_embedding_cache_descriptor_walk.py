"""The store's descriptor walk through get and put: refusals by name, a vanished root (#825)."""

import shutil
import stat
from pathlib import Path

import pytest

from lovspor.embeddings.cache_format import encode_entry
from lovspor.embeddings.cache_store import entry_location
from lovspor.errors import EmbeddingCacheError, EmbeddingCacheStorageError
from tests.unit.test_embedding_cache_store import _entry, _key, _open, _unit


def _space(root: Path) -> Path:
    parts, _name = entry_location(_key())
    return root.joinpath(*parts[:2])


@pytest.mark.parametrize("operation", ["get", "put"])
def test_a_directory_swapped_for_a_symlink_is_refused_by_name(
    tmp_path: Path, operation: str
) -> None:
    root = tmp_path / "cache"
    store = _open(root)
    store.put(_entry())
    space = _space(root)
    space.rename(tmp_path / "moved")
    space.symlink_to(tmp_path / "moved", target_is_directory=True)

    with pytest.raises(EmbeddingCacheError) as refused:
        store.get(_key()) if operation == "get" else store.put(_unit())
    assert str(refused.value) == f"embedding cache refuses a symlink: {space.name}"


@pytest.mark.parametrize("operation", ["get", "put"])
def test_a_directory_swapped_for_a_file_is_refused_by_name(tmp_path: Path, operation: str) -> None:
    root = tmp_path / "cache"
    store = _open(root)
    store.put(_entry())
    space = _space(root)
    shutil.rmtree(space)
    space.write_bytes(b"not a directory")

    with pytest.raises(EmbeddingCacheError) as refused:
        store.get(_key()) if operation == "get" else store.put(_unit())
    assert str(refused.value) == f"embedding cache path is not a directory: {space.name}"


@pytest.mark.parametrize("operation", ["get", "put"])
def test_a_root_removed_after_open_is_a_storage_error_not_a_miss(
    tmp_path: Path, operation: str
) -> None:
    root = tmp_path / "cache"
    store = _open(root)
    root.rmdir()

    with pytest.raises(EmbeddingCacheStorageError, match="root is gone"):
        store.get(_key()) if operation == "get" else store.put(_unit())
    assert not root.exists()


@pytest.mark.parametrize("depth", [1, 2, 3, 4])
def test_missing_unit_directory_is_a_read_only_miss_then_recreated_on_write(
    tmp_path: Path, depth: int
) -> None:
    """get's absent-entry contract applies at every step of the descriptor walk."""
    root = tmp_path / "cache"
    store = _open(root)
    entry = _unit()
    store.put(entry)
    parts, name = entry_location(entry.key)
    missing = root.joinpath(*parts[:depth])
    shutil.rmtree(missing)
    before = set(root.rglob("*"))

    assert store.get(entry.key) is None
    assert set(root.rglob("*")) == before
    assert not missing.exists()

    store.put(entry)

    target = root.joinpath(*parts, name)
    assert target.read_bytes() == encode_entry(entry)
    for level in range(1, len(parts) + 1):
        assert stat.S_IMODE(root.joinpath(*parts[:level]).stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(root.rglob("*.tmp.*"))
    found = _open(root).get(entry.key)
    assert found is not None
    assert encode_entry(found) == encode_entry(entry)
