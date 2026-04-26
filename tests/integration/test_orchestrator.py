"""End-to-end orchestrator integration tests.

These tests use pytest-httpx to mock the Lovdata API plus a real
temp git repo for the corpus side. The tarballs fed to the mocked
downloader are synthetic, built in-process with the same Lovdata-
style HTML structure we render against in production.
"""

import io
import json
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from lovspor.errors import ConfigError
from lovspor.settings import Settings
from lovspor.sources.lovdata import DEFAULT_BASE_URL
from lovspor.sync.orchestrator import run_sync


def _minimal_law_html(doc_id: str, title: str) -> bytes:
    return (
        '<!DOCTYPE html><html lang="nb"><head><title>'
        f"{title}</title></head>"
        '<body><header class="documentHeader"><dl>'
        '<dt class="title">Tittel</dt>'
        f'<dd class="title">{title}</dd>'
        '<dt class="refid">RefID</dt>'
        f'<dd class="refid">lov/{doc_id}</dd>'
        "</dl></header>"
        '<main id="dokument">'
        f"<h1>{title}</h1>"
        f'<article class="legalP" id="ledd-1">Body of {title}.</article>'
        "</main></body></html>"
    ).encode()


def _build_tarball(path: Path, members: list[tuple[str, bytes]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, mode="w:bz2") as tar:
        for name, content in members:
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            info.type = tarfile.REGTYPE
            tar.addfile(info, io.BytesIO(content))


def _git_init_corpus(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        cwd=path,
        check=True,
    )


def _register_lovdata_mocks(
    httpx_mock: HTTPXMock,
    lover_tar: Path,
    forskrifter_tar: Path,
) -> None:
    catalogue: list[dict[str, Any]] = [
        {
            "filename": "gjeldende-lover.tar.bz2",
            "description": "Gjeldende lover",
            "sizeBytes": str(lover_tar.stat().st_size),
            "lastModified": "2026-04-22T01:31:00Z",
        },
        {
            "filename": "gjeldende-sentrale-forskrifter.tar.bz2",
            "description": "Gjeldende sentrale forskrifter",
            "sizeBytes": str(forskrifter_tar.stat().st_size),
            "lastModified": "2026-04-22T01:31:00Z",
        },
    ]
    httpx_mock.add_response(
        url=f"{DEFAULT_BASE_URL}/list",
        json=catalogue,
    )
    httpx_mock.add_response(
        url=f"{DEFAULT_BASE_URL}/get/gjeldende-lover.tar.bz2",
        content=lover_tar.read_bytes(),
    )
    httpx_mock.add_response(
        url=f"{DEFAULT_BASE_URL}/get/gjeldende-sentrale-forskrifter.tar.bz2",
        content=forskrifter_tar.read_bytes(),
    )


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep retry sleeps out of integration runs."""
    monkeypatch.setattr("lovspor.retry.time.sleep", lambda _seconds: None)


def test_run_sync_seeds_empty_corpus_with_single_law(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    tar_dir = tmp_path / "tarballs"
    lover_tar = tar_dir / "lover.tar.bz2"
    forskrifter_tar = tar_dir / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-19990326-014.xml", _minimal_law_html("19990326-014", "Skatteloven"))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)

    settings = Settings(data_dir=data_dir, lovverk_repo_path=corpus)
    report = run_sync(settings)

    assert report.new_count == 1
    assert report.changed_count == 0
    assert report.removed_count == 0
    assert report.unchanged_count == 0

    md_path = corpus / "lover" / "lov-19990326-014.md"
    assert md_path.exists()
    md = md_path.read_text(encoding="utf-8")
    assert 'title: "Skatteloven"' in md
    assert "# Skatteloven" in md

    manifest_path = corpus / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == 1
    assert "lov-19990326-014" in manifest["documents"]


def test_run_sync_is_idempotent_on_unchanged_state(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """HIGH regression guard: running sync twice without upstream changes
    produces 0 changed docs on the second run AND no new commit (previously
    the second run rewrote manifest.last_seen and created a sync commit).
    Codex PR #15 reproducer."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-17410217-000.xml", _minimal_law_html("17410217-000", "Vimpel"))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    first = run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))
    assert first.new_count == 1
    commit_count_after_first = _git_commit_count(corpus)

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    second = run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))
    assert second.new_count == 0
    assert second.changed_count == 0
    assert second.unchanged_count == 1
    assert _git_commit_count(corpus) == commit_count_after_first

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=corpus,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""


def test_run_sync_retains_removed_docs_as_tombstones(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """MEDIUM regression guard: a removed doc must remain in the manifest
    with status='removed' rather than vanishing. Preserves the audit
    trail and matches the contract read by detect_changes. Codex PR #15
    reproducer."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-gone.xml", _minimal_law_html("gone", "Goes Away"))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    _build_tarball(lover_tar, [])  # upstream dropped the doc
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    assert "lov-gone" in manifest["documents"]
    assert manifest["documents"]["lov-gone"]["status"] == "removed"


def _git_commit_count(repo: Path) -> int:
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


def test_run_sync_detects_and_commits_changed_document(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-x.xml", _minimal_law_html("x", "Version One"))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    # Rebuild tarball with changed content
    _build_tarball(
        lover_tar,
        [("nl/lov-x.xml", _minimal_law_html("x", "Version Two"))],
    )
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    report = run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert report.changed_count == 1
    assert report.new_count == 0
    md = (corpus / "lover" / "lov-x.md").read_text(encoding="utf-8")
    assert "Version Two" in md
    assert "Version One" not in md


def test_run_sync_removes_disappearing_document(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-gone.xml", _minimal_law_html("gone", "To Be Removed"))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))
    assert (corpus / "lover" / "lov-gone.md").exists()

    _build_tarball(lover_tar, [])  # upstream dropped the doc
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    report = run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert report.removed_count == 1
    assert not (corpus / "lover" / "lov-gone.md").exists()


def test_run_sync_raises_config_error_when_corpus_not_a_git_repo(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "not-a-repo"
    corpus.mkdir()
    settings = Settings(
        data_dir=tmp_path / "data",
        lovverk_repo_path=corpus,
    )
    with pytest.raises(ConfigError, match="not a git repository"):
        run_sync(settings)


def test_run_sync_raises_config_error_on_missing_upstream_archive(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """If Lovdata's /list catalogue no longer includes one of our tracked
    datasets, that's a configuration mismatch we should surface loudly
    rather than silently skip."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    _build_tarball(lover_tar, [])

    # Catalogue missing gjeldende-sentrale-forskrifter
    catalogue: list[dict[str, Any]] = [
        {
            "filename": "gjeldende-lover.tar.bz2",
            "description": "Gjeldende lover",
            "sizeBytes": str(lover_tar.stat().st_size),
            "lastModified": "2026-04-22T01:31:00Z",
        },
    ]
    httpx_mock.add_response(
        url=f"{DEFAULT_BASE_URL}/list",
        json=catalogue,
    )
    httpx_mock.add_response(
        url=f"{DEFAULT_BASE_URL}/get/gjeldende-lover.tar.bz2",
        content=lover_tar.read_bytes(),
    )

    settings = Settings(data_dir=data_dir, lovverk_repo_path=corpus)
    with pytest.raises(ConfigError, match="missing expected archive"):
        run_sync(settings)
