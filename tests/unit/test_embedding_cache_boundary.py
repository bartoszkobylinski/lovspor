"""The one paid-embedding boundary, with and without the durable cache (#802).

Sync (change path) and backfill/repair both reach the provider only through
``_write_embeddings_for_doc``. With ``LOVSPOR_EMBEDDING_CACHE_DIR`` unset the
boundary is today's code; with it set, a cached document costs zero provider
calls, a long document is checkpointed per provider request, and a sidecar
built from the cache is byte-identical to one built without it.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

import lovspor.sync.orchestrator as orchestrator_module
from lovspor.embeddings.cache import (
    CACHE_DIR_VARIABLE,
    CachingEmbedder,
    quantized_document_vectors,
    with_embedding_cache,
)
from lovspor.embeddings.cache_format import ReadPolicy
from lovspor.embeddings.inputs import EmbeddingInput, hash_embedding_inputs
from lovspor.errors import (
    EmbeddingCacheError,
    EmbeddingCacheExpiredError,
    EmbeddingCacheStorageError,
)
from lovspor.settings import Settings
from lovspor.sync.orchestrator import _write_embeddings_for_doc
from tests.unit.test_embedding_space_identity import _FakeEmbedder, _UnidentifiedEmbedder

_RENDERED = "---\ntitle: t\n---\n\n" + "".join(
    f"### § {i}. Overskrift\n\nTekst nummer {i} om regler.\n\n" for i in range(1, 8)
)


class _Provider(_FakeEmbedder):
    """A fake paid provider: deterministic per text, two inputs per request."""

    def __init__(self, fail_after: int | None = None, descriptor: str | None = None) -> None:
        super().__init__(**({"descriptor": descriptor} if descriptor else {}))
        self.requests = 0
        self.texts = 0
        self._fail_after = fail_after

    def plan_requests(self, texts: list[str]) -> list[tuple[int, int]]:
        return [(i, min(i + 2, len(texts))) for i in range(0, len(texts), 2)]

    def encode(self, texts: list[str]) -> np.ndarray:
        if self._fail_after is not None and self.requests >= self._fail_after:
            raise RuntimeError("job killed")
        self.requests += 1
        self.texts += len(texts)
        rows = [np.frombuffer(hashlib.sha256(t.encode()).digest()[:4], np.int8) for t in texts]
        matrix = np.asarray(rows, dtype=np.float32) + 0.5
        return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "embedding-cache"
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(root))
    return root


def _settings(tmp_path: Path, lspe: int = 2) -> Settings:
    corpus = tmp_path / "lovverk"
    corpus.mkdir(exist_ok=True)
    return Settings(data_dir=tmp_path / "data", lovverk_repo_path=corpus, lspe_writer_version=lspe)


def _write(settings: Settings, embedder: object, slug: str = "lov-1") -> bytes:
    path, _hash = _write_embeddings_for_doc(
        settings.lovverk_repo_path, "gjeldende-lover", slug, _RENDERED, embedder
    )
    return path.read_bytes()


def test_cache_is_off_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CACHE_DIR_VARIABLE, raising=False)
    provider = _Provider()
    assert with_embedding_cache(provider, _settings(tmp_path)) is provider


def test_keyless_sync_builds_no_cache(cache_dir: Path, tmp_path: Path) -> None:
    assert with_embedding_cache(None, _settings(tmp_path)) is None
    assert not cache_dir.exists()


def test_load_embedder_is_the_one_wrapping_point(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _Provider()
    monkeypatch.setattr(orchestrator_module, "create_embedder", lambda _config: provider)
    loaded = orchestrator_module._load_embedder(_settings(tmp_path))
    assert isinstance(loaded, CachingEmbedder)
    assert loaded.space_id == provider.space_id
    assert loaded.descriptor == provider.descriptor
    assert loaded.get_dimension() == provider.get_dimension()


def test_cached_sidecar_is_byte_identical_to_uncached(cache_dir: Path, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    uncached = _write(settings, _Provider())
    cached = _write(settings, with_embedding_cache(_Provider(), settings))
    assert cached == uncached


def test_second_run_pays_nothing_and_writes_the_same_bytes(cache_dir: Path, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = _write(settings, with_embedding_cache(_Provider(), settings))
    again = _Provider()
    second = _write(settings, with_embedding_cache(again, settings))
    assert again.requests == 0
    assert second == first


def test_interrupted_long_document_pays_only_the_missing_requests(
    cache_dir: Path, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    killed = _Provider(fail_after=2)
    with pytest.raises(RuntimeError, match="job killed"):
        _write(settings, with_embedding_cache(killed, settings))
    assert killed.requests == 2
    resumed = _Provider()
    _write(settings, with_embedding_cache(resumed, settings))
    assert resumed.requests == 2  # 7 inputs = 4 requests; 2 were checkpointed
    assert resumed.texts == 3


def test_resumed_sidecar_equals_an_uninterrupted_one(cache_dir: Path, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    reference = _write(settings, _Provider())
    with pytest.raises(RuntimeError):
        _write(settings, with_embedding_cache(_Provider(fail_after=3), settings))
    assert _write(settings, with_embedding_cache(_Provider(), settings)) == reference


def test_corrupt_entry_stops_the_run_without_a_paid_call(cache_dir: Path, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write(settings, with_embedding_cache(_Provider(), settings))
    for entry in cache_dir.rglob("*.lsec"):
        data = bytearray(entry.read_bytes())
        data[-40] ^= 0x01
        entry.write_bytes(bytes(data))
    provider = _Provider()
    with pytest.raises(EmbeddingCacheError):
        _write(settings, with_embedding_cache(provider, settings))
    assert provider.requests == 0


def test_another_space_never_reads_this_space(cache_dir: Path, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write(settings, with_embedding_cache(_Provider(), settings))
    other = _Provider(descriptor="provider=openai;model=other;dim=4;endpoint=default")
    _write(settings, with_embedding_cache(other, settings))
    assert other.requests == 4


def test_changed_inputs_are_a_miss(cache_dir: Path, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    cached = with_embedding_cache(_Provider(), settings)
    _write(settings, cached)
    provider = _Provider()
    path, _hash = _write_embeddings_for_doc(
        settings.lovverk_repo_path,
        "gjeldende-lover",
        "lov-1",
        _RENDERED.replace("nummer 3", "nummer tre"),
        with_embedding_cache(provider, settings),
    )
    assert provider.requests == 4
    assert path.exists()


def test_unknown_space_is_refused(cache_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(EmbeddingCacheError, match="identity"):
        with_embedding_cache(_UnidentifiedEmbedder(), _settings(tmp_path))


def test_lspe_version_1_writer_is_refused(cache_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(EmbeddingCacheError, match="LSPE"):
        with_embedding_cache(_Provider(), _settings(tmp_path, lspe=1))


def test_cache_inside_the_corpus_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(settings.lovverk_repo_path / ".cache"))
    with pytest.raises(EmbeddingCacheError, match="inside"):
        with_embedding_cache(_Provider(), settings)


def test_cache_inside_the_engine_checkout_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = Path(__file__).resolve().parents[2]
    target = engine / "data" / "embedding-cache-never-created"
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(target))
    with pytest.raises(EmbeddingCacheError, match="inside"):
        with_embedding_cache(_Provider(), _settings(tmp_path))
    assert not target.exists()


def test_relative_cache_dir_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CACHE_DIR_VARIABLE, "relative/cache")
    with pytest.raises(EmbeddingCacheError, match="absolute"):
        with_embedding_cache(_Provider(), _settings(tmp_path))


def test_bad_retention_is_refused(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOVSPOR_EMBEDDING_CACHE_MAX_AGE_DAYS", "zero")
    with pytest.raises(EmbeddingCacheError, match="MAX_AGE_DAYS"):
        with_embedding_cache(_Provider(), _settings(tmp_path))


def test_storage_failure_stops_before_the_next_paid_request(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    provider = _Provider()
    cached = with_embedding_cache(provider, settings)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("lovspor.embeddings.cache_store._publish", refuse)
    with pytest.raises(EmbeddingCacheStorageError):
        _write(settings, cached)
    assert provider.requests == 1


def test_empty_document_pays_nothing_and_stores_nothing(cache_dir: Path, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    provider = _Provider()
    path, input_hash = _write_embeddings_for_doc(
        settings.lovverk_repo_path,
        "gjeldende-lover",
        "tom",
        "---\ntitle: t\n---\n\n# Bare tittel\n",
        with_embedding_cache(provider, settings),
    )
    assert provider.requests == 0
    assert path.exists()
    assert not list(cache_dir.rglob("*.lsec"))
    assert input_hash == hash_embedding_inputs([])


def test_uncached_boundary_is_todays_encode_then_quantize() -> None:
    provider = _Provider()
    inputs = [EmbeddingInput("§ 1", "a"), EmbeddingInput("§ 2", "b")]
    quantized, scale = quantized_document_vectors(provider, inputs, hash_embedding_inputs(inputs))
    assert quantized.dtype == np.int8
    assert quantized.shape == (2, 4)
    assert scale > 0
    assert provider.requests == 1


def test_provider_returning_the_wrong_shape_is_refused(cache_dir: Path, tmp_path: Path) -> None:
    class _Short(_Provider):
        def encode(self, texts: list[str]) -> np.ndarray:
            return super().encode(texts)[:, :3]

    with pytest.raises(EmbeddingCacheError, match="shape"):
        _write(_settings(tmp_path), with_embedding_cache(_Short(), _settings(tmp_path)))


def test_progress_reports_counts_only(
    cache_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(tmp_path)
    cached = with_embedding_cache(_Provider(), settings)
    assert isinstance(cached, CachingEmbedder)
    _write(settings, cached)
    _write(settings, cached, slug="lov-2")
    progress = cached.progress
    assert (progress.documents, progress.documents_cached) == (2, 1)
    assert (progress.units_paid, progress.units_cached) == (4, 0)
    assert progress.tokens_paid > 0
    line = progress.line()
    assert "Tekst" not in line
    assert str(cache_dir) not in line


def test_single_request_document_is_reused_without_unit_checkpoints(
    cache_dir: Path, tmp_path: Path
) -> None:
    """Design §6: a one-request document persists the document entry directly."""
    settings = _settings(tmp_path)
    inputs = [EmbeddingInput("§ 1", "en"), EmbeddingInput("§ 2", "to")]
    input_hash = hash_embedding_inputs(inputs)
    provider = _Provider()
    cached = with_embedding_cache(provider, settings)
    assert isinstance(cached, CachingEmbedder)
    first, scale = quantized_document_vectors(cached, inputs, input_hash)
    assert provider.requests == 1
    entries = list(cache_dir.rglob("*.lsec"))
    assert len(entries) == 1
    assert entries[0].parent.name == "documents"

    resumed = _Provider(fail_after=0)
    again = with_embedding_cache(resumed, settings)
    assert isinstance(again, CachingEmbedder)
    second, second_scale = quantized_document_vectors(again, inputs, input_hash)
    np.testing.assert_array_equal(second, first)
    assert second_scale == scale
    assert resumed.requests == 0


def test_expired_checkpoint_stops_resume_before_any_payment(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design §3.4 and §5.3: expired units are refusals, never paid-over misses."""
    settings = _settings(tmp_path)
    with pytest.raises(RuntimeError, match="job killed"):
        _write(settings, with_embedding_cache(_Provider(fail_after=1), settings))
    before = {p: p.read_bytes() for p in cache_dir.rglob("*.lsec")}
    assert len(before) == 1
    future = datetime.now(UTC) + timedelta(days=31)
    monkeypatch.setattr(
        "lovspor.embeddings.cache._read_policy",
        lambda: ReadPolicy(max_age=timedelta(days=30), clock=lambda: future),
    )
    provider = _Provider()
    with pytest.raises(EmbeddingCacheExpiredError):
        _write(settings, with_embedding_cache(provider, settings))
    assert provider.requests == 0
    assert {p: p.read_bytes() for p in cache_dir.rglob("*.lsec")} == before
    assert not list(settings.lovverk_repo_path.rglob("*.bin"))


