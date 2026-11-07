"""The root symlink refusal in cache design §5.1 applies after startup too."""

import stat
from pathlib import Path

import pytest

from lovspor.errors import EmbeddingCacheError
from tests.unit.test_embedding_cache_store import _entry, _key, _open, _unit


@pytest.mark.parametrize("operation", ["get", "put"])
def test_open_store_refuses_root_replaced_with_symlink(tmp_path: Path, operation: str) -> None:
    """§5.1: 'The root ... must not be a symlink'.

    This is a contract violation probe, not a proposed stronger contract:
    replacing the root must not redirect an already-open store's reads or
    writes into the symlink target, even when its descendants remain private.
    """
    root = tmp_path / "cache"
    store = _open(root)
    store.put(_entry())
    relocated = tmp_path / "relocated"
    root.rename(relocated)
    root.symlink_to(relocated, target_is_directory=True)
    before = {path.relative_to(relocated): path.read_bytes() for path in relocated.rglob("*.lsec")}

    with pytest.raises(EmbeddingCacheError, match="symlink"):
        if operation == "get":
            store.get(_key())
        else:
            store.put(_unit())

    assert root.is_symlink()
    assert {
        path.relative_to(relocated): path.read_bytes() for path in relocated.rglob("*.lsec")
    } == before


@pytest.mark.parametrize("operation", ["get", "put"])
@pytest.mark.parametrize("mode", [0o710, 0o704])
def test_open_store_refuses_root_permissions_changed_after_open(
    tmp_path: Path, operation: str, mode: int
) -> None:
    """The store's owner-only directory contract also applies after open()."""
    root = tmp_path / "cache"
    store = _open(root)
    store.put(_entry())
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*.lsec")}
    root.chmod(mode)

    with pytest.raises(EmbeddingCacheError, match="owner-only"):
        if operation == "get":
            store.get(_key())
        else:
            store.put(_unit())

    assert stat.S_IMODE(root.stat().st_mode) == mode
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*.lsec")} == before
    assert not list(root.rglob("*.tmp.*"))


@pytest.mark.parametrize("operation", ["get", "put"])
def test_open_store_refuses_root_replaced_with_regular_file(tmp_path: Path, operation: str) -> None:
    """A non-directory root is a refusal, including when a read would miss."""
    root = tmp_path / "cache"
    store = _open(root)
    store.put(_entry())
    relocated = tmp_path / "relocated"
    root.rename(relocated)
    root.write_bytes(b"replacement root")
    before = {
        path.relative_to(relocated): path.read_bytes()
        for path in relocated.rglob("*")
        if path.is_file()
    }

    with pytest.raises(EmbeddingCacheError, match="not a directory"):
        if operation == "get":
            store.get(_unit().key)
        else:
            store.put(_unit())

    assert root.read_bytes() == b"replacement root"
    assert {
        path.relative_to(relocated): path.read_bytes()
        for path in relocated.rglob("*")
        if path.is_file()
    } == before
