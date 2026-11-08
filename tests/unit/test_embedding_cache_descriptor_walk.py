"""The store's descriptor walk through get and put: refusals by name, a vanished root (#825)."""

import shutil
from pathlib import Path

import pytest

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
