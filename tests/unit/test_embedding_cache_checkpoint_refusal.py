"""Design #802 §5.3: a bad checkpoint is a refusal, never a paid-over miss."""

from pathlib import Path

import pytest

from lovspor.embeddings.cache import quantized_document_vectors
from lovspor.embeddings.inputs import hash_embedding_inputs
from lovspor.errors import EmbeddingCacheError
from tests.unit.test_embedding_cache_boundary import _Provider, cache_dir
from tests.unit.test_embedding_cache_format import _with_meta
from tests.unit.test_embedding_cache_remediation import _cached, _inputs

__all__ = ["cache_dir"]


@pytest.mark.parametrize("damage", ["torn", "digest", "identity", "shape"])
def test_corrupt_checkpoint_stops_resume_without_payment_or_replacement(
    cache_dir: Path, tmp_path: Path, damage: str
) -> None:
    """Enforce docs/embeddings.md: bad entries stop sync rather than being paid over."""
    inputs = _inputs()
    digest = hash_embedding_inputs(inputs)
    with pytest.raises(RuntimeError, match="job killed"):
        quantized_document_vectors(_cached(_Provider(fail_after=1), tmp_path), inputs, digest)
    (checkpoint,) = cache_dir.rglob("*.lsec")
    original = checkpoint.read_bytes()
    if damage == "torn":
        corrupted = original[:12]
    elif damage == "digest":
        corrupted = original[:-1] + bytes([original[-1] ^ 1])
    elif damage == "identity":
        corrupted = _with_meta(original, input_hash="f" * 64)
    else:
        corrupted = _with_meta(original, rows=99)
    checkpoint.write_bytes(corrupted)
    before = {path: path.read_bytes() for path in cache_dir.rglob("*.lsec")}

    provider = _Provider(fail_after=0)
    resumed = _cached(provider, tmp_path)
    with pytest.raises(EmbeddingCacheError):
        quantized_document_vectors(resumed, inputs, digest)

    assert provider.requests == 0
    assert resumed.progress.units_paid == 0
    assert resumed.progress.units_cached == 0
    assert {path: path.read_bytes() for path in cache_dir.rglob("*.lsec")} == before
    assert not list(cache_dir.rglob("*.tmp.*"))
