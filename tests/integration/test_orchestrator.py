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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

import lovspor.sync.orchestrator as orchestrator_module
from lovspor.errors import ConfigError
from lovspor.parsing.xml_normalizer import hash_normalized_xml
from lovspor.settings import Settings
from lovspor.sources.lovdata import DEFAULT_BASE_URL
from lovspor.storage.manifest import (
    Manifest,
    ManifestRecord,
    write_manifest,
)
from lovspor.sync.orchestrator import (
    _commit_with_history,
    _DocAction,
    _has_rename_path_overlap,
    run_sync,
)

_COLLISION_TITLE = "Vass og avlopsanleggslova"
_COLLISION_SLUG = "vass-og-avlopsanleggslova"


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


def _law_with_extra(title: str, extra_body: str) -> bytes:
    """Variant of _minimal_law_html that lets the test vary body content
    independently of the title (so the slug stays stable across runs)."""
    return (
        '<!DOCTYPE html><html lang="nb"><head><title>'
        f"{title}</title></head>"
        '<body><header class="documentHeader"><dl>'
        '<dt class="title">Tittel</dt>'
        f'<dd class="title">{title}</dd>'
        '<dt class="refid">RefID</dt>'
        '<dd class="refid">lov/x</dd>'
        "</dl></header>"
        '<main id="dokument">'
        f"<h1>{title}</h1>"
        f'<article class="legalP" id="ledd-1">{extra_body}</article>'
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


def _action(action: str, doc_id: str, *paths: str) -> _DocAction:
    return _DocAction(
        action=action,
        doc_type="lov",
        doc_id=doc_id,
        slug=doc_id,
        paths=tuple(Path(path) for path in paths),
    )


def _seed_collision_manifest(
    corpus: Path,
    xml_by_doc_id: dict[str, bytes],
    prior_slugs: dict[str, str],
) -> None:
    (corpus / "lover" / "history").mkdir(parents=True, exist_ok=True)
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)

    records: dict[str, ManifestRecord] = {}
    for doc_id, prior_slug in prior_slugs.items():
        old_path = corpus / "lover" / f"{prior_slug}.md"
        old_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.write_text(f"# Prior {doc_id}\n", encoding="utf-8")
        records[doc_id] = ManifestRecord(
            doc_type="lov",
            xml_hash=hash_normalized_xml(xml_by_doc_id[doc_id]),
            markdown_path=f"lover/{prior_slug}.md",
            source_dataset="gjeldende-lover",
            last_seen=datetime(2026, 4, 30, tzinfo=UTC),
            status="current",
            slug=prior_slug,
            title=_COLLISION_TITLE,
            eu_basis=[],
        )

    write_manifest(
        Manifest(generated_at=datetime(2026, 4, 30, tzinfo=UTC), documents=records),
        corpus / "manifest.json",
    )
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "seed collision state"], cwd=corpus, check=True)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep retry sleeps out of integration runs."""
    monkeypatch.setattr("lovspor.retry.time.sleep", lambda _seconds: None)


@pytest.mark.parametrize(
    ("actions", "expected"),
    [
        (
            [
                _action("rename", "a", "lover/x.md", "lover/y.md"),
                _action("rename", "b", "lover/y.md", "lover/x.md"),
            ],
            True,
        ),
        (
            [
                _action("rename", "a", "lover/x.md", "lover/y.md"),
                _action("rename", "b", "lover/y.md", "lover/z.md"),
                _action("rename", "c", "lover/z.md", "lover/x.md"),
            ],
            True,
        ),
        (
            [
                _action("rename", "a", "lover/a1.md", "lover/a2.md"),
                _action("rename", "b", "lover/b1.md", "lover/b2.md"),
            ],
            False,
        ),
        ([_action("rename", "a", "lover/x.md", "lover/y.md")], False),
        ([], False),
        ([_action("rename", "a", "lover/x.md")], False),
        (
            # update+rename overlap: an update with a slug change has
            # paths=(old, new) just like a rename. The detector now
            # catches this variant too (Codex PR-43 round 1 finding).
            [
                _action("update", "a", "lover/x.md", "lover/y.md"),
                _action("rename", "b", "lover/y.md", "lover/z.md"),
            ],
            True,
        ),
        (
            [
                _action("update", "a", "lover/x.md", "lover/y.md"),
                _action("update", "b", "lover/y.md", "lover/x.md"),
            ],
            True,
        ),
        (
            [
                _action("rename", "a", "lover/x.md", "lover/y.md"),
                _action("update", "b", "lover/y.md", "lover/z.md"),
            ],
            True,
        ),
        (
            [
                _action("add", "a", "lover/y.md"),
                _action("rename", "b", "lover/y.md", "lover/z.md"),
            ],
            False,
        ),
        (
            [
                _action("add", "a", "lover/x.md", "lover/y.md"),
                _action("rename", "b", "lover/y.md", "lover/z.md"),
            ],
            False,
        ),
    ],
)
def test_has_rename_path_overlap_cases(
    actions: list[_DocAction],
    expected: bool,
) -> None:
    assert _has_rename_path_overlap(actions) is expected


@pytest.mark.parametrize(
    (
        "git_commit_mode",
        "is_sprint4_migration",
        "force_bulk_commit",
        "expected_call",
        "expected_message",
    ),
    [
        (
            "per-document",
            False,
            True,
            "bulk",
            "sync: 0 new, 0 changed, 1 renamed, 0 removed",
        ),
        (
            "per-document",
            True,
            False,
            "bulk",
            "migration: rename 1 documents to slug-based filenames",
        ),
        (
            "single",
            False,
            False,
            "bulk",
            "sync: 0 new, 0 changed, 1 renamed, 0 removed",
        ),
        ("per-document", False, False, "per-doc", None),
        (
            "per-document",
            True,
            True,
            "bulk",
            "migration: rename 1 documents to slug-based filenames",
        ),
    ],
)
def test_commit_with_history_routing_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    git_commit_mode: str,
    is_sprint4_migration: bool,
    force_bulk_commit: bool,
    expected_call: str,
    expected_message: str | None,
) -> None:
    calls: list[tuple[str, str | None]] = []

    def record_bulk(
        _repo: Path,
        _actions: list[_DocAction],
        _extra_paths: list[Path],
        message: str,
    ) -> None:
        calls.append(("bulk", message))

    def record_per_doc(_repo: Path, _actions: list[_DocAction]) -> None:
        calls.append(("per-doc", None))

    def record_history(
        _repo: Path,
        _manifest_path: Path,
        _new_records: dict[str, ManifestRecord],
        _target_doc_ids: list[str],
        _now: datetime,
    ) -> None:
        calls.append(("history", None))

    monkeypatch.setattr(orchestrator_module, "_commit_bulk", record_bulk)
    monkeypatch.setattr(orchestrator_module, "_commit_per_doc_actions_only", record_per_doc)
    monkeypatch.setattr(orchestrator_module, "_commit_history_followup", record_history)
    monkeypatch.setattr(
        orchestrator_module,
        "_generate_and_apply_history",
        lambda _repo, records, _target_doc_ids: (records, []),
    )
    monkeypatch.setattr(orchestrator_module, "write_manifest", lambda _manifest, _path: None)
    monkeypatch.setattr(
        orchestrator_module,
        "generate_index",
        lambda repo, dataset, _manifest: repo / dataset / "INDEX.md",
    )
    monkeypatch.setattr(orchestrator_module, "git_add", lambda _repo, _paths: None)
    monkeypatch.setattr(orchestrator_module, "has_staged_changes", lambda _repo: False)

    settings = Settings(
        data_dir=tmp_path / "data",
        lovverk_repo_path=tmp_path / "lovverk",
        git_commit_mode=git_commit_mode,
    )
    record = ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path="lover/y.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 4, 30, tzinfo=UTC),
        status="current",
        slug="y",
        title="Y",
        eu_basis=[],
    )

    _commit_with_history(
        settings,
        repo=settings.lovverk_repo_path,
        manifest_path=settings.lovverk_repo_path / "manifest.json",
        actions=[_action("rename", "lov-a", "lover/x.md", "lover/y.md")],
        new_records={"lov-a": record},
        now=datetime(2026, 4, 30, tzinfo=UTC),
        is_sprint4_migration=is_sprint4_migration,
        force_bulk_commit=force_bulk_commit,
    )

    assert calls[0] == (expected_call, expected_message)
    if expected_call == "bulk":
        assert calls[1:] == [("history", None)]


def test_commit_with_history_default_force_bulk_commit_keeps_per_doc_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        orchestrator_module, "_commit_per_doc_actions_only", lambda *_args: calls.append("per-doc")
    )
    monkeypatch.setattr(
        orchestrator_module,
        "_generate_and_apply_history",
        lambda _repo, records, _target_doc_ids: (records, []),
    )
    monkeypatch.setattr(orchestrator_module, "write_manifest", lambda _manifest, _path: None)
    monkeypatch.setattr(
        orchestrator_module,
        "generate_index",
        lambda repo, dataset, _manifest: repo / dataset / "INDEX.md",
    )
    monkeypatch.setattr(orchestrator_module, "git_add", lambda _repo, _paths: None)
    monkeypatch.setattr(orchestrator_module, "has_staged_changes", lambda _repo: False)

    settings = Settings(
        data_dir=tmp_path / "data",
        lovverk_repo_path=tmp_path / "lovverk",
        git_commit_mode="per-document",
    )
    record = ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path="lover/y.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 4, 30, tzinfo=UTC),
        status="current",
        slug="y",
        title="Y",
        eu_basis=[],
    )

    _commit_with_history(
        settings,
        repo=settings.lovverk_repo_path,
        manifest_path=settings.lovverk_repo_path / "manifest.json",
        actions=[_action("rename", "lov-a", "lover/x.md", "lover/y.md")],
        new_records={"lov-a": record},
        now=datetime(2026, 4, 30, tzinfo=UTC),
        is_sprint4_migration=False,
    )

    assert calls == ["per-doc"]


def test_commit_with_history_bulk_stages_manifest_and_both_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_extra_paths: list[Path] = []

    def record_bulk(
        _repo: Path,
        _actions: list[_DocAction],
        extra_paths: list[Path],
        _message: str,
    ) -> None:
        captured_extra_paths.extend(extra_paths)

    monkeypatch.setattr(orchestrator_module, "_commit_bulk", record_bulk)
    monkeypatch.setattr(orchestrator_module, "_commit_history_followup", lambda *_args: None)
    monkeypatch.setattr(orchestrator_module, "write_manifest", lambda _manifest, _path: None)
    monkeypatch.setattr(
        orchestrator_module,
        "generate_index",
        lambda repo, dataset, _manifest: repo / dataset / "INDEX.md",
    )

    settings = Settings(
        data_dir=tmp_path / "data",
        lovverk_repo_path=tmp_path / "lovverk",
        git_commit_mode="per-document",
    )
    record = ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path="lover/y.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 4, 30, tzinfo=UTC),
        status="current",
        slug="y",
        title="Y",
        eu_basis=[],
    )

    _commit_with_history(
        settings,
        repo=settings.lovverk_repo_path,
        manifest_path=settings.lovverk_repo_path / "manifest.json",
        actions=[_action("rename", "lov-a", "lover/x.md", "lover/y.md")],
        new_records={"lov-a": record},
        now=datetime(2026, 4, 30, tzinfo=UTC),
        is_sprint4_migration=False,
        force_bulk_commit=True,
    )

    assert captured_extra_paths == [
        settings.lovverk_repo_path / "manifest.json",
        settings.lovverk_repo_path / "gjeldende-lover" / "INDEX.md",
        settings.lovverk_repo_path / "gjeldende-sentrale-forskrifter" / "INDEX.md",
    ]


def test_commit_with_history_bulk_excludes_removed_docs_from_history_followup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_target_doc_ids: list[str] = []

    def record_history(
        _repo: Path,
        _manifest_path: Path,
        _new_records: dict[str, ManifestRecord],
        target_doc_ids: list[str],
        _now: datetime,
    ) -> None:
        captured_target_doc_ids.extend(target_doc_ids)

    monkeypatch.setattr(orchestrator_module, "_commit_bulk", lambda *_args: None)
    monkeypatch.setattr(orchestrator_module, "_commit_history_followup", record_history)
    monkeypatch.setattr(orchestrator_module, "write_manifest", lambda _manifest, _path: None)
    monkeypatch.setattr(
        orchestrator_module,
        "generate_index",
        lambda repo, dataset, _manifest: repo / dataset / "INDEX.md",
    )

    settings = Settings(
        data_dir=tmp_path / "data",
        lovverk_repo_path=tmp_path / "lovverk",
        git_commit_mode="per-document",
    )
    record = ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path="lover/added.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 4, 30, tzinfo=UTC),
        status="current",
        slug="added",
        title="Added",
        eu_basis=[],
    )

    _commit_with_history(
        settings,
        repo=settings.lovverk_repo_path,
        manifest_path=settings.lovverk_repo_path / "manifest.json",
        actions=[
            _action("add", "lov-added", "lover/added.md"),
            _action("remove", "lov-removed", "lover/removed.md"),
        ],
        new_records={"lov-added": record},
        now=datetime(2026, 4, 30, tzinfo=UTC),
        is_sprint4_migration=False,
        force_bulk_commit=True,
    )

    assert captured_target_doc_ids == ["lov-added"]


def test_run_sync_changed_slug_delete_skips_path_written_by_new_doc(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)
    (corpus / "lover" / "history").mkdir(parents=True, exist_ok=True)
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)

    old_alpha_xml = _law_with_extra("Alpha", "Old A body.")
    new_alpha_xml = _law_with_extra("Alpha", "New doc body.")
    changed_beta_xml = _law_with_extra("Beta", "Changed A body.")
    legacy_path = corpus / "lover" / "alpha.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Prior alpha\n", encoding="utf-8")
    manifest = Manifest(
        generated_at=datetime(2026, 4, 30, tzinfo=UTC),
        documents={
            "lov-a": ManifestRecord(
                doc_type="lov",
                xml_hash=hash_normalized_xml(old_alpha_xml),
                markdown_path="lover/alpha.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 30, tzinfo=UTC),
                status="current",
                slug="alpha",
                title="Alpha",
                eu_basis=[],
            ),
        },
    )
    write_manifest(manifest, corpus / "manifest.json")
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "seed alpha"], cwd=corpus, check=True)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [
            ("nl/lov-a.xml", changed_beta_xml),
            ("nl/lov-new.xml", new_alpha_xml),
        ],
    )
    _build_tarball(forskrifter_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)

    calls: list[tuple[str, str]] = []

    def tracking_write(path: Path, content: str) -> None:
        calls.append(("write", path.name))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def tracking_delete(path: Path) -> None:
        calls.append(("delete", path.name))
        path.unlink(missing_ok=True)

    monkeypatch.setattr(orchestrator_module, "write_document", tracking_write)
    monkeypatch.setattr(orchestrator_module, "delete_document", tracking_delete)
    monkeypatch.setattr(orchestrator_module, "_commit_with_history", lambda *_args, **_kwargs: None)

    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert ("write", "alpha.md") in calls
    assert ("write", "beta.md") in calls
    assert ("delete", "alpha.md") not in calls
    assert "New doc body." in (corpus / "lover" / "alpha.md").read_text(encoding="utf-8")
    assert "Changed A body." in (corpus / "lover" / "beta.md").read_text(encoding="utf-8")


def test_run_sync_removed_delete_is_not_gated_by_written_paths(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)
    (corpus / "lover" / "history").mkdir(parents=True, exist_ok=True)
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)

    old_alpha_xml = _law_with_extra("Alpha", "Removed doc body.")
    new_alpha_xml = _law_with_extra("Alpha", "Replacement doc body.")
    legacy_path = corpus / "lover" / "alpha.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Prior alpha\n", encoding="utf-8")
    manifest = Manifest(
        generated_at=datetime(2026, 4, 30, tzinfo=UTC),
        documents={
            "lov-old": ManifestRecord(
                doc_type="lov",
                xml_hash=hash_normalized_xml(old_alpha_xml),
                markdown_path="lover/alpha.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 30, tzinfo=UTC),
                status="current",
                slug="alpha",
                title="Alpha",
                eu_basis=[],
            ),
        },
    )
    write_manifest(manifest, corpus / "manifest.json")
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "seed removed alpha"], cwd=corpus, check=True)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-new.xml", new_alpha_xml)])
    _build_tarball(forskrifter_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)

    calls: list[tuple[str, str]] = []

    def tracking_write(path: Path, content: str) -> None:
        calls.append(("write", path.name))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def tracking_delete(path: Path) -> None:
        calls.append(("delete", path.name))
        path.unlink(missing_ok=True)

    monkeypatch.setattr(orchestrator_module, "write_document", tracking_write)
    monkeypatch.setattr(orchestrator_module, "delete_document", tracking_delete)
    monkeypatch.setattr(orchestrator_module, "_commit_with_history", lambda *_args, **_kwargs: None)

    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert calls == [("write", "alpha.md"), ("delete", "alpha.md")]
    assert not (corpus / "lover" / "alpha.md").exists()


def test_run_sync_writes_all_rename_targets_before_deleting_old_paths(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    xml_by_doc_id = {
        "lov-a": _law_with_extra(_COLLISION_TITLE, "Current A body."),
        "lov-b": _law_with_extra(_COLLISION_TITLE, "Current B body."),
        "lov-c": _law_with_extra(_COLLISION_TITLE, "Current C body."),
    }
    prior_slugs = {
        "lov-a": f"{_COLLISION_SLUG}-2",
        "lov-b": f"{_COLLISION_SLUG}-3",
        "lov-c": f"{_COLLISION_SLUG}-4",
    }
    _seed_collision_manifest(corpus, xml_by_doc_id, prior_slugs)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [(f"nl/{doc_id}.xml", xml) for doc_id, xml in xml_by_doc_id.items()],
    )
    _build_tarball(forskrifter_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)

    calls: list[tuple[str, str]] = []

    def tracking_write(path: Path, content: str) -> None:
        calls.append(("write", path.name))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def tracking_delete(path: Path) -> None:
        calls.append(("delete", path.name))
        path.unlink(missing_ok=True)

    monkeypatch.setattr(orchestrator_module, "write_document", tracking_write)
    monkeypatch.setattr(orchestrator_module, "delete_document", tracking_delete)

    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert calls == [
        ("write", f"{_COLLISION_SLUG}.md"),
        ("write", f"{_COLLISION_SLUG}-2.md"),
        ("write", f"{_COLLISION_SLUG}-3.md"),
        ("delete", f"{_COLLISION_SLUG}-4.md"),
    ]


def test_run_sync_handles_two_way_collision_suffix_swap_with_bulk_commit(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    xml_by_doc_id = {
        "lov-a": _law_with_extra(_COLLISION_TITLE, "Current A body."),
        "lov-b": _law_with_extra(_COLLISION_TITLE, "Current B body."),
    }
    _seed_collision_manifest(
        corpus,
        xml_by_doc_id,
        {
            "lov-a": f"{_COLLISION_SLUG}-2",
            "lov-b": _COLLISION_SLUG,
        },
    )
    commits_before = _git_commit_count(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [(f"nl/{doc_id}.xml", xml) for doc_id, xml in xml_by_doc_id.items()],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    report = run_sync(
        Settings(
            data_dir=data_dir,
            lovverk_repo_path=corpus,
            git_commit_mode="per-document",
        ),
    )

    assert report.new_count == 0
    assert report.changed_count == 0
    assert report.removed_count == 0
    assert report.unchanged_count == 2

    base_path = corpus / "lover" / f"{_COLLISION_SLUG}.md"
    suffixed_path = corpus / "lover" / f"{_COLLISION_SLUG}-2.md"
    assert "Current A body." in base_path.read_text(encoding="utf-8")
    assert "Current B body." in suffixed_path.read_text(encoding="utf-8")

    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["documents"]["lov-a"]["markdown_path"] == (f"lover/{_COLLISION_SLUG}.md")
    assert manifest["documents"]["lov-b"]["markdown_path"] == (f"lover/{_COLLISION_SLUG}-2.md")
    assert _git_commit_count(corpus) == commits_before + 2
    log = _git_log_subjects(corpus)
    assert "sync: 0 new, 0 changed, 2 renamed, 0 removed" in log
    assert "sync: update history for 2 documents" in log
    assert f"rename(lov): {_COLLISION_SLUG}" not in log


def test_run_sync_handles_changed_slug_overlapping_rename_old_path(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    old_alpha_xml = _law_with_extra("Alpha", "Old A body.")
    new_beta_xml = _law_with_extra("Beta", "New A body.")
    gamma_xml = _law_with_extra("Gamma", "Stable B body.")
    _seed_collision_manifest(
        corpus,
        {
            "lov-a": old_alpha_xml,
            "lov-b": gamma_xml,
        },
        {
            "lov-a": "alpha",
            "lov-b": "beta",
        },
    )
    commits_before = _git_commit_count(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [
            ("nl/lov-a.xml", new_beta_xml),
            ("nl/lov-b.xml", gamma_xml),
        ],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert "New A body." in (corpus / "lover" / "beta.md").read_text(encoding="utf-8")
    assert "Stable B body." in (corpus / "lover" / "gamma.md").read_text(encoding="utf-8")
    assert _git_commit_count(corpus) == commits_before + 2
    log = _git_log_subjects(corpus)
    assert "sync: 0 new, 1 changed, 1 renamed, 0 removed" in log
    assert "sync: update history for 2 documents" in log
    assert "update(lov): beta" not in log
    assert "rename(lov): gamma" not in log


def test_run_sync_writes_index_files_for_both_datasets(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """End-to-end: a successful sync writes lover/INDEX.md and
    forskrifter/INDEX.md listing every current doc."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-1.xml", _law_with_extra("Skatteloven", "body"))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    lover_index = corpus / "lover" / "INDEX.md"
    forskrifter_index = corpus / "forskrifter" / "INDEX.md"
    assert lover_index.exists()
    assert forskrifter_index.exists()
    assert "skatteloven" in lover_index.read_text(encoding="utf-8")
    assert "_0 current documents_" in forskrifter_index.read_text(encoding="utf-8")


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

    md_path = corpus / "lover" / "skatteloven.md"
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


