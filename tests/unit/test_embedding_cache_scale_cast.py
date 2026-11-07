"""Observe document scales across the durable cache boundary."""

from pathlib import Path

import numpy as np

from lovspor.embeddings.cache import quantized_document_vectors
from lovspor.embeddings.inputs import hash_embedding_inputs
from tests.unit.test_embedding_cache_boundary import cache_dir
from tests.unit.test_embedding_cache_remediation import _cached, _inputs
from tests.unit.test_embedding_space_identity import _FakeEmbedder

__all__ = ["cache_dir"]


def test_document_scale_reconstructs_vectors_on_miss_and_reopened_hit(
    cache_dir: Path, tmp_path: Path
) -> None:
    inputs = _inputs()
    digest = hash_embedding_inputs(inputs)
    for _ in range(2):
        cached = _cached(_FakeEmbedder(), tmp_path)
        vectors, scale = quantized_document_vectors(cached, inputs, digest)
        assert isinstance(scale, float)
        assert scale == 1 / 127
        np.testing.assert_array_equal(vectors.astype(np.float64) * scale, np.ones((len(inputs), 4)))
