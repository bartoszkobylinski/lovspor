"""Reading one artifact out of the archive for promotion, and the corpus boundary (ADR-0016 S3)."""

from __future__ import annotations

import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from lovspor.errors import PromotionRefusedError
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import Tombstone
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT, ObservatoryRoot
from lovspor.promotion.corpus import CorpusCheckout
from tests.unit.promotion_cli_fixtures import (
    FIRST_SEEN,
    PAGE_URL,
    Decision,
    approve,
    invoke,
    make_corpus,
    promote,
    register,
    store,
)
from tests.unit.promotion_fixtures import html_page

COPY_URL = "https://eksempel.kommune.invalid/kopi/renovasjon"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    observatory = tmp_path / "observatory"
    observatory.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    register(observatory)
    return observatory


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    return make_corpus(tmp_path)


def test_bytes_served_at_two_urls_need_the_url_and_the_other_corroborates(
    root: Path, corpus: Path, tmp_path: Path
) -> None:
    sha256 = store(root, html_page())
    store(root, html_page(), url=COPY_URL, observed_at=FIRST_SEEN + timedelta(hours=1))

    by_hash = promote("preview", sha256, corpus)
    approve(PAGE_URL, Decision().write(tmp_path))
    promoted = promote("local", PAGE_URL, corpus)

    assert by_hash.exit_code == 1
    assert "names 2 artifact(s)" in by_hash.output
    assert promoted.exit_code == 0, promoted.output
    [path] = (corpus / "lokale-forskrifter" / "0301" / "observations").glob("*.json")
    version = json.loads(path.read_text("utf-8"))["versions"][0]
    assert version["primary_url"] == PAGE_URL
    assert version["corroborating_urls"] == [COPY_URL]


def test_a_tombstoned_blob_is_refused(root: Path, corpus: Path) -> None:
    sha256 = store(root, html_page())
    log = ObservationLog(ObservatoryRoot(root, []))
    log.blob_path(sha256).unlink()
    log.append(Tombstone(sha256=sha256, removed_at=FIRST_SEEN, basis="privacy", authorised_by="o"))

    result = promote("preview", sha256, corpus)

    assert result.exit_code == 1
    assert "tombstoned" in result.output


def test_a_missing_blob_is_refused(root: Path, corpus: Path) -> None:
    sha256 = store(root, html_page())
    ObservationLog(ObservatoryRoot(root, [])).blob_path(sha256).unlink()

    result = promote("preview", sha256, corpus)

    assert result.exit_code == 1
    assert "not on disk" in result.output


def test_a_blob_that_no_longer_hashes_to_its_name_is_refused(root: Path, corpus: Path) -> None:
    sha256 = store(root, html_page())
    ObservationLog(ObservatoryRoot(root, [])).blob_path(sha256).write_bytes(b"altered")

    result = promote("preview", sha256, corpus)

    assert result.exit_code == 1
    assert "no longer hashes" in result.output


def test_a_damaged_log_is_refused(root: Path, corpus: Path) -> None:
    sha256 = store(root, html_page())
    with (root / "observations.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("not json\n")

    result = promote("preview", sha256, corpus)

    assert result.exit_code == 1
    assert "damaged" in result.output


def test_an_unregistered_authority_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observatory = tmp_path / "observatory"
    observatory.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    sha256 = store(observatory, html_page())

    result = promote("preview", sha256, make_corpus(tmp_path))

    assert result.exit_code == 1
    assert "source register" in result.output


def test_a_blank_klass_version_is_refused(root: Path, corpus: Path) -> None:
    sha256 = store(root, html_page())
    args = ("promote", "preview", "--authority", "0301", "--artifact", sha256)

    result = invoke(*args, "--corpus", str(corpus), "--klass-version", "")

    assert result.exit_code == 1
    assert "authority block" in result.output


def test_the_corpus_refuses_a_path_outside_the_local_dataset(corpus: Path) -> None:
    checkout = CorpusCheckout(corpus, [])

    with pytest.raises(PromotionRefusedError, match="outside"):
        checkout.inside("../manifest.json")


def test_a_directory_that_is_not_a_git_checkout_is_refused(corpus: Path) -> None:
    shutil.rmtree(corpus / ".git")

    with pytest.raises(PromotionRefusedError, match="no .git"):
        CorpusCheckout(corpus, [])


def test_central_ref_ids_are_read_from_sf_records(corpus: Path) -> None:
    assert CorpusCheckout(corpus, []).central_ref_ids() == frozenset({"forskrift/2020-01-14-63"})
