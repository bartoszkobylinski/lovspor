"""Design §3: preserve completed requests and never pay over refused checkpoints."""

from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.cache import quantized_document_vectors
from lovspor.embeddings.inputs import hash_embedding_inputs
from lovspor.errors import EmbeddingCacheError
from tests.unit.test_embedding_cache_boundary import _Provider, cache_dir
from tests.unit.test_embedding_cache_remediation import _cached, _inputs

__all__ = ["cache_dir"]


@pytest.mark.parametrize("invalid", ["shape", "nan", "infinity"])
def test_invalid_later_response_preserves_checkpoint_for_retry(
    cache_dir: Path, tmp_path: Path, invalid: str
) -> None:
    """A refused response must not destroy work already paid for (§3.3 and §3.4)."""

    class InvalidSecondResponse(_Provider):
        def encode(self, texts: list[str]) -> np.ndarray:
            vectors = super().encode(texts)
            if self.requests == 2:
                if invalid == "shape":
                    return vectors[:-1]
                vectors[0, 0] = np.nan if invalid == "nan" else np.inf
            return vectors

    inputs = _inputs()
    identity = hash_embedding_inputs(inputs)
    provider = InvalidSecondResponse()
    with pytest.raises(EmbeddingCacheError, match="provider returned"):
        quantized_document_vectors(_cached(provider, tmp_path), inputs, identity)
    assert provider.requests == 2
    checkpoints = {p: p.read_bytes() for p in cache_dir.rglob("*.lsec")}
    assert len(checkpoints) == 1
    assert next(iter(checkpoints)).name.startswith("0-2-")

    resumed_provider = _Provider()
    resumed = _cached(resumed_provider, tmp_path)
    actual, scale = quantized_document_vectors(resumed, inputs, identity)
    expected, expected_scale = quantized_document_vectors(_Provider(), inputs, identity)
    np.testing.assert_array_equal(actual, expected)
    assert scale == expected_scale
    assert resumed_provider.requests == 1
    assert resumed_provider.texts == 2
    assert resumed.progress.units_cached == 1
    assert all(p.read_bytes() == data for p, data in checkpoints.items())


def test_corrupt_checkpoint_stops_resume_without_payment_or_replacement(
    cache_dir: Path, tmp_path: Path
) -> None:
    """A corrupt completed request is a refusal, never a miss (§3.4)."""
    inputs = _inputs()
    identity = hash_embedding_inputs(inputs)
    with pytest.raises(RuntimeError, match="job killed"):
        quantized_document_vectors(_cached(_Provider(fail_after=1), tmp_path), inputs, identity)
    checkpoint = next(cache_dir.rglob("*.lsec"))
    damaged = bytearray(checkpoint.read_bytes())
    damaged[-1] ^= 1
    checkpoint.write_bytes(damaged)
    before = {p: p.read_bytes() for p in cache_dir.rglob("*.lsec")}

    provider = _Provider()
    with pytest.raises(EmbeddingCacheError, match="digest"):
        quantized_document_vectors(_cached(provider, tmp_path), inputs, identity)
    assert provider.requests == 0
    assert {p: p.read_bytes() for p in cache_dir.rglob("*.lsec")} == before
