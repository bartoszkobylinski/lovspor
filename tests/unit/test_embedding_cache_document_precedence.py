"""Design #802 §2: a valid document is read before planning paid requests."""

from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.cache import quantized_document_vectors
from lovspor.embeddings.inputs import hash_embedding_inputs
from tests.unit.test_embedding_cache_boundary import _Provider, cache_dir
from tests.unit.test_embedding_cache_remediation import _cached, _inputs

__all__ = ["cache_dir"]


@pytest.mark.parametrize("checkpoint_state", ["removed", "corrupt"])
def test_valid_document_hit_does_not_consult_obsolete_checkpoints_or_request_plan(
    cache_dir: Path, tmp_path: Path, checkpoint_state: str
) -> None:
    """The read-first contract (§2) survives cleanup or damage of transient units."""
    inputs = _inputs()
    identity = hash_embedding_inputs(inputs)
    paying_provider = _Provider()
    expected, expected_scale = quantized_document_vectors(
        _cached(paying_provider, tmp_path), inputs, identity
    )
    assert paying_provider.requests == 2
    checkpoints = [path for path in cache_dir.rglob("*.lsec") if "units" in path.parts]
    assert len(checkpoints) == 2
    for path in checkpoints:
        if checkpoint_state == "removed":
            path.unlink()
        else:
            path.write_bytes(b"torn obsolete checkpoint")
    before = {path: path.read_bytes() for path in cache_dir.rglob("*.lsec")}

    class HitOnlyProvider(_Provider):
        def plan_requests(self, texts: list[str]) -> list[tuple[int, int]]:
            pytest.fail("a document hit must not need a provider request plan")

    provider = HitOnlyProvider(fail_after=0)
    cached = _cached(provider, tmp_path)
    actual, actual_scale = quantized_document_vectors(cached, inputs, identity)

    np.testing.assert_array_equal(actual, expected)
    assert actual_scale == expected_scale
    assert provider.requests == 0
    assert provider.texts == 0
    assert cached.progress.documents_cached == 1
    assert cached.progress.units_cached == 0
    assert cached.progress.units_paid == 0
    assert cached.progress.tokens_paid == 0
    assert {path: path.read_bytes() for path in cache_dir.rglob("*.lsec")} == before
