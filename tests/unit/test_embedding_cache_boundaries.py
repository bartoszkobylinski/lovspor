"""Remaining payload and identity boundaries from cache design §4 and §5.3."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.cache_format import decode_entry, encode_entry
from lovspor.errors import EmbeddingCacheError
from tests.unit.test_embedding_cache_format import (
    INPUT_HASH,
    _doc_entry,
    _policy,
    _reseal,
    _unit_entry,
)
from tests.unit.test_embedding_cache_store import _open


@pytest.mark.parametrize("unit", [False, True])
@pytest.mark.parametrize("size_delta", [-1, 1, 4])
def test_payload_length_must_be_exact_even_with_valid_digest(unit: bool, size_delta: int) -> None:
    """§5.3 requires exactly rows * dim * itemsize, not merely enough bytes."""
    entry = _unit_entry() if unit else _doc_entry()
    body = encode_entry(entry)[:-32]
    altered = body[:size_delta] if size_delta < 0 else body + b"\0" * size_delta
    with pytest.raises(EmbeddingCacheError, match="payload"):
        decode_entry(_reseal(altered), entry.key, _policy())


@pytest.mark.parametrize("field", ["count", "input_hash"])
def test_unit_count_and_slice_hash_have_independent_storage_paths(
    tmp_path: Path, field: str
) -> None:
    """§4 keys include both the unit count and the hash of its own input slice."""
    first = _unit_entry()
    assert first.key.unit is not None
    changes = {"count": 3} if field == "count" else {"input_hash": INPUT_HASH}
    other_unit = first.key.unit.model_copy(update=changes)
    second = replace(
        first,
        key=first.key.model_copy(update={"unit": other_unit}),
        vectors=-first.vectors,
    )
    root = tmp_path / "cache"
    store = _open(root)
    store.put(first)
    assert store.get(second.key) is None
    store.put(second)

    reopened = _open(root)
    for expected in (first, second):
        found = reopened.get(expected.key)
        assert found is not None
        np.testing.assert_array_equal(found.vectors, expected.vectors)
    assert len(list(root.rglob("*.lsec"))) == 2


@pytest.mark.parametrize("unit", [False, True])
def test_mutating_returned_vectors_cannot_change_persisted_entry(
    tmp_path: Path, unit: bool
) -> None:
    """§5.2's immutable published value survives caller-owned array mutations."""
    entry = _unit_entry() if unit else _doc_entry()
    expected = entry.vectors.copy()
    root = tmp_path / "cache"
    store = _open(root)
    returned = store.put(entry)
    target = next(root.rglob("*.lsec"))
    original = target.read_bytes()
    returned.vectors[:] = 0
    found = store.get(entry.key)
    assert found is not None
    np.testing.assert_array_equal(found.vectors, expected)
    found.vectors[:] = 0
    reread = _open(root).get(entry.key)
    assert reread is not None
    np.testing.assert_array_equal(reread.vectors, expected)
    assert target.read_bytes() == original
