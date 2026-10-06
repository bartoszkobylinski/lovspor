"""The local dataset read from one corpus commit, for ``recorded_at`` (ADR-0016 5; #569)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lovspor.errors import AmbiguousSlugError, CorpusNotFoundError, LocalCorpusError
from lovspor.local_corpus import LocalDataset
from lovspor.snapshot import CorpusSnapshot, StateIntegrityError
from tests.unit.backfilled_corpus_fixtures import (
    LOCAL,
    backfilled_corpus,
    dated_git,
    local_address,
)


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return backfilled_corpus(tmp_path, monkeypatch)


def _commits(corpus: Path) -> list[str]:
    """Oldest first: the skeleton, then v1, v2, v3."""
    return dated_git(corpus, "2026-10-01T00:00:00Z", "rev-list", "--reverse", "HEAD").split()


def _state(corpus: Path, index: int) -> LocalDataset:
    snapshot = CorpusSnapshot(corpus, _commits(corpus)[index])
    return LocalDataset(corpus).at(snapshot, f"at commit {index}")


def _manifest_path(corpus: Path) -> Path:
    return corpus / LOCAL / "manifest.json"


def _observations_path(corpus: Path) -> Path:
    authority, slug = local_address(corpus).split("/")
    return corpus / LOCAL / authority / "observations" / f"{slug}.json"


class TestState:
    @pytest.mark.parametrize("version", [1, 2, 3])
    def test_each_commit_serves_the_version_it_recorded(self, corpus: Path, version: int) -> None:
        served = _state(corpus, version).document(local_address(corpus))

        assert served.record.version == version
        assert f"version: {version}\n" in served.markdown

    def test_the_working_tree_is_never_read(self, corpus: Path) -> None:
        shutil.rmtree(corpus / LOCAL)

        served = _state(corpus, 2).document(local_address_of_commit(corpus))

        assert served.record.version == 2

    def test_a_commit_before_the_dataset_has_no_local_document(self, corpus: Path) -> None:
        empty = {"documents": {}, "generated_at": None, "version": 1}
        assert (
            json.loads(
                dated_git(corpus, "x", "show", f"{_commits(corpus)[0]}:{LOCAL}/manifest.json")
            )
            == empty
        )

        with pytest.raises(CorpusNotFoundError, match="in the corpus state at commit 0") as caught:
            _state(corpus, 0).document(local_address(corpus))

        assert _commits(corpus)[0] in str(caught.value)
        assert not isinstance(caught.value, LocalCorpusError)

    def test_a_state_without_the_directory_is_an_empty_dataset(self, tmp_path: Path) -> None:
        dated_git(tmp_path, "2026-09-01T00:00:00Z", "init", "-q")
        (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
        dated_git(tmp_path, "2026-09-01T00:00:00Z", "add", "-A")
        dated_git(tmp_path, "2026-09-01T00:00:00Z", "commit", "-q", "-m", "central only")
        sha = dated_git(tmp_path, "x", "rev-parse", "HEAD").strip()

        state = LocalDataset(tmp_path).at(CorpusSnapshot(tmp_path, sha), "at 2026-09-01")

        assert state.manifest().documents == {}

    def test_a_path_outside_the_dataset_is_refused(self, corpus: Path) -> None:
        state = _state(corpus, 3)

        with pytest.raises(LocalCorpusError, match="outside lokale-forskrifter/"):
            state._read("manifest.json")
        with pytest.raises(LocalCorpusError, match="outside lokale-forskrifter/"):
            state._read(f"{LOCAL}/../manifest.json")

    def test_a_listed_file_missing_from_the_commit_is_damage(self, corpus: Path) -> None:
        with pytest.raises(StateIntegrityError, match="not in the commit's tree"):
            _state(corpus, 3)._read(f"{LOCAL}/0301/finnes-ikke.md")

    def test_an_ambiguous_address_stays_ambiguous(self, corpus: Path) -> None:
        manifest = json.loads(_manifest_path(corpus).read_text(encoding="utf-8"))
        [(doc_id, record)] = manifest["documents"].items()
        manifest["documents"][f"{doc_id}-twin"] = record
        _manifest_path(corpus).write_text(json.dumps(manifest), encoding="utf-8")
        dated_git(corpus, "2026-10-02T00:00:00Z", "commit", "-q", "-am", "twin")

        with pytest.raises(AmbiguousSlugError):
            _state(corpus, 4).document(local_address(corpus))


def local_address_of_commit(corpus: Path) -> str:
    """The address as the commits know it: read from git, the working tree is gone."""
    text = dated_git(corpus, "x", "show", f"HEAD:{LOCAL}/manifest.json")
    [record] = json.loads(text)["documents"].values()
    return f"{record['authority_id']}/{record['slug']}"
