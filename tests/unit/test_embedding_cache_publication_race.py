"""Design §5.2: adopt a publication winner, or refuse it before paying on."""

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.cache import quantized_document_vectors, read_policy_from_env
from lovspor.embeddings.cache_format import CachedVectors, EntryKey, UnitKey
from lovspor.embeddings.cache_store import LocalEntryStore
from lovspor.embeddings.inputs import hash_embedding_inputs
from lovspor.embeddings.quantize import quantize_int8
from lovspor.errors import EmbeddingCacheError
from tests.unit.test_embedding_cache_boundary import _Provider, cache_dir
from tests.unit.test_embedding_cache_remediation import _cached, _inputs

__all__ = ["cache_dir"]


@pytest.mark.parametrize("rows", [2, 4], ids=["document", "unit"])
@pytest.mark.parametrize("corrupt", [False, True], ids=["valid-winner", "corrupt-winner"])
def test_paid_request_adopts_or_refuses_intervening_publication(
    cache_dir: Path, tmp_path: Path, rows: int, corrupt: bool
) -> None:
    """Publish after the cache miss but before encode returns, without threads.

    §5.2.4 requires the stored winner to replace the caller's fetched value;
    an invalid winner must instead stop the run without an overwrite.
    """
    inputs = _inputs()[:rows]
    digest = hash_embedding_inputs(inputs)
    store = LocalEntryStore.open(cache_dir, (), read_policy_from_env())
    winner_vectors = np.array([[8, -4, 2, 1], [-2, 1, 4, -8]], dtype="<f4")
    winner_scale = None
    if rows == 2:
        winner_vectors, winner_scale = quantize_int8(winner_vectors)
    key = EntryKey(
        esi=_Provider().space_id,
        input_hash=digest,
        dim=4,
        rows=2,
        unit=None
        if rows == 2
        else UnitKey(index=0, count=2, input_hash=hash_embedding_inputs(inputs[:2])),
    )
    published: dict[Path, bytes] = {}

    class RacingProvider(_Provider):
        def encode(self, texts: list[str]) -> np.ndarray:
            fetched = super().encode(texts)
            if self.requests == 1:
                store.put(CachedVectors(key, winner_vectors, winner_scale, datetime.now(UTC)))
                path = next(cache_dir.rglob("*.lsec"))
                if corrupt:
                    damaged = bytearray(path.read_bytes())
                    damaged[-1] ^= 1
                    path.write_bytes(damaged)
                published[path] = path.read_bytes()
            return fetched

    provider = RacingProvider()
    cached = _cached(provider, tmp_path)
    if corrupt:
        with pytest.raises(EmbeddingCacheError, match="digest"):
            quantized_document_vectors(cached, inputs, digest)
        assert provider.requests == 1
        assert {p: p.read_bytes() for p in cache_dir.rglob("*.lsec")} == published
    else:
        actual, scale = quantized_document_vectors(cached, inputs, digest)
        if rows == 2:
            expected, expected_scale = winner_vectors, winner_scale
        else:
            tail = _Provider().encode([item.text for item in inputs[2:]])
            expected, expected_scale = quantize_int8(np.vstack([winner_vectors, tail]))
        np.testing.assert_array_equal(actual, expected)
        assert scale == expected_scale
        assert provider.requests == rows // 2
        assert all(path.read_bytes() == data for path, data in published.items())

        # A fresh reader must see exactly the value adopted by the losing writer.
        hit_provider = _Provider(fail_after=0)
        again, again_scale = quantized_document_vectors(
            _cached(hit_provider, tmp_path), inputs, digest
        )
        np.testing.assert_array_equal(again, actual)
        assert again_scale == scale
        assert hit_provider.requests == 0
    assert not list(cache_dir.rglob("*.tmp.*"))
