"""Completed-call budgets survive provider failures and checkpoint retries."""

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


@pytest.mark.parametrize("failed_request", [1, 2])
def test_failed_cached_call_can_retry_with_exact_remaining_budget(
    cache_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    failed_request: int,
) -> None:
    """docs/embeddings.md: failed calls are uncounted; cached units are free."""

    class FailsOnce(_Provider):
        attempts = 0

        def encode(self, texts: list[str]) -> np.ndarray:
            self.attempts += 1
            if self.attempts == failed_request:
                raise TimeoutError("response timed out")
            return super().encode(texts)

    inputs = [EmbeddingInput(str(index), "ord") for index in range(4)]
    digest = hash_embedding_inputs(inputs)
    provider = FailsOnce()
    meter = MeteredEmbedder(provider, budget=4, every=2)
    cached = _cached(meter, tmp_path)

    with pytest.raises(TimeoutError, match="response timed out"):
        quantized_document_vectors(cached, inputs, digest)

    completed = failed_request - 1
    assert provider.attempts == failed_request
    assert meter.meter == SpendMeter(requests=completed, inputs=2 * completed, tokens=2 * completed)
    assert capsys.readouterr().err == ""
    checkpoints = {path: path.read_bytes() for path in cache_dir.rglob("*.lsec")}
    assert len(checkpoints) == completed
    assert all(path.parent.parent.name == "units" for path in checkpoints)

    actual, scale = quantized_document_vectors(cached, inputs, digest)
    expected, expected_scale = quantized_document_vectors(_Provider(), inputs, digest)
    np.testing.assert_array_equal(actual, expected)
    assert scale == expected_scale
    assert provider.attempts == 3
    assert meter.meter == SpendMeter(requests=2, inputs=4, tokens=4)
    assert cached.progress.units_cached == completed
    assert all(path.read_bytes() == data for path, data in checkpoints.items())
    assert capsys.readouterr().err == (
        "embedding-spend: completed_calls=2 inputs=4 estimated_tokens_completed=4 budget=4\n"
    )

    persisted = {path: path.read_bytes() for path in cache_dir.rglob("*.lsec")}
    other = [EmbeddingInput("other", "ord")]
    with pytest.raises(EmbeddingBudgetError):
        quantized_document_vectors(cached, other, hash_embedding_inputs(other))
    assert provider.attempts == 3
    assert meter.meter == SpendMeter(requests=2, inputs=4, tokens=4)
    assert {path: path.read_bytes() for path in cache_dir.rglob("*.lsec")} == persisted
    assert capsys.readouterr().err == ""
