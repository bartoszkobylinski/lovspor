"""Versions of one document from its primary URL's observations (ADR-0016 1d, 2; S6).

The archive is real on disk under ``tmp_path`` (``promotion_cli_fixtures``):
every capture is appended by the observatory's own ``append_artifact``, every
tombstone by its own ``append``, and read back through the corrected view.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from lovspor.errors import PromotionRefusedError
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import Tombstone
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT, ObservatoryRoot
from lovspor.promotion.archive import authority_fetches
from lovspor.promotion.identity import content_hash
from lovspor.promotion.versions import (
    ExclusionReason,
    PrimaryHistory,
    Sighting,
    derive_versions,
    read_primary,
)
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    FIRST_SEEN,
    PAGE_URL,
    register,
    store,
    store_failure,
)
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page

CHANGED = (*REGULATION_LINES[:-1], "Forskriften trer i kraft 1. februar 2020.")
COPY_URL = "https://eksempel.kommune.invalid/kopi/renovasjon"
DAY = timedelta(days=1)
EMPTY_PAGE = b"<!doctype html><html><body><main><p>Laster ...</p></main></body></html>"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    observatory = tmp_path / "observatory"
    observatory.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    register(observatory)
    return observatory


def _log(root: Path) -> ObservationLog:
    return ObservationLog(ObservatoryRoot(root, []))


def _history(root: Path, through_days: float | None = None) -> PrimaryHistory:
    log = _log(root)
    through = None if through_days is None else FIRST_SEEN + through_days * DAY
    return read_primary(log, authority_fetches(log, AUTHORITY), PAGE_URL, through)


def _tombstone(root: Path, sha256: str) -> None:
    removal = Tombstone(
        sha256=sha256, removed_at=FIRST_SEEN + 30 * DAY, basis="privacy", authorised_by="owner"
    )
    _log(root).append(removal)


def _hash(lines: tuple[str, ...]) -> str:
    return content_hash("\n".join(lines))


class TestVersionsAreRunsOfOneContent:
    def test_a_then_b_then_a_is_three_versions_not_a_return_to_the_first(self, root: Path) -> None:
        store(root, html_page(), observed_at=FIRST_SEEN)
        store(root, html_page(), observed_at=FIRST_SEEN + DAY)
        store(root, html_page(CHANGED), observed_at=FIRST_SEEN + 2 * DAY)
        store(root, html_page(), observed_at=FIRST_SEEN + 3 * DAY)

        versions = _history(root).versions

        assert [v.version for v in versions] == [1, 2, 3]
        first, second, third = versions
        assert first.content_hash == third.content_hash != second.content_hash
        assert (first.observed_at_first, first.observed_at_last) == (FIRST_SEEN, FIRST_SEEN + DAY)
        assert second.observed_at_first == second.observed_at_last == FIRST_SEEN + 2 * DAY
        assert third.observed_at_first == FIRST_SEEN + 3 * DAY
        assert [len(v.sightings) for v in versions] == [2, 1, 1]

    def test_the_intervals_are_the_logs_instants(self, root: Path) -> None:
        instants = [FIRST_SEEN + n * DAY for n in (0, 4, 9)]
        for instant in instants:
            store(root, html_page(), observed_at=instant)

        [version] = _history(root).versions

        assert [o.observed_at for o in version.observations] == [
            i.isoformat().replace("+00:00", "Z") for i in instants
        ]
        assert {o.url for o in version.observations} == {PAGE_URL}

    def test_bytes_that_change_without_the_text_changing_are_one_version(self, root: Path) -> None:
        first = store(root, html_page(updated="Sist oppdatert 01.09.2026"))
        second = store(
            root, html_page(updated="Sist oppdatert 02.09.2026"), observed_at=FIRST_SEEN + DAY
        )

        [version] = _history(root).versions

        assert version.source_sha256s == (first, second)

    def test_a_failed_fetch_neither_ends_nor_starts_a_version(self, root: Path) -> None:
        store(root, html_page())
        store_failure(root, FIRST_SEEN + DAY)
        store(root, html_page(), observed_at=FIRST_SEEN + 2 * DAY)
        store_failure(root, FIRST_SEEN + 3 * DAY)

        history = _history(root)

        assert len(history.versions) == 1
        assert history.versions[0].observed_at_last == FIRST_SEEN + 2 * DAY
        assert history.source_status.outcome == "http_error"
        assert history.source_status.http_status == 404

    def test_a_held_text_is_a_version_candidate_of_its_own_carrying_the_hold(
        self, root: Path
    ) -> None:
        store(root, html_page())
        store(root, EMPTY_PAGE, observed_at=FIRST_SEEN + DAY)
        store(root, html_page(), observed_at=FIRST_SEEN + 2 * DAY)

        versions = _history(root).versions

        assert [v.content_hash is None for v in versions] == [False, True, False]
        held = versions[1].held
        assert held is not None
        assert held.held_reason == "empty_text"
        assert versions[0].held is None

    def test_the_text_hash_is_the_extractors(self, root: Path) -> None:
        store(root, html_page())

        [version] = _history(root).versions

        assert version.content_hash == _hash(REGULATION_LINES)


class TestTombstonedBlobs:
    def test_altered_blob_refuses_the_read(self, root: Path) -> None:
        sha256 = store(root, html_page())
        _log(root).blob_path(sha256).write_bytes(html_page(CHANGED))

        with pytest.raises(PromotionRefusedError, match="no longer hashes to its name"):
            _history(root)

    def test_only_tombstoned_captures_leave_no_versions(self, root: Path) -> None:
        sha256 = store(root, html_page())
        store(root, html_page(), observed_at=FIRST_SEEN + DAY)
        _tombstone(root, sha256)
        _log(root).blob_path(sha256).unlink()

        history = _history(root)

        assert history.versions == ()
        assert [e.sha256 for e in history.excluded] == [sha256, sha256]
        assert [e.observed_at for e in history.excluded] == [
            "2026-08-19T15:17:23Z",
            "2026-08-20T15:17:23Z",
        ]
        assert history.source_status.outcome == "retrieved"

    def test_are_excluded_from_the_comparison_and_listed(self, root: Path) -> None:
        store(root, html_page())
        gone = store(root, html_page(CHANGED), observed_at=FIRST_SEEN + DAY)
        store(root, html_page(), observed_at=FIRST_SEEN + 2 * DAY)
        _tombstone(root, gone)

        history = _history(root)

        assert len(history.versions) == 1
        assert gone not in history.versions[0].source_sha256s
        [excluded] = history.excluded
        assert excluded.sha256 == gone
        assert excluded.reason is ExclusionReason.TOMBSTONED
        assert excluded.observed_at == "2026-08-20T15:17:23Z"
        assert excluded.url == PAGE_URL

    def test_a_missing_blob_without_a_tombstone_refuses_the_read(self, root: Path) -> None:
        sha256 = store(root, html_page())
        _log(root).blob_path(sha256).unlink()

        with pytest.raises(PromotionRefusedError, match="not on disk"):
            _history(root)


class TestTheCutOff:
    def test_exact_cutoff_includes_capture_but_not_later_failure_or_copy(self, root: Path) -> None:
        sha256 = store(root, html_page())
        store(root, html_page(), observed_at=FIRST_SEEN + DAY)
        store(root, html_page(), url=COPY_URL, observed_at=FIRST_SEEN + 2 * DAY)
        store_failure(root, FIRST_SEEN + 3 * DAY)
        # A damaged future capture must not be read in this snapshot.
        future = store(root, html_page(CHANGED), observed_at=FIRST_SEEN + 4 * DAY)
        _log(root).blob_path(future).unlink()

        history = _history(root, through_days=1)

        [version] = history.versions
        assert version.observed_at_last == FIRST_SEEN + DAY
        assert len(version.sightings) == 2
        assert version.source_sha256s == (sha256,)
        assert version.corroborating_urls == ()
        assert history.source_status.outcome == "retrieved"
        assert history.source_status.observed_at == "2026-08-20T15:17:23Z"

    def test_failure_only_history_has_status_but_no_version(self, root: Path) -> None:
        store_failure(root, FIRST_SEEN)

        history = _history(root)

        assert history.versions == ()
        assert history.excluded == ()
        assert history.source_status.outcome == "http_error"
        assert history.source_status.http_status == 404
        with pytest.raises(PromotionRefusedError, match="no observation"):
            _history(root, through_days=-1)

    def test_observations_after_it_are_not_read(self, root: Path) -> None:
        store(root, html_page())
        store(root, html_page(CHANGED), observed_at=FIRST_SEEN + 2 * DAY)

        assert len(_history(root, through_days=1).versions) == 1
        assert len(_history(root).versions) == 2

    def test_a_version_seen_through_a_cut_off_keeps_only_what_was_observed_by_then(
        self, root: Path
    ) -> None:
        store(root, html_page())
        store(root, html_page(), observed_at=FIRST_SEEN + 2 * DAY)
        [version] = _history(root).versions

        cut = version.through(FIRST_SEEN + DAY)
        before = version.through(FIRST_SEEN - DAY)

        assert cut is not None
        assert cut.observed_at_last == FIRST_SEEN
        assert before is None

    def test_a_url_never_observed_is_refused(self, root: Path) -> None:
        store(root, html_page(), url=COPY_URL)

        with pytest.raises(PromotionRefusedError, match="no observation"):
            _history(root)


class TestCorroboratingUrls:
    def test_same_text_in_different_bytes_does_not_corroborate(self, root: Path) -> None:
        primary = store(root, html_page())
        copy = store(root, html_page(updated="Sist oppdatert 02.09.2026"), url=COPY_URL)
        assert primary != copy

        [version] = _history(root).versions

        assert version.corroborating_urls == ()
        assert len(version.sightings) == 1

    def test_another_url_serving_the_very_bytes_corroborates(self, root: Path) -> None:
        store(root, html_page())
        store(root, html_page(), url=COPY_URL, observed_at=FIRST_SEEN + DAY)
        store(root, html_page(CHANGED), observed_at=FIRST_SEEN + 2 * DAY)

        first, second = _history(root).versions

        assert first.corroborating_urls == (COPY_URL,)
        assert second.corroborating_urls == ()


class TestDeriveVersionsIsPure:
    def test_empty_input_has_no_versions(self) -> None:
        assert derive_versions(PAGE_URL, iter(())) == ()

    def test_repeated_held_bytes_are_one_run(self) -> None:
        sightings = tuple(
            Sighting(
                observed_at=FIRST_SEEN + n * DAY,
                sha256="a" * 64,
                content_hash=None,
                held_reason="empty_text",
                held_detail="No regulation text",
            )
            for n in (0, 1)
        )

        [version] = derive_versions(PAGE_URL, sightings)

        assert version.sightings == sightings
        assert version.held == sightings[0]
        assert version.source_sha256s == ("a" * 64,)
        assert version.through(FIRST_SEEN + DAY) == version

    def test_input_order_does_not_matter(self) -> None:
        sightings = [
            Sighting(observed_at=FIRST_SEEN + n * DAY, sha256=f"{n}" * 64, content_hash=h)
            for n, h in ((0, "a" * 64), (1, "b" * 64), (2, "b" * 64), (3, "a" * 64))
        ]

        forwards = derive_versions(PAGE_URL, sightings)
        backwards = derive_versions(PAGE_URL, reversed(sightings))

        assert forwards == backwards
        assert [len(v.sightings) for v in forwards] == [1, 2, 1]

    def test_two_held_blobs_in_a_row_are_two_candidates(self) -> None:
        sightings = [
            Sighting(observed_at=FIRST_SEEN + n * DAY, sha256=f"{n}" * 64, content_hash=None)
            for n in (1, 2)
        ]

        assert len(derive_versions(PAGE_URL, sightings)) == 2
