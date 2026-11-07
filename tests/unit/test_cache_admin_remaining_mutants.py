"""Behavioral attempts for the five remaining cache-admin survivors."""

from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings import cache_admin
from lovspor.embeddings.cache_format import CachedVectors
from lovspor.embeddings.cache_store import LocalEntryStore
from lovspor.errors import EmbeddingCacheError
from tests.unit.test_embedding_cache_admin import POLICY, _doc, _unit


@pytest.mark.parametrize(("dim", "rows"), [(1, 1), (2, 1), (1, 2), (4, 3)])
def test_prune_superseded_checkpoint_with_document_shapes(tmp_path, dim, rows) -> None:
    store = LocalEntryStore.open(tmp_path / "cache", (), POLICY)
    document = _doc("finished")
    document = CachedVectors(
        key=document.key.model_copy(update={"dim": dim, "rows": rows}),
        vectors=np.ones((rows, dim), dtype=np.int8),
        scale=document.scale,
        created_at=document.created_at,
    )
    checkpoint = _unit("finished")
    pending = _unit("pending")
    for entry in (document, checkpoint, pending):
        store.put(entry)

    report = cache_admin.prune(store)

    assert report.superseded_units == 1
    assert report.refused == []
    assert store.get(checkpoint.key) is None
    assert store.get(pending.key) is not None
    restored = store.get(document.key)
    assert restored is not None
    np.testing.assert_array_equal(restored.vectors, document.vectors)


@pytest.mark.parametrize("walker", ["_subdirs", "_all_dirs", "_entry_files"])
@pytest.mark.parametrize("target_exists", [False, True])
def test_walk_refuses_directory_links_even_when_target_exists(
    tmp_path: Path, walker: str, target_exists: bool
) -> None:
    target = tmp_path / "target"
    if target_exists:
        target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(EmbeddingCacheError, match="refuses a symlink: link"):
        list(getattr(cache_admin, walker)(link))

    assert link.is_symlink()
    assert target.exists() == target_exists


@pytest.mark.parametrize("walker", ["_subdirs", "_all_dirs", "_entry_files"])
def test_walk_missing_directory_is_empty(tmp_path: Path, walker: str) -> None:
    missing = tmp_path / "missing"
    assert list(getattr(cache_admin, walker)(missing)) == []
    assert not missing.exists()
