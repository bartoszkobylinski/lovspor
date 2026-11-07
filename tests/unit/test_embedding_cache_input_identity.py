"""Cache identity and document-first lookup contracts from #802, design §3."""

from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.cache import CachingEmbedder, quantized_document_vectors
from lovspor.embeddings.inputs import EmbeddingInput, hash_embedding_inputs
from tests.unit.test_embedding_cache_boundary import _Provider, cache_dir
from tests.unit.test_embedding_cache_remediation import _cached, _inputs

__all__ = ["cache_dir"]


def test_partial_checkpoints_are_isolated_by_embedding_space(
    cache_dir: Path, tmp_path: Path
) -> None:
    """ADR-0005: equal dimensions never make two spaces compatible (§3.1)."""
    inputs = _inputs()
    digest = hash_embedding_inputs(inputs)
    with pytest.raises(RuntimeError, match="job killed"):
        quantized_document_vectors(_cached(_Provider(fail_after=1), tmp_path), inputs, digest)
    checkpoints = {path: path.read_bytes() for path in cache_dir.rglob("*.lsec")}
    assert len(checkpoints) == 1

    class OtherSpace(_Provider):
        def __init__(self) -> None:
            super().__init__(descriptor="provider=openai;model=other;dim=4;endpoint=default")

        def encode(self, texts: list[str]) -> np.ndarray:
            return -super().encode(texts)

    provider = OtherSpace()
    cached = _cached(provider, tmp_path)
    actual, scale = quantized_document_vectors(cached, inputs, digest)
    expected, expected_scale = quantized_document_vectors(OtherSpace(), inputs, digest)
    np.testing.assert_array_equal(actual, expected)
    assert scale == expected_scale
    assert provider.requests == 2
    assert cached.progress.units_cached == 0
    assert all(path.read_bytes() == data for path, data in checkpoints.items())

    original_provider = _Provider()
    resumed = _cached(original_provider, tmp_path)
    actual, scale = quantized_document_vectors(resumed, inputs, digest)
    expected, expected_scale = quantized_document_vectors(_Provider(), inputs, digest)
    np.testing.assert_array_equal(actual, expected)
    assert scale == expected_scale
    assert original_provider.requests == 1
    assert resumed.progress.units_cached == 1


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


@pytest.mark.parametrize(
    ("bounds", "reused", "paid_texts"),
    [
        ([(0, 2), (2, 6)], 0, [2, 4]),
        ([(0, 2), (2, 3), (3, 6)], 1, [1, 3]),
    ],
)
def test_resume_after_request_plan_change_respects_unit_identity(
    cache_dir: Path,
    tmp_path: Path,
    bounds: list[tuple[int, int]],
    reused: int,
    paid_texts: list[int],
) -> None:
    """Design §3.1 binds checkpoints to unit count, index and slice hash."""
    inputs = [*_inputs(), EmbeddingInput("4", "fifth"), EmbeddingInput("5", "sixth")]
    digest = hash_embedding_inputs(inputs)
    with pytest.raises(RuntimeError, match="job killed"):
        quantized_document_vectors(_cached(_Provider(fail_after=1), tmp_path), inputs, digest)
    checkpoints = {path: path.read_bytes() for path in cache_dir.rglob("*.lsec")}
    assert len(checkpoints) == 1

    class ReplannedProvider(_Provider):
        def __init__(self) -> None:
            super().__init__()
            self.sent: list[list[str]] = []

        def plan_requests(self, texts: list[str]) -> list[tuple[int, int]]:
            return bounds

        def encode(self, texts: list[str]) -> np.ndarray:
            self.sent.append(list(texts))
            return super().encode(texts)

    provider = ReplannedProvider()
    resumed = _cached(provider, tmp_path)
    actual, scale = quantized_document_vectors(resumed, inputs, digest)
    expected, expected_scale = quantized_document_vectors(_Provider(), inputs, digest)
    np.testing.assert_array_equal(actual, expected)
    assert scale == expected_scale
    assert resumed.progress.units_cached == reused
    assert resumed.progress.units_paid == len(bounds) - reused
    assert [len(request) for request in provider.sent] == paid_texts
    assert provider.sent == [
        [item.text for item in inputs[start:end]] for start, end in bounds[reused:]
    ]
    assert all(path.read_bytes() == data for path, data in checkpoints.items())

    # Once the complete document exists, another plan change cannot cause payment.
    hit_provider = _Provider(fail_after=0)
    hit = _cached(hit_provider, tmp_path)
    again, again_scale = quantized_document_vectors(hit, inputs, digest)
    np.testing.assert_array_equal(again, actual)
    assert again_scale == scale
    assert hit_provider.requests == 0
    assert hit.progress.documents_cached == 1
