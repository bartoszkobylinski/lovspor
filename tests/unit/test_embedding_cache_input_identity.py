"""Cache identity and document-first lookup contracts from #802, design §3."""

from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.cache import CachingEmbedder, quantized_document_vectors
from lovspor.embeddings.inputs import EmbeddingInput, hash_embedding_inputs
from tests.unit.test_embedding_cache_boundary import _Provider, cache_dir
from tests.unit.test_embedding_cache_remediation import _cached, _inputs

__all__ = ["cache_dir"]


@pytest.mark.parametrize("split", [1, 3])
def test_changed_request_slices_do_not_reuse_same_index_checkpoints(
    cache_dir: Path, tmp_path: Path, split: int
) -> None:
    """Design §3: unit identity includes the slice, even at the same index/count."""

    class BatchSensitiveProvider(_Provider):
        boundary = 2

        def plan_requests(self, texts: list[str]) -> list[tuple[int, int]]:
            if len(texts) < 4:
                return [(0, len(texts))]
            return [(0, self.boundary), (self.boundary, len(texts))]

        def encode(self, texts: list[str]) -> np.ndarray:
            parts = []
            for start, end in self.plan_requests(texts):
                batch = texts[start:end]
                parts.append(super().encode(batch) + len(batch))
            return np.vstack(parts)

    inputs = _inputs()
    digest = hash_embedding_inputs(inputs)
    with pytest.raises(RuntimeError, match="job killed"):
        quantized_document_vectors(
            _cached(BatchSensitiveProvider(fail_after=1), tmp_path), inputs, digest
        )
    checkpoint = next(cache_dir.rglob("*.lsec"))
    original_bytes = checkpoint.read_bytes()

    provider = BatchSensitiveProvider()
    provider.boundary = split
    cached = _cached(provider, tmp_path)
    actual, scale = quantized_document_vectors(cached, inputs, digest)
    reference = BatchSensitiveProvider()
    reference.boundary = split
    expected, expected_scale = quantized_document_vectors(reference, inputs, digest)

    assert provider.requests == 2
    assert cached.progress.units_cached == 0
    assert cached.progress.units_paid == 2
    np.testing.assert_array_equal(actual, expected)
    assert scale == expected_scale
    assert checkpoint.read_bytes() == original_bytes
    assert len(list(cache_dir.glob("schema-1/*/units/**/*.lsec"))) == 3


@pytest.mark.parametrize("change", ["order", "section_id"])
def test_changed_document_identity_does_not_reuse_old_checkpoints(
    cache_dir: Path, tmp_path: Path, change: str
) -> None:
    """§3.1 binds even an unchanged unit to the whole document's input hash."""
    original = _inputs()
    original_hash = hash_embedding_inputs(original)
    with pytest.raises(RuntimeError, match="job killed"):
        quantized_document_vectors(
            _cached(_Provider(fail_after=1), tmp_path), original, original_hash
        )
    assert len(list(cache_dir.rglob("*.lsec"))) == 1

    # Keep the first request unchanged; changing only the tail must still
    # invalidate that request's checkpoint under the document identity.
    changed = list(original)
    if change == "order":
        changed[2:] = reversed(changed[2:])
    else:
        changed[-1] = EmbeddingInput("renamed-section", changed[-1].text)
    changed_hash = hash_embedding_inputs(changed)
    assert changed_hash != original_hash
    provider = _Provider()
    cached = _cached(provider, tmp_path)
    actual, scale = quantized_document_vectors(cached, changed, changed_hash)
    expected, expected_scale = quantized_document_vectors(_Provider(), changed, changed_hash)
    assert provider.requests == 2
    assert cached.progress.units_cached == 0
    np.testing.assert_array_equal(actual, expected)
    assert scale == expected_scale

    # The interrupted original retains its independently reusable checkpoint.
    resumed = _cached(_Provider(), tmp_path)
    quantized_document_vectors(resumed, original, original_hash)
    assert resumed.progress.units_cached == 1
    assert resumed.progress.units_paid == 1


def test_document_hit_does_not_read_obsolete_corrupt_unit_checkpoints(
    cache_dir: Path, tmp_path: Path
) -> None:
    """§3.2: a validated document is returned before consulting unit entries."""
    inputs = _inputs()
    digest = hash_embedding_inputs(inputs)
    expected, expected_scale = quantized_document_vectors(
        _cached(_Provider(), tmp_path), inputs, digest
    )
    units = list(cache_dir.glob("schema-1/*/units/**/*.lsec"))
    assert len(units) == 2
    for unit in units:
        unit.write_bytes(b"obsolete torn checkpoint")

    provider = _Provider(fail_after=0)
    cached = _cached(provider, tmp_path)
    assert isinstance(cached, CachingEmbedder)
    actual, scale = quantized_document_vectors(cached, inputs, digest)
    np.testing.assert_array_equal(actual, expected)
    assert scale == expected_scale
    assert provider.requests == 0
    assert cached.progress.documents_cached == 1
    assert cached.progress.units_cached == 0
    assert all(unit.read_bytes() == b"obsolete torn checkpoint" for unit in units)
