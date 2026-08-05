"""ADR-0005 Stage 2 cutover: bit-faithful, .bin-only, fail-closed.

The cutover may touch exactly one artifact class — embedding sidecars — and
must preserve every stored vector bit-for-bit. No provider is ever involved:
the module has no embedder import, and the happy-path test additionally pins
that constructing one would explode.
"""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

import lovspor.sync.lspe_cutover as cutover_module
from lovspor.embeddings.store import read_embeddings, write_embeddings
from lovspor.errors import CorpusStateError
from lovspor.settings import Settings
from lovspor.storage.manifest import Manifest, ManifestRecord, write_manifest
from lovspor.sync.lspe_cutover import migrate_lspe_v2

_ESI = "738c919fa57385d94c558d93c4b0e588"
_DOC = "---\ntitle: {title}\n---\n# {title}\n\n### § 1. Virkeområde\n\n{body}\n"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _record(slug: str, status: str = "current", esi: str | None = _ESI) -> ManifestRecord:
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
        embedding_space_id=esi,
    )


def _seed_corpus(tmp_path: Path, records: dict[str, ManifestRecord]) -> tuple[Settings, Path]:
    corpus = tmp_path / "lovverk"
    (corpus / "lover" / "embeddings").mkdir(parents=True)
    for record in records.values():
        if record.status != "current" or record.slug is None:
            continue
        (corpus / record.markdown_path).write_text(
            _DOC.format(title=record.slug.upper(), body=f"Tekst for {record.slug}."),
            encoding="utf-8",
        )
        vector = np.arange(4, dtype=np.int8) + len(record.slug)
        write_embeddings(
            corpus / "lover" / "embeddings" / f"{record.slug}.bin",
            [("1", vector)],
            scale=0.01,
            dim=4,
        )
    manifest = Manifest(generated_at=datetime(2026, 8, 1, tzinfo=UTC), documents=records)
    write_manifest(manifest, corpus / "manifest.json")
    _git(corpus, "init")
    _git(corpus, "config", "user.email", "t@t")
    _git(corpus, "config", "user.name", "t")
    _git(corpus, "add", ".")
    _git(corpus, "commit", "-m", "seed")
    settings = Settings(data_dir=tmp_path / "data", lovverk_repo_path=corpus)
    return settings, corpus


def _default_records() -> dict[str, ManifestRecord]:
    return {
        "lov-x": _record("x"),
        "lov-y": _record("y"),
        "lov-gone": _record("gone", status="removed"),
    }


