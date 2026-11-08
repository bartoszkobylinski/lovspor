"""Design §3.2: reuse every valid checkpoint, including after a missing unit."""

from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.cache import quantized_document_vectors
from lovspor.embeddings.inputs import EmbeddingInput, hash_embedding_inputs
from lovspor.embeddings.meter import MeteredEmbedder, SpendMeter
from tests.unit.test_embedding_cache_boundary import _Provider, cache_dir
from tests.unit.test_embedding_cache_remediation import _cached

__all__ = ["cache_dir"]


@pytest.mark.parametrize("missing", [0, 1], ids=["missing-first", "missing-middle"])
def test_resume_reuses_checkpoints_after_a_hole(
    cache_dir: Path, tmp_path: Path, missing: int
) -> None:
    """A hole must not repay the cached suffix or reorder the document's rows."""
    inputs = [EmbeddingInput(str(index), f"ord {index}") for index in range(8)]
    digest = hash_embedding_inputs(inputs)
    with pytest.raises(RuntimeError, match="job killed"):
        quantized_document_vectors(_cached(_Provider(fail_after=3), tmp_path), inputs, digest)

    checkpoints = list(cache_dir.rglob("*.lsec"))
    assert len(checkpoints) == 3
    next(path for path in checkpoints if path.name.startswith(f"{missing}-4-")).unlink()
    retained = {path: path.read_bytes() for path in cache_dir.rglob("*.lsec")}

    class RecordingProvider(_Provider):
        def __init__(self) -> None:
            super().__init__()
            self.sent: list[list[str]] = []

        def encode(self, texts: list[str]) -> np.ndarray:
            self.sent.append(list(texts))
            return super().encode(texts)

    provider = RecordingProvider()
    # Only the hole and the never-completed final request may consume this budget.
    meter = MeteredEmbedder(provider, budget=12, every=25)
    resumed = _cached(meter, tmp_path)
    actual, scale = quantized_document_vectors(resumed, inputs, digest)
    expected, expected_scale = quantized_document_vectors(_Provider(), inputs, digest)

    np.testing.assert_array_equal(actual, expected)
    assert scale == expected_scale
    assert provider.sent == [
        [item.text for item in inputs[2 * missing : 2 * missing + 2]],
        [item.text for item in inputs[6:]],
    ]
    assert meter.meter == SpendMeter(requests=2, inputs=4, tokens=12)
    assert resumed.progress.units_cached == 2
    assert resumed.progress.units_paid == 2
    assert all(path.read_bytes() == data for path, data in retained.items())
