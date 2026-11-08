"""The durable cache inside a real ``run_sync`` (#802 cross-stack audit).

Only ``create_embedder`` is a fake provider; ``_load_embedder`` builds the
production adapter (meter, then cache). These pin, on the whole sync rather
than one function, that turning the cache on changes nothing but who pays:
the #803/#815 vetoes still come before any spend and leave the cache empty,
and the published corpus is byte-for-byte the one an uncached sync writes.
"""

import re
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

import lovspor.sync.orchestrator as orchestrator_module
from lovspor.embeddings.cache import CACHE_DIR_VARIABLE
from lovspor.errors import MassReembedError
from lovspor.sync.orchestrator import run_sync
from lovspor.temporal_attestation import AttestationError
from tests.unit.test_sync_change_guard_before_backfill import _with_one_stale_sidecar
from tests.unit.test_sync_change_reembed_guard import (
    _CORPUS,
    _corpus,
    _CountingEmbedder,
    _doc,
    _state,
)
from tests.unit.test_sync_orchestrator import _disable_migrations


@pytest.fixture
def cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "cache"
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(root))
    return root


_PRODUCTION_LOAD_EMBEDDER = orchestrator_module._load_embedder


def _provider_for(monkeypatch: pytest.MonkeyPatch, docs: list, provider: object) -> None:
    """Fake only the provider factory; the loader is production's.

    ``_disable_migrations`` replaces ``_load_embedder`` too, so the real one is
    put back here, after it.
    """
    monkeypatch.setattr(orchestrator_module, "_load_embedder", _PRODUCTION_LOAD_EMBEDDER)
    monkeypatch.setattr(orchestrator_module, "create_embedder", lambda _config: provider)
    monkeypatch.setattr(
        orchestrator_module,
        "_collect_upstream",
        lambda *_args: ({doc.doc_id: doc for doc in docs}, ()),
    )


def _cache_entries(root: Path) -> list[Path]:
    return sorted(root.rglob("*.lsec")) if root.exists() else []


def test_mass_reembed_veto_comes_before_any_spend_with_the_cache_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_root: Path
) -> None:
    """#803: an ELI-scale change is refused before the backfill pays, cache on."""
    settings = _with_one_stale_sidecar(tmp_path)
    before = _state(settings.lovverk_repo_path)
    provider = _CountingEmbedder()
    _provider_for(monkeypatch, [_doc(i, "Body. ELI row.") for i in range(_CORPUS)], provider)

    with pytest.raises(MassReembedError):
        run_sync(settings)

    assert provider.texts == 0
    assert _state(settings.lovverk_repo_path) == before
    assert _cache_entries(cache_root) == []


def test_unattestable_change_veto_comes_before_any_spend_with_the_cache_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_root: Path
) -> None:
    """#815: an unrenderable changed document stops the sync before any embedding."""
    settings = _corpus(tmp_path, 3)
    _disable_migrations(monkeypatch)
    before = _state(settings.lovverk_repo_path)
    provider = _CountingEmbedder()
    broken = replace(_doc(1), xml_bytes=b"<html", xml_hash="f" * 64)
    _provider_for(
        monkeypatch, [_doc(0, "Body. Amended."), broken, _doc(2, "Body. Amended.")], provider
    )

    with pytest.raises(AttestationError, match="before any embedding"):
        run_sync(settings)

    assert provider.texts == 0
    assert _state(settings.lovverk_repo_path) == before
    assert _cache_entries(cache_root) == []


_TIMESTAMP = re.compile(rb"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:\+00:00|Z)")
_COMMIT_JSON = re.compile(rb'"commit": "[0-9a-f]{7,40}"')
_COMMIT_MD = re.compile(rb"`[0-9a-f]{7,40}`")


def _tree(repo: Path) -> dict[str, bytes]:
    """Every tracked file of the corpus at HEAD, by path.

    The sync stamps wall-clock times (retrieved_at and the like) into text
    files, and history files name commits whose ids depend on those times, so
    ISO timestamps and history commit ids are masked there; every other byte,
    and every byte of a binary sidecar, is compared as written.
    """
    names = subprocess.run(
        ["git", "ls-files"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.split()
    tree = {}
    for name in names:
        data = (repo / name).read_bytes()
        if not name.endswith(".bin"):
            data = _TIMESTAMP.sub(b"<time>", data)
            data = _COMMIT_MD.sub(b"`<id>`", _COMMIT_JSON.sub(b'"commit": "<id>"', data))
        tree[name] = data
    return tree


def _sync_backfill_and_change(
    base: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, bytes], int]:
    """One backfill and one change; the corpus tree and the texts paid for."""
    settings = _with_one_stale_sidecar(base)
    docs = [_doc(0), _doc(1, "Body. Amended."), *(_doc(i) for i in range(2, _CORPUS))]
    provider = _CountingEmbedder()
    _provider_for(monkeypatch, docs, provider)
    run_sync(settings)
    return _tree(settings.lovverk_repo_path), provider.texts


def test_cached_sync_publishes_the_same_corpus_bytes_as_an_uncached_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cache off, cache cold, cache warm: one corpus, byte for byte."""
    monkeypatch.delenv(CACHE_DIR_VARIABLE, raising=False)
    uncached, paid_off = _sync_backfill_and_change(tmp_path / "off", monkeypatch)
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(tmp_path / "cache"))
    cold, paid_cold = _sync_backfill_and_change(tmp_path / "cold", monkeypatch)
    warm, paid_warm = _sync_backfill_and_change(tmp_path / "warm", monkeypatch)

    assert (paid_off, paid_cold, paid_warm) == (2, 2, 0)

    assert uncached.keys() == cold.keys() == warm.keys()
    for name in uncached:
        assert cold[name] == uncached[name], name
        assert warm[name] == uncached[name], name
    assert any(name.endswith(".bin") for name in uncached)
