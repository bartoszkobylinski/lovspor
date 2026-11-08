"""The durable document value remains authoritative after a caller uses its rows."""

from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.cache import quantized_document_vectors
from lovspor.embeddings.inputs import hash_embedding_inputs
from tests.unit.test_embedding_cache_boundary import _Provider, cache_dir
from tests.unit.test_embedding_cache_remediation import _cached, _inputs

__all__ = ["cache_dir"]


@pytest.mark.parametrize("rows", [1, 4], ids=["single-request", "checkpointed-document"])
def test_caller_mutation_cannot_change_a_later_document_hit(
    cache_dir: Path, tmp_path: Path, rows: int
) -> None:
    """Design §3.2: document hits reuse the persisted int8 rows and one scale."""
    inputs = _inputs()[:rows]
    digest = hash_embedding_inputs(inputs)
    expected, expected_scale = quantized_document_vectors(_Provider(), inputs, digest)
    provider = _Provider()
    cached = _cached(provider, tmp_path)
    first, scale = quantized_document_vectors(cached, inputs, digest)
    np.testing.assert_array_equal(first, expected)
    assert scale == expected_scale
    paid = provider.requests
    persisted = {path: path.read_bytes() for path in cache_dir.rglob("*.lsec")}
    assert len(persisted) == (1 if rows == 1 else 3)

    first.fill(0)
    hit, hit_scale = quantized_document_vectors(cached, inputs, digest)
    np.testing.assert_array_equal(hit, expected)
    assert hit_scale == expected_scale
    assert provider.requests == paid
    hit.fill(0)

    # A fresh adapter must see the same paid value, with no provider access.
    reopened_provider = _Provider(fail_after=0)
    reopened, reopened_scale = quantized_document_vectors(
        _cached(reopened_provider, tmp_path), inputs, digest
    )
    np.testing.assert_array_equal(reopened, expected)
    assert reopened_scale == expected_scale
    assert reopened_provider.requests == 0
    assert {path: path.read_bytes() for path in cache_dir.rglob("*.lsec")} == persisted
