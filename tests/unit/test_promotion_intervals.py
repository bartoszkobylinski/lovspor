"""Promoted versions' observation intervals, re-read from the log (ADR-0016 2, 3; S6).

The observations file under test is the one ``lovspor promote local`` wrote
(slice S3), reached through the supported commands over a synthetic archive.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from lovspor.errors import PromotionRefusedError
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import Tombstone
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT, ObservatoryRoot
from lovspor.promotion.archive import authority_fetches
from lovspor.promotion.intervals import with_intervals
from lovspor.promotion.versions import PrimaryHistory, read_primary
from lovspor.promotion.writer import ObservationsFile, observations_text
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    FIRST_SEEN,
    PAGE_URL,
    Decision,
    approve,
    make_corpus,
    promote,
    register,
    store,
    store_failure,
)
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page

CHANGED = (*REGULATION_LINES[:-1], "Forskriften trer i kraft 1. februar 2020.")
COPY_URL = "https://eksempel.kommune.invalid/kopi/renovasjon"
DAY = timedelta(days=1)


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


def _promoted(root: Path, corpus: Path, tmp_path: Path, sha256: str) -> Path:
    """Approve and promote ``sha256`` through S3; the observations file it wrote."""
    assert approve(sha256, Decision().write(tmp_path)).exit_code == 0
    result = promote("local", sha256, corpus)
    assert result.exit_code == 0, result.output
    [path] = (corpus / "lokale-forskrifter" / AUTHORITY / "observations").glob("*.json")
    return path


def _file(path: Path) -> ObservationsFile:
    return ObservationsFile.model_validate_json(path.read_bytes())


def _history(root: Path) -> PrimaryHistory:
    log = ObservationLog(ObservatoryRoot(root, []))
    return read_primary(log, authority_fetches(log, AUTHORITY), PAGE_URL, None)


class TestRefreshedIntervals:
    def test_refresh_rederives_intervals_instead_of_accumulating_stale_facts(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        path = _promoted(root, corpus, tmp_path, store(root, html_page()))
        original = _file(path)
        store(root, html_page(), observed_at=FIRST_SEEN + DAY)
        store(root, html_page(), url=COPY_URL, observed_at=FIRST_SEEN + 2 * DAY)
        gone = store(root, html_page(CHANGED), observed_at=FIRST_SEEN + 3 * DAY)
        ObservationLog(ObservatoryRoot(root, [])).append(
            Tombstone(
                sha256=gone,
                removed_at=FIRST_SEEN + 5 * DAY,
                basis="privacy",
                authorised_by="owner",
            )
        )
        store_failure(root, FIRST_SEEN + 4 * DAY)
        latest = with_intervals(original, _history(root))
        assert latest.versions[0].observation_count == 2
        assert latest.versions[0].corroborating_urls == (COPY_URL,)
        assert len(latest.excluded) == 1
        assert latest.source_status.outcome == "http_error"
        log = ObservationLog(ObservatoryRoot(root, []))
        earlier = read_primary(log, authority_fetches(log, AUTHORITY), PAGE_URL, FIRST_SEEN)

        refreshed = with_intervals(latest, earlier)

        assert refreshed == original
        assert observations_text(refreshed) == path.read_text(encoding="utf-8")

    def test_refresh_is_idempotent_and_does_not_mutate_the_committed_file(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        path = _promoted(root, corpus, tmp_path, store(root, html_page()))
        original_bytes = path.read_bytes()
        existing = _file(path)
        before = existing.model_dump(mode="json")
        store(root, html_page(), observed_at=FIRST_SEEN + DAY)
        history = _history(root)

        refreshed = with_intervals(existing, history)

        assert refreshed.versions[0].observation_count == 2
        assert with_intervals(refreshed, history) == refreshed
        assert existing.model_dump(mode="json") == before
        assert path.read_bytes() == original_bytes

    def test_later_observations_of_the_same_text_extend_the_interval(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = store(root, html_page())
        path = _promoted(root, corpus, tmp_path, sha256)
        later = store(
            root, html_page(updated="Sist oppdatert 09.09.2026"), observed_at=FIRST_SEEN + 9 * DAY
        )
        store(root, html_page(), url=COPY_URL, observed_at=FIRST_SEEN + 10 * DAY)
        store_failure(root, FIRST_SEEN + 11 * DAY)

        [entry] = with_intervals(_file(path), _history(root)).versions
        refreshed = with_intervals(_file(path), _history(root))

        assert entry.observed_at_first == "2026-08-19T15:17:23Z"
        assert entry.observed_at_last == "2026-08-28T15:17:23Z"
        assert entry.observation_count == 2
        assert entry.source_sha256s == (sha256, later)
        assert entry.corroborating_urls == (COPY_URL,)
        assert refreshed.source_status.outcome == "http_error"

    def test_the_promotion_audit_is_kept_as_committed(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        path = _promoted(root, corpus, tmp_path, store(root, html_page()))
        store(root, html_page(), observed_at=FIRST_SEEN + 3 * DAY)

        [before] = _file(path).versions
        [after] = with_intervals(_file(path), _history(root)).versions

        assert after.promotion == before.promotion
        assert after.content_hash == before.content_hash

    def test_a_later_text_ends_the_promoted_version_where_it_began(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        path = _promoted(root, corpus, tmp_path, store(root, html_page()))
        store(root, html_page(CHANGED), observed_at=FIRST_SEEN + 2 * DAY)
        store(root, html_page(), observed_at=FIRST_SEEN + 4 * DAY)

        [entry] = with_intervals(_file(path), _history(root)).versions

        assert entry.observed_at_last == "2026-08-19T15:17:23Z"
        assert entry.observation_count == 1

    def test_the_text_is_deterministic_and_sorted(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        path = _promoted(root, corpus, tmp_path, store(root, html_page()))
        store(root, html_page(), observed_at=FIRST_SEEN + 3 * DAY)

        first = observations_text(with_intervals(_file(path), _history(root)))
        second = observations_text(with_intervals(_file(path), _history(root)))

        assert first == second
        assert (
            first
            == json.dumps(json.loads(first), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
        )


class TestExcludedObservations:
    def test_a_tombstoned_blob_is_listed_in_the_file(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        path = _promoted(root, corpus, tmp_path, store(root, html_page()))
        gone = store(root, html_page(CHANGED), observed_at=FIRST_SEEN + DAY)
        store(root, html_page(), observed_at=FIRST_SEEN + 2 * DAY)
        removal = Tombstone(
            sha256=gone, removed_at=FIRST_SEEN + 5 * DAY, basis="privacy", authorised_by="owner"
        )
        ObservationLog(ObservatoryRoot(root, [])).append(removal)

        text = observations_text(with_intervals(_file(path), _history(root)))

        document = ObservationsFile.model_validate_json(text)
        assert document == with_intervals(_file(path), _history(root))
        assert observations_text(document) == text
        [excluded] = json.loads(text)["excluded"]
        assert excluded == {
            "observed_at": "2026-08-20T15:17:23Z",
            "reason": "tombstoned",
            "sha256": gone,
            "url": PAGE_URL,
        }
        assert json.loads(text)["versions"][0]["observed_at_last"] == "2026-08-21T15:17:23Z"

    def test_a_file_with_nothing_excluded_has_no_excluded_key(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        path = _promoted(root, corpus, tmp_path, store(root, html_page()))

        text = observations_text(with_intervals(_file(path), _history(root)))

        assert "excluded" not in json.loads(text)
        assert text == path.read_text(encoding="utf-8")


class TestDivergenceIsRefused:
    def test_a_promoted_version_that_is_not_the_logs_first_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        store(root, html_page(CHANGED))
        sha256 = store(root, html_page(), observed_at=FIRST_SEEN + DAY)
        path = _promoted(root, corpus, tmp_path, sha256)

        with pytest.raises(PromotionRefusedError, match="another text") as refused:
            with_intervals(_file(path), _history(root))
        assert str(refused.value) == (
            "promoted version 1 does not match the log: "
            "the log's version of that number has another text"
        )

    def test_an_earlier_capture_of_the_same_text_moves_the_first_and_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        store(root, html_page(updated="Sist oppdatert 01.08.2026"))
        sha256 = store(root, html_page(), observed_at=FIRST_SEEN + DAY)
        path = _promoted(root, corpus, tmp_path, sha256)

        with pytest.raises(PromotionRefusedError, match="first observed at"):
            with_intervals(_file(path), _history(root))

    def test_another_extractor_is_a_migration_not_a_refresh(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        path = _promoted(root, corpus, tmp_path, store(root, html_page()))
        existing = _file(path)
        [entry] = existing.versions
        audit = entry.promotion.model_copy(update={"extractor_version": 0})
        stale = existing.model_copy(
            update={"versions": (entry.model_copy(update={"promotion": audit}),)}
        )

        with pytest.raises(PromotionRefusedError, match="migration"):
            with_intervals(stale, _history(root))

    def test_a_version_the_log_does_not_have_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        path = _promoted(root, corpus, tmp_path, store(root, html_page()))
        existing = _file(path)
        [entry] = existing.versions
        extra = existing.model_copy(
            update={"versions": (entry, entry.model_copy(update={"version": 2}))}
        )

        with pytest.raises(PromotionRefusedError, match="no such version") as refused:
            with_intervals(extra, _history(root))
        assert str(refused.value) == (
            "promoted version 2 does not match the log: the log has no such version at this URL"
        )

    def test_a_version_at_another_url_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        path = _promoted(root, corpus, tmp_path, store(root, html_page()))
        existing = _file(path)
        [entry] = existing.versions
        moved = existing.model_copy(
            update={"versions": (entry.model_copy(update={"primary_url": COPY_URL}),)}
        )

        with pytest.raises(PromotionRefusedError, match="primary URL"):
            with_intervals(moved, _history(root))