def test_cutover_rewrites_v1_to_v2_bit_faithfully_and_commits_bins_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, corpus = _seed_corpus(tmp_path, _default_records())
    monkeypatch.setattr(
        "lovspor.embeddings.model.OpenAIEmbedder",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("no provider")),
    )
    before = {
        slug: read_embeddings(corpus / "lover" / "embeddings" / f"{slug}.bin")
        for slug in ("x", "y")
    }
    manifest_before = (corpus / "manifest.json").read_bytes()
    md_before = (corpus / "lover" / "x.md").read_bytes()

    report = migrate_lspe_v2(settings)

    assert report.rewritten == 2
    assert report.already_v2 == 0
    assert report.tombstones_skipped == 1
    assert report.header_only == 0
    for slug, old in before.items():
        new = read_embeddings(corpus / "lover" / "embeddings" / f"{slug}.bin")
        assert new.version == 2
        assert new.embedding_space_id == _ESI
        assert new.dim == old.dim
        assert new.scale == old.scale
        assert [s for s, _v in new.sections] == [s for s, _v in old.sections]
        for (_, new_vec), (_, old_vec) in zip(new.sections, old.sections, strict=True):
            np.testing.assert_array_equal(new_vec, old_vec)
    assert (corpus / "manifest.json").read_bytes() == manifest_before
    assert (corpus / "lover" / "x.md").read_bytes() == md_before
    assert _git(corpus, "log", "--format=%s", "-1") == (
        "migration: rewrite embedding sidecars as LSPE version 2"
    )
    changed = _git(corpus, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert sorted(changed) == ["lover/embeddings/x.bin", "lover/embeddings/y.bin"]


def test_a_second_run_is_idempotent_with_no_new_commit(tmp_path: Path) -> None:
    settings, corpus = _seed_corpus(tmp_path, _default_records())
    migrate_lspe_v2(settings)
    count = _git(corpus, "rev-list", "--count", "HEAD")

    report = migrate_lspe_v2(settings)

    assert report.rewritten == 0
    assert report.already_v2 == 2
    assert _git(corpus, "rev-list", "--count", "HEAD") == count


def test_a_record_without_esi_aborts_with_nothing_written(tmp_path: Path) -> None:
    """A version-2 header carries an identity; a record whose identity was
    never recorded cannot be given one — that would be fabricated
    provenance, the failure this project treats as worse than missing
    data."""
    records = _default_records()
    records["lov-y"] = _record("y", esi=None)
    settings, corpus = _seed_corpus(tmp_path, records)
    x_before = (corpus / "lover" / "embeddings" / "x.bin").read_bytes()

    with pytest.raises(CorpusStateError, match="no recorded embedding_space_id"):
        migrate_lspe_v2(settings)

    assert (corpus / "lover" / "embeddings" / "x.bin").read_bytes() == x_before
    assert _git(corpus, "log", "--format=%s", "-1") == "seed"


def test_a_missing_sidecar_aborts(tmp_path: Path) -> None:
    settings, corpus = _seed_corpus(tmp_path, _default_records())
    (corpus / "lover" / "embeddings" / "y.bin").unlink()
    _git(corpus, "add", "-A")
    _git(corpus, "commit", "-m", "drop sidecar")

    with pytest.raises(CorpusStateError, match="no embedding sidecar"):
        migrate_lspe_v2(settings)


def test_an_unreadable_sidecar_aborts(tmp_path: Path) -> None:
    settings, corpus = _seed_corpus(tmp_path, _default_records())
    (corpus / "lover" / "embeddings" / "y.bin").write_bytes(b"not-an-lspe-file")
    _git(corpus, "add", "-A")
    _git(corpus, "commit", "-m", "corrupt sidecar")

    with pytest.raises(CorpusStateError, match="unreadable embedding sidecar"):
        migrate_lspe_v2(settings)


def test_a_dirty_worktree_aborts_before_any_work(tmp_path: Path) -> None:
    settings, corpus = _seed_corpus(tmp_path, _default_records())
    (corpus / "UNRELATED.txt").write_text("smuggled\n", encoding="utf-8")
    x_before = (corpus / "lover" / "embeddings" / "x.bin").read_bytes()

    with pytest.raises(CorpusStateError, match="worktree is not clean"):
        migrate_lspe_v2(settings)

    assert (corpus / "lover" / "embeddings" / "x.bin").read_bytes() == x_before


def test_corpus_head_drift_aborts_before_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, corpus = _seed_corpus(tmp_path, _default_records())
    x_before = (corpus / "lover" / "embeddings" / "x.bin").read_bytes()
    heads = iter(["a" * 40, "b" * 40, "c" * 40])
    monkeypatch.setattr(cutover_module, "_git_head", lambda _repo: next(heads))

    with pytest.raises(CorpusStateError, match="corpus HEAD moved"):
        migrate_lspe_v2(settings)

    assert (corpus / "lover" / "embeddings" / "x.bin").read_bytes() == x_before
    assert _git(corpus, "log", "--format=%s", "-1") == "seed"


def test_an_existing_v2_file_disagreeing_with_the_manifest_aborts(tmp_path: Path) -> None:
    """An already-v2 file whose header identity contradicts the manifest is
    the tamper case Stage 2 exists to expose — never harmonized, always an
    abort."""
    settings, corpus = _seed_corpus(tmp_path, _default_records())
    other_esi = "0" * 32
    parsed = read_embeddings(corpus / "lover" / "embeddings" / "y.bin")
    write_embeddings(
        corpus / "lover" / "embeddings" / "y.bin",
        parsed.sections,
        parsed.scale,
        dim=parsed.dim,
        embedding_space_id=other_esi,
    )
    _git(corpus, "add", "-A")
    _git(corpus, "commit", "-m", "tampered v2")

    with pytest.raises(CorpusStateError, match="disagrees with the manifest"):
        migrate_lspe_v2(settings)


def test_header_only_sidecars_are_rewritten_and_counted(tmp_path: Path) -> None:
    records = _default_records()
    settings, corpus = _seed_corpus(tmp_path, records)
    write_embeddings(
        corpus / "lover" / "embeddings" / "x.bin",
        [],
        scale=1.0,
        dim=4,
    )
    _git(corpus, "add", "-A")
    _git(corpus, "commit", "-m", "header-only x")

    report = migrate_lspe_v2(settings)

    assert report.rewritten == 2
    assert report.header_only == 1
    rewritten = read_embeddings(corpus / "lover" / "embeddings" / "x.bin")
    assert rewritten.version == 2
    assert rewritten.embedding_space_id == _ESI
    assert rewritten.sections == []


def test_verification_failure_aborts_without_a_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a written file does not re-read as the exact original payload the
    cutover must stop before the commit — a partially rewritten worktree is
    discardable, a published bad rewrite is not."""
    settings, corpus = _seed_corpus(tmp_path, _default_records())
    original_verify = cutover_module._verify_rewrite

    def sabotage_then_verify(item: cutover_module._CutoverItem) -> None:
        raw = bytearray(item.path.read_bytes())
        raw[-1] ^= 0xFF
        item.path.write_bytes(bytes(raw))
        original_verify(item)

    monkeypatch.setattr(cutover_module, "_verify_rewrite", sabotage_then_verify)

    with pytest.raises(CorpusStateError, match="did not verify"):
        migrate_lspe_v2(settings)

    assert _git(corpus, "log", "--format=%s", "-1") == "seed"
