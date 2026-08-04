"""ADR-0006 metadata-only annotation migration: truthful, drift-guarded, inert.

The migration may touch exactly one artifact class — manifest records — and
must abort on any basis drift rather than annotate from stale assumptions.
No provider is ever involved: the module has no embedder import, and the
tests additionally pin that constructing one would explode.
"""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

import lovspor.sync.input_annotation as annotation_module
from lovspor.embeddings.inputs import build_embedding_inputs, hash_embedding_inputs
from lovspor.embeddings.store import write_embeddings
from lovspor.errors import CorpusStateError
from lovspor.settings import Settings
from lovspor.storage.manifest import Manifest, ManifestRecord, read_manifest, write_manifest
from lovspor.sync.input_annotation import annotate_embedding_input_identity

_DOC = "---\ntitle: {title}\n---\n# {title}\n\n### § 1. Virkeområde\n\n{body}\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _seed_corpus(tmp_path: Path) -> tuple[Settings, Path]:
    corpus = tmp_path / "lovverk"
    (corpus / "lover" / "embeddings").mkdir(parents=True)
    (corpus / "lover" / "x.md").write_text(
        _DOC.format(title="X", body="Første tekst."),
        encoding="utf-8",
    )
    (corpus / "lover" / "y.md").write_text(
        _DOC.format(title="Y", body="Andre tekst."),
        encoding="utf-8",
    )
    for slug in ("x", "y"):
        write_embeddings(
            corpus / "lover" / "embeddings" / f"{slug}.bin",
            [("1", np.ones(4, dtype=np.int8))],
            scale=0.01,
            dim=4,
        )

    def record(slug: str, status: str = "current") -> ManifestRecord:
        return ManifestRecord(
            doc_type="lov",
            xml_hash=f"hash-{slug}",
            markdown_path=f"lover/{slug}.md",
            source_dataset="gjeldende-lover",
            last_seen=datetime(2026, 8, 1, tzinfo=UTC),
            status=status,  # type: ignore[arg-type]
            slug=slug,
            title=slug.upper(),
            embedding_hash=f"hash-{slug}",
            embedding_space_id="738c919fa57385d94c558d93c4b0e588",
        )

    manifest = Manifest(
        generated_at=datetime(2026, 8, 1, tzinfo=UTC),
        documents={
            "lov-x": record("x"),
            "lov-y": record("y"),
            "lov-gone": record("gone", status="removed"),
        },
    )
    write_manifest(manifest, corpus / "manifest.json")
    _git(corpus, "init")
    _git(corpus, "config", "user.email", "t@t")
    _git(corpus, "config", "user.name", "t")
    _git(corpus, "add", ".")
    _git(corpus, "commit", "-m", "seed")
    settings = Settings(data_dir=tmp_path / "data", lovverk_repo_path=corpus)
    return settings, corpus


def _class_bytes(corpus: Path) -> dict[str, bytes]:
    return {
        "x.md": (corpus / "lover" / "x.md").read_bytes(),
        "y.md": (corpus / "lover" / "y.md").read_bytes(),
        "x.bin": (corpus / "lover" / "embeddings" / "x.bin").read_bytes(),
        "y.bin": (corpus / "lover" / "embeddings" / "y.bin").read_bytes(),
    }


