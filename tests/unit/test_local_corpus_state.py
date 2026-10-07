"""The local dataset read from one corpus commit, and its observation files (ADR-0016 5; S7)."""

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


class TestObservations:
    def test_a_state_reads_its_own_observation_versions_without_the_working_tree(
        self, corpus: Path
    ) -> None:
        state = _state(corpus, 2)
        address = local_address(corpus)
        shutil.rmtree(corpus / LOCAL)

        served = state.document(address)
        observed = state.observations(served)

        assert [entry.version for entry in observed.versions] == [1, 2]
        assert observed.source_status.observed_at == "2026-08-22T15:17:23Z"
        assert served.observation.observed_at_last == observed.versions[-1].observed_at_last

    def test_observations_missing_from_a_commit_are_damage_even_if_present_on_disk(
        self, corpus: Path
    ) -> None:
        path = _observations_path(corpus)
        original = path.read_bytes()
        path.unlink()
        dated_git(corpus, "2026-10-02T00:00:00Z", "commit", "-q", "-am", "missing observations")
        path.write_bytes(original)

        with pytest.raises(StateIntegrityError, match="not in the commit's tree"):
            _state(corpus, 4).document(local_address(corpus))

    def test_the_file_reads_with_the_s6_model(self, corpus: Path) -> None:
        local = LocalDataset(corpus)
        served = local.document(local_address(corpus))

        observed = local.observations(served)

        assert [v.version for v in observed.versions] == [1, 2, 3]
        assert observed.doc_id == served.doc_id

    def test_a_file_of_another_document_is_refused(self, corpus: Path) -> None:
        path = _observations_path(corpus)
        payload = json.loads(path.read_text(encoding="utf-8"))
        local = LocalDataset(corpus)
        served = local.document(local_address(corpus))
        path.write_text(json.dumps({**payload, "doc_id": "lk-0301-000000000000"}), "utf-8")

        with pytest.raises(
            LocalCorpusError, match="holds the observations of lk-0301-000000000000"
        ):
            local.observations(served)

    def test_a_file_without_versions_is_refused(self, corpus: Path) -> None:
        path = _observations_path(corpus)
        payload = json.loads(path.read_text(encoding="utf-8"))
        local = LocalDataset(corpus)
        served = local.document(local_address(corpus))
        path.write_text(json.dumps({**payload, "versions": []}), "utf-8")

        with pytest.raises(LocalCorpusError, match="lists no promoted version"):
            local.observations(served)

    def test_a_file_off_its_contract_is_refused(self, corpus: Path) -> None:
        path = _observations_path(corpus)
        payload = json.loads(path.read_text(encoding="utf-8"))
        local = LocalDataset(corpus)
        served = local.document(local_address(corpus))
        path.write_text(json.dumps({**payload, "schema_version": 2}), "utf-8")

        with pytest.raises(LocalCorpusError, match="does not read as the observations file"):
            local.observations(served)


class TestVersionText:
    def test_the_current_version_is_the_file_itself(self, corpus: Path) -> None:
        local = LocalDataset(corpus)
        served = local.document(local_address(corpus))

        text = local.version_text(served, 3, served.record.content_hash)

        assert text == served.markdown

    def test_a_number_with_another_hash_is_not_that_version(self, corpus: Path) -> None:
        local = LocalDataset(corpus)
        served = local.document(local_address(corpus))

        with pytest.raises(LocalCorpusError, match="v2 of .* is not in this checkout's history"):
            local.version_text(served, 2, served.record.content_hash)

    def test_a_checkout_without_history_cannot_read_an_old_version(self, corpus: Path) -> None:
        shutil.rmtree(corpus / ".git")
        local = LocalDataset(corpus)
        served = local.document(local_address(corpus))

        with pytest.raises(LocalCorpusError, match="has no history to read v1 from"):
            local.version_text(served, 1, served.record.content_hash)