def test_per_document_commit_mode_creates_one_commit_per_change(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """In per-document mode each changed/new/removed/renamed doc gets
    its own commit, plus a final 'sync: update manifest' commit."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    body = "Stable body content."
    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [
            ("nl/lov-1.xml", _law_with_extra("First", body)),
            ("nl/lov-2.xml", _law_with_extra("Second", body)),
        ],
    )
    _build_tarball(forskrifter_tar, [])

    settings = Settings(
        data_dir=data_dir,
        lovverk_repo_path=corpus,
        git_commit_mode="per-document",
    )
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(settings)

    # Initial seed: 2 add commits + 1 manifest commit = 3
    assert _git_commit_count(corpus) == 3
    log = _git_log_subjects(corpus)
    assert "add(lov): first" in log
    assert "add(lov): second" in log
    assert "sync: update manifest" in log


def test_single_commit_mode_creates_bulk_commit_plus_history_followup(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """Single mode produces TWO commits in Sprint 5+: the bulk
    docs+meta commit (the "single" semantic, unchanged) and a
    follow-up commit that adds per-act history. The follow-up is
    required because history extraction needs the docs commit to
    exist before ``git log`` can see it (chicken-and-egg). See
    decisions.md §12d."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [
            ("nl/lov-1.xml", _law_with_extra("Alpha", "body")),
            ("nl/lov-2.xml", _law_with_extra("Beta", "body")),
        ],
    )
    _build_tarball(forskrifter_tar, [])

    settings = Settings(
        data_dir=data_dir,
        lovverk_repo_path=corpus,
        git_commit_mode="single",
    )
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(settings)

    assert _git_commit_count(corpus) == 2
    log = _git_log_subjects(corpus)
    assert "sync: 2 new" in log
    assert "sync: update history for 2 documents" in log


