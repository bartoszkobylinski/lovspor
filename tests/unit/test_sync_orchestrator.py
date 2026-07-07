import subprocess
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

import lovspor.sync.orchestrator as orchestrator_module
from lovspor.embeddings.store import write_embeddings
from lovspor.errors import ConfigError, CorpusStateError, MassRemovalError
from lovspor.history import HistoryRecord
from lovspor.settings import Settings
from lovspor.sources.lovdata import LovdataArchive
from lovspor.storage.manifest import Manifest, ManifestRecord, read_manifest, write_manifest
from lovspor.sync.orchestrator import (
    SyncReport,
    _collect_upstream,
    _commit_with_history,
    _DocAction,
    _ensure_corpus_git_repo,
    _find_undersized_embeddings,
    _guard_mass_removal,
    _index_tarball,
    _load_or_empty_manifest,
    _UpstreamDoc,
    _with_slug,
    _write_one,
    mark_undersized_embeddings_stale,
    run_sync,
)


def _record(
    doc_id: str,
    *,
    xml_hash: str,
    slug: str,
    status: str = "current",
) -> ManifestRecord:
    return ManifestRecord(
        doc_type="lov",
        xml_hash=xml_hash,
        markdown_path=f"lover/{slug}.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 5, 1, tzinfo=UTC),
        status=status,
        slug=slug,
        title=doc_id,
        eu_basis=[],
    )


def _upstream(doc_id: str, *, xml_hash: str, slug: str) -> _UpstreamDoc:
    return _UpstreamDoc(
        doc_id=doc_id,
        source_dataset="gjeldende-lover",
        xml_bytes=f"<xml>{doc_id}</xml>".encode(),
        xml_hash=xml_hash,
        slug=slug,
        title=doc_id,
        eu_basis=(),
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _settings(tmp_path: Path) -> Settings:
    corpus = tmp_path / "lovverk"
    corpus.mkdir(parents=True)
    _git(corpus, "init", "-q")
    _git(corpus, "config", "user.name", "test")
    _git(corpus, "config", "user.email", "test@example.com")
    _git(corpus, "config", "commit.gpgsign", "false")
    return Settings(
        data_dir=tmp_path / "data",
        lovverk_repo_path=corpus,
    )


def _disable_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        orchestrator_module,
        "_needs_sprint5_history_migration",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        orchestrator_module,
        "_needs_sprint8_eu_basis_migration",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        orchestrator_module,
        "_needs_sprint9_embeddings_migration",
        lambda *_args: False,
    )
    monkeypatch.setattr(orchestrator_module, "_load_embedder", lambda _settings: None)


def test_sync_report_is_immutable() -> None:
    report = SyncReport(
        new_count=1,
        changed_count=2,
        removed_count=3,
        unchanged_count=4,
    )

    with pytest.raises(ValidationError):
        report.new_count = 99


def test_upstream_doc_is_immutable() -> None:
    upstream = _UpstreamDoc(
        doc_id="lov-1",
        source_dataset="gjeldende-lover",
        xml_bytes=b"<xml/>",
        xml_hash="a" * 64,
        slug="lov-1",
        title="Lov 1",
        eu_basis=("32024R0001",),
    )

    with pytest.raises(FrozenInstanceError):
        upstream.slug = "changed"


def test_doc_action_exposes_commit_message_stage_paths_and_is_immutable() -> None:
    action = _DocAction(
        action="update",
        doc_type="lov",
        doc_id="lov-1",
        slug="skatteloven",
        paths=(Path("lover/skatteloven.md"),),
        sidecar_paths=(Path("lover/embeddings/skatteloven.bin"),),
    )

    assert action.commit_message == "update(lov): skatteloven"
    assert action.all_paths_to_stage == (
        Path("lover/skatteloven.md"),
        Path("lover/embeddings/skatteloven.bin"),
    )
    with pytest.raises(FrozenInstanceError):
        action.slug = "changed"


def test_load_or_empty_manifest_returns_epoch_manifest_when_missing(tmp_path: Path) -> None:
    manifest = _load_or_empty_manifest(tmp_path / "manifest.json")

    assert manifest.generated_at == datetime.fromtimestamp(0, tz=UTC)
    assert manifest.documents == {}


def test_ensure_corpus_git_repo_requires_dot_git_directory(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"expected \.git"):
        _ensure_corpus_git_repo(tmp_path)

    (tmp_path / ".git").mkdir()
    _ensure_corpus_git_repo(tmp_path)


def test_with_slug_replaces_only_slug() -> None:
    upstream = _upstream("lov-1", xml_hash="a" * 64, slug="old")

    replaced = _with_slug(upstream, "new")

    assert replaced == _UpstreamDoc(
        doc_id="lov-1",
        source_dataset="gjeldende-lover",
        xml_bytes=b"<xml>lov-1</xml>",
        xml_hash="a" * 64,
        slug="new",
        title="lov-1",
        eu_basis=(),
    )
    assert upstream.slug == "old"


def test_pick_archive_returns_named_archive_and_rejects_missing() -> None:
    archive = LovdataArchive(
        filename="gjeldende-lover.tar.bz2",
        description="Lover",
        sizeBytes=123,
        lastModified="2026-05-01T00:00:00Z",
    )

    assert (
        orchestrator_module._pick_archive(
            {"gjeldende-lover.tar.bz2": archive},
            "gjeldende-lover.tar.bz2",
        )
        is archive
    )
    with pytest.raises(ConfigError) as exc_info:
        orchestrator_module._pick_archive({}, "gjeldende-lover.tar.bz2")
    assert str(exc_info.value) == (
        "upstream catalogue is missing expected archive 'gjeldende-lover.tar.bz2'"
    )


def test_collect_upstream_downloads_tracked_datasets_and_resolves_per_dataset_slugs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    archives = {
        "gjeldende-lover.tar.bz2": LovdataArchive(
            filename="gjeldende-lover.tar.bz2",
            description="Lover",
            sizeBytes=1,
            lastModified="2026-05-01T00:00:00Z",
        ),
        "gjeldende-sentrale-forskrifter.tar.bz2": LovdataArchive(
            filename="gjeldende-sentrale-forskrifter.tar.bz2",
            description="Forskrifter",
            sizeBytes=1,
            lastModified="2026-05-01T00:00:00Z",
        ),
    }
    downloaded: list[tuple[str, Path]] = []
    client_kwargs: dict[str, object] = {}

    class FakeDownload:
        def __init__(self, path: Path) -> None:
            self.path = path

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            client_kwargs.update(kwargs)

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def list_datasets(self) -> list[LovdataArchive]:
            return list(archives.values())

        def download(self, archive: LovdataArchive, cache_dir: Path) -> FakeDownload:
            downloaded.append((archive.filename, cache_dir))
            return FakeDownload(cache_dir / archive.filename)

    def fake_index(tar_path: Path, dataset: str) -> list[_UpstreamDoc]:
        return [
            _upstream(
                f"{dataset}-1",
                xml_hash="a" * 64,
                slug="shared",
            ).__class__(
                doc_id=f"{dataset}-1",
                source_dataset=dataset,
                xml_bytes=b"<xml/>",
                xml_hash="a" * 64,
                slug="shared",
                title=tar_path.name,
                eu_basis=(),
            ),
            _UpstreamDoc(
                doc_id=f"{dataset}-2",
                source_dataset=dataset,
                xml_bytes=b"<xml/>",
                xml_hash="b" * 64,
                slug="shared",
                title=tar_path.name,
                eu_basis=(),
            ),
        ]

    monkeypatch.setattr(orchestrator_module, "LovdataClient", FakeClient)
    monkeypatch.setattr(orchestrator_module, "_index_tarball", fake_index)

    upstream = _collect_upstream(settings, tmp_path / "cache")

    assert client_kwargs == {
        "timeout_seconds": settings.http_timeout_seconds,
        "user_agent": settings.http_user_agent,
    }
    assert downloaded == [
        ("gjeldende-lover.tar.bz2", tmp_path / "cache"),
        ("gjeldende-sentrale-forskrifter.tar.bz2", tmp_path / "cache"),
    ]
    assert upstream["gjeldende-lover-1"].slug == "shared"
    assert upstream["gjeldende-lover-2"].slug == "shared-2"
    assert upstream["gjeldende-sentrale-forskrifter-1"].slug == "shared"
    assert upstream["gjeldende-sentrale-forskrifter-2"].slug == "shared-2"


