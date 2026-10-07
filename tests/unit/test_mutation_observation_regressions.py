"""Observable local history contracts, exercised through real files and git trees."""

import json
import time
from datetime import timedelta
from pathlib import Path

import pytest

from lovspor.errors import CorpusNotFoundError, LocalCorpusError, LocalScopeError, ObservedAtError
from lovspor.local_corpus import LocalDataset
from lovspor.mcp import CorpusReader
from lovspor.mcp_local import ServedCorpus
from lovspor.observation_history import observation_history, parse_observed_at
from lovspor.snapshot import CorpusSnapshot
from tests.unit.backfilled_corpus_fixtures import backfilled_corpus, dated_git, local_address
from tests.unit.local_dataset_fixtures import (
    LOCAL_ID,
    LOCAL_SLUG,
    add_local_dataset,
    build_central,
)

ADDRESS = f"9999/{LOCAL_SLUG}"
RELATIVE = f"lokale-forskrifter/9999/{LOCAL_SLUG}.md"
OBSERVATIONS = f"lokale-forskrifter/9999/observations/{LOCAL_SLUG}.json"


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    build_central(tmp_path)
    add_local_dataset(tmp_path)
    dated_git(tmp_path, "2026-09-01T12:00:00Z", "init", "-q")
    commit(tmp_path)
    return tmp_path


def commit(corpus: Path) -> str:
    dated_git(corpus, "2026-09-01T12:00:00Z", "add", "-A")
    dated_git(corpus, "2026-09-01T12:00:00Z", "commit", "-q", "-m", "fixture")
    return dated_git(corpus, "x", "rev-parse", "HEAD").strip()


def state(corpus: Path) -> LocalDataset:
    sha = dated_git(corpus, "x", "rev-parse", "HEAD").strip()
    return LocalDataset(corpus).at(CorpusSnapshot(corpus, sha), "at fixture")


def test_snapshot_can_read_an_older_version_from_git(corpus: Path) -> None:
    original = (corpus / RELATIVE).read_text("utf-8")
    manifest_path = corpus / "lokale-forskrifter/manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["documents"][LOCAL_ID]["version"] = 2
    manifest_path.write_text(json.dumps(manifest), "utf-8")
    (corpus / RELATIVE).write_text(original.replace("version: 1", "version: 2"), "utf-8")
    path = corpus / OBSERVATIONS
    observations = json.loads(path.read_text("utf-8"))
    observations["versions"][0]["version"] = 2
    path.write_text(json.dumps(observations), "utf-8")
    commit(corpus)
    local = state(corpus)
    served = local.document(ADDRESS)
    assert local.version_text(served, 1, "c" * 64) == original


def test_unreadable_historical_front_matter_names_the_revision_path(corpus: Path) -> None:
    local = LocalDataset(corpus)
    served = local.document(ADDRESS)
    (corpus / RELATIVE).write_text("# torn historical file\n", "utf-8")
    commit(corpus)
    with pytest.raises(LocalCorpusError) as caught:
        local.version_text(served, 2, "e" * 64)
    assert str(caught.value).startswith(f"{RELATIVE} has no readable front matter: ")


def test_missing_version_explains_shallow_history(corpus: Path) -> None:
    local = LocalDataset(corpus)
    served = local.document(ADDRESS)
    with pytest.raises(LocalCorpusError) as caught:
        local.version_text(served, 2, "e" * 64)
    assert str(caught.value) == (
        f"v2 of {LOCAL_ID} is not in this checkout's history of {RELATIVE}; "
        "a shallow clone cannot reach it"
    )


def test_version_disagreement_names_file_and_both_versions(corpus: Path) -> None:
    path = corpus / RELATIVE
    path.write_text(path.read_text("utf-8").replace("version: 1", "version: 2"), "utf-8")
    with pytest.raises(LocalCorpusError) as caught:
        LocalDataset(corpus).document(ADDRESS)
    assert str(caught.value) == f"{path.resolve()} is v2; the manifest records v1"


