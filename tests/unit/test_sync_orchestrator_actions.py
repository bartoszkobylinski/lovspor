"""run_sync's phase 2 — deletes and action build for changed and renamed docs (issue #229).

After every write has landed, the changed loop and the rename loop each delete
the file and embedding sidecar a document just moved away from, then record the
action its commit is made from. Both deletes are guarded by the set of paths
this sync wrote: a slot another document has just moved into must survive, or
the sync destroys the other document's content (production crashes 2026-04-30
and 2026-05-05). The module's other tests replace ``_write_one`` and the commit
step, so none of this was pinned against real files. Here rendering, the
manifest and git are real; only the upstream download is replaced, and the
embedder is either switched off (a keyless sync) or a local stand-in that
returns fixed vectors in place of the OpenAI call.
"""

import hashlib
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

import lovspor.sync.orchestrator as orchestrator_module
from lovspor.embeddings import LEGACY_SPACE_DESCRIPTOR, esi_for_descriptor
from lovspor.settings import Settings
from lovspor.storage.manifest import Manifest, ManifestRecord, read_manifest, write_manifest
from lovspor.sync.orchestrator import SyncReport, _UpstreamDoc, _write_one, run_sync
from tests.unit.test_sync_orchestrator import _git, _settings

_PUBLISHED_AT = datetime(2026, 8, 1, 4, 0, tzinfo=UTC)


class _LocalEmbedder:
    """Fixed vectors under a declared identity, in place of the network call."""

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 4), dtype=np.float32)

    def get_dimension(self) -> int:
        return 4

    @property
    def descriptor(self) -> str:
        return LEGACY_SPACE_DESCRIPTOR

    @property
    def space_id(self) -> str:
        return esi_for_descriptor(LEGACY_SPACE_DESCRIPTOR)


def _xml(body: str) -> bytes:
    return (
        b'<!DOCTYPE html><html lang="nb"><head><title>Lov</title></head>'
        b'<body><header class="documentHeader"><dl>'
        b'<dt class="title">Tittel</dt><dd class="title">Lov</dd>'
        b'<dt class="refid">RefID</dt><dd class="refid">lov/x</dd>'
        b'</dl></header><main id="dokument"><h1>Lov</h1>'
        b'<article class="legalP" id="ledd-1">' + body.encode() + b"</article>"
        b"</main></body></html>"
    )


def _doc(doc_id: str, slug: str, body: str) -> _UpstreamDoc:
    return _UpstreamDoc(
        doc_id=doc_id,
        source_dataset="gjeldende-lover",
        xml_bytes=_xml(body),
        xml_hash=hashlib.sha256(body.encode()).hexdigest(),
        slug=slug,
        title="Lov",
        eu_basis=(),
    )


def _corpus(
    tmp_path: Path,
    published: list[_UpstreamDoc],
    embedder: _LocalEmbedder | None = None,
) -> Settings:
    """A committed corpus holding ``published`` exactly as a sync wrote them."""
    settings = _settings(tmp_path)
    repo = settings.lovverk_repo_path
    documents: dict[str, ManifestRecord] = {}
    for doc in published:
        seeded = orchestrator_module._with_retrieved_at(doc, _PUBLISHED_AT)
        documents[doc.doc_id], _ = _write_one(settings, seeded, _PUBLISHED_AT, embedder)  # type: ignore[arg-type]
    (repo / "lover" / "history").mkdir(exist_ok=True)
    (repo / "lover" / "history" / ".keep").write_text("", encoding="utf-8")
    write_manifest(
        Manifest(generated_at=_PUBLISHED_AT, documents=documents), repo / "manifest.json"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "publish")
    return settings


def _plant_sidecar(settings: Settings, slug: str) -> None:
    """A sidecar a keyed sync left behind, committed with the corpus."""
    repo = settings.lovverk_repo_path
    path = orchestrator_module._embeddings_path(repo, "gjeldende-lover", slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"prior vectors")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"sidecar {slug}")


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    upstream: list[_UpstreamDoc],
    embedder: _LocalEmbedder | None = None,
) -> None:
    monkeypatch.setattr(orchestrator_module, "_load_embedder", lambda _settings: embedder)
    served = {doc.doc_id: doc for doc in upstream}
    monkeypatch.setattr(orchestrator_module, "_collect_upstream", lambda *_args: (served, ()))


def _tracked(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return set(out.stdout.split())


def _subjects(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "log", "--format=%s"], cwd=repo, check=True, capture_output=True, text=True
    )
    return out.stdout.splitlines()


