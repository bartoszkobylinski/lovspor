"""run_sync's preflight against a real corpus on disk (issue #229).

Before any change detection, ``run_sync`` reads the manifest, runs the one-shot
Sprint 5/8/9 migrations when their triggers fire, and collects upstream. The
module's other tests switch every migration off, so nothing pinned which
manifest, which arguments and which result each step hands the next. Here the
triggers are real, each migration runs for real behind a recording spy, and
only the upstream download is replaced.
"""

import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import lovspor.sync.orchestrator as orchestrator_module
from lovspor.settings import Settings
from lovspor.storage.manifest import Manifest, ManifestRecord, read_manifest, write_manifest
from lovspor.sync.orchestrator import _UpstreamDoc, _write_one, run_sync
from tests.unit.test_embedding_space_identity import _FakeEmbedder
from tests.unit.test_sync_orchestrator import _git, _settings

_OBSERVED = datetime(2026, 8, 1, 4, 0, tzinfo=UTC)
_XML = (
    b'<!DOCTYPE html><html lang="nb"><head><title>Skattie</title></head>'
    b'<body><header class="documentHeader"><dl>'
    b'<dt class="title">Tittel</dt><dd class="title">Skattie</dd>'
    b'<dt class="refid">RefID</dt><dd class="refid">lov/x</dd>'
    b'</dl></header><main id="dokument"><h1>Skattie</h1>'
    b'<article class="legalP" id="ledd-1">Body of Skattie.</article>'
    b"</main></body></html>"
)
_DOC = _UpstreamDoc(
    doc_id="lov-1",
    source_dataset="gjeldende-lover",
    xml_bytes=_XML,
    xml_hash="a" * 64,
    slug="skattie",
    title="Skattie",
    eu_basis=(),
)


class _Spy:
    """Record each call's arguments, then run the real function."""

    def __init__(self, real: Callable[..., Any]) -> None:
        self.real = real
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.results: list[Any] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        result = self.real(*args, **kwargs)
        self.results.append(result)
        return result


