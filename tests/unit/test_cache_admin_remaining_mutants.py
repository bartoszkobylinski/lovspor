"""Behavioral attempts for the five remaining cache-admin survivors."""

from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings import cache_admin
from lovspor.embeddings.cache_format import CachedVectors
from lovspor.embeddings.cache_store import LocalEntryStore
from lovspor.errors import EmbeddingCacheError
from tests.unit.test_embedding_cache_admin import POLICY, _doc, _hash, _unit


def _store_with_document_of_shape(
    tmp_path, dim: int, rows: int
) -> tuple[LocalEntryStore, CachedVectors, CachedVectors, CachedVectors]:
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
    return store, document, checkpoint, pending


@pytest.mark.parametrize("rows", [1, 3])
def test_prune_superseded_checkpoint_with_document_shapes(tmp_path, rows) -> None:
    """A valid document of the units' dimension (4) supersedes them, whatever its rows."""
    store, document, checkpoint, pending = _store_with_document_of_shape(tmp_path, 4, rows)

    report = cache_admin.prune(store)

    assert report.superseded_units == 1
    assert report.refused == []
    assert store.get(checkpoint.key) is None
    assert store.get(pending.key) is not None
    restored = store.get(document.key)
    assert restored is not None
    np.testing.assert_array_equal(restored.vectors, document.vectors)


@pytest.mark.parametrize(("dim", "rows"), [(1, 1), (2, 1), (1, 2)])
def test_prune_keeps_checkpoints_behind_a_document_of_another_dimension(
    tmp_path, dim, rows
) -> None:
    """GPT-6 review on #827: a document of another dimension supersedes nothing."""
    store, document, checkpoint, pending = _store_with_document_of_shape(tmp_path, dim, rows)

    report = cache_admin.prune(store)

    assert report.superseded_units == 0
    assert len(report.refused) == 1
    assert "dimension" in report.refused[0]
    assert store.get(checkpoint.key) is not None
    assert store.get(pending.key) is not None
    assert store.get(document.key) is not None


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


def test_prune_continues_after_a_checkpoint_without_a_document(tmp_path: Path) -> None:
    """An unfinished document must not prevent pruning a later finished one."""
    store = LocalEntryStore.open(tmp_path / "cache", (), POLICY)
    pending_text, finished_text = sorted(("first", "second"), key=_hash)
    pending = _unit(pending_text)
    finished = _unit(finished_text)
    document = _doc(finished_text)
    for entry in (pending, finished, document):
        store.put(entry)

    report = cache_admin.prune(store)

    assert (report.superseded_units, report.refused) == (1, [])
    assert store.get(pending.key) is not None
    assert store.get(finished.key) is None
    assert store.get(document.key) is not None


def test_prune_reports_one_dimension_refusal_per_document_with_key_prefixes(
    tmp_path: Path,
) -> None:
    """Keep paid checkpoints; report each mismatched document once with safe prefixes."""
    store, document, checkpoint, pending = _store_with_document_of_shape(tmp_path, 2, 1)
    second_checkpoint = _unit("finished", 1)
    store.put(second_checkpoint)

    report = cache_admin.prune(store)

    assert report.refused == [
        "embedding cache entry refused (document esi=01234567 "
        f"input_hash={document.key.input_hash[:12]}): dimension 2 differs from "
        "its unit checkpoints' 4; the checkpoints are kept"
    ]
    assert report.superseded_units == 0
    for entry in (document, checkpoint, second_checkpoint, pending):
        assert store.get(entry.key) is not None
