"""Behavioral regressions for the cache survivors in mutation-result.json."""

import io
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.cache import (
    CachingEmbedder,
    _read_policy,
    quantized_document_vectors,
    with_embedding_cache,
)
from lovspor.embeddings.inputs import EmbeddingInput, hash_embedding_inputs
from lovspor.embeddings.model import count_input_tokens
from lovspor.embeddings.provider import UNKNOWN_SPACE_ID
from lovspor.errors import EmbeddingCacheError
from tests.unit.test_embedding_cache_boundary import _Provider, _settings, cache_dir
from tests.unit.test_embedding_cache_format import _with_meta
from tests.unit.test_embedding_space_identity import _FakeEmbedder

__all__ = ["cache_dir"]


def _cached(provider: object, tmp_path: Path) -> CachingEmbedder:
    cached = with_embedding_cache(provider, _settings(tmp_path))
    assert isinstance(cached, CachingEmbedder)
    return cached


def _inputs() -> list[EmbeddingInput]:
    return [
        EmbeddingInput(str(i), text)
        for i, text in enumerate(
            ["Norwegian law and rules", "hello world", "a longer legal provision", "four"]
        )
    ]


def test_multiple_document_hits_accumulate(cache_dir: Path, tmp_path: Path) -> None:
    cached = _cached(_Provider(), tmp_path)
    inputs = _inputs()
    for _ in range(3):
        quantized_document_vectors(cached, inputs, hash_embedding_inputs(inputs))
    assert cached.progress.documents_cached == 2


def test_resume_counts_all_checkpoints_and_paid_tokens(cache_dir: Path, tmp_path: Path) -> None:
    class Provider(_Provider):
        model_name = "gpt-4"

    inputs = [*_inputs(), EmbeddingInput("last", "another request")]
    digest = hash_embedding_inputs(inputs)
    interrupted = _cached(Provider(fail_after=2), tmp_path)
    with pytest.raises(RuntimeError, match="job killed"):
        quantized_document_vectors(interrupted, inputs, digest)
    assert interrupted.progress.tokens_paid == sum(
        count_input_tokens(item.text, "gpt-4") for item in inputs[:4]
    )
    resumed = _cached(Provider(), tmp_path)
    quantized_document_vectors(resumed, inputs, digest)
    assert resumed.progress.units_cached == 2
    assert resumed.progress.tokens_paid == count_input_tokens(inputs[-1].text, "gpt-4")


def test_paid_tokens_use_the_providers_tokenizer(cache_dir: Path, tmp_path: Path) -> None:
    class Provider(_Provider):
        model_name = "text-davinci-001"

    inputs = [EmbeddingInput("1", "Tekst om norske lover og forskrifter")]
    digest = hash_embedding_inputs(inputs)
    assert count_input_tokens(inputs[0].text, Provider.model_name) == 14
    assert count_input_tokens(inputs[0].text) == 9
    cached = _cached(Provider(), tmp_path)
    quantized_document_vectors(cached, inputs, digest)
    assert cached.progress.tokens_paid == 14
    quantized_document_vectors(cached, inputs, digest)
    assert cached.progress.tokens_paid == 14
    assert cached.progress.documents_cached == 1


@pytest.mark.parametrize("scale", [0.0, -0.0, None, False])
def test_falsey_document_scale_is_refused_before_returning_vectors(
    cache_dir: Path, tmp_path: Path, scale: float | None
) -> None:
    cached = _cached(_FakeEmbedder(), tmp_path)
    inputs = _inputs()
    digest = hash_embedding_inputs(inputs)
    quantized_document_vectors(cached, inputs, digest)
    (entry,) = cache_dir.rglob("*.lsec")
    entry.write_bytes(_with_meta(entry.read_bytes(), scale=scale))
    with pytest.raises(EmbeddingCacheError, match="scale is not a finite positive float"):
        quantized_document_vectors(cached, inputs, digest)
    assert cached.progress.units_paid == 1
    reopened = _cached(_FakeEmbedder(), tmp_path)
    with pytest.raises(EmbeddingCacheError, match="scale is not a finite positive float"):
        quantized_document_vectors(reopened, inputs, digest)
    assert reopened.progress.units_paid == 0


def test_paid_vectors_are_float32(cache_dir: Path, tmp_path: Path) -> None:
    class DoubleProvider(_FakeEmbedder):
        def encode(self, texts: list[str]) -> np.ndarray:
            return np.full((len(texts), 4), 0.1, dtype=np.float64)

    cached = _cached(DoubleProvider(), tmp_path)
    vectors = cached._pay(["hello"])
    assert vectors.dtype == np.float32
    np.testing.assert_array_equal(vectors, np.full((1, 4), 0.1, dtype=np.float32))