def test_torn_observations_names_the_file(corpus: Path) -> None:
    path = corpus / OBSERVATIONS
    path.write_text("{", "utf-8")
    with pytest.raises(LocalCorpusError) as caught:
        LocalDataset(corpus).document(ADDRESS)
    assert str(caught.value).startswith(
        f"{path.resolve()} does not read as the observations file: "
    )


def test_torn_snapshot_manifest_names_the_file(corpus: Path) -> None:
    (corpus / "lokale-forskrifter/manifest.json").write_text("{", "utf-8")
    commit(corpus)
    with pytest.raises(LocalCorpusError) as caught:
        state(corpus).manifest()
    assert str(caught.value).startswith(
        "lokale-forskrifter/manifest.json does not read as the local manifest: "
    )


def test_missing_snapshot_document_explains_current_read(corpus: Path) -> None:
    local = state(corpus)
    with pytest.raises(CorpusNotFoundError) as caught:
        local.document("9999/missing")
    sha = dated_git(corpus, "x", "rev-parse", "HEAD").strip()
    assert str(caught.value) == (
        "no current local regulation '9999/missing' in the corpus state at fixture "
        f"(corpus_commit {sha}); it may have been promoted later — "
        "omit recorded_at to read the current corpus"
    )


def test_history_defaults_to_no_text_and_returns_json_arrays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = backfilled_corpus(tmp_path, monkeypatch)
    served = ServedCorpus(CorpusReader(corpus), warm=False)
    history = served.get_observation_history(local_address(corpus))
    assert history["at"] is None
    assert isinstance(history["versions"], list)
    assert isinstance(history["versions"][0]["corroborating_urls"], list)
    assert json.loads(json.dumps(history)) == history


def test_historical_section_resolves_central_cross_reference(corpus: Path) -> None:
    path = corpus / RELATIVE
    path.write_text(
        path.read_text("utf-8") + "\n## § 3 Hjemmel\n\nSe ordensloven § 2 og ordensloven § 9.\n",
        "utf-8",
    )
    commit(corpus)
    section = ServedCorpus(CorpusReader(corpus), warm=False).get_section(
        ADDRESS, "3", None, "2026-09-02"
    )
    assert [
        (r["target_slug"], r["target_section_id"], r["valid"]) for r in section["cross_references"]
    ] == [("ordensloven", "2", True), ("ordensloven", "9", False)]


@pytest.mark.parametrize("value", ["2026-09-01", "nonsense"])
def test_invalid_instant_has_precise_guidance(value: str) -> None:
    with pytest.raises(ObservedAtError) as caught:
        parse_observed_at(value)
    why = (
        "; a calendar date would hide which side of a version change it means"
        if value == "2026-09-01"
        else ""
    )
    assert str(caught.value) == (
        "observed_at must be an instant with an offset, e.g. 2026-09-01T12:00:00Z, "
        f"got {value!r}{why}"
    )


def test_parsed_instant_is_utc_even_in_a_non_utc_process(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as environment:
        environment.setenv("TZ", "EST5EDT")
        time.tzset()
        try:
            parsed = parse_observed_at("2026-09-01T14:00:00+02:00")
            assert parsed.utcoffset() == timedelta(0)
            assert parsed.isoformat() == "2026-09-01T12:00:00+00:00"
        finally:
            environment.undo()
            time.tzset()


def test_include_text_requires_an_instant(corpus: Path) -> None:
    with pytest.raises(ObservedAtError) as caught:
        observation_history(LocalDataset(corpus), ADDRESS, None, True)
    assert (
        str(caught.value)
        == "include_text needs observed_at: the text is that of the version observed then"
    )


def test_central_history_request_explains_local_addresses(corpus: Path) -> None:
    with pytest.raises(LocalScopeError) as caught:
        observation_history(LocalDataset(corpus), "proveloven", None, False)
    assert str(caught.value) == (
        "'proveloven' is not a local regulation; observation history exists only for "
        "<authority_id>/<slug> or lf-/lk- addresses. A central law's history is "
        "get_law_history"
    )
