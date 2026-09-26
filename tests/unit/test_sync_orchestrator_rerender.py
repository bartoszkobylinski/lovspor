"""run_sync's changed-document phase against a real corpus on disk (issue #229).

A document reaches the changed loop two ways: its upstream XML hash moved (a
real content change), or it was promoted to re-render with an unchanged hash
(a stale ``renderer_version`` stamp, or ``force_rerender``). The two must stay
apart all the way through: a re-render keeps the file's observation timestamp,
reports as ``rerendered_count`` rather than ``changed_count``, lands in the
history-exempt ``migration: re-render`` commit, and a byte-identical re-render
writes no file at all. The module's other tests replace ``_write_one`` and the
commit step, so none of that was pinned end to end. Here rendering, the manifest
and git are real; only the upstream download is replaced and the embedder is
switched off (a keyless sync, which is a supported production mode).
"""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

import lovspor.sync.orchestrator as orchestrator_module
from lovspor.rendering.markdown_renderer import RENDERER_VERSION
from lovspor.settings import Settings
from lovspor.storage.manifest import Manifest, ManifestRecord, read_manifest, write_manifest
from lovspor.sync.orchestrator import SyncReport, _UpstreamDoc, _write_one, run_sync
from lovspor.temporal_attestation import AttestationError
from tests.unit.test_sync_orchestrator import _git, _settings

_OBSERVED = datetime(2026, 8, 1, 4, 0, tzinfo=UTC)
_DRIFTED = datetime(2026, 8, 20, 4, 0, tzinfo=UTC)
_STALE_VERSION = RENDERER_VERSION - 1


def _xml(body: str) -> bytes:
    return (
        b'<!DOCTYPE html><html lang="nb"><head><title>Skattie</title></head>'
        b'<body><header class="documentHeader"><dl>'
        b'<dt class="title">Tittel</dt><dd class="title">Skattie</dd>'
        b'<dt class="refid">RefID</dt><dd class="refid">lov/x</dd>'
        b'</dl></header><main id="dokument"><h1>Skattie</h1>'
        b'<article class="legalP" id="ledd-1">' + body.encode() + b"</article>"
        b"</main></body></html>"
    )


def _doc(*, xml_hash: str = "a" * 64, slug: str = "skattie", body: str = "Body.") -> _UpstreamDoc:
    return _UpstreamDoc(
        doc_id="lov-1",
        source_dataset="gjeldende-lover",
        xml_bytes=_xml(body),
        xml_hash=xml_hash,
        slug=slug,
        title="Skattie",
        eu_basis=(),
    )


_PUBLISHED = _doc()


def _corpus(tmp_path: Path, **record_updates: object) -> Settings:
    """A committed corpus holding one law as a real sync published it.

    ``record_updates`` then edits that law's manifest record only, which is how
    a corpus written by an older renderer, or with a drifted ``last_seen``,
    looks on disk: the file is untouched, the record says otherwise.
    """
    settings = _settings(tmp_path)
    repo = settings.lovverk_repo_path
    seeded = orchestrator_module._with_retrieved_at(_PUBLISHED, _OBSERVED)
    record, _paths = _write_one(settings, seeded, _OBSERVED, None)
    (repo / "lover" / "history").mkdir()
    (repo / "lover" / "history" / ".keep").write_text("", encoding="utf-8")
    documents = {"lov-1": record.model_copy(update=record_updates)}
    write_manifest(Manifest(generated_at=_OBSERVED, documents=documents), repo / "manifest.json")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "publish lov-1")
    return settings


def _serve(monkeypatch: pytest.MonkeyPatch, settings: Settings, doc: _UpstreamDoc) -> None:
    def load(passed: Settings) -> None:
        assert passed is settings

    monkeypatch.setattr(orchestrator_module, "_load_embedder", load)
    monkeypatch.setattr(
        orchestrator_module, "_collect_upstream", lambda *_args: ({"lov-1": doc}, ())
    )


def _subjects(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "log", "--format=%s"], cwd=repo, check=True, capture_output=True, text=True
    )
    return out.stdout.splitlines()