def test_resumed_units_use_one_document_scale(cache_dir: Path, tmp_path: Path) -> None:
    """Design §4: early units retain floats until the whole document is available."""

    class _DifferentMagnitudes(_Provider):
        def encode(self, texts: list[str]) -> np.ndarray:
            super().encode(texts)  # Keep the interruption and paid-request accounting.
            return np.array([[float(text), 0, 0, 0] for text in texts], dtype=np.float32)

    settings = _settings(tmp_path)
    inputs = [EmbeddingInput(f"§ {i}", str(value)) for i, value in enumerate([1, 2, 64, 127])]
    input_hash = hash_embedding_inputs(inputs)
    interrupted = with_embedding_cache(_DifferentMagnitudes(fail_after=1), settings)
    assert isinstance(interrupted, CachingEmbedder)
    with pytest.raises(RuntimeError, match="job killed"):
        quantized_document_vectors(interrupted, inputs, input_hash)

    provider = _DifferentMagnitudes()
    resumed = with_embedding_cache(provider, settings)
    assert isinstance(resumed, CachingEmbedder)
    vectors, scale = quantized_document_vectors(resumed, inputs, input_hash)
    assert provider.requests == 1
    assert resumed.progress.units_cached == 1
    assert scale == 1.0
    np.testing.assert_array_equal(
        vectors, np.array([[1, 0, 0, 0], [2, 0, 0, 0], [64, 0, 0, 0], [127, 0, 0, 0]], np.int8)
    )