class _Upstream:
    """The download step, recording what ``run_sync`` asked it for."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any) -> tuple[dict[str, _UpstreamDoc], tuple[str, ...]]:
        self.calls.append(args)
        return {_DOC.doc_id: _DOC}, ()


def _corpus(tmp_path: Path, *, history: bool = True, eu_basis: bool = True) -> Settings:
    """A committed corpus holding one published law and its manifest."""
    settings = _settings(tmp_path)
    repo = settings.lovverk_repo_path
    seeded = orchestrator_module._with_retrieved_at(_DOC, _OBSERVED)
    record, _paths = _write_one(settings, seeded, _OBSERVED, None)
    if not eu_basis:
        record = record.model_copy(update={"eu_basis": None})
    if history:
        (repo / "lover" / "history").mkdir()
        (repo / "lover" / "history" / ".keep").write_text("", encoding="utf-8")
    write_manifest(
        Manifest(generated_at=_OBSERVED, documents={"lov-1": record}), repo / "manifest.json"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "publish lov-1")
    return settings


def _spy(monkeypatch: pytest.MonkeyPatch, name: str) -> _Spy:
    spy = _Spy(getattr(orchestrator_module, name))
    monkeypatch.setattr(orchestrator_module, name, spy)
    return spy


def _keyless(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    def load(passed: Settings) -> None:
        assert passed is settings

    monkeypatch.setattr(orchestrator_module, "_load_embedder", load)


def _commit_subjects(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "log", "--format=%s"], cwd=repo, check=True, capture_output=True, text=True
    )
    return out.stdout.splitlines()


def _stored(settings: Settings) -> dict[str, ManifestRecord]:
    return read_manifest(settings.lovverk_repo_path / "manifest.json").documents


def test_upstream_is_collected_with_the_settings_the_archive_cache_and_the_disk_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path)
    cache_dir = settings.data_dir / "cache" / "archives"
    cache_dir.mkdir(parents=True)  # a warm cache from an earlier run must not abort this one
    upstream = _Upstream()
    _keyless(monkeypatch, settings)
    monkeypatch.setattr(orchestrator_module, "_collect_upstream", upstream)

    report = run_sync(settings)

    [(passed_settings, passed_cache, passed_prior)] = upstream.calls
    assert passed_settings is settings
    assert passed_cache == cache_dir
    assert passed_prior.documents == _stored(settings)
    assert report.unchanged_count == 1


def test_a_corpus_without_history_runs_the_history_migration_before_collecting_upstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path, history=False)
    repo = settings.lovverk_repo_path
    on_disk = _stored(settings)
    migrate = _spy(monkeypatch, "_run_sprint5_history_migration")
    upstream = _Upstream()
    _keyless(monkeypatch, settings)
    monkeypatch.setattr(orchestrator_module, "_collect_upstream", upstream)

    run_sync(settings)

    [(args, kwargs)] = migrate.calls
    assert kwargs == {}
    passed_repo, passed_manifest_path, passed_prior, passed_now = args
    assert passed_repo == repo
    assert passed_manifest_path == repo / "manifest.json"
    assert passed_prior.documents == on_disk
    assert passed_now.tzinfo is UTC
    assert (repo / "lover" / "history").is_dir()
    assert any(
        subject.startswith("migration: generate history") for subject in _commit_subjects(repo)
    )
    # Everything after the migration works from the manifest it returned.
    assert upstream.calls[0][2] is migrate.results[0]


def test_a_corpus_with_history_skips_the_history_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path)
    migrate = _spy(monkeypatch, "_run_sprint5_history_migration")
    _keyless(monkeypatch, settings)
    monkeypatch.setattr(orchestrator_module, "_collect_upstream", _Upstream())

    run_sync(settings)

    assert migrate.calls == []


def test_a_record_without_eu_basis_runs_the_backfill_with_the_collected_upstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path, eu_basis=False)
    repo = settings.lovverk_repo_path
    on_disk = _stored(settings)
    migrate = _spy(monkeypatch, "_run_sprint8_eu_basis_migration")
    _keyless(monkeypatch, settings)
    monkeypatch.setattr(orchestrator_module, "_collect_upstream", _Upstream())

    report = run_sync(settings)

    [(args, kwargs)] = migrate.calls
    assert kwargs == {}
    passed_settings, passed_manifest_path, passed_prior, passed_upstream, passed_now = args
    assert passed_settings is settings
    assert passed_manifest_path == repo / "manifest.json"
    assert passed_prior.documents == on_disk
    assert passed_upstream == {_DOC.doc_id: _DOC}
    assert passed_now.tzinfo is UTC
    assert _stored(settings)["lov-1"].eu_basis == []
    # The backfilled manifest is what the rest of the sync compares against,
    # so the unchanged law stays unchanged instead of being re-written.
    assert report.unchanged_count == 1
    assert report.changed_count == 0


def test_a_keyed_sync_backfills_a_missing_sidecar_with_the_adapters_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = _corpus(tmp_path)
    repo = settings.lovverk_repo_path
    on_disk = _stored(settings)
    embedder = _FakeEmbedder()

    def load(passed: Settings) -> _FakeEmbedder:
        assert passed is settings
        return embedder

    monkeypatch.setattr(orchestrator_module, "_load_embedder", load)
    needs = _spy(monkeypatch, "_needs_sprint9_embeddings_migration")
    migrate = _spy(monkeypatch, "_run_sprint9_embeddings_migration")
    monkeypatch.setattr(orchestrator_module, "_collect_upstream", _Upstream())

    report = run_sync(settings, allow_mass_reembed=True)

    [(needs_args, _needs_kwargs)] = needs.calls
    assert needs_args[0].documents == on_disk
    assert needs_args[1] == repo
    assert needs_args[2] == embedder.space_id
    [(args, kwargs)] = migrate.calls
    passed_settings, passed_prior, passed_embedder, passed_now, passed_target = args
    assert passed_settings is settings
    assert passed_prior.documents == on_disk
    assert passed_embedder is embedder
    assert passed_now.tzinfo is UTC
    assert passed_target == embedder.space_id
    assert kwargs == {"allow_mass_reembed": True}
    assert "mass-reembed override active" in capsys.readouterr().err
    assert (repo / "lover" / "embeddings" / "skattie.bin").is_file()
    assert _stored(settings)["lov-1"].embedding_space_id == embedder.space_id
    assert "migration: backfill embeddings for 1 documents" in _commit_subjects(repo)
    assert report.unchanged_count == 1


def test_a_keyed_sync_with_every_sidecar_current_skips_the_backfill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path)
    embedder = _FakeEmbedder()
    monkeypatch.setattr(orchestrator_module, "_load_embedder", lambda _settings: embedder)
    monkeypatch.setattr(orchestrator_module, "_collect_upstream", _Upstream())
    run_sync(settings, allow_mass_reembed=True)
    migrate = _spy(monkeypatch, "_run_sprint9_embeddings_migration")

    run_sync(settings)

    assert migrate.calls == []


def test_a_keyless_sync_never_asks_whether_embeddings_need_a_backfill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path)  # its one law has no sidecar at all
    needs = _spy(monkeypatch, "_needs_sprint9_embeddings_migration")
    migrate = _spy(monkeypatch, "_run_sprint9_embeddings_migration")
    _keyless(monkeypatch, settings)
    monkeypatch.setattr(orchestrator_module, "_collect_upstream", _Upstream())

    run_sync(settings)

    assert needs.calls == []
    assert migrate.calls == []
    assert not (settings.lovverk_repo_path / "lover" / "embeddings").exists()
