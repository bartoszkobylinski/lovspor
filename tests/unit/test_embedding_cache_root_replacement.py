"""The root symlink refusal in cache design §5.1 applies after startup too."""

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
