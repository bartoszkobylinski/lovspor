"""The sync consults and extends ``slugs.json`` (#871, ADR-0023 draft point 2, U1).

Rendering, the manifest and git are real; only the upstream download is
replaced (or, for ``_collect_upstream``, the Lovdata client and tarball index).
"""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

import lovspor.sync.orchestrator as orchestrator_module
from lovspor.errors import SlugOwnershipError
from lovspor.identity.registry import SlugOwner, SlugRegistry, read_registry, replace_registry
from lovspor.sources.lovdata import LovdataArchive
from lovspor.storage.manifest import Manifest
from lovspor.sync.document_io import write_derived_views
from lovspor.sync.orchestrator import _collect_upstream, _UpstreamDoc, run_sync
from tests.unit.test_sync_orchestrator import _git, _settings
from tests.unit.test_sync_orchestrator_actions import _ALFA, _BETA, _corpus, _doc, _serve, _stored

_LOST_OWNER = SlugOwner(owner="lov-0", assigned_at="2026-05-01T00:00:00+00:00")


def _seed_registry(repo: Path, registry: SlugRegistry) -> None:
    replace_registry(registry, repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "registry")


def _clean(repo: Path) -> bool:
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True
    )
    return status.stdout == ""


def test_a_sync_registers_a_new_slug_and_commits_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path, [_ALFA])
    repo = settings.lovverk_repo_path
    alfa_owner = SlugOwner(owner="lov-1", assigned_at="2026-08-01T04:00:00+00:00")
    _seed_registry(repo, SlugRegistry().assign("lover", "alfa", alfa_owner))
    _serve(monkeypatch, [_ALFA, _doc("lov-9", "ny", "Ny tekst.")])

    run_sync(settings)

    registry = read_registry(repo)
    assert registry is not None
    assert registry.owner_of("lover", "ny") == "lov-9"
    assert registry.owner_of("lover", "alfa") == "lov-1"
    assert _clean(repo)


def test_a_sync_without_a_registry_does_not_create_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path, [_ALFA])
    _serve(monkeypatch, [_ALFA, _BETA])

    run_sync(settings)

    assert read_registry(settings.lovverk_repo_path) is None
    assert _stored(settings)["lov-2"].slug == "beta"


def test_a_sync_refuses_to_serve_a_document_at_another_documents_slug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard 5b: ``_collect_upstream`` would have deflected the slug; an
    upstream that still arrives on a registered slug is refused, not committed."""
    settings = _corpus(tmp_path, [_ALFA])
    repo = settings.lovverk_repo_path
    _seed_registry(repo, SlugRegistry().assign("lover", "beta", _LOST_OWNER))
    _serve(monkeypatch, [_ALFA, _BETA])

    with pytest.raises(SlugOwnershipError, match="lov-0"):
        run_sync(settings)


def test_write_derived_views_stages_the_extended_registry(tmp_path: Path) -> None:
    settings = _corpus(tmp_path, [_ALFA])
    repo = settings.lovverk_repo_path
    _seed_registry(repo, SlugRegistry())
    manifest = Manifest(generated_at=datetime(2026, 11, 1, tzinfo=UTC), documents=_stored(settings))

    paths = write_derived_views(repo, manifest, ("gjeldende-lover",))

    assert repo / "slugs.json" in paths
    registry = read_registry(repo)
    assert registry is not None
    assert registry.owner_of("lover", "alfa") == "lov-1"


def _fake_client(archives: list[LovdataArchive]) -> type:
    class FakeDownload:
        def __init__(self, path: Path) -> None:
            self.path = path

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def list_datasets(self) -> list[LovdataArchive]:
            return archives

        def download(self, archive: LovdataArchive, cache_dir: Path) -> FakeDownload:
            return FakeDownload(cache_dir / archive.filename)

    return FakeClient


def test_collect_upstream_deflects_a_registered_slug_whose_owner_left_the_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """U1: the slug's owner has no manifest record (it was lost), so only the
    registry knows the slug is taken; the newer document gets a distinct slug."""
    settings = _settings(tmp_path)
    replace_registry(
        SlugRegistry().assign("lover", "alfa", _LOST_OWNER), settings.lovverk_repo_path
    )
    archives = [
        LovdataArchive(
            filename=f"{name}.tar.bz2",
            description=name,
            sizeBytes=1,
            lastModified="2026-11-01T00:00:00Z",
        )
        for name in ("gjeldende-lover", "gjeldende-sentrale-forskrifter")
    ]

    def fake_index(_tar: Path, dataset: str) -> list[_UpstreamDoc]:
        if dataset != "gjeldende-lover":
            return []
        return [_doc("lov-20260101-001", "alfa", "Alfa text.")]

    monkeypatch.setattr(orchestrator_module, "LovdataClient", _fake_client(archives))
    monkeypatch.setattr(orchestrator_module, "_index_tarball", fake_index)

    upstream, _drift = _collect_upstream(settings, tmp_path / "cache")

    assert upstream["lov-20260101-001"].slug == "alfa-2026"
