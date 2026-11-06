"""The change-path veto comes before the paid backfill migration (GPT-6 review on #803).

`run_sync` ran the Sprint 9 backfill, which embeds and commits stale
sidecars, before the change-path guard. A keyed corpus with one stale
sidecar (well under the backfill's own threshold) and an ELI-scale upstream
change paid for the repair, then was refused, and the refused job never
pushed the paid work. Here no migration is switched off: the refusal must
come before any encode, write, commit or manifest change.
"""

import subprocess
from pathlib import Path

import pytest

import lovspor.sync.orchestrator as orchestrator_module
from lovspor.embeddings.inputs import build_embedding_inputs
from lovspor.embeddings.model import count_input_tokens
from lovspor.errors import MassReembedError, MassRemovalError
from lovspor.settings import Settings
from lovspor.sync.orchestrator import run_sync
from tests.unit.test_sync_change_reembed_guard import (
    _CORPUS,
    _OBSERVED,
    _corpus,
    _CountingEmbedder,
    _doc,
    _state,
)
from tests.unit.test_sync_orchestrator import _git


def _with_one_stale_sidecar(tmp_path: Path) -> Settings:
    """A committed, embedded corpus with history, where one sidecar is missing."""
    settings = _corpus(tmp_path, _CORPUS)
    repo = settings.lovverk_repo_path
    (repo / "lover" / "history").mkdir(parents=True, exist_ok=True)
    (repo / "lover" / "history" / ".keep").write_text("", encoding="utf-8")
    (repo / "lover" / "embeddings" / "lov-0.bin").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one sidecar lost")
    return settings


def _live_migrations(
    monkeypatch: pytest.MonkeyPatch, docs: list, embedder: _CountingEmbedder
) -> None:
    monkeypatch.setattr(orchestrator_module, "_load_embedder", lambda _settings: embedder)
    monkeypatch.setattr(
        orchestrator_module,
        "_collect_upstream",
        lambda *_args: ({doc.doc_id: doc for doc in docs}, ()),
    )


def _tokens_of_one() -> str:
    """The input tokens of one corpus document, formatted as the guard prints them."""
    doc = _doc(0)
    rendered = orchestrator_module.render_full_document(
        doc.xml_bytes, orchestrator_module._frontmatter_context(doc, _OBSERVED)
    )
    return f"{sum(count_input_tokens(i.text) for i in build_embedding_inputs(rendered)):,}"


def _manifest_bytes(settings: Settings) -> bytes:
    return (settings.lovverk_repo_path / "manifest.json").read_bytes()


def test_an_eli_scale_change_is_refused_before_the_backfill_pays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _with_one_stale_sidecar(tmp_path)
    repo = settings.lovverk_repo_path
    before, manifest = _state(repo), _manifest_bytes(settings)
    embedder = _CountingEmbedder()
    _live_migrations(monkeypatch, [_doc(i, "Body. ELI row.") for i in range(_CORPUS)], embedder)

    with pytest.raises(MassReembedError, match=f"{_CORPUS} changed document"):
        run_sync(settings)

    assert embedder.texts == 0
    assert _state(repo) == before
    assert _manifest_bytes(settings) == manifest
    assert not (repo / "lover" / "embeddings" / "lov-0.bin").exists()


def test_the_backfill_still_repairs_a_stale_sidecar_when_nothing_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _with_one_stale_sidecar(tmp_path)
    repo = settings.lovverk_repo_path
    embedder = _CountingEmbedder()
    _live_migrations(monkeypatch, [_doc(i) for i in range(_CORPUS)], embedder)

    report = run_sync(settings)

    assert report.changed_count == 0
    assert embedder.texts == 1
    assert (repo / "lover" / "embeddings" / "lov-0.bin").is_file()
    subjects = subprocess.run(
        ["git", "log", "--format=%s"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    assert "migration: backfill embeddings for 1 documents" in subjects


def test_an_ordinary_change_and_the_backfill_both_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _with_one_stale_sidecar(tmp_path)
    embedder = _CountingEmbedder()
    docs = [_doc(0)] + [_doc(1, "Body. Amended.")] + [_doc(i) for i in range(2, _CORPUS)]
    _live_migrations(monkeypatch, docs, embedder)

    report = run_sync(settings)

    assert report.changed_count == 1
    assert embedder.texts == 2


@pytest.mark.parametrize("allow_mass_reembed", [False, True])
def test_mass_removal_is_refused_before_the_backfill_pays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, allow_mass_reembed: bool
) -> None:
    """The moved removal veto precedes backfill; a re-embed override cannot waive it.

    An empty upstream has no change-path embedding workload, but the missing
    sidecar still needs paid repair. Keep migrations live to exercise the
    ordering of the removal guard and that repair in run_sync.
    """
    settings = _with_one_stale_sidecar(tmp_path)
    repo = settings.lovverk_repo_path
    before, manifest = _state(repo), _manifest_bytes(settings)
    files_before = {
        path.relative_to(repo): path.read_bytes()
        for path in repo.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(repo).parts
    }
    embedder = _CountingEmbedder()
    _live_migrations(monkeypatch, [], embedder)

    with pytest.raises(MassRemovalError, match="100/100"):
        run_sync(settings, allow_mass_reembed=allow_mass_reembed)

    assert embedder.texts == 0
    assert _state(repo) == before
    assert _manifest_bytes(settings) == manifest
    assert {
        path.relative_to(repo): path.read_bytes()
        for path in repo.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(repo).parts
    } == files_before


def test_an_override_with_no_change_work_names_only_the_backfill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The change guard has nothing to authorize, so it prints no override notice."""
    settings = _with_one_stale_sidecar(tmp_path)
    embedder = _CountingEmbedder()
    _live_migrations(monkeypatch, [_doc(i) for i in range(_CORPUS)], embedder)

    run_sync(settings, allow_mass_reembed=True)

    notices = [line for line in capsys.readouterr().err.splitlines() if "override active" in line]
    assert notices == [
        f"mass-reembed override active: 1 document(s) selected for re-embedding "
        f"(1.0% of {_CORPUS} current documents), ~{_tokens_of_one()} input tokens"
    ]