def test_migration_creates_bulk_commit_plus_history_followup_in_per_document_mode(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """Sprint 3 -> Sprint 4 transition: a manifest with slug=None
    records triggers a single 'migration: rename ...' commit covering
    all renames + manifest + INDEX, overriding per-document mode.
    Sprint 5 PR-B added a second commit ('sync: update history for N
    documents') because history extraction has to wait for the
    migration commit to land before ``git log`` can see it."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    body = "Body that does not change."
    xml = _law_with_extra("Skattie", body)
    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-x.xml", xml)])
    _build_tarball(forskrifter_tar, [])

    # Pre-write a Sprint-3-style manifest: same hash, no slug,
    # markdown_path uses old doc_id naming.
    legacy_path = corpus / "lover" / "lov-x.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Legacy file\n", encoding="utf-8")
    manifest = Manifest(
        generated_at=datetime(2026, 4, 25, tzinfo=UTC),
        documents={
            "lov-x": ManifestRecord(
                doc_type="lov",
                xml_hash=hash_normalized_xml(xml),
                markdown_path="lover/lov-x.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 25, tzinfo=UTC),
                status="current",
                # slug=None and title=None — Sprint 3 record
            ),
        },
    )
    write_manifest(manifest, corpus / "manifest.json")
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "Sprint 3 seed"], cwd=corpus, check=True)
    commits_before = _git_commit_count(corpus)

    settings = Settings(
        data_dir=data_dir,
        lovverk_repo_path=corpus,
        git_commit_mode="per-document",
    )
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(settings)

    # Migration commit + history follow-up commit = 2 new commits.
    assert _git_commit_count(corpus) == commits_before + 2
    log = _git_log_subjects(corpus)
    assert "migration: rename" in log
    assert "sync: update history for 1 documents" in log

    assert (corpus / "lover" / "skattie.md").exists()
    assert not (corpus / "lover" / "lov-x.md").exists()


def _git_log_subjects(repo: Path) -> str:
    return subprocess.run(
        ["git", "log", "--format=%s"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_collision_resolution_is_scoped_per_dataset(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """MEDIUM regression guard: a law and a regulation that slugify to the
    same name must coexist as lover/<slug>.md and forskrifter/<slug>.md.
    They live in different subdirectories so there is no real filename
    conflict. Codex PR #17 reproducer."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    body = "body"
    _build_tarball(
        lover_tar,
        [("nl/lov-1.xml", _law_with_extra("Skatteloven", body))],
    )
    _build_tarball(
        forskrifter_tar,
        [("sf/sf-1.xml", _law_with_extra("Skatteloven", body))],
    )

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    # Both keep bare 'skatteloven' — different subdirs, no conflict.
    assert (corpus / "lover" / "skatteloven.md").exists()
    assert (corpus / "forskrifter" / "skatteloven.md").exists()
    # Confirm no avoidable -2 suffix was applied.
    assert not (corpus / "lover" / "skatteloven-2.md").exists()
    assert not (corpus / "forskrifter" / "skatteloven-2.md").exists()


def test_tombstone_preserves_slug_and_title_for_audit_trail(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """LOW regression guard: when a doc is removed upstream, its
    manifest tombstone must keep slug and title (along with the other
    historical fields) so the audit trail and any downstream INDEX-
    style historical view remain reconstructable. Codex PR #17
    reproducer."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-gone.xml", _law_with_extra("Disappearingloven", "body"))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    _build_tarball(lover_tar, [])  # upstream dropped the doc
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    record = manifest["documents"]["lov-gone"]
    assert record["status"] == "removed"
    assert record["slug"] == "disappearingloven"
    assert record["title"] == "Disappearingloven"


def test_run_sync_renames_when_upstream_slug_changes(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """If Lovdata renames a kortform (or fixes a typo in title), the
    same content gets a different slug. The orchestrator must delete
    the old path and write the new path even though xml_hash is
    unchanged. Migration from a Sprint 3 manifest with slug=None goes
    through the same code path."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    body = "Same body, only the title (and thus slug) changes."
    _build_tarball(
        lover_tar,
        [("nl/lov-x.xml", _law_with_extra("Oldname", body))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))
    assert (corpus / "lover" / "oldname.md").exists()

    _build_tarball(
        lover_tar,
        [("nl/lov-x.xml", _law_with_extra("Newname", body))],
    )
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert not (corpus / "lover" / "oldname.md").exists()
    assert (corpus / "lover" / "newname.md").exists()


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
    """Content change with stable slug: same title → same slug → file
    is overwritten in place (no rename). Verifies that change_detector
    + orchestrator correctly updates only the file content."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-x.xml", _law_with_extra("Stableloven", "First version note."))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    _build_tarball(
        lover_tar,
        [("nl/lov-x.xml", _law_with_extra("Stableloven", "Second version note."))],
    )
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    report = run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert report.changed_count == 1
    assert report.new_count == 0
    md = (corpus / "lover" / "stableloven.md").read_text(encoding="utf-8")
    assert "Second version note" in md
    assert "First version note" not in md


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
    assert (corpus / "lover" / "to-be-removed.md").exists()

    _build_tarball(lover_tar, [])  # upstream dropped the doc
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    report = run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert report.removed_count == 1
    assert not (corpus / "lover" / "to-be-removed.md").exists()


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


# ---------- Sprint 5: per-act history generation ----------


def test_history_files_generated_for_added_doc_in_per_document_mode(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """A normal incremental sync in per-doc mode (default) writes
    history/<slug>.json + history/<slug>.md alongside the doc, and
    bundles them into the final 'sync: update manifest, index, and
    history' commit."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-1.xml", _law_with_extra("Skattie", "body"))])
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    history_json = corpus / "lover" / "history" / "skattie.json"
    history_md = corpus / "lover" / "history" / "skattie.md"
    assert history_json.exists()
    assert history_md.exists()

    payload = json.loads(history_json.read_text(encoding="utf-8"))
    assert payload["slug"] == "skattie"
    assert payload["doc_id"] == "lov-1"
    assert payload["schema_version"] == 1
    assert len(payload["events"]) >= 1
    assert payload["events"][0]["type"] == "added"

    log = _git_log_subjects(corpus)
    assert "sync: update manifest, index, and history" in log


def test_manifest_records_total_changes_and_last_changed_after_sync(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """After a sync that adds a doc, the manifest record for that doc
    carries Sprint 5 history metadata so future MCP-style queries
    (e.g. list_recent_changes) can sort without loading every
    history.json."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-1.xml", _law_with_extra("Skattie", "body"))])
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    record = manifest["documents"]["lov-1"]
    assert record["total_changes"] >= 1
    # last_changed is an ISO date string like "2026-04-27"
    assert isinstance(record["last_changed"], str)
    assert len(record["last_changed"]) == 10
    assert record["last_changed"][4] == "-"


def test_sprint5_history_migration_triggers_on_first_sync_after_prb(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """A pre-Sprint-5 corpus (manifest with current docs but no
    history/ dirs anywhere) gets a one-time 'migration: generate
    history for N documents' commit on the first sync after PR-B
    ships, before any regular sync work for that day."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    body = "Body that does not change."
    xml = _law_with_extra("Skattie", body)
    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-1.xml", xml)])
    _build_tarball(forskrifter_tar, [])

    # Pre-write a Sprint-4-style manifest: slug populated but no
    # history/ directory on disk.
    legacy_path = corpus / "lover" / "skattie.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Existing file\n", encoding="utf-8")
    manifest = Manifest(
        generated_at=datetime(2026, 4, 27, tzinfo=UTC),
        documents={
            "lov-1": ManifestRecord(
                doc_type="lov",
                xml_hash=hash_normalized_xml(xml),
                markdown_path="lover/skattie.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 27, tzinfo=UTC),
                status="current",
                slug="skattie",
                title="Skattie",
            ),
        },
    )
    write_manifest(manifest, corpus / "manifest.json")
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "Sprint 4 seed"], cwd=corpus, check=True)
    commits_before = _git_commit_count(corpus)

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    # Two migrations fire on this Sprint-4 baseline: Sprint 5 backfills
    # history (no history/ dir on disk), Sprint 8 backfills eu_basis
    # (no eu_basis field on the seeded record). Upstream is unchanged
    # so no regular sync commit follows.
    assert _git_commit_count(corpus) == commits_before + 2
    log = _git_log_subjects(corpus)
    assert "migration: generate history for 1 documents" in log
    assert "migration: backfill eu_basis for 1 documents" in log
    assert (corpus / "lover" / "history" / "skattie.json").exists()