def _changed_files(repo: Path, subject: str) -> set[str]:
    """Paths the (single) commit with ``subject`` touched."""
    sha = subprocess.run(
        ["git", "log", "--format=%H", f"--grep=^{subject}$"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert len(sha) == 1, f"expected one commit {subject!r}, got {sha}"
    out = subprocess.run(
        ["git", "show", "--name-only", "--no-renames", "--format=", sha[0]],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return set(out.stdout.split())


def _stored(settings: Settings) -> ManifestRecord:
    return read_manifest(settings.lovverk_repo_path / "manifest.json").documents["lov-1"]


def _file(settings: Settings, slug: str = "skattie") -> Path:
    return settings.lovverk_repo_path / "lover" / f"{slug}.md"


def _retrieved_at_line(path: Path) -> str:
    return next(
        line for line in path.read_text("utf-8").splitlines() if line.startswith("retrieved_at:")
    )


_MIGRATION_SUBJECT = f"migration: re-render 1 documents (renderer v{RENDERER_VERSION})"


def test_a_stale_stamp_on_an_identical_render_refreshes_the_stamp_and_writes_no_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path, renderer_version=_STALE_VERSION)
    repo = settings.lovverk_repo_path
    before = _file(settings).read_bytes()
    _serve(monkeypatch, settings, _PUBLISHED)

    report = run_sync(settings)

    assert report == SyncReport(new_count=0, changed_count=0, removed_count=0, unchanged_count=1)
    assert _file(settings).read_bytes() == before
    stored = _stored(settings)
    assert stored.renderer_version == RENDERER_VERSION
    assert stored.last_seen == _OBSERVED
    assert _MIGRATION_SUBJECT not in _subjects(repo)
    # The stamp refresh is a manifest-only change: it still has to land.
    assert _subjects(repo)[0] == "sync: update manifest, index, and history"
    assert "lover/skattie.md" not in _changed_files(repo, _subjects(repo)[0])


def test_an_identical_render_reconciles_a_drifted_last_seen_to_the_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path, renderer_version=_STALE_VERSION, last_seen=_DRIFTED)
    before = _file(settings).read_bytes()
    _serve(monkeypatch, settings, _PUBLISHED)

    run_sync(settings)

    assert _file(settings).read_bytes() == before
    stored = _stored(settings)
    assert stored.last_seen == _OBSERVED
    assert stored.renderer_version == RENDERER_VERSION


def test_a_drifted_last_seen_alone_is_reconciled_under_force_rerender(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path, last_seen=_DRIFTED)
    _serve(monkeypatch, settings, _PUBLISHED)

    report = run_sync(settings, force_rerender=True)

    assert report.unchanged_count == 1
    assert report.rerendered_count == 0
    stored = _stored(settings)
    assert stored.last_seen == _OBSERVED
    assert stored.renderer_version == RENDERER_VERSION


def test_force_rerender_of_a_current_identical_corpus_commits_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path)
    repo = settings.lovverk_repo_path
    manifest_before = (repo / "manifest.json").read_bytes()
    _serve(monkeypatch, settings, _PUBLISHED)

    report = run_sync(settings, force_rerender=True)

    assert report == SyncReport(new_count=0, changed_count=0, removed_count=0, unchanged_count=1)
    assert _subjects(repo) == ["publish lov-1"]
    assert (repo / "manifest.json").read_bytes() == manifest_before


