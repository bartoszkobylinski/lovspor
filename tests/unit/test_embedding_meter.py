"""Progress and the per-run token budget at the paid embedding call (#790 Task 1).

The budget is a ceiling, not a report: a request that would cross it is
refused before it is sent. Progress lines carry counts only.
"""

from pathlib import Path

import numpy as np
import pytest

import lovspor.sync.orchestrator as orchestrator_module
from lovspor.embeddings.cache import CACHE_DIR_VARIABLE, CachingEmbedder, with_embedding_cache
from lovspor.embeddings.meter import (
    BUDGET_VARIABLE,
    PROGRESS_VARIABLE,
    MeteredEmbedder,
    with_meter,
)
from lovspor.errors import ConfigError, EmbeddingBudgetError, LovsporError
from lovspor.settings import Settings
from lovspor.sync.orchestrator import _write_embeddings_for_doc
from tests.unit.test_embedding_space_identity import _FakeEmbedder, _UnidentifiedEmbedder

_RENDERED = "---\ntitle: t\n---\n\n" + "".join(
    f"### § {i}. Overskrift\n\nTekst nummer {i} om regler.\n\n" for i in range(1, 8)
)


class _Provider(_FakeEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.requests = 0

    def plan_requests(self, texts: list[str]) -> list[tuple[int, int]]:
        return [(i, min(i + 2, len(texts))) for i in range(0, len(texts), 2)]

    def encode(self, texts: list[str]) -> np.ndarray:
        self.requests += 1
        return super().encode(texts)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in (BUDGET_VARIABLE, PROGRESS_VARIABLE, CACHE_DIR_VARIABLE):
        monkeypatch.delenv(variable, raising=False)


def _settings(tmp_path: Path) -> Settings:
    corpus = tmp_path / "lovverk"
    corpus.mkdir(exist_ok=True)
    return Settings(data_dir=tmp_path / "data", lovverk_repo_path=corpus)


def _write(settings: Settings, embedder: object) -> None:
    _write_embeddings_for_doc(
        settings.lovverk_repo_path, "gjeldende-lover", "lov", _RENDERED, embedder
    )


def test_budget_error_is_a_lovspor_error() -> None:
    assert issubclass(EmbeddingBudgetError, LovsporError)


def test_no_budget_by_default() -> None:
    metered = with_meter(_Provider())
    assert isinstance(metered, MeteredEmbedder)
    metered.encode(["a"] * 3)
    assert metered.meter.requests == 1


def test_keyless_is_not_metered() -> None:
    assert with_meter(None) is None


def test_request_that_would_cross_the_budget_is_never_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(BUDGET_VARIABLE, "5")
    provider = _Provider()
    metered = with_meter(provider)
    assert metered is not None
    metered.encode(["ord ord"])
    with pytest.raises(EmbeddingBudgetError, match="budget reached"):
        metered.encode(["ord ord ord ord"])
    assert provider.requests == 1


def test_budget_exactly_reached_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BUDGET_VARIABLE, "2")
    provider = _Provider()
    metered = with_meter(provider)
    assert metered is not None
    metered.encode(["ord ord"])
    assert provider.requests == 1
    assert metered.meter.tokens == 2


@pytest.mark.parametrize("raw", ["0", "-1", "lots"])
def test_bad_budget_is_a_config_error(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv(BUDGET_VARIABLE, raw)
    with pytest.raises(ConfigError, match=BUDGET_VARIABLE):
        with_meter(_Provider())


def test_progress_line_every_n_requests_counts_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(PROGRESS_VARIABLE, "2")
    metered = with_meter(_Provider())
    assert metered is not None
    metered.encode(["hemmelig tekst"])
    assert capsys.readouterr().err == ""
    metered.encode(["hemmelig tekst"])
    err = capsys.readouterr().err
    assert "embedding-spend: requests=2 inputs=2" in err
    assert "hemmelig" not in err


def test_metered_adapter_keeps_the_space_identity() -> None:
    provider = _Provider()
    metered = with_meter(provider)
    assert metered is not None
    assert metered.space_id == provider.space_id
    assert metered.descriptor == provider.descriptor
    assert metered.get_dimension() == provider.get_dimension()
    assert metered.plan_requests(["a"] * 5) == provider.plan_requests(["a"] * 5)


def test_unidentified_adapter_stays_unidentified() -> None:
    metered = with_meter(_UnidentifiedEmbedder())
    assert metered is not None
    assert metered.descriptor is None
    assert orchestrator_module._embedder_identity(metered) == (None, None)


def test_metered_sidecar_is_byte_identical_to_unmetered(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write(settings, _Provider())
    plain = next(settings.lovverk_repo_path.rglob("*.bin")).read_bytes()
    _write(settings, with_meter(_Provider()))
    assert next(settings.lovverk_repo_path.rglob("*.bin")).read_bytes() == plain


def test_load_embedder_meters_and_then_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(tmp_path / "cache"))
    provider = _Provider()
    monkeypatch.setattr(orchestrator_module, "create_embedder", lambda _config: provider)
    loaded = orchestrator_module._load_embedder(_settings(tmp_path))
    assert isinstance(loaded, CachingEmbedder)
    assert loaded.space_id == provider.space_id


def test_budget_stop_with_cache_resumes_without_repaying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The budget stops a long document mid-way; the rerun pays only the rest."""
    settings = _settings(tmp_path)
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(tmp_path / "cache"))
    first = _Provider()
    monkeypatch.setenv(BUDGET_VARIABLE, "40")
    with pytest.raises(EmbeddingBudgetError):
        _write(settings, with_embedding_cache(with_meter(first), settings))
    assert 0 < first.requests < 4
    monkeypatch.delenv(BUDGET_VARIABLE)
    second = _Provider()
    _write(settings, with_embedding_cache(with_meter(second), settings))
    assert first.requests + second.requests == 4
