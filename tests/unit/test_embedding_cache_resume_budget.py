"""Cache checkpoints are free when a fresh run resumes under a finite budget.

Enforces docs/embeddings.md's completed-call budget and cache resume contract.
"""

from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.cache import quantized_document_vectors
from lovspor.embeddings.inputs import EmbeddingInput, hash_embedding_inputs
from lovspor.embeddings.meter import MeteredEmbedder, SpendMeter
from lovspor.errors import EmbeddingBudgetError
from tests.unit.test_embedding_cache_boundary import _Provider, cache_dir
from tests.unit.test_embedding_cache_remediation import _cached

__all__ = ["cache_dir"]


@pytest.mark.parametrize("completed_units", [1, 2])
def test_resume_charges_only_missing_units_and_keeps_budget_for_whole_run(
    cache_dir: Path, tmp_path: Path, completed_units: int
) -> None:
    """A cached prefix consumes no new budget; the paid tail exhausts it exactly."""
    inputs = [EmbeddingInput(str(index), "ord") for index in range(6)]
    digest = hash_embedding_inputs(inputs)
    first_provider = _Provider()
    first_meter = MeteredEmbedder(first_provider, budget=2 * completed_units, every=25)

    with pytest.raises(EmbeddingBudgetError):
        quantized_document_vectors(_cached(first_meter, tmp_path), inputs, digest)

    assert first_provider.requests == completed_units
    checkpoints = {path: path.read_bytes() for path in cache_dir.rglob("*.lsec")}
    assert len(checkpoints) == completed_units
    assert all(path.parent.parent.name == "units" for path in checkpoints)

    missing_units = 3 - completed_units
    provider = _Provider()
    meter = MeteredEmbedder(provider, budget=2 * missing_units, every=25)
    resumed = _cached(meter, tmp_path)
    actual, scale = quantized_document_vectors(resumed, inputs, digest)
    expected, expected_scale = quantized_document_vectors(_Provider(), inputs, digest)

    np.testing.assert_array_equal(actual, expected)
    assert scale == expected_scale
    assert provider.requests == missing_units
    assert resumed.progress.units_cached == completed_units
    spent = SpendMeter(requests=missing_units, inputs=2 * missing_units, tokens=2 * missing_units)
    assert meter.meter == spent
    assert all(path.read_bytes() == data for path, data in checkpoints.items())

    # Even with no budget left, the completed document is free to replay.
    replay, replay_scale = quantized_document_vectors(resumed, inputs, digest)
    np.testing.assert_array_equal(replay, actual)
    assert replay_scale == scale
    before = {path: path.read_bytes() for path in cache_dir.rglob("*.lsec")}
    other = [EmbeddingInput("other", "ord")]
    with pytest.raises(EmbeddingBudgetError):
        quantized_document_vectors(resumed, other, hash_embedding_inputs(other))
    assert provider.requests == missing_units
    assert meter.meter == spent
    assert {path: path.read_bytes() for path in cache_dir.rglob("*.lsec")} == before