@pytest.mark.parametrize(
    ("record_updates", "force"),
    [({"renderer_version": _STALE_VERSION}, False), ({}, True)],
    ids=["stale-stamp", "force-rerender"],
)
def test_a_rerender_that_differs_rewrites_the_file_in_the_exempt_migration_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record_updates: dict[str, object],
    force: bool,
) -> None:
    settings = _corpus(tmp_path, **record_updates)
    repo = settings.lovverk_repo_path
    published = _file(settings).read_text("utf-8")
    # An older renderer's output: same frontmatter, different body.
    _file(settings).write_text(published + "\nstale renderer tail\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "older renderer output")
    _serve(monkeypatch, settings, _PUBLISHED)

    report = run_sync(settings, force_rerender=force)

    assert report == SyncReport(
        new_count=0, changed_count=0, removed_count=0, unchanged_count=0, rerendered_count=1
    )
    assert _file(settings).read_text("utf-8") == published
    assert _MIGRATION_SUBJECT in _subjects(repo)
    assert _changed_files(repo, _MIGRATION_SUBJECT) == {"lover/skattie.md"}
    assert not any(subject.startswith("update(") for subject in _subjects(repo))
    # A renderer-only write is not a new observation of the source.
    assert _retrieved_at_line(_file(settings)) == f'retrieved_at: "{_OBSERVED.isoformat()}"'
    stored = _stored(settings)
    assert stored.last_seen == _OBSERVED
    assert stored.renderer_version == RENDERER_VERSION


def test_a_real_content_change_is_counted_as_changed_and_observed_now(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A stale stamp on the same document must not turn a real change into a re-render.
    settings = _corpus(tmp_path, renderer_version=_STALE_VERSION)
    repo = settings.lovverk_repo_path
    _serve(monkeypatch, settings, _doc(xml_hash="b" * 64, body="Amended body."))

    report = run_sync(settings)

    assert report == SyncReport(new_count=0, changed_count=1, removed_count=0, unchanged_count=0)
    assert "Amended body." in _file(settings).read_text("utf-8")
    assert _retrieved_at_line(_file(settings)) != f'retrieved_at: "{_OBSERVED.isoformat()}"'
    assert _MIGRATION_SUBJECT not in _subjects(repo)
    assert "update(lov): skattie" in _subjects(repo)
    assert _changed_files(repo, r"update(lov): skattie") == {"lover/skattie.md"}
    stored = _stored(settings)
    assert stored.xml_hash == "b" * 64
    assert stored.last_seen > _OBSERVED
    assert stored.renderer_version == RENDERER_VERSION


def test_a_content_change_under_a_new_slug_moves_the_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path)
    repo = settings.lovverk_repo_path
    _serve(monkeypatch, settings, _doc(xml_hash="b" * 64, slug="skattelova", body="Amended."))

    report = run_sync(settings)

    assert report.changed_count == 1
    assert not _file(settings).exists()
    assert "Amended." in _file(settings, "skattelova").read_text("utf-8")
    assert _changed_files(repo, r"update(lov): skattelova") == {
        "lover/skattie.md",
        "lover/skattelova.md",
    }
    stored = _stored(settings)
    assert stored.slug == "skattelova"
    assert stored.markdown_path == "lover/skattelova.md"


def test_an_unrenderable_content_change_keeps_the_published_version_and_refuses_to_attest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path)
    before = _file(settings).read_bytes()
    unrenderable = _UpstreamDoc(
        doc_id="lov-1",
        source_dataset="gjeldende-lover",
        xml_bytes=b"<not-xml",
        xml_hash="b" * 64,
        slug="skattie",
        title="Skattie",
        eu_basis=(),
    )
    _serve(monkeypatch, settings, unrenderable)

    # The old rendering is carried forward, but it cannot be counted against
    # the new source, so the run fails its attestation gate before anything is
    # pushed (ADR-0012 point 2a; review of PR #227, blocker 3).
    with pytest.raises(AttestationError, match="carried-forward document"):
        run_sync(settings)

    assert _file(settings).read_bytes() == before
    # The prior hash is kept, so the change is detected and retried next sync.
    assert _stored(settings).xml_hash == "a" * 64
    assert _stored(settings).slug == "skattie"


def test_a_renamed_document_with_a_stale_stamp_is_a_rename_not_a_rerender(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path, renderer_version=_STALE_VERSION)
    repo = settings.lovverk_repo_path
    _serve(monkeypatch, settings, _doc(slug="skattelova"))

    report = run_sync(settings)

    assert report.rerendered_count == 0
    assert report.changed_count == 0
    assert _MIGRATION_SUBJECT not in _subjects(repo)
    assert not _file(settings).exists()
    assert _file(settings, "skattelova").is_file()
    assert _stored(settings).renderer_version == RENDERER_VERSION