def _touched(repo: Path, subject: str) -> set[str]:
    """Paths the single commit whose subject is ``subject`` touched."""
    sha = subprocess.run(
        ["git", "log", "--format=%H", "--fixed-strings", f"--grep={subject}"],
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


def _body(settings: Settings, slug: str) -> str:
    return (settings.lovverk_repo_path / "lover" / f"{slug}.md").read_text("utf-8")


def _stored(settings: Settings) -> dict[str, ManifestRecord]:
    return read_manifest(settings.lovverk_repo_path / "manifest.json").documents


_ALFA = _doc("lov-1", "alfa", "Alfa text.")
_BETA = _doc("lov-2", "beta", "Beta text.")


class TestChangedDocumentPhaseTwo:
    def test_a_changed_doc_that_moved_leaves_its_old_file_and_sidecar_in_its_own_commit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = _corpus(tmp_path, [_ALFA])
        _plant_sidecar(settings, "alfa")
        repo = settings.lovverk_repo_path
        _serve(monkeypatch, [_doc("lov-1", "gamma", "Changed text.")])

        report = run_sync(settings)

        assert report == SyncReport(
            new_count=0, changed_count=1, removed_count=0, unchanged_count=0
        )
        tracked = _tracked(repo)
        assert "lover/gamma.md" in tracked
        assert "lover/alfa.md" not in tracked
        assert "lover/embeddings/alfa.bin" not in tracked
        assert not (repo / "lover" / "alfa.md").exists()
        assert not (repo / "lover" / "embeddings" / "alfa.bin").exists()
        assert "Changed text." in _body(settings, "gamma")
        assert _touched(repo, "update(lov): gamma") == {
            "lover/alfa.md",
            "lover/gamma.md",
            "lover/embeddings/alfa.bin",
        }
        record = _stored(settings)["lov-1"]
        assert (record.slug, record.markdown_path) == ("gamma", "lover/gamma.md")

    def test_a_changed_doc_that_kept_its_slug_rewrites_in_place_and_keeps_its_sidecar(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = _corpus(tmp_path, [_ALFA])
        _plant_sidecar(settings, "alfa")
        repo = settings.lovverk_repo_path
        _serve(monkeypatch, [_doc("lov-1", "alfa", "Changed text.")])

        report = run_sync(settings)

        assert report.changed_count == 1
        assert "Changed text." in _body(settings, "alfa")
        assert (repo / "lover" / "embeddings" / "alfa.bin").read_bytes() == b"prior vectors"
        assert _touched(repo, "update(lov): alfa") == {"lover/alfa.md"}

    def test_a_new_doc_taking_the_vacated_slot_keeps_its_content(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = _corpus(tmp_path, [_ALFA])
        repo = settings.lovverk_repo_path
        _serve(
            monkeypatch,
            [_doc("lov-1", "gamma", "Changed text."), _doc("lov-2", "alfa", "Newcomer text.")],
        )

        report = run_sync(settings)

        assert (report.new_count, report.changed_count) == (1, 1)
        assert "Newcomer text." in _body(settings, "alfa")
        assert "Changed text." in _body(settings, "gamma")
        assert {"lover/alfa.md", "lover/gamma.md"} <= _tracked(repo)
        stored = _stored(settings)
        assert stored["lov-1"].markdown_path == "lover/gamma.md"
        assert stored["lov-2"].markdown_path == "lover/alfa.md"

    def test_a_keyed_new_doc_taking_the_vacated_slot_keeps_its_sidecar(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        embedder = _LocalEmbedder()
        settings = _corpus(tmp_path, [_ALFA], embedder)
        repo = settings.lovverk_repo_path
        _serve(
            monkeypatch,
            [_doc("lov-1", "gamma", "Changed text."), _doc("lov-2", "alfa", "Newcomer text.")],
            embedder,
        )

        run_sync(settings)

        tracked = _tracked(repo)
        assert {"lover/embeddings/alfa.bin", "lover/embeddings/gamma.bin"} <= tracked
        assert (repo / "lover" / "embeddings" / "alfa.bin").exists()

    def test_a_keyed_move_into_a_slot_another_doc_vacated_keeps_the_new_sidecar(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        embedder = _LocalEmbedder()
        settings = _corpus(tmp_path, [_ALFA, _BETA], embedder)
        repo = settings.lovverk_repo_path
        _serve(
            monkeypatch,
            [_doc("lov-1", "beta", "Changed alfa."), _doc("lov-2", "delta", "Changed beta.")],
            embedder,
        )

        run_sync(settings)

        tracked = _tracked(repo)
        assert {"lover/beta.md", "lover/delta.md"} <= tracked
        assert "lover/alfa.md" not in tracked
        assert "Changed alfa." in _body(settings, "beta")
        assert {"lover/embeddings/beta.bin", "lover/embeddings/delta.bin"} <= tracked
        assert "lover/embeddings/alfa.bin" not in tracked
        assert (repo / "lover" / "embeddings" / "beta.bin").exists()


class TestRenamedDocumentPhaseTwo:
    def test_a_rename_moves_the_file_and_drops_the_old_sidecar_in_its_own_commit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = _corpus(tmp_path, [_ALFA])
        _plant_sidecar(settings, "alfa")
        repo = settings.lovverk_repo_path
        _serve(monkeypatch, [replace(_ALFA, slug="gamma")])

        report = run_sync(settings)

        assert report == SyncReport(
            new_count=0, changed_count=0, removed_count=0, unchanged_count=1
        )
        tracked = _tracked(repo)
        assert "lover/gamma.md" in tracked
        assert "lover/alfa.md" not in tracked
        assert "lover/embeddings/alfa.bin" not in tracked
        assert not (repo / "lover" / "alfa.md").exists()
        assert "Alfa text." in _body(settings, "gamma")
        assert _touched(repo, "rename(lov): gamma") == {
            "lover/alfa.md",
            "lover/gamma.md",
            "lover/embeddings/alfa.bin",
        }
        record = _stored(settings)["lov-1"]
        assert (record.slug, record.markdown_path) == ("gamma", "lover/gamma.md")

    def test_two_docs_swapping_slugs_both_survive(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = _corpus(tmp_path, [_ALFA, _BETA])
        repo = settings.lovverk_repo_path
        _serve(monkeypatch, [replace(_ALFA, slug="beta"), replace(_BETA, slug="alfa")])

        run_sync(settings)

        assert "Alfa text." in _body(settings, "beta")
        assert "Beta text." in _body(settings, "alfa")
        assert {"lover/alfa.md", "lover/beta.md"} <= _tracked(repo)
        stored = _stored(settings)
        assert stored["lov-1"].markdown_path == "lover/beta.md"
        assert stored["lov-2"].markdown_path == "lover/alfa.md"

    def test_a_keyed_slug_swap_keeps_both_freshly_written_sidecars(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        embedder = _LocalEmbedder()
        settings = _corpus(tmp_path, [_ALFA, _BETA], embedder)
        repo = settings.lovverk_repo_path
        _serve(
            monkeypatch,
            [replace(_ALFA, slug="beta"), replace(_BETA, slug="alfa")],
            embedder,
        )

        run_sync(settings)

        tracked = _tracked(repo)
        assert {"lover/embeddings/alfa.bin", "lover/embeddings/beta.bin"} <= tracked
        assert (repo / "lover" / "embeddings" / "alfa.bin").exists()
        assert (repo / "lover" / "embeddings" / "beta.bin").exists()

    def test_a_rename_into_the_slot_a_changed_doc_vacated_keeps_the_renamed_content(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = _corpus(tmp_path, [_ALFA, _BETA])
        repo = settings.lovverk_repo_path
        _serve(
            monkeypatch,
            [_doc("lov-1", "gamma", "Changed alfa."), replace(_BETA, slug="alfa")],
        )

        report = run_sync(settings)

        assert report.changed_count == 1
        assert "Beta text." in _body(settings, "alfa")
        assert "Changed alfa." in _body(settings, "gamma")
        assert not (repo / "lover" / "beta.md").exists()
        tracked = _tracked(repo)
        assert {"lover/alfa.md", "lover/gamma.md"} <= tracked
        assert "lover/beta.md" not in tracked

    def test_a_rename_whose_old_slot_a_new_doc_took_keeps_the_newcomer(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = _corpus(tmp_path, [_ALFA])
        repo = settings.lovverk_repo_path
        _serve(
            monkeypatch,
            [replace(_ALFA, slug="gamma"), _doc("lov-2", "alfa", "Newcomer text.")],
        )

        report = run_sync(settings)

        assert report.new_count == 1
        assert "Newcomer text." in _body(settings, "alfa")
        assert "Alfa text." in _body(settings, "gamma")
        assert {"lover/alfa.md", "lover/gamma.md"} <= _tracked(repo)
        assert _subjects(repo)[0].startswith("sync:")