def test_index_tarball_builds_upstream_docs_from_member_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Member:
        def __init__(self, name: str, content: bytes) -> None:
            self.name = name
            self.content = content

    extracted: list[bytes] = []
    derived: list[tuple[str, str, str]] = []

    def fake_extract(content: bytes) -> dict[str, object]:
        extracted.append(content)
        return {
            "short_title": "Kort",
            "title": "Full tittel",
            "eu_basis": ["32024R0001"],
        }

    def fake_derive(short_title: str, title: str, doc_id: str) -> str:
        derived.append((short_title, title, doc_id))
        return f"slug-{doc_id}"

    monkeypatch.setattr(
        orchestrator_module,
        "iter_tarball_xml",
        lambda _path: [Member("nested/lov-1.xml", b"<xml/>")],
    )
    monkeypatch.setattr(orchestrator_module, "extract_xml_metadata", fake_extract)
    monkeypatch.setattr(orchestrator_module, "derive_slug", fake_derive)
    monkeypatch.setattr(orchestrator_module, "hash_normalized_xml", lambda _xml: "c" * 64)

    docs = _index_tarball(tmp_path / "archive.tar.bz2", "gjeldende-lover")

    assert docs == [
        _UpstreamDoc(
            doc_id="lov-1",
            source_dataset="gjeldende-lover",
            xml_bytes=b"<xml/>",
            xml_hash="c" * 64,
            slug="slug-lov-1",
            title="Full tittel",
            eu_basis=("32024R0001",),
        )
    ]
    assert extracted == [b"<xml/>"]
    assert derived == [("Kort", "Full tittel", "lov-1")]


def test_rename_carry_tombstone_and_migration_helpers() -> None:
    prior = Manifest(
        generated_at=datetime(2026, 5, 1, tzinfo=UTC),
        documents={
            "same": _record("same", xml_hash="a" * 64, slug="same"),
            "renamed": _record("renamed", xml_hash="b" * 64, slug="old"),
            "legacy": _record("legacy", xml_hash="c" * 64, slug="legacy").model_copy(
                update={"slug": None}
            ),
        },
    )
    upstream = {
        "same": _upstream("same", xml_hash="a" * 64, slug="same"),
        "renamed": _upstream("renamed", xml_hash="b" * 64, slug="new"),
        "legacy": _upstream("legacy", xml_hash="c" * 64, slug="legacy"),
    }

    assert orchestrator_module._identify_renames(
        ["same", "renamed", "legacy"],
        prior,
        upstream,
    ) == ["renamed", "legacy"]
    assert orchestrator_module._carry_unchanged(prior, ["same"]) == {
        "same": prior.documents["same"]
    }
    assert orchestrator_module._is_migration(prior, ["renamed"]) is False
    assert orchestrator_module._is_migration(prior, ["legacy"]) is True

    tombstone = orchestrator_module._tombstone(prior.documents["renamed"])
    assert tombstone == prior.documents["renamed"].model_copy(
        update={"status": "removed", "eu_basis": None}
    )


def test_embedding_paths_delete_helpers_and_overlap_detection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[Path] = []
    monkeypatch.setattr(orchestrator_module, "delete_document", deleted.append)
    existing = tmp_path / "lover" / "embeddings" / "old.bin"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"sidecar")

    assert orchestrator_module._embeddings_path(tmp_path, "gjeldende-lover", "old") == existing
    assert (
        orchestrator_module._maybe_delete_old_embeddings(
            tmp_path,
            "gjeldende-lover",
            "old",
            {existing},
        )
        is None
    )
    assert deleted == []

    assert (
        orchestrator_module._maybe_delete_old_embeddings(
            tmp_path,
            "gjeldende-lover",
            "old",
            set(),
        )
        == existing
    )
    assert deleted == [existing]

    actions = [
        _DocAction("rename", "lov", "a", "a", (Path("shared.md"), Path("a.md"))),
        _DocAction("remove", "lov", "b", "b", (Path("shared.md"),)),
    ]
    assert orchestrator_module._has_rename_path_overlap(actions) is True
    assert orchestrator_module._has_rename_path_overlap([actions[0]]) is False


def test_write_one_renders_document_record_and_optional_embeddings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    upstream = _UpstreamDoc(
        doc_id="lov-1",
        source_dataset="gjeldende-lover",
        xml_bytes=b"<xml/>",
        xml_hash="a" * 64,
        slug="slug-1",
        title="Lov 1",
        eu_basis=("32024R0001",),
    )
    now = datetime(2026, 5, 2, tzinfo=UTC)
    written_docs: list[tuple[Path, str]] = []
    rendered_contexts: list[object] = []

    def fake_render(xml_bytes: bytes, context: object) -> str:
        assert xml_bytes == b"<xml/>"
        rendered_contexts.append(context)
        return "rendered markdown"

    def fake_write_document(path: Path, content: str) -> None:
        written_docs.append((path, content))

    monkeypatch.setattr(orchestrator_module, "render_full_document", fake_render)
    monkeypatch.setattr(orchestrator_module, "write_document", fake_write_document)
    monkeypatch.setattr(
        orchestrator_module,
        "_write_embeddings_for_doc",
        lambda repo, dataset, slug, rendered, embedder: (
            repo / "lover" / "embeddings" / f"{slug}.bin"
        ),
    )

    record, paths = _write_one(settings, upstream, now, embedder=object())  # type: ignore[arg-type]

    expected_path = settings.lovverk_repo_path / "lover" / "slug-1.md"
    assert written_docs == [(expected_path, "rendered markdown")]
    assert paths == [
        expected_path,
        settings.lovverk_repo_path / "lover" / "embeddings" / "slug-1.bin",
    ]
    assert record == ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path="lover/slug-1.md",
        source_dataset="gjeldende-lover",
        last_seen=now,
        status="current",
        slug="slug-1",
        title="Lov 1",
        eu_basis=["32024R0001"],
        embedding_hash="a" * 64,
    )
    context = rendered_contexts[0]
    assert context.doc_id == "lov-1"
    assert context.slug == "slug-1"
    assert context.doc_type == "lov"
    assert context.xml_hash == "a" * 64
    assert context.source_dataset == "gjeldende-lover"
    assert context.retrieved_at == now


