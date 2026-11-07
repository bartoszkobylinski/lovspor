"""One token budget and one meter for a whole ``run_sync`` (GPT-6 review on #828).

``run_sync`` pays in two phases: the Sprint 9 backfill (stale sidecars) and
the regular new/changed/renamed loop. Each phase used to call
``_load_embedder``, and each call wrapped the provider in a fresh
``MeteredEmbedder`` with its own ``SpendMeter`` at zero, so a sync could spend
``LOVSPOR_EMBEDDING_TOKEN_BUDGET`` in the backfill and again in the change
loop. These tests drive the real ``_load_embedder`` (only ``create_embedder``
is replaced by a fake provider) so the metered, optionally cached adapter is
the one production builds.
"""

from pathlib import Path

import numpy as np
import pytest

import lovspor.sync.orchestrator as orchestrator_module
from lovspor.embeddings.cache import CACHE_DIR_VARIABLE
from lovspor.embeddings.inputs import build_embedding_inputs
from lovspor.embeddings.meter import BUDGET_VARIABLE, PROGRESS_VARIABLE
from lovspor.embeddings.model import count_input_tokens
from lovspor.errors import EmbeddingBudgetError
from lovspor.sync.orchestrator import run_sync
from tests.unit.test_sync_change_guard_before_backfill import _with_one_stale_sidecar
from tests.unit.test_sync_change_reembed_guard import (
    _CORPUS,
    _OBSERVED,
    _corpus,
    _CountingEmbedder,
    _doc,
)

_AMENDED = "Body. Amended."


class _Provider(_CountingEmbedder):
    """The fake paid provider, also counting requests."""

    def __init__(self) -> None:
        super().__init__()
        self.requests = 0

    def encode(self, texts: list[str]) -> np.ndarray:
        self.requests += 1
        return super().encode(texts)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in (BUDGET_VARIABLE, PROGRESS_VARIABLE, CACHE_DIR_VARIABLE):
        monkeypatch.delenv(variable, raising=False)


def _tokens(index: int, body: str = "Body.") -> int:
    """The estimated tokens of one corpus document's single embedding request."""
    doc = _doc(index, body)
    rendered = orchestrator_module.render_full_document(
        doc.xml_bytes, orchestrator_module._frontmatter_context(doc, _OBSERVED)
    )
    return sum(count_input_tokens(i.text) for i in build_embedding_inputs(rendered))


def _upstream(changed: bool) -> list:
    second = _doc(1, _AMENDED) if changed else _doc(1)
    return [_doc(0), second, *(_doc(i) for i in range(2, _CORPUS))]


def _provider_for(monkeypatch: pytest.MonkeyPatch, docs: list, provider: _Provider | None) -> None:
    """Replace only the provider factory; ``_load_embedder`` stays production."""
    monkeypatch.setattr(orchestrator_module, "create_embedder", lambda _config: provider)
    monkeypatch.setattr(
        orchestrator_module,
        "_collect_upstream",
        lambda *_args: ({doc.doc_id: doc for doc in docs}, ()),
    )


def _one_request_budget() -> int:
    """Room for either phase's request alone, never for both."""
    return max(_tokens(0), _tokens(1, _AMENDED))


def _cache_on(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(root))


@pytest.mark.parametrize("cache", [False, True], ids=["cache-off", "cache-on"])
def test_backfill_and_change_share_one_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cache: bool,
) -> None:
    """The backfill pays its one request; the change request is refused unsent."""
    settings = _with_one_stale_sidecar(tmp_path)
    if cache:
        _cache_on(monkeypatch, tmp_path / "cache")
    provider = _Provider()
    _provider_for(monkeypatch, _upstream(changed=True), provider)
    monkeypatch.setenv(BUDGET_VARIABLE, str(_one_request_budget()))
    monkeypatch.setenv(PROGRESS_VARIABLE, "1")

    with pytest.raises(
        EmbeddingBudgetError, match=f"{_tokens(0)} estimated tokens in completed calls"
    ):
        run_sync(settings)

    assert provider.requests == 1
    spend = [line for line in capsys.readouterr().err.splitlines() if "embedding-spend" in line]
    assert spend == [
        f"embedding-spend: completed_calls=1 inputs=1 estimated_tokens_completed={_tokens(0)} "
        f"budget={_one_request_budget()}"
    ]


def test_backfill_only_fits_the_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _with_one_stale_sidecar(tmp_path)
    provider = _Provider()
    _provider_for(monkeypatch, _upstream(changed=False), provider)
    monkeypatch.setenv(BUDGET_VARIABLE, str(_tokens(0)))

    report = run_sync(settings)

    assert report.changed_count == 0
    assert provider.requests == 1
    assert (settings.lovverk_repo_path / "lover" / "embeddings" / "lov-0.bin").is_file()


def test_change_only_fits_the_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _corpus(tmp_path, _CORPUS)
    provider = _Provider()
    _provider_for(monkeypatch, _upstream(changed=True), provider)
    monkeypatch.setenv(BUDGET_VARIABLE, str(_tokens(1, _AMENDED)))

    report = run_sync(settings)

    assert report.changed_count == 1
    assert provider.requests == 1


def test_no_op_sync_pays_nothing_under_any_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _corpus(tmp_path, _CORPUS)
    provider = _Provider()
    _provider_for(monkeypatch, _upstream(changed=False), provider)
    monkeypatch.setenv(BUDGET_VARIABLE, "1")

    report = run_sync(settings)

    assert report.changed_count == 0
    assert provider.requests == 0


def test_keyless_sync_ignores_the_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _with_one_stale_sidecar(tmp_path)
    _provider_for(monkeypatch, _upstream(changed=True), None)
    monkeypatch.setenv(BUDGET_VARIABLE, "1")

    report = run_sync(settings)

    assert report.changed_count == 1


def test_cache_hits_cost_nothing_against_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second corpus whose vectors are all cached pays zero under a 1-token budget."""
    _cache_on(monkeypatch, tmp_path / "cache")
    first = _with_one_stale_sidecar(tmp_path / "first")
    _provider_for(monkeypatch, _upstream(changed=True), _Provider())
    run_sync(first)

    second = _with_one_stale_sidecar(tmp_path / "second")
    provider = _Provider()
    _provider_for(monkeypatch, _upstream(changed=True), provider)
    monkeypatch.setenv(BUDGET_VARIABLE, "1")

    report = run_sync(second)

    assert report.changed_count == 1
    assert provider.requests == 0
