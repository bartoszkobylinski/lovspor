"""Design §5.2: the adapter consumes the immutable value that won publication."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.cache import CachingEmbedder, quantized_document_vectors
from lovspor.embeddings.cache_format import (
    CachedVectors,
    EntryKey,
    ReadPolicy,
    UnitKey,
    encode_entry,
)
from lovspor.embeddings.cache_store import LocalEntryStore, entry_location
from lovspor.embeddings.inputs import EmbeddingInput, hash_embedding_inputs
from lovspor.embeddings.quantize import quantize_int8
from tests.unit.test_embedding_cache_store import ESI


@pytest.mark.parametrize("kind", ["unit", "document"])
def test_adapter_uses_competing_publication_instead_of_its_paid_response(
    tmp_path: Path, kind: str
) -> None:
    """A writer appearing after the miss wins even when its vectors differ."""
    root = tmp_path / "cache"
    policy = ReadPolicy(max_age=timedelta(days=30), clock=lambda: datetime.now(UTC))
    store = LocalEntryStore.open(root, (), policy)
    competitor = LocalEntryStore.open(root, (), policy)
    inputs = [EmbeddingInput(str(i), f"legal text {i}") for i in range(4)]
    digest = hash_embedding_inputs(inputs)
    document_key = EntryKey(esi=ESI, input_hash=digest, dim=4, rows=4)
    if kind == "unit":
        winner_key = EntryKey(
            esi=ESI,
            input_hash=digest,
            dim=4,
            rows=2,
            unit=UnitKey(index=0, count=2, input_hash=hash_embedding_inputs(inputs[:2])),
        )
        winner_vectors = np.full((2, 4), -0.75, dtype=np.float32)
        winner_scale = None
    else:
        winner_key = document_key
        winner_vectors = np.full((4, 4), -37, dtype=np.int8)
        winner_scale = 0.0125
    winner = CachedVectors(winner_key, winner_vectors, winner_scale, datetime.now(UTC))

    class Provider:
        calls = 0

        def get_dimension(self) -> int:
            return 4

        def plan_requests(self, texts: list[str]) -> list[tuple[int, int]]:
            return [(0, 2), (2, 4)]

        def encode(self, texts: list[str]) -> np.ndarray:
            self.calls += 1
            if self.calls == 1:
                # The adapter has already observed a miss. Publish through a
                # separate real store before it attempts its own publication.
                competitor.put(winner)
            return np.full((len(texts), 4), 0.25, dtype=np.float32)

    provider = Provider()
    cached = CachingEmbedder(provider, store, ESI)
    actual, scale = quantized_document_vectors(cached, inputs, digest)
    if kind == "unit":
        expected, expected_scale = quantize_int8(
            np.vstack([winner_vectors, np.full((2, 4), 0.25, dtype=np.float32)])
        )
    else:
        expected, expected_scale = winner_vectors, winner_scale
    np.testing.assert_array_equal(actual, expected)
    assert scale == expected_scale
    assert provider.calls == 2
    parts, name = entry_location(winner_key)
    assert root.joinpath(*parts, name).read_bytes() == encode_entry(winner)

    # The assembled document, including adopted unit values, must be durable
    # and returned on a fresh lookup without another provider request.
    reopened = CachingEmbedder(provider, LocalEntryStore.open(root, (), policy), ESI)
    hit, hit_scale = quantized_document_vectors(reopened, inputs, digest)
    np.testing.assert_array_equal(hit, expected)
    assert hit_scale == expected_scale
    assert provider.calls == 2
    assert reopened.progress.documents_cached == 1