def test_write_one_can_preserve_prior_retrieved_at(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    prior_retrieved_at = datetime(2026, 4, 30, tzinfo=UTC)
    now = datetime(2026, 5, 2, tzinfo=UTC)
    upstream = _UpstreamDoc(
        doc_id="lov-1",
        source_dataset="gjeldende-lover",
        xml_bytes=b"<xml/>",
        xml_hash="a" * 64,
        slug="slug-1",
        title="Lov 1",
        eu_basis=(),
        retrieved_at=prior_retrieved_at,
    )
    rendered_contexts: list[object] = []

    def fake_render(_xml_bytes: bytes, context: object) -> str:
        rendered_contexts.append(context)
        return "rendered markdown"

    monkeypatch.setattr(orchestrator_module, "render_full_document", fake_render)
    monkeypatch.setattr(orchestrator_module, "write_document", lambda *_a: None)

    record, _paths = _write_one(settings, upstream, now)

    assert record.last_seen == now
    assert rendered_contexts[0].retrieved_at == prior_retrieved_at


def test_write_one_without_embedder_leaves_embedding_hash_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A keyless sync writes Markdown but no sidecar, so the record's
    embedding_hash stays None — the next keyed sync sees it != xml_hash
    and re-embeds the doc."""
    settings = _settings(tmp_path)
    upstream = _UpstreamDoc(
        doc_id="lov-1",
        source_dataset="gjeldende-lover",
        xml_bytes=b"<xml/>",
        xml_hash="a" * 64,
        slug="slug-1",
        title="Lov 1",
        eu_basis=(),
    )
    monkeypatch.setattr(orchestrator_module, "render_full_document", lambda *_a: "md")
    monkeypatch.setattr(orchestrator_module, "write_document", lambda *_a: None)

    record, paths = _write_one(settings, upstream, datetime(2026, 5, 2, tzinfo=UTC))

    assert record.embedding_hash is None
    assert len(paths) == 1  # markdown only, no sidecar


def test_needs_sprint9_migration_detects_stale_hash_with_bin_present(
    tmp_path: Path,
) -> None:
    """A present .bin whose embedding_hash no longer matches xml_hash (a
    keyless sync changed the doc) must still trigger the backfill —
    existence alone cannot catch it."""
    record = _record("d1", xml_hash="n" * 64, slug="d1").model_copy(
        update={"embedding_hash": "o" * 64},
    )
    prior = Manifest(
        generated_at=datetime(2026, 5, 1, tzinfo=UTC),
        documents={"d1": record},
    )
    (tmp_path / "lover" / "embeddings").mkdir(parents=True)
    (tmp_path / "lover" / "embeddings" / "d1.bin").write_bytes(b"")

    assert orchestrator_module._needs_sprint9_embeddings_migration(prior, tmp_path) is True


def test_record_with_history_and_generate_history_skip_rules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = _record("active", xml_hash="a" * 64, slug="active")
    removed = _record("removed", xml_hash="b" * 64, slug="removed", status="removed")
    legacy = _record("legacy", xml_hash="c" * 64, slug="legacy").model_copy(update={"slug": None})
    history = HistoryRecord(
        slug="active",
        doc_id="active",
        events=[
            {
                "date": "2026-05-02",
                "commit": "abc123",
                "type": "updated",
                "subject": "update(lov): active",
            },
            {
                "date": "2026-05-01",
                "commit": "def456",
                "type": "added",
                "subject": "add(lov): active",
            },
        ],
    )

    extracted: list[tuple[Path, str, str, str]] = []

    def fake_extract_history(
        repo_path: Path,
        current_path: str,
        doc_id: str,
        slug: str,
    ) -> HistoryRecord:
        extracted.append((repo_path, current_path, doc_id, slug))
        return history

    def fake_write_history(record: HistoryRecord, target_dir: Path) -> tuple[Path, Path]:
        return (
            target_dir / "history" / f"{record.slug}.json",
            target_dir / "history" / f"{record.slug}.md",
        )

    monkeypatch.setattr(orchestrator_module, "extract_history", fake_extract_history)
    monkeypatch.setattr(orchestrator_module, "write_history", fake_write_history)

    updated, written = orchestrator_module._generate_and_apply_history(
        tmp_path,
        {"active": active, "removed": removed, "legacy": legacy},
        ["active", "removed", "legacy", "missing"],
    )

    assert extracted == [(tmp_path, "lover/active.md", "active", "active")]
    assert written == [
        tmp_path / "lover" / "history" / "active.json",
        tmp_path / "lover" / "history" / "active.md",
    ]
    assert updated["active"].total_changes == 2
    assert updated["active"].last_changed == "2026-05-02"
    assert updated["removed"] == removed
    assert updated["legacy"] == legacy


def test_commit_with_history_bulk_path_writes_manifest_indexes_and_followup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    now = datetime(2026, 5, 3, tzinfo=UTC)
    records = {"lov-1": _record("lov-1", xml_hash="a" * 64, slug="one")}
    actions = [
        _DocAction(
            "add",
            "lov",
            "lov-1",
            "one",
            (settings.lovverk_repo_path / "lover" / "one.md",),
        ),
        _DocAction(
            "remove",
            "lov",
            "lov-removed",
            "removed",
            (settings.lovverk_repo_path / "lover" / "removed.md",),
        ),
    ]
    written_manifests: list[tuple[Manifest, Path]] = []
    generated_indexes: list[tuple[Path, str, Manifest]] = []
    bulk_calls: list[tuple[list[_DocAction], list[Path], str]] = []
    followups: list[list[str]] = []

    def fake_write_manifest(manifest: Manifest, path: Path) -> None:
        written_manifests.append((manifest, path))

    def fake_generate_index(repo: Path, dataset: str, manifest: Manifest) -> Path:
        generated_indexes.append((repo, dataset, manifest))
        return repo / dataset / "INDEX.md"

    def fake_commit_bulk(
        _repo: Path,
        bulk_actions: list[_DocAction],
        extra_paths: list[Path],
        message: str,
    ) -> None:
        bulk_calls.append((bulk_actions, extra_paths, message))

    def fake_followup(
        _repo: Path,
        _manifest_path: Path,
        _new_records: dict[str, ManifestRecord],
        target_doc_ids: list[str],
        _now: datetime,
    ) -> None:
        followups.append(target_doc_ids)

    monkeypatch.setattr(orchestrator_module, "write_manifest", fake_write_manifest)
    monkeypatch.setattr(orchestrator_module, "generate_index", fake_generate_index)
    monkeypatch.setattr(orchestrator_module, "_commit_bulk", fake_commit_bulk)
    monkeypatch.setattr(
        orchestrator_module,
        "_commit_history_followup",
        fake_followup,
    )

    _commit_with_history(
        settings,
        repo=settings.lovverk_repo_path,
        manifest_path=settings.lovverk_repo_path / "manifest.json",
        actions=actions,
        new_records=records,
        now=now,
        is_sprint4_migration=False,
        force_bulk_commit=True,
    )

    manifest = Manifest(generated_at=now, documents=records)
    assert written_manifests == [(manifest, settings.lovverk_repo_path / "manifest.json")]
    assert [dataset for _, dataset, _ in generated_indexes] == [
        "gjeldende-lover",
        "gjeldende-sentrale-forskrifter",
    ]
    assert bulk_calls == [
        (
            actions,
            [
                settings.lovverk_repo_path / "manifest.json",
                settings.lovverk_repo_path / "gjeldende-lover" / "INDEX.md",
                settings.lovverk_repo_path / "gjeldende-sentrale-forskrifter" / "INDEX.md",
            ],
            "sync: 1 new, 0 changed, 0 renamed, 1 removed",
        )
    ]
    assert followups == [["lov-1"]]


def test_commit_with_history_per_doc_path_generates_history_and_final_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    now = datetime(2026, 5, 3, tzinfo=UTC)
    original_records = {"lov-1": _record("lov-1", xml_hash="a" * 64, slug="one")}
    history_record = original_records["lov-1"].model_copy(
        update={"total_changes": 1, "last_changed": "2026-05-03"},
    )
    actions = [
        _DocAction(
            "update",
            "lov",
            "lov-1",
            "one",
            (settings.lovverk_repo_path / "lover" / "one.md",),
        ),
    ]
    per_doc_calls: list[list[_DocAction]] = []
    history_targets: list[list[str]] = []
    git_add_calls: list[list[Path]] = []
    commit_messages: list[str] = []

    def fake_commit_per_doc(_repo: Path, per_doc_actions: list[_DocAction]) -> None:
        per_doc_calls.append(per_doc_actions)

    monkeypatch.setattr(orchestrator_module, "_commit_per_doc_actions_only", fake_commit_per_doc)

    def fake_history(
        _repo: Path,
        records: dict[str, ManifestRecord],
        target_doc_ids: list[str],
    ) -> tuple[dict[str, ManifestRecord], list[Path]]:
        history_targets.append(target_doc_ids)
        assert records == original_records
        return (
            {"lov-1": history_record},
            [settings.lovverk_repo_path / "lover" / "history" / "one.json"],
        )

    monkeypatch.setattr(orchestrator_module, "_generate_and_apply_history", fake_history)
    monkeypatch.setattr(
        orchestrator_module,
        "generate_index",
        lambda repo, dataset, _manifest: repo / dataset / "INDEX.md",
    )
    monkeypatch.setattr(orchestrator_module, "write_manifest", lambda _manifest, _path: None)
    monkeypatch.setattr(
        orchestrator_module,
        "git_add",
        lambda _repo, paths: git_add_calls.append(paths),
    )
    monkeypatch.setattr(orchestrator_module, "has_staged_changes", lambda _repo: True)
    monkeypatch.setattr(
        orchestrator_module,
        "git_commit_msg",
        lambda _repo, msg: commit_messages.append(msg),
    )

    _commit_with_history(
        settings,
        repo=settings.lovverk_repo_path,
        manifest_path=settings.lovverk_repo_path / "manifest.json",
        actions=actions,
        new_records=original_records,
        now=now,
        is_sprint4_migration=False,
    )

    assert per_doc_calls == [actions]
    assert history_targets == [["lov-1"]]
    assert git_add_calls == [
        [
            settings.lovverk_repo_path / "manifest.json",
            settings.lovverk_repo_path / "gjeldende-lover" / "INDEX.md",
            settings.lovverk_repo_path / "gjeldende-sentrale-forskrifter" / "INDEX.md",
            settings.lovverk_repo_path / "lover" / "history" / "one.json",
        ]
    ]
    assert commit_messages == ["sync: update manifest, index, and history"]


def test_commit_history_followup_updates_manifest_and_commits_document_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest_path = repo / "manifest.json"
    now = datetime(2026, 5, 4, tzinfo=UTC)
    records = {"a": _record("a", xml_hash="a" * 64, slug="a")}
    updated = {
        "a": records["a"].model_copy(
            update={"total_changes": 2, "last_changed": "2026-05-04"},
        )
    }
    history_paths = [
        repo / "lover" / "history" / "a.json",
        repo / "lover" / "history" / "a.md",
    ]
    written_manifests: list[Manifest] = []
    added_paths: list[list[Path]] = []
    messages: list[str] = []

    monkeypatch.setattr(
        orchestrator_module,
        "_generate_and_apply_history",
        lambda _repo, _records, ids: (updated, history_paths),
    )
    monkeypatch.setattr(
        orchestrator_module,
        "write_manifest",
        lambda manifest, _path: written_manifests.append(manifest),
    )
    monkeypatch.setattr(
        orchestrator_module,
        "git_add",
        lambda _repo, paths: added_paths.append(paths),
    )
    monkeypatch.setattr(orchestrator_module, "has_staged_changes", lambda _repo: True)
    monkeypatch.setattr(
        orchestrator_module,
        "git_commit_msg",
        lambda _repo, message: messages.append(message),
    )

    orchestrator_module._commit_history_followup(
        repo,
        manifest_path,
        records,
        ["a"],
        now,
    )

    assert written_manifests == [Manifest(generated_at=now, documents=updated)]
    assert added_paths == [[manifest_path, *history_paths]]
    assert messages == ["sync: update history for 1 documents"]


def test_commit_history_followup_skips_when_no_history_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        orchestrator_module,
        "_generate_and_apply_history",
        lambda _repo, records, _ids: (records, []),
    )
    monkeypatch.setattr(
        orchestrator_module,
        "write_manifest",
        lambda *_args: called.append("write"),
    )
    monkeypatch.setattr(orchestrator_module, "git_add", lambda *_args: called.append("add"))

    orchestrator_module._commit_history_followup(
        tmp_path,
        tmp_path / "manifest.json",
        {"a": _record("a", xml_hash="a" * 64, slug="a")},
        ["a"],
        datetime(2026, 5, 4, tzinfo=UTC),
    )

    assert called == []


def test_commit_bulk_stages_action_and_extra_paths_and_respects_clean_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = _DocAction(
        "rename",
        "lov",
        "a",
        "a",
        (Path("old.md"), Path("new.md")),
        sidecar_paths=(Path("new.bin"),),
    )
    added: list[list[Path]] = []
    messages: list[str] = []
    monkeypatch.setattr(orchestrator_module, "git_add", lambda _repo, paths: added.append(paths))
    monkeypatch.setattr(orchestrator_module, "has_staged_changes", lambda _repo: False)
    monkeypatch.setattr(
        orchestrator_module,
        "git_commit_msg",
        lambda _repo, message: messages.append(message),
    )

    orchestrator_module._commit_bulk(tmp_path, [action], [Path("manifest.json")], "msg")

    assert added == [[Path("old.md"), Path("new.md"), Path("new.bin"), Path("manifest.json")]]
    assert messages == []

    monkeypatch.setattr(orchestrator_module, "has_staged_changes", lambda _repo: True)
    orchestrator_module._commit_bulk(tmp_path, [action], [], "msg")
    assert messages == ["msg"]


def test_generate_history_continues_after_missing_removed_and_slugless_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = _record("active", xml_hash="a" * 64, slug="active")
    later = _record("later", xml_hash="b" * 64, slug="later")
    removed = _record("removed", xml_hash="c" * 64, slug="removed", status="removed")
    slugless = _record("slugless", xml_hash="d" * 64, slug="slugless").model_copy(
        update={"slug": None},
    )
    extracted: list[str] = []

    def fake_extract_history(
        repo_path: Path,
        current_path: str,
        doc_id: str,
        slug: str,
    ) -> HistoryRecord:
        extracted.append(doc_id)
        return HistoryRecord(slug=slug, doc_id=doc_id, events=[])

    monkeypatch.setattr(orchestrator_module, "extract_history", fake_extract_history)
    monkeypatch.setattr(
        orchestrator_module,
        "write_history",
        lambda record, target_dir: (
            target_dir / "history" / f"{record.slug}.json",
            target_dir / "history" / f"{record.slug}.md",
        ),
    )

    updated, written = orchestrator_module._generate_and_apply_history(
        tmp_path,
        {
            "active": active,
            "later": later,
            "removed": removed,
            "slugless": slugless,
        },
        ["missing", "removed", "slugless", "active", "later"],
    )

    assert extracted == ["active", "later"]
    assert updated["active"].total_changes == 0
    assert updated["later"].total_changes == 0
    assert len(written) == 4


def test_run_sprint5_history_migration_filters_targets_and_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest_path = repo / "manifest.json"
    now = datetime(2026, 5, 4, tzinfo=UTC)
    current = _record("current", xml_hash="a" * 64, slug="current")
    removed = _record("removed", xml_hash="b" * 64, slug="removed", status="removed")
    slugless = _record("slugless", xml_hash="c" * 64, slug="slugless").model_copy(
        update={"slug": None},
    )
    prior = Manifest(
        generated_at=now,
        documents={"current": current, "removed": removed, "slugless": slugless},
    )
    updated_records = {"current": current.model_copy(update={"total_changes": 1})}
    captured_ids: list[list[str]] = []
    messages: list[str] = []

    def fake_generate(
        _repo: Path,
        _records: dict[str, ManifestRecord],
        ids: list[str],
    ) -> tuple[dict[str, ManifestRecord], list[Path]]:
        captured_ids.append(ids)
        return updated_records, [repo / "lover" / "history" / "current.json"]

    monkeypatch.setattr(orchestrator_module, "_generate_and_apply_history", fake_generate)
    monkeypatch.setattr(orchestrator_module, "write_manifest", lambda *_args: None)
    monkeypatch.setattr(orchestrator_module, "git_add", lambda *_args: None)
    monkeypatch.setattr(orchestrator_module, "has_staged_changes", lambda _repo: True)
    monkeypatch.setattr(
        orchestrator_module,
        "git_commit_msg",
        lambda _repo, message: messages.append(message),
    )

    result = orchestrator_module._run_sprint5_history_migration(
        repo,
        manifest_path,
        prior,
        now,
    )

    assert captured_ids == [["current"]]
    assert result == Manifest(generated_at=now, documents=updated_records)
    assert messages == ["migration: generate history for 1 documents"]


def test_run_sprint5_history_migration_returns_prior_when_no_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 5, 4, tzinfo=UTC)
    prior = Manifest(
        generated_at=now,
        documents={"current": _record("current", xml_hash="a" * 64, slug="current")},
    )
    monkeypatch.setattr(
        orchestrator_module,
        "_generate_and_apply_history",
        lambda _repo, records, _ids: (records, []),
    )

    assert (
        orchestrator_module._run_sprint5_history_migration(
            tmp_path,
            tmp_path / "manifest.json",
            prior,
            now,
        )
        is prior
    )


def test_migration_triggers_and_messages(tmp_path: Path) -> None:
    # embedding_hash matches xml_hash so these records are stale only via a
    # missing .bin, not a hash mismatch — isolates the existence dimension.
    current = _record("current", xml_hash="a" * 64, slug="current").model_copy(
        update={"embedding_hash": "a" * 64},
    )
    removed = _record("removed", xml_hash="b" * 64, slug="removed", status="removed")
    legacy = _record("legacy", xml_hash="c" * 64, slug="legacy").model_copy(update={"slug": None})
    eu_missing = _record("eu", xml_hash="d" * 64, slug="eu").model_copy(
        update={"eu_basis": None, "embedding_hash": "d" * 64},
    )
    prior = Manifest(
        generated_at=datetime(2026, 5, 1, tzinfo=UTC),
        documents={
            "current": current,
            "removed": removed,
            "legacy": legacy,
            "eu": eu_missing,
        },
    )

    assert orchestrator_module._needs_sprint5_history_migration(tmp_path, prior) is True
    (tmp_path / "lover" / "history").mkdir(parents=True)
    assert orchestrator_module._needs_sprint5_history_migration(tmp_path, prior) is False
    assert (
        orchestrator_module._needs_sprint5_history_migration(
            tmp_path,
            Manifest(generated_at=datetime(2026, 5, 1, tzinfo=UTC), documents={}),
        )
        is False
    )
    assert orchestrator_module._needs_sprint8_eu_basis_migration(prior) is True
    assert (
        orchestrator_module._needs_sprint8_eu_basis_migration(
            Manifest(
                generated_at=datetime(2026, 5, 1, tzinfo=UTC),
                documents={"current": current, "legacy": legacy},
            )
        )
        is False
    )
    assert orchestrator_module._needs_sprint9_embeddings_migration(prior, tmp_path) is True
    (tmp_path / "lover" / "embeddings").mkdir()
    (tmp_path / "lover" / "embeddings" / "current.bin").write_bytes(b"")
    (tmp_path / "lover" / "embeddings" / "eu.bin").write_bytes(b"")
    assert orchestrator_module._needs_sprint9_embeddings_migration(prior, tmp_path) is False

    actions = [
        _DocAction("add", "lov", "a", "a", (Path("a.md"),)),
        _DocAction("update", "lov", "b", "b", (Path("b.md"),)),
        _DocAction("rename", "lov", "c", "c", (Path("c-old.md"), Path("c.md"))),
        _DocAction("remove", "lov", "d", "d", (Path("d.md"),)),
    ]
    assert (
        orchestrator_module._migration_message(actions)
        == "migration: rename 4 documents to slug-based filenames"
    )
    assert orchestrator_module._single_message(actions) == (
        "sync: 1 new, 1 changed, 1 renamed, 1 removed"
    )


def test_single_message_counts_repeated_actions() -> None:
    actions = [
        _DocAction("add", "lov", "a", "a", (Path("a.md"),)),
        _DocAction("add", "lov", "b", "b", (Path("b.md"),)),
        _DocAction("update", "lov", "c", "c", (Path("c.md"),)),
        _DocAction("remove", "lov", "d", "d", (Path("d.md"),)),
        _DocAction("remove", "lov", "e", "e", (Path("e.md"),)),
    ]

    assert orchestrator_module._single_message(actions) == (
        "sync: 2 new, 1 changed, 0 renamed, 2 removed"
    )


def test_needs_eu_basis_migration_empty_manifest_is_false() -> None:
    assert (
        orchestrator_module._needs_sprint8_eu_basis_migration(
            Manifest(generated_at=datetime(2026, 5, 4, tzinfo=UTC), documents={}),
        )
        is False
    )


def test_run_sprint8_eu_basis_migration_skips_unsafe_records_and_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    now = datetime(2026, 5, 4, tzinfo=UTC)
    current = _record("current", xml_hash="a" * 64, slug="current").model_copy(
        update={"eu_basis": None},
    )
    removed = _record("removed", xml_hash="b" * 64, slug="removed", status="removed")
    slugless = _record("slugless", xml_hash="c" * 64, slug="slugless").model_copy(
        update={"slug": None, "eu_basis": None},
    )
    missing_upstream = _record("missing", xml_hash="d" * 64, slug="missing").model_copy(
        update={"eu_basis": None},
    )
    renamed = _record("renamed", xml_hash="e" * 64, slug="old").model_copy(
        update={"eu_basis": None},
    )
    prior = Manifest(
        generated_at=now,
        documents={
            "current": current,
            "removed": removed,
            "slugless": slugless,
            "missing": missing_upstream,
            "renamed": renamed,
        },
    )
    current_upstream = _upstream("current", xml_hash="a" * 64, slug="current")
    renamed_upstream = _upstream("renamed", xml_hash="e" * 64, slug="new")
    written_record = current.model_copy(update={"eu_basis": []})
    written_path = settings.lovverk_repo_path / "lover" / "current.md"
    write_calls: list[str] = []
    retrieved_at_values: list[datetime | None] = []
    added_paths: list[list[Path]] = []
    messages: list[str] = []

    def fake_write_one(
        _settings: Settings,
        upstream_doc: _UpstreamDoc,
        _now: datetime,
    ) -> tuple[ManifestRecord, list[Path]]:
        write_calls.append(upstream_doc.doc_id)
        retrieved_at_values.append(upstream_doc.retrieved_at)
        return written_record, [written_path]

    monkeypatch.setattr(orchestrator_module, "_write_one", fake_write_one)
    monkeypatch.setattr(orchestrator_module, "write_manifest", lambda *_args: None)
    monkeypatch.setattr(
        orchestrator_module,
        "git_add",
        lambda _repo, paths: added_paths.append(paths),
    )
    monkeypatch.setattr(orchestrator_module, "has_staged_changes", lambda _repo: True)
    monkeypatch.setattr(
        orchestrator_module,
        "git_commit_msg",
        lambda _repo, message: messages.append(message),
    )

    result = orchestrator_module._run_sprint8_eu_basis_migration(
        settings,
        settings.lovverk_repo_path / "manifest.json",
        prior,
        {"current": current_upstream, "renamed": renamed_upstream},
        now,
    )

    assert write_calls == ["current"]
    assert retrieved_at_values == [current.last_seen]
    assert result == Manifest(
        generated_at=now,
        documents={
            "current": written_record,
            "removed": removed,
            "slugless": slugless,
            "missing": missing_upstream,
            "renamed": renamed,
        },
    )
    assert added_paths == [[settings.lovverk_repo_path / "manifest.json", written_path]]
    assert messages == ["migration: backfill eu_basis for 1 documents"]


def test_run_sprint8_eu_basis_migration_returns_prior_when_nothing_written(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    now = datetime(2026, 5, 4, tzinfo=UTC)
    record = _record("renamed", xml_hash="a" * 64, slug="old").model_copy(
        update={"eu_basis": None},
    )
    prior = Manifest(generated_at=now, documents={"renamed": record})

    assert (
        orchestrator_module._run_sprint8_eu_basis_migration(
            settings,
            settings.lovverk_repo_path / "manifest.json",
            prior,
            {"renamed": _upstream("renamed", xml_hash="a" * 64, slug="new")},
            now,
        )
        is prior
    )


def test_run_sync_aborts_on_dirty_corpus_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C3 regression: a prior sync that wrote the manifest/docs but crashed
    before committing leaves the worktree dirty and the on-disk manifest
    ahead of git. run_sync must abort loudly rather than read the poisoned
    manifest, classify everything unchanged, and silently drop the work.

    Upstream + manifest load are stubbed so that WITHOUT the guard the run
    would return a clean no-op — making the guard the only thing that can
    raise here."""
    settings = _settings(tmp_path)
    repo = settings.lovverk_repo_path
    (repo / "lover").mkdir()
    (repo / "lover" / "x.md").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    # Crash residue: a tracked file modified but never committed.
    (repo / "lover" / "x.md").write_text("v2 uncommitted\n", encoding="utf-8")

    _disable_migrations(monkeypatch)
    monkeypatch.setattr(
        orchestrator_module,
        "_load_or_empty_manifest",
        lambda _path: Manifest(generated_at=datetime(2026, 5, 1, tzinfo=UTC), documents={}),
    )
    monkeypatch.setattr(orchestrator_module, "_collect_upstream", lambda *_args: {})

    with pytest.raises(CorpusStateError, match="uncommitted"):
        run_sync(settings)


def _current_manifest(dataset: str, count: int) -> Manifest:
    docs = {
        f"{dataset}-{i}": ManifestRecord(
            doc_type="lov",
            xml_hash=f"{i:064x}",
            markdown_path=f"lover/{dataset}-{i}.md",
            source_dataset=dataset,
            last_seen=datetime(2026, 5, 1, tzinfo=UTC),
            status="current",
            slug=f"{dataset}-{i}",
            title=f"doc {i}",
            eu_basis=[],
        )
        for i in range(count)
    }
    return Manifest(generated_at=datetime(2026, 5, 1, tzinfo=UTC), documents=docs)


def test_guard_mass_removal_raises_over_threshold() -> None:
    manifest = _current_manifest("gjeldende-lover", 100)
    removed = [f"gjeldende-lover-{i}" for i in range(50)]

    with pytest.raises(MassRemovalError, match="50/100"):
        _guard_mass_removal(removed, manifest, max_ratio=0.10)


def test_guard_mass_removal_allows_removal_under_threshold() -> None:
    manifest = _current_manifest("gjeldende-lover", 100)
    removed = [f"gjeldende-lover-{i}" for i in range(5)]

    _guard_mass_removal(removed, manifest, max_ratio=0.10)  # no raise


def test_guard_mass_removal_skips_datasets_below_min_docs() -> None:
    """A small/early-stage dataset can legitimately churn a large fraction;
    the absolute risk (a handful of docs) is low, so the guard skips it."""
    manifest = _current_manifest("gjeldende-lover", 10)
    removed = [f"gjeldende-lover-{i}" for i in range(10)]

    _guard_mass_removal(removed, manifest, max_ratio=0.10)  # no raise


def test_guard_mass_removal_is_noop_for_empty_removed() -> None:
    manifest = _current_manifest("gjeldende-lover", 100)

    _guard_mass_removal([], manifest, max_ratio=0.10)  # no raise


def test_guard_mass_removal_is_per_dataset_not_global() -> None:
    """A wiped small dataset must trip even when a large healthy sibling
    keeps the global removal fraction tiny (30/1030 = 3%)."""
    docs = {
        **_current_manifest("gjeldende-lover", 1000).documents,
        **_current_manifest("gjeldende-sentrale-forskrifter", 30).documents,
    }
    manifest = Manifest(generated_at=datetime(2026, 5, 1, tzinfo=UTC), documents=docs)
    removed = [f"gjeldende-sentrale-forskrifter-{i}" for i in range(30)]

    with pytest.raises(MassRemovalError, match="gjeldende-sentrale-forskrifter"):
        _guard_mass_removal(removed, manifest, max_ratio=0.10)


def test_guard_mass_removal_respects_configured_ratio() -> None:
    manifest = _current_manifest("gjeldende-lover", 100)

    _guard_mass_removal(
        [f"gjeldende-lover-{i}" for i in range(50)],
        manifest,
        max_ratio=0.60,
    )  # 50% < 60% -> no raise
    with pytest.raises(MassRemovalError):
        _guard_mass_removal(
            [f"gjeldende-lover-{i}" for i in range(70)],
            manifest,
            max_ratio=0.60,
        )


def test_run_sync_aborts_on_mass_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Safety guard: an empty/truncated upstream makes every doc in a
    dataset look removed. run_sync must abort before deleting anything
    rather than wipe the dataset and let the workflow push it."""
    settings = _settings(tmp_path)
    prior = _current_manifest("gjeldende-lover", 30)
    _disable_migrations(monkeypatch)
    monkeypatch.setattr(orchestrator_module, "_load_or_empty_manifest", lambda _path: prior)
    monkeypatch.setattr(orchestrator_module, "_collect_upstream", lambda *_args: {})

    def fail_commit(*_args: object, **_kwargs: object) -> None:
        pytest.fail("mass-removal sync must not reach commit")

    monkeypatch.setattr(orchestrator_module, "_commit_with_history", fail_commit)

    with pytest.raises(MassRemovalError, match="30/30"):
        run_sync(settings)


def test_run_sync_noops_without_rewriting_manifest_or_committing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    prior = Manifest(
        generated_at=datetime(2026, 5, 1, tzinfo=UTC),
        documents={
            "lov-same": _record("lov-same", xml_hash="a" * 64, slug="same"),
        },
    )

    _disable_migrations(monkeypatch)
    monkeypatch.setattr(orchestrator_module, "_load_or_empty_manifest", lambda _path: prior)
    monkeypatch.setattr(
        orchestrator_module,
        "_collect_upstream",
        lambda *_args: {
            "lov-same": _upstream("lov-same", xml_hash="a" * 64, slug="same"),
        },
    )

    def fail_commit(*_args: object, **_kwargs: object) -> None:
        pytest.fail("no-op sync must not commit")

    monkeypatch.setattr(orchestrator_module, "_commit_with_history", fail_commit)

    report = run_sync(settings)

    assert report == SyncReport(
        new_count=0,
        changed_count=0,
        removed_count=0,
        unchanged_count=1,
    )
    assert (settings.data_dir / "cache" / "archives").is_dir()


def test_run_sync_builds_actions_for_new_changed_renamed_and_removed_docs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    prior = Manifest(
        generated_at=datetime(2026, 5, 1, tzinfo=UTC),
        documents={
            "lov-same": _record("lov-same", xml_hash="a" * 64, slug="same"),
            "lov-changed": _record("lov-changed", xml_hash="b" * 64, slug="changed-old"),
            "lov-renamed": _record("lov-renamed", xml_hash="c" * 64, slug="renamed-old"),
            "lov-removed": _record("lov-removed", xml_hash="d" * 64, slug="removed"),
        },
    )
    upstream = {
        "lov-same": _upstream("lov-same", xml_hash="a" * 64, slug="same"),
        "lov-changed": _upstream("lov-changed", xml_hash="e" * 64, slug="changed-new"),
        "lov-renamed": _upstream("lov-renamed", xml_hash="c" * 64, slug="renamed-new"),
        "lov-new": _upstream("lov-new", xml_hash="f" * 64, slug="new"),
    }
    captured: dict[str, object] = {}
    deleted: list[Path] = []
    retrieved_at_by_doc: dict[str, datetime | None] = {}

    def fake_write_one(
        settings: Settings,
        upstream_doc: _UpstreamDoc,
        _now: datetime,
        _embedder: object,
    ) -> tuple[ManifestRecord, list[Path]]:
        retrieved_at_by_doc[upstream_doc.doc_id] = upstream_doc.retrieved_at
        path = settings.lovverk_repo_path / "lover" / f"{upstream_doc.slug}.md"
        return (
            _record(upstream_doc.doc_id, xml_hash=upstream_doc.xml_hash, slug=upstream_doc.slug),
            [path],
        )

    def capture_commit(*_args: object, **kwargs: object) -> None:
        captured.update(kwargs)

    _disable_migrations(monkeypatch)
    monkeypatch.setattr(orchestrator_module, "_load_or_empty_manifest", lambda _path: prior)
    monkeypatch.setattr(orchestrator_module, "_collect_upstream", lambda *_args: upstream)
    monkeypatch.setattr(orchestrator_module, "_write_one", fake_write_one)
    monkeypatch.setattr(orchestrator_module, "delete_document", deleted.append)
    monkeypatch.setattr(orchestrator_module, "_commit_with_history", capture_commit)

    report = run_sync(settings)

    assert report == SyncReport(
        new_count=1,
        changed_count=1,
        removed_count=1,
        unchanged_count=2,
    )
    assert captured["manifest_path"] == settings.lovverk_repo_path / "manifest.json"
    assert captured["repo"] == settings.lovverk_repo_path
    assert captured["is_sprint4_migration"] is False
    assert captured["force_bulk_commit"] is False

    actions = captured["actions"]
    assert isinstance(actions, list)
    assert [(action.action, action.doc_id, action.slug) for action in actions] == [
        ("add", "lov-new", "new"),
        ("update", "lov-changed", "changed-new"),
        ("rename", "lov-renamed", "renamed-new"),
        ("remove", "lov-removed", "removed"),
    ]
    assert retrieved_at_by_doc == {
        "lov-new": None,
        "lov-changed": None,
        "lov-renamed": prior.documents["lov-renamed"].last_seen,
    }
    assert actions[1].paths == (
        settings.lovverk_repo_path / "lover" / "changed-old.md",
        settings.lovverk_repo_path / "lover" / "changed-new.md",
    )
    assert actions[2].paths == (
        settings.lovverk_repo_path / "lover" / "renamed-old.md",
        settings.lovverk_repo_path / "lover" / "renamed-new.md",
    )
    assert actions[3].paths == (settings.lovverk_repo_path / "lover" / "removed.md",)
    assert deleted == [
        settings.lovverk_repo_path / "lover" / "changed-old.md",
        settings.lovverk_repo_path / "lover" / "renamed-old.md",
        settings.lovverk_repo_path / "lover" / "removed.md",
    ]


def _fake_write_one_slug_path(
    settings: Settings,
    upstream_doc: _UpstreamDoc,
    _now: datetime,
    _embedder: object,
) -> tuple[ManifestRecord, list[Path]]:
    path = settings.lovverk_repo_path / "lover" / f"{upstream_doc.slug}.md"
    return (
        _record(upstream_doc.doc_id, xml_hash=upstream_doc.xml_hash, slug=upstream_doc.slug),
        [path],
    )


def test_run_sync_carries_prior_tombstone_forward(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tombstone written by an earlier sync must persist in the manifest
    across every subsequent sync that commits — a removal is a permanent
    audit record, not a one-sync artifact. ``detect_changes`` excludes
    tombstones from its ``current`` set, so without an explicit carry they
    fall through every classification list and vanish from ``new_records``.
    """
    settings = _settings(tmp_path)
    prior = Manifest(
        generated_at=datetime(2026, 5, 1, tzinfo=UTC),
        documents={
            "lov-active": _record("lov-active", xml_hash="a" * 64, slug="active"),
            "lov-gone": _record("lov-gone", xml_hash="d" * 64, slug="gone", status="removed"),
        },
    )
    upstream = {
        "lov-active": _upstream("lov-active", xml_hash="a" * 64, slug="active"),
        "lov-new": _upstream("lov-new", xml_hash="f" * 64, slug="new"),
    }
    captured: dict[str, object] = {}

    def capture_commit(*_args: object, **kwargs: object) -> None:
        captured.update(kwargs)

    _disable_migrations(monkeypatch)
    monkeypatch.setattr(orchestrator_module, "_load_or_empty_manifest", lambda _path: prior)
    monkeypatch.setattr(orchestrator_module, "_collect_upstream", lambda *_args: upstream)
    monkeypatch.setattr(orchestrator_module, "_write_one", _fake_write_one_slug_path)
    monkeypatch.setattr(orchestrator_module, "delete_document", lambda _p: None)
    monkeypatch.setattr(orchestrator_module, "_commit_with_history", capture_commit)

    run_sync(settings)

    new_records = captured["new_records"]
    assert isinstance(new_records, dict)
    assert "lov-gone" in new_records, "tombstone dropped from manifest on the next sync"
    assert new_records["lov-gone"] == prior.documents["lov-gone"]


def test_run_sync_does_not_carry_reappeared_tombstone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tombstoned doc that reappears upstream is reclassified ``new`` and
    rewritten as ``current`` — the tombstone carry must not resurrect the
    stale ``removed`` record over the fresh one."""
    settings = _settings(tmp_path)
    prior = Manifest(
        generated_at=datetime(2026, 5, 1, tzinfo=UTC),
        documents={
            "lov-back": _record("lov-back", xml_hash="d" * 64, slug="back", status="removed"),
        },
    )
    upstream = {
        "lov-back": _upstream("lov-back", xml_hash="f" * 64, slug="back"),
    }
    captured: dict[str, object] = {}

    def capture_commit(*_args: object, **kwargs: object) -> None:
        captured.update(kwargs)

    _disable_migrations(monkeypatch)
    monkeypatch.setattr(orchestrator_module, "_load_or_empty_manifest", lambda _path: prior)
    monkeypatch.setattr(orchestrator_module, "_collect_upstream", lambda *_args: upstream)
    monkeypatch.setattr(orchestrator_module, "_write_one", _fake_write_one_slug_path)
    monkeypatch.setattr(orchestrator_module, "delete_document", lambda _p: None)
    monkeypatch.setattr(orchestrator_module, "_commit_with_history", capture_commit)

    run_sync(settings)

    new_records = captured["new_records"]
    assert new_records["lov-back"].status == "current"
    assert new_records["lov-back"].xml_hash == "f" * 64


def test_carry_tombstones_keeps_only_absent_removed_records() -> None:
    prior = Manifest(
        generated_at=datetime(2026, 5, 1, tzinfo=UTC),
        documents={
            "current": _record("current", xml_hash="a" * 64, slug="current"),
            "gone": _record("gone", xml_hash="b" * 64, slug="gone", status="removed"),
            "back": _record("back", xml_hash="c" * 64, slug="back", status="removed"),
        },
    )

    carried = orchestrator_module._carry_tombstones(prior, {"back", "other"})

    assert carried == {"gone": prior.documents["gone"]}


# --- repair under-embedded docs (flat ## § acts embedded before the parser fix) ---


def _write_md(repo: Path, slug: str, body: str) -> None:
    path = repo / "lover" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nid: x\ntitle: T\n---\n\n{body}", encoding="utf-8")


def _write_bin(repo: Path, slug: str, section_ids: list[str]) -> None:
    path = orchestrator_module._embeddings_path(repo, "gjeldende-lover", slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    pairs = [(sid, np.zeros(4, dtype=np.int8)) for sid in section_ids]
    write_embeddings(path, pairs, scale=1.0, dim=4)


def _embedded_record(slug: str) -> ManifestRecord:
    # embedding_hash == xml_hash: looks fully embedded, so hash-based staleness
    # can never flag it — the whole point of the section-count repair.
    return _record(slug, xml_hash="a" * 64, slug=slug).model_copy(
        update={"embedding_hash": "a" * 64},
    )


def test_find_undersized_embeddings_flags_only_under_embedded_current_docs(
    tmp_path: Path,
) -> None:
    # flat act: markdown has 2 H2 sections, .bin has 0 vectors -> under-embedded.
    _write_md(tmp_path, "flat", "## § 1. A\n\nx\n\n## § 2.\n\ny\n")
    _write_bin(tmp_path, "flat", [])
    # healthy act: 1 section, 1 vector -> counts match, not flagged.
    _write_md(tmp_path, "healthy", "### § 1. A\n\nx\n")
    _write_bin(tmp_path, "healthy", ["1"])
    # preamble-only act: genuinely 0 sections, 0 vectors -> not flagged.
    _write_md(tmp_path, "preamble", "Bare tekst uten paragrafer.\n")
    _write_bin(tmp_path, "preamble", [])
    prior = Manifest(
        generated_at=datetime(2026, 7, 7, tzinfo=UTC),
        documents={
            "flat": _embedded_record("flat"),
            "healthy": _embedded_record("healthy"),
            "preamble": _embedded_record("preamble"),
            "removed": _record("removed", xml_hash="b" * 64, slug="removed", status="removed"),
        },
    )

    assert _find_undersized_embeddings(tmp_path, prior) == {"flat"}


def test_mark_undersized_embeddings_stale_clears_hash_and_commits(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    repo = settings.lovverk_repo_path
    _write_md(repo, "flat", "## § 1. A\n\nx\n\n## § 2.\n\ny\n")
    _write_bin(repo, "flat", [])
    _write_md(repo, "healthy", "### § 1. A\n\nx\n")
    _write_bin(repo, "healthy", ["1"])
    write_manifest(
        Manifest(
            generated_at=datetime(2026, 7, 7, tzinfo=UTC),
            documents={"flat": _embedded_record("flat"), "healthy": _embedded_record("healthy")},
        ),
        repo / "manifest.json",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")

    count = mark_undersized_embeddings_stale(settings)

    assert count == 1
    updated = read_manifest(repo / "manifest.json")
    assert updated.documents["flat"].embedding_hash is None
    assert updated.documents["healthy"].embedding_hash == "a" * 64
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "under-embedded" in log


def test_mark_undersized_embeddings_stale_is_noop_when_all_current(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    repo = settings.lovverk_repo_path
    _write_md(repo, "healthy", "### § 1. A\n\nx\n")
    _write_bin(repo, "healthy", ["1"])
    write_manifest(
        Manifest(
            generated_at=datetime(2026, 7, 7, tzinfo=UTC),
            documents={"healthy": _embedded_record("healthy")},
        ),
        repo / "manifest.json",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert mark_undersized_embeddings_stale(settings) == 0

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert head_before == head_after