def test_sprint5_history_migration_skipped_when_history_dirs_already_exist(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """A Sprint-5-ready corpus (history/ already populated) must NOT
    re-run the migration on every sync — the no-op contract from
    decisions.md §5 still holds when upstream is unchanged."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-1.xml", _law_with_extra("Skattie", "body"))])
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    settings = Settings(data_dir=data_dir, lovverk_repo_path=corpus)
    run_sync(settings)  # first sync populates history/ for both datasets
    commits_after_first = _git_commit_count(corpus)

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(settings)  # second sync: upstream unchanged, history exists

    assert _git_commit_count(corpus) == commits_after_first
    log = _git_log_subjects(corpus)
    assert "migration: generate history" not in log


def _law_with_eea(title: str, celex_list: list[str]) -> bytes:
    """Variant of _minimal_law_html that adds a <dd class='eeaReferences'>
    block with one anchor per CELEX. Used by Sprint 8 PR-D tests so the
    fake upstream XML matches Lovdata's actual EEA-references shape."""
    anchors = "".join(f'<a href="eu/{celex.lower()}">label</a>' for celex in celex_list)
    return (
        '<!DOCTYPE html><html lang="nb"><head><title>'
        f"{title}</title></head>"
        '<body><header class="documentHeader"><dl>'
        '<dt class="title">Tittel</dt>'
        f'<dd class="title">{title}</dd>'
        '<dt class="refid">RefID</dt>'
        '<dd class="refid">lov/x</dd>'
        f'<dd class="eeaReferences">{anchors}</dd>'
        "</dl></header>"
        '<main id="dokument">'
        f"<h1>{title}</h1>"
        '<article class="legalP" id="ledd-1">body.</article>'
        "</main></body></html>"
    ).encode()


def test_sprint8_eu_basis_migration_backfills_existing_corpus(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """A Sprint-7-or-earlier corpus (manifest with eu_basis=None on every
    current record) gets a one-time
    'migration: backfill eu_basis for N documents' commit on the first
    sync after PR-D ships. Re-renders the markdown so frontmatter
    carries the new field too. Subsequent syncs see populated
    eu_basis (possibly []) and skip the migration."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    xml = _law_with_eea("Skattie", ["32016R0679"])
    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-1.xml", xml)])
    _build_tarball(forskrifter_tar, [])

    # Pre-write a Sprint-7-style manifest: slug + history fields populated
    # but eu_basis omitted (defaults to None). Also seed history/ so
    # the Sprint 5 migration does not also fire.
    legacy_path = corpus / "lover" / "skattie.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Existing file\n", encoding="utf-8")
    history_dir = corpus / "lover" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / "skattie.json").write_text("{}", encoding="utf-8")
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)
    manifest = Manifest(
        generated_at=datetime(2026, 4, 27, tzinfo=UTC),
        documents={
            "lov-1": ManifestRecord(
                doc_type="lov",
                xml_hash=hash_normalized_xml(xml),
                markdown_path="lover/skattie.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 27, tzinfo=UTC),
                status="current",
                slug="skattie",
                title="Skattie",
                total_changes=1,
                last_changed="2026-04-27",
                # eu_basis omitted -> None -> Sprint 8 trigger
            ),
        },
    )
    write_manifest(manifest, corpus / "manifest.json")
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Sprint 7 seed"],
        cwd=corpus,
        check=True,
    )
    commits_before = _git_commit_count(corpus)

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    # Sprint 8 migration emits exactly one new commit; upstream xml_hash
    # matches the seeded manifest record so no regular sync work
    # follows.
    assert _git_commit_count(corpus) == commits_before + 1
    log = _git_log_subjects(corpus)
    assert "migration: backfill eu_basis for 1 documents" in log

    # Manifest record now carries the extracted CELEX.
    written = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    assert written["documents"]["lov-1"]["eu_basis"] == ["32016R0679"]

    # Frontmatter of the rendered markdown also carries it (re-render
    # is the whole point of the migration — manifest alone is not
    # enough; downstream MCP / search tools may read either source).
    body = (corpus / "lover" / "skattie.md").read_text(encoding="utf-8")
    assert "eu_basis:" in body
    assert "32016R0679" in body


def test_sprint8_eu_basis_migration_skipped_when_already_backfilled(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """After the first sync post-PR-D, every record carries eu_basis
    (possibly []). Subsequent syncs with unchanged upstream must not
    re-fire the backfill — the no-op contract from decisions.md §5
    still holds."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-1.xml", _law_with_eea("Skattie", ["32016R0679"]))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    settings = Settings(data_dir=data_dir, lovverk_repo_path=corpus)
    run_sync(settings)  # initial sync populates eu_basis from upstream
    commits_after_first = _git_commit_count(corpus)

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(settings)  # second sync: upstream unchanged

    assert _git_commit_count(corpus) == commits_after_first
    log = _git_log_subjects(corpus)
    assert "migration: backfill eu_basis" not in log


def test_sprint8_eu_basis_migration_records_empty_list_when_no_eea_block(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """Acts whose upstream XML has no <dd class='eeaReferences'> block
    must still get eu_basis populated — as an empty list. Empty list
    is the canonical 'no EU basis' value; only None means 'pre-Sprint-8
    record, unknown'."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    xml = _law_with_extra("Skattie", "body")  # no eeaReferences block
    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-1.xml", xml)])
    _build_tarball(forskrifter_tar, [])

    legacy_path = corpus / "lover" / "skattie.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Existing\n", encoding="utf-8")
    (corpus / "lover" / "history").mkdir(parents=True, exist_ok=True)
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)
    manifest = Manifest(
        generated_at=datetime(2026, 4, 27, tzinfo=UTC),
        documents={
            "lov-1": ManifestRecord(
                doc_type="lov",
                xml_hash=hash_normalized_xml(xml),
                markdown_path="lover/skattie.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 27, tzinfo=UTC),
                status="current",
                slug="skattie",
                title="Skattie",
            ),
        },
    )
    write_manifest(manifest, corpus / "manifest.json")
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=corpus, check=True)

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    written = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    assert written["documents"]["lov-1"]["eu_basis"] == []


def test_sprint8_eu_basis_migration_preserves_tombstones(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """Removed records keep their pre-migration shape (eu_basis=None
    stays None) because their files do not exist on disk and the
    migration cannot re-render them. Reverse-lookup tools skip None
    records; dropping the record entirely would lose audit trail."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    xml = _law_with_eea("Skattie", ["32016R0679"])
    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-1.xml", xml)])
    _build_tarball(forskrifter_tar, [])

    legacy_path = corpus / "lover" / "skattie.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Existing\n", encoding="utf-8")
    (corpus / "lover" / "history").mkdir(parents=True, exist_ok=True)
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)
    manifest = Manifest(
        generated_at=datetime(2026, 4, 27, tzinfo=UTC),
        documents={
            "lov-1": ManifestRecord(
                doc_type="lov",
                xml_hash=hash_normalized_xml(xml),
                markdown_path="lover/skattie.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 27, tzinfo=UTC),
                status="current",
                slug="skattie",
                title="Skattie",
            ),
            "lov-old": ManifestRecord(
                doc_type="lov",
                xml_hash="b" * 64,
                markdown_path="lover/old.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 27, tzinfo=UTC),
                status="removed",
                slug="old",
                title="Old",
            ),
        },
    )
    write_manifest(manifest, corpus / "manifest.json")
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=corpus, check=True)

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    written = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    # Current doc gets backfilled.
    assert written["documents"]["lov-1"]["eu_basis"] == ["32016R0679"]
    # Tombstone keeps its pre-migration shape — eu_basis omitted entirely
    # (Pydantic excludes None defaults from model_dump unless asked
    # otherwise) or null.
    tombstone = written["documents"]["lov-old"]
    assert tombstone.get("eu_basis") is None


