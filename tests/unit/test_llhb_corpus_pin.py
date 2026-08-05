"""Corpus-pin primitives: real git repo in tmp_path, fail-closed verification."""

import subprocess
from pathlib import Path

import pytest

from lovspor.llhb.corpus_pin import (
    CorpusPin,
    CorpusPinError,
    current_pin,
    document_pin,
    git_head_sha,
    verify_pin,
    working_tree_clean,
)
from tests.unit.llhb_fixtures import GENERATED_AT, record_for, standard_corpus


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def corpus_repo(tmp_path: Path) -> Path:
    standard_corpus(tmp_path)
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "add", "-A")
    _git(
        tmp_path,
        "-c",
        "user.name=llhb-test",
        "-c",
        "user.email=llhb@test.invalid",
        "commit",
        "--quiet",
        "-m",
        "seed",
    )
    return tmp_path


def test_head_sha_is_full_and_matches_git(corpus_repo: Path) -> None:
    sha = git_head_sha(corpus_repo)
    assert sha == _git(corpus_repo, "rev-parse", "HEAD")
    assert len(sha) == 40


def test_current_pin_reads_manifest_generated_at(corpus_repo: Path) -> None:
    pin = current_pin(corpus_repo)
    assert pin.lovverk_commit == git_head_sha(corpus_repo)
    assert pin.manifest_generated_at == GENERATED_AT


def test_verify_pin_accepts_clean_matching_checkout(corpus_repo: Path) -> None:
    verify_pin(corpus_repo, current_pin(corpus_repo))


def test_verify_pin_rejects_wrong_commit(corpus_repo: Path) -> None:
    pin = CorpusPin(lovverk_commit="f" * 40, manifest_generated_at=GENERATED_AT)
    with pytest.raises(CorpusPinError, match="does not match pinned commit"):
        verify_pin(corpus_repo, pin)


def test_verify_pin_rejects_wrong_manifest_timestamp(corpus_repo: Path) -> None:
    """Codex PR #16 finding 2: verify_pin ignored manifest_generated_at, so a
    lock with the right SHA and a fake timestamp verified. It must not."""
    good = current_pin(corpus_repo)
    forged = CorpusPin(
        lovverk_commit=good.lovverk_commit,
        manifest_generated_at=good.manifest_generated_at.replace(year=2000),
    )
    with pytest.raises(CorpusPinError, match="generated_at"):
        verify_pin(corpus_repo, forged)


def test_verify_pin_rejects_dirty_tree(corpus_repo: Path) -> None:
    pin = current_pin(corpus_repo)
    (corpus_repo / "lover" / "testloven.md").write_text("tampered", encoding="utf-8")
    assert working_tree_clean(corpus_repo) is False
    with pytest.raises(CorpusPinError, match="dirty"):
        verify_pin(corpus_repo, pin)


def test_pin_model_requires_full_lowercase_sha() -> None:
    with pytest.raises(ValueError, match="40-char"):
        CorpusPin(lovverk_commit="abc123", manifest_generated_at=GENERATED_AT)
    with pytest.raises(ValueError, match="40-char"):
        CorpusPin(lovverk_commit="F" * 40, manifest_generated_at=GENERATED_AT)


def test_git_failure_raises_typed_error(tmp_path: Path) -> None:
    with pytest.raises(CorpusPinError, match="rev-parse"):
        git_head_sha(tmp_path / "not-a-repo")


def test_document_pin_carries_freeze_fields() -> None:
    record = record_for("testloven", "Lov om testing (testloven)")
    assert document_pin(record) == {
        "xml_hash": "a" * 64,
        "renderer_version": 8,
        "embedding_space_id": "test-space",
        "embedding_hash": "a" * 64,
    }
