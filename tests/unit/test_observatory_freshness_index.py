"""The derived freshness index (issue #201).

Every test is anchored to the one invariant the issue names: divergence
between index and log may only ever cost a redundant fold, never a wrong
capture state — the indexed answer must equal the full re-fold, or the
index must be discarded and rebuilt. The log stays primary (ADR-0010 §7).
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lovspor.observatory.commands import _capture_state
from lovspor.observatory.freshness import CaptureState, collect_capture_state
from lovspor.observatory.freshness_index import (
    INDEX_DERIVATION_VERSION,
    freshness_index_path,
    indexed_capture_state,
)
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import (
    ArtifactObservation,
    FetchFailure,
    RetrievalProvenance,
)
from lovspor.observatory.storage import ObservatoryRoot

NOW = datetime(2026, 9, 3, 8, 0, tzinfo=UTC)


def make_log(root: Path) -> ObservationLog:
    return ObservationLog(ObservatoryRoot(root, []))


def _provenance() -> RetrievalProvenance:
    return RetrievalProvenance(
        adapter="http",
        channel="sitemap",
        discovery_method="sitemap",
        user_agent="test-agent",
        rate_limit_seconds=1.0,
    )


def _observation(url: str, when: datetime = NOW) -> ArtifactObservation:
    return ArtifactObservation(
        authority_id="3201",
        url=url,
        observed_at=when,
        provenance=_provenance(),
        sha256="0" * 64,
        content_type="text/html",
        http_status=200,
    )


def _failure(url: str, when: datetime = NOW, outcome: str = "http_404") -> FetchFailure:
    return FetchFailure(
        authority_id="3201",
        url=url,
        observed_at=when,
        provenance=_provenance(),
        outcome=outcome,
        http_status=None,
    )


def _full_fold(log: ObservationLog) -> CaptureState:
    state = CaptureState.empty()
    scan = log.scan_into(collect_capture_state(state))
    assert scan.complete
    return state


class TestIndexedFoldEqualsTheFullFold:
    def test_cold_start_builds_the_state_and_writes_the_index(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append(_observation("https://example.invalid/a"))
        log.append(_failure("https://example.invalid/b"))

        state, scan = indexed_capture_state(log)

        assert scan.complete
        assert state == _full_fold(log)
        assert freshness_index_path(log).exists()

    def test_a_grown_log_folds_only_the_tail_and_matches_the_full_fold(
        self, tmp_path: Path
    ) -> None:
        log = make_log(tmp_path)
        log.append(_observation("https://example.invalid/a"))
        indexed_capture_state(log)
        log.append(_observation("https://example.invalid/b", NOW + timedelta(hours=1)))
        log.append(_failure("https://example.invalid/c", NOW + timedelta(hours=2)))

        state, scan = indexed_capture_state(log)

        assert scan.records_read == 2
        assert state == _full_fold(log)

    def test_an_unchanged_log_reads_no_records_at_all(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append(_observation("https://example.invalid/a"))
        indexed_capture_state(log)

        state, scan = indexed_capture_state(log)

        assert scan.records_read == 0
        assert state == _full_fold(log)

    def test_a_hold_run_continues_correctly_across_the_index_boundary(self, tmp_path: Path) -> None:
        """The fold is sequential: two failures before the anchor and one
        after must count as a run of three, exactly as one full read."""
        log = make_log(tmp_path)
        log.append(_failure("https://example.invalid/x", NOW))
        log.append(_failure("https://example.invalid/x", NOW + timedelta(hours=1)))
        indexed_capture_state(log)
        log.append(_failure("https://example.invalid/x", NOW + timedelta(hours=2)))

        state, _scan = indexed_capture_state(log)

        assert state.holds["https://example.invalid/x"].consecutive == 3
        assert state == _full_fold(log)

    def test_an_observation_in_the_tail_clears_a_cached_failure_hold(self, tmp_path: Path) -> None:
        url = "https://example.invalid/x"
        log = make_log(tmp_path)
        log.append(_failure(url))
        indexed_capture_state(log)
        log.append(_observation(url, NOW + timedelta(hours=1)))

        state, scan = indexed_capture_state(log)

        assert scan.records_read == 1
        assert url in state.observed
        assert url not in state.holds
        assert state == _full_fold(log)

    def test_the_written_index_is_byte_identical_for_one_log_state(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append(_observation("https://example.invalid/b"))
        log.append(_observation("https://example.invalid/a"))
        indexed_capture_state(log)
        first = freshness_index_path(log).read_bytes()
        freshness_index_path(log).unlink()
        indexed_capture_state(log)

        assert freshness_index_path(log).read_bytes() == first


class TestAnyDoubtRebuilds:
    def test_garbage_index_content_is_discarded_and_rebuilt(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append(_observation("https://example.invalid/a"))
        freshness_index_path(log).write_text("not json at all")

        state, scan = indexed_capture_state(log)

        assert scan.complete
        assert state == _full_fold(log)

    def test_non_utf8_index_content_is_discarded_and_rebuilt(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append(_observation("https://example.invalid/a"))
        freshness_index_path(log).write_bytes(b"\xff\xfe not utf-8")

        state, scan = indexed_capture_state(log)

        assert scan.complete
        assert state == _full_fold(log)

    def test_another_derivation_version_is_discarded_and_rebuilt(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append(_observation("https://example.invalid/a"))
        indexed_capture_state(log)
        path = freshness_index_path(log)
        doc = json.loads(path.read_text())
        doc["derivation_version"] = INDEX_DERIVATION_VERSION + 1
        path.write_text(json.dumps(doc))

        state, scan = indexed_capture_state(log)

        assert scan.records_read == 1
        assert state == _full_fold(log)

    def test_a_shrunken_log_forces_a_full_rebuild(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append(_observation("https://example.invalid/a"))
        log.append(_observation("https://example.invalid/b"))
        indexed_capture_state(log)
        lines = log.log_path.read_bytes().splitlines(keepends=True)
        log.log_path.write_bytes(lines[0])

        state, scan = indexed_capture_state(log)

        assert scan.records_read == 1
        assert state == _full_fold(log)

    def test_a_deleted_log_discards_all_cached_sightings(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        url = "https://example.invalid/a"
        log.append(_observation(url))
        indexed_capture_state(log)
        log.log_path.unlink()

        state, scan = indexed_capture_state(log)

        assert scan.complete
        assert state == CaptureState.empty()
        assert url not in state.observed

    def test_a_same_size_in_place_rewrite_is_caught_by_the_digest(self, tmp_path: Path) -> None:
        """The append-only assumption's one blind spot: same length,
        different bytes. The offset alone cannot see it; the anchor must."""
        log = make_log(tmp_path)
        log.append(_observation("https://example.invalid/aaaa"))
        indexed_capture_state(log)
        original = log.log_path.read_bytes()
        log.log_path.write_bytes(original.replace(b"/aaaa", b"/bbbb"))

        state, scan = indexed_capture_state(log)

        assert scan.records_read == 1
        assert "https://example.invalid/bbbb" in state.observed
        assert "https://example.invalid/aaaa" not in state.observed
        assert state == _full_fold(log)

    def test_a_same_size_rewrite_before_the_fingerprint_window_forces_a_rebuild(
        self, tmp_path: Path
    ) -> None:
        log = make_log(tmp_path)
        old_url = "https://example.invalid/aaaa"
        new_url = "https://example.invalid/bbbb"
        log.append(_observation(old_url))
        for number in range(30):
            log.append(_observation(f"https://example.invalid/padding/{number:02d}/" + "x" * 160))
        indexed_capture_state(log)
        original = log.log_path.read_bytes()
        assert original.index(old_url.encode()) < len(original) - 4096
        log.log_path.write_bytes(original.replace(old_url.encode(), new_url.encode(), 1))

        state, scan = indexed_capture_state(log)

        assert scan.records_read == 31
        assert new_url in state.observed
        assert old_url not in state.observed
        assert state == _full_fold(log)

    def test_valid_but_altered_cached_state_is_not_treated_as_evidence(
        self, tmp_path: Path
    ) -> None:
        """The prefix digest proves the log bytes, not the cached fold.

        A derived artifact whose state no longer agrees with those bytes must
        cost a rebuild rather than inventing a sighting that can suppress a
        future fetch.
        """
        log = make_log(tmp_path)
        real_url = "https://example.invalid/real"
        invented_url = "https://example.invalid/invented"
        log.append(_observation(real_url))
        indexed_capture_state(log)
        path = freshness_index_path(log)
        doc = json.loads(path.read_text())
        doc["observed"] = {invented_url: (NOW + timedelta(days=1)).isoformat()}
        path.write_text(json.dumps(doc))

        state, scan = indexed_capture_state(log)

        assert scan.records_read == 1
        assert state == _full_fold(log)
        assert real_url in state.observed
        assert invented_url not in state.observed


class TestDamageNeverAdvancesTheIndex:
    def test_damage_in_a_rewritten_prefix_refuses_and_does_not_advance_the_index(
        self, tmp_path: Path
    ) -> None:
        log = make_log(tmp_path)
        log.append(_observation("https://example.invalid/a"))
        indexed_capture_state(log)
        before = freshness_index_path(log).read_bytes()
        original = log.log_path.read_bytes()
        log.log_path.write_bytes(b"!" + original[1:])

        _state, scan = indexed_capture_state(log)

        assert not scan.complete
        assert scan.malformed_lines == (1,)
        assert freshness_index_path(log).read_bytes() == before

    def test_a_torn_tail_refuses_and_keeps_the_old_anchor(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append(_observation("https://example.invalid/a"))
        indexed_capture_state(log)
        before = freshness_index_path(log).read_bytes()
        with log.log_path.open("ab") as handle:
            handle.write(b'{"kind": "artifa')

        _state, scan = indexed_capture_state(log)

        assert not scan.complete
        assert scan.incomplete_final_record
        assert freshness_index_path(log).read_bytes() == before

    def test_a_damaged_cold_log_writes_no_index(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append(_observation("https://example.invalid/a"))
        with log.log_path.open("ab") as handle:
            handle.write(b'{"kind": "artifa')

        _state, scan = indexed_capture_state(log)

        assert not scan.complete
        assert not freshness_index_path(log).exists()

    def test_a_newline_terminated_malformed_tail_keeps_the_old_anchor(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append(_observation("https://example.invalid/a"))
        indexed_capture_state(log)
        before = freshness_index_path(log).read_bytes()
        with log.log_path.open("ab") as handle:
            handle.write(b'{"kind": "artifact", "malformed": true}\n')

        _state, scan = indexed_capture_state(log)

        assert not scan.complete
        assert scan.malformed_lines == (1,)
        assert freshness_index_path(log).read_bytes() == before

    def test_an_absent_log_folds_to_nothing_and_writes_an_empty_index(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)

        state, scan = indexed_capture_state(log)

        assert scan.complete
        assert state == CaptureState.empty()


class TestTheSweepPathReachesTheIndex:
    """The operator's question: the supported register-wide fold — what
    capture-all and nightly call — must be the indexed one, and a narrowed
    capture must not be (a different function of the log)."""

    def test_the_register_wide_fold_writes_and_reuses_the_index(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append(_observation("https://example.invalid/a"))
        first = _capture_state(log, None)
        assert freshness_index_path(log).exists()

        second = _capture_state(log, None)

        assert first == second == _full_fold(log)

    def test_a_narrowed_fold_stays_on_the_direct_read(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append(_observation("https://example.invalid/a"))

        state = _capture_state(log, "3201")

        assert state.observed
        assert not freshness_index_path(log).exists()