def test_sprint8_eu_basis_migration_defers_slug_renames_to_rename_flow(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """MEDIUM regression guard: when a pre-Sprint-8 record has both
    eu_basis=None AND an upstream slug change (Lovdata renamed the
    kortform), the backfill must NOT rewrite the file at the new slug
    path — that would update prior.markdown_path to the new path and
    the subsequent rename detector would see prior.slug already
    matching upstream.slug and skip the rename, orphaning the old
    <old-slug>.md on disk.

    Expected behavior: Sprint 8 migration skips the record (defers to
    the rename flow), the rename flow writes the new path AND deletes
    the old, and _write_one populates eu_basis as a side effect.
    Codex PR-D round 1 reproducer.
    """
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    # Upstream XML derives slug "newskattie" from its title.
    xml = _law_with_eea("Newskattie", ["32016R0679"])
    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-1.xml", xml)])
    _build_tarball(forskrifter_tar, [])

    # Seed a Sprint-7 manifest with the OLD slug "oldskattie" but the
    # SAME xml_hash that the upstream XML produces (Lovdata changed
    # the kortform, not the body).
    legacy_path = corpus / "lover" / "oldskattie.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Legacy old-slug file\n", encoding="utf-8")
    (corpus / "lover" / "history").mkdir(parents=True, exist_ok=True)
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)
    manifest = Manifest(
        generated_at=datetime(2026, 4, 27, tzinfo=UTC),
        documents={
            "lov-1": ManifestRecord(
                doc_type="lov",
                xml_hash=hash_normalized_xml(xml),
                markdown_path="lover/oldskattie.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 27, tzinfo=UTC),
                status="current",
                slug="oldskattie",
                title="Oldskattie",
                # eu_basis omitted -> None -> would have triggered the
                # buggy Sprint 8 rewrite at the new slug path.
            ),
        },
    )
    write_manifest(manifest, corpus / "manifest.json")
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Sprint 7 seed (slug-renamed upstream)"],
        cwd=corpus,
        check=True,
    )

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    # Old slug file is gone; new slug file exists.
    assert not (corpus / "lover" / "oldskattie.md").exists()
    assert (corpus / "lover" / "newskattie.md").exists()

    # Manifest record carries the new slug AND the extracted CELEX —
    # rename flow's _write_one populated eu_basis as a side effect.
    written = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    record = written["documents"]["lov-1"]
    assert record["slug"] == "newskattie"
    assert record["markdown_path"] == "lover/newskattie.md"
    assert record["eu_basis"] == ["32016R0679"]

    # No standalone backfill commit — the rename flow handled it.
    log = _git_log_subjects(corpus)
    assert "migration: backfill eu_basis" not in log
    assert "rename(lov): newskattie" in log