def test_annotation_is_manifest_only_deterministic_and_committed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, corpus = _seed_corpus(tmp_path)
    monkeypatch.setattr(
        "lovspor.embeddings.model.OpenAIEmbedder",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("no provider")),
    )
    before = _class_bytes(corpus)

    report = annotate_embedding_input_identity(settings)

    assert report.annotated == 2
    assert report.already_annotated == 0
    assert report.tombstones_skipped == 1
    assert _class_bytes(corpus) == before, "only the manifest may change"
    manifest = read_manifest(corpus / "manifest.json")
    for slug in ("x", "y"):
        record = next(r for r in manifest.documents.values() if r.slug == slug)
        expected = hash_embedding_inputs(
            build_embedding_inputs(
                (corpus / "lover" / f"{slug}.md").read_text(encoding="utf-8"),
            ),
        )
        assert record.embedding_input_hash == expected
        # Nothing else on the record moved.
        assert record.embedding_hash == f"hash-{slug}"
        assert record.embedding_space_id == "738c919fa57385d94c558d93c4b0e588"
    tombstone = manifest.documents["lov-gone"]
    assert tombstone.embedding_input_hash is None
    log = subprocess.run(
        ["git", "log", "--format=%s", "-1"],
        cwd=corpus,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert log == "migration: annotate embedding input identity in manifest"


def test_a_second_run_is_idempotent_with_no_new_commit(tmp_path: Path) -> None:
    settings, corpus = _seed_corpus(tmp_path)
    annotate_embedding_input_identity(settings)
    manifest_after_first = (corpus / "manifest.json").read_bytes()
    count = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=corpus,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    report = annotate_embedding_input_identity(settings)

    assert report.annotated == 0
    assert report.already_annotated == 2
    assert (corpus / "manifest.json").read_bytes() == manifest_after_first
    count_after = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=corpus,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert count_after == count


def test_corpus_head_drift_aborts_with_nothing_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ADR-0006 §6 drift invariant: the basis is re-verified immediately
    before publication; a moved HEAD aborts the whole migration."""
    settings, corpus = _seed_corpus(tmp_path)
    manifest_before = (corpus / "manifest.json").read_bytes()
    heads = iter(["a" * 40, "b" * 40])
    monkeypatch.setattr(annotation_module, "_git_head", lambda _repo: next(heads))

    with pytest.raises(CorpusStateError, match="corpus HEAD moved"):
        annotate_embedding_input_identity(settings)

    assert (corpus / "manifest.json").read_bytes() == manifest_before


def test_recompute_drift_aborts_with_nothing_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent on-disk edit between the first pass and publication makes
    the recompute disagree — abort, never annotate a moving target."""
    settings, corpus = _seed_corpus(tmp_path)
    manifest_before = (corpus / "manifest.json").read_bytes()
    original = annotation_module._verify_basis_unchanged

    def edit_then_verify(repo: Path, head_before: str, hashes: dict[str, str]) -> None:
        (corpus / "lover" / "x.md").write_text(
            _DOC.format(title="X", body="Endret tekst."),
            encoding="utf-8",
        )
        original(repo, head_before, hashes)

    monkeypatch.setattr(annotation_module, "_verify_basis_unchanged", edit_then_verify)

    with pytest.raises(CorpusStateError, match="recomputed input hashes differ"):
        annotate_embedding_input_identity(settings)

    # The mutated markdown is the test's doing; the manifest must be untouched.
    assert (corpus / "manifest.json").read_bytes() == manifest_before


def test_a_missing_sidecar_basis_aborts(tmp_path: Path) -> None:
    """Metadata-only annotation claims the stored vectors match the digested
    inputs; a record with no sidecar has no vectors to claim for — abort and
    point at regeneration instead of annotating on faith."""
    settings, corpus = _seed_corpus(tmp_path)
    (corpus / "lover" / "embeddings" / "y.bin").unlink()
    _git(corpus, "add", "-A")
    _git(corpus, "commit", "-m", "drop sidecar")

    with pytest.raises(CorpusStateError, match="no embedding sidecar"):
        annotate_embedding_input_identity(settings)


def test_a_corrupt_sidecar_basis_aborts(tmp_path: Path) -> None:
    """An existing-but-unreadable sidecar is no basis either: the current
    reader rejects it, so there are no vectors for the annotation to certify.
    Existence-only checking would bless the corrupt file."""
    settings, corpus = _seed_corpus(tmp_path)
    manifest_before = (corpus / "manifest.json").read_bytes()
    sidecar = corpus / "lover" / "embeddings" / "y.bin"
    sidecar.write_bytes(b"not-an-lspe-file")
    _git(corpus, "add", ".")
    _git(corpus, "commit", "-m", "corrupt sidecar")

    with pytest.raises(CorpusStateError, match="unreadable embedding sidecar"):
        annotate_embedding_input_identity(settings)

    assert (corpus / "manifest.json").read_bytes() == manifest_before


def test_a_dirty_worktree_aborts_before_any_work(tmp_path: Path) -> None:
    """The migration commit sweeps the index, so pre-staged unrelated files
    would ride along and break the manifest-only invariant — refuse to start
    on anything but a pristine clone."""
    settings, corpus = _seed_corpus(tmp_path)
    manifest_before = (corpus / "manifest.json").read_bytes()
    (corpus / "UNRELATED.txt").write_text("smuggled\n", encoding="utf-8")
    _git(corpus, "add", "UNRELATED.txt")
    count_before = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=corpus,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    with pytest.raises(CorpusStateError, match="worktree is not clean"):
        annotate_embedding_input_identity(settings)

    assert (corpus / "manifest.json").read_bytes() == manifest_before
    count_after = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=corpus,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert count_after == count_before