def test_progress_is_periodic_flushed_stderr(
    cache_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cached = _cached(_Provider(), tmp_path)
    inputs = _inputs()
    digest = hash_embedding_inputs(inputs)
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stderr", stream)
        for _ in range(49):
            quantized_document_vectors(cached, inputs, digest)
        assert buffer.getvalue() == b""
        quantized_document_vectors(cached, inputs, digest)
        line = buffer.getvalue().decode()
        assert line == (
            "embedding-cache: documents=50 documents_cached=48 units_cached=0 "
            f"units_paid=2 estimated_tokens_paid={cached.progress.tokens_paid}\n"
        )
        quantized_document_vectors(cached, inputs, digest)
        assert buffer.getvalue().decode() == line
    assert capsys.readouterr().out == ""


def test_writer_refusal_explains_recovery(cache_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(EmbeddingCacheError) as error:
        with_embedding_cache(_Provider(), _settings(tmp_path, lspe=1))
    assert str(error.value) == (
        "the embedding cache serves only the LSPE version-2 writer; "
        "unset LOVSPOR_EMBEDDING_CACHE_DIR to write version 1"
    )
    assert not cache_dir.exists()


@pytest.mark.parametrize("descriptor,space", [(42, "a" * 32), ("declared", UNKNOWN_SPACE_ID)])
def test_invalid_identity_is_refused(
    cache_dir: Path,
    tmp_path: Path,
    descriptor: object,
    space: str,
) -> None:
    class Provider:
        def get_dimension(self) -> int:
            return 4

        def encode(self, texts: list[str]) -> np.ndarray:
            return np.ones((len(texts), 4), dtype=np.float32)

    provider = Provider()
    provider.descriptor = descriptor
    provider.space_id = space
    with pytest.raises(EmbeddingCacheError) as error:
        with_embedding_cache(provider, _settings(tmp_path))
    assert str(error.value) == (
        "the embedding cache needs an embedder that declares its embedding-space "
        "identity; Unknown is never comparable (ADR-0005)"
    )
    assert not cache_dir.exists()


def test_one_day_retention_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOVSPOR_EMBEDDING_CACHE_MAX_AGE_DAYS", "1")
    assert _read_policy().max_age == timedelta(days=1)


def test_adapter_without_request_plan_uses_one_request(cache_dir: Path, tmp_path: Path) -> None:
    cached = _cached(_FakeEmbedder(), tmp_path)
    inputs = _inputs()
    vectors, scale = quantized_document_vectors(cached, inputs, hash_embedding_inputs(inputs))
    np.testing.assert_array_equal(vectors, np.full((4, 4), 127, dtype=np.int8))
    assert scale == 1 / 127
    assert cached.progress.units_paid == 1


@pytest.mark.parametrize("bounds", [[(1, 4)], [(0, 1), (2, 4)]])
def test_broken_request_plan_is_refused(
    cache_dir: Path,
    tmp_path: Path,
    bounds: list[tuple[int, int]],
) -> None:
    class Provider(_Provider):
        def plan_requests(self, texts: list[str]) -> list[tuple[int, int]]:
            return bounds

    cached = _cached(Provider(), tmp_path)
    inputs = _inputs()
    with pytest.raises(EmbeddingCacheError) as error:
        quantized_document_vectors(cached, inputs, hash_embedding_inputs(inputs))
    assert str(error.value) == "the adapter's request plan does not cover its inputs in order"
    assert not list(cache_dir.rglob("*.lsec"))


def test_zero_vectors_keep_the_document_scale(cache_dir: Path, tmp_path: Path) -> None:
    class ZeroProvider(_FakeEmbedder):
        def encode(self, texts: list[str]) -> np.ndarray:
            return np.zeros((len(texts), 4), dtype=np.float32)

    cached = _cached(ZeroProvider(), tmp_path)
    inputs = _inputs()
    for _ in range(2):
        vectors, scale = quantized_document_vectors(cached, inputs, hash_embedding_inputs(inputs))
        assert scale == 1.0
        assert not vectors.any()


def test_new_document_timestamp_is_aware_utc(cache_dir: Path, tmp_path: Path) -> None:
    cached = _cached(_Provider(), tmp_path)
    inputs = _inputs()
    stored = cached.document_vectors(inputs, hash_embedding_inputs(inputs))
    assert stored.created_at.tzinfo is not None
    assert stored.created_at.utcoffset() == timedelta(0)
