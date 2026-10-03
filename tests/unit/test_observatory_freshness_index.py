"""The derived freshness index (issue #201).

Every test is anchored to the one invariant the issue names: divergence
between index and log may only ever cost a redundant fold, never a wrong
capture state — the indexed answer must equal the full re-fold, or the
index must be discarded and rebuilt. The log stays primary (ADR-0010 §7).
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lovspor.observatory.commands import _capture_state
from lovspor.observatory.freshness import CaptureState, ContentRun, collect_capture_state
from lovspor.observatory.freshness_index import (
    INDEX_DERIVATION_VERSION,
    FreshnessIndex,
    StoredHold,
    StoredRun,
    _prefix_digest,
    _state_binding,
    freshness_index_path,
    indexed_capture_state,
)
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import (
    ArtifactObservation,
    Correction,
    FetchFailure,
    RecordTombstone,
    RefiledObservation,
    RetrievalProvenance,
    record_key,
    record_to_json_line,
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


def _observation(url: str, when: datetime = NOW, sha256: str = "0" * 64) -> ArtifactObservation:
    return ArtifactObservation(
        authority_id="3201",
        url=url,
        observed_at=when,
        provenance=_provenance(),
        sha256=sha256,
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

    def test_a_content_run_continues_correctly_across_the_index_boundary(
        self, tmp_path: Path
    ) -> None:
        """Issue #415: two identical captures before the anchor and one after
        are a run of two unchanged, exactly as one full read counts them."""
        url = "https://example.invalid/x"
        log = make_log(tmp_path)
        log.append(_observation(url, NOW))
        log.append(_observation(url, NOW + timedelta(hours=1)))
        indexed_capture_state(log)
        log.append(_observation(url, NOW + timedelta(hours=2)))

        state, scan = indexed_capture_state(log)

        assert scan.records_read == 1
        assert state.content[url] == ContentRun("0" * 64, 2)
        assert state == _full_fold(log)

    def test_changed_content_in_the_tail_resets_a_cached_run(self, tmp_path: Path) -> None:
        url = "https://example.invalid/x"
        log = make_log(tmp_path)
        log.append(_observation(url, NOW))
        log.append(_observation(url, NOW + timedelta(hours=1)))
        indexed_capture_state(log)
        log.append(_observation(url, NOW + timedelta(hours=2), "1" * 64))

        state, _scan = indexed_capture_state(log)

        assert state.content[url] == ContentRun("1" * 64, 0)
        assert state == _full_fold(log)

    def test_an_older_tail_capture_resets_a_cached_run_without_replacing_its_bytes(
        self, tmp_path: Path
    ) -> None:
        """A late record cannot extend the cached run it does not follow.
        The reset must retain the latest bytes and match a full re-fold."""
        url = "https://example.invalid/x"
        log = make_log(tmp_path)
        log.append(_observation(url, NOW, "0" * 64))
        log.append(_observation(url, NOW + timedelta(hours=2), "0" * 64))
        indexed_capture_state(log)
        log.append(_observation(url, NOW + timedelta(hours=1), "1" * 64))

        state, scan = indexed_capture_state(log)

        assert scan.records_read == 1
        assert state.content[url] == ContentRun("0" * 64, 0)
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

    def test_an_altered_cached_run_is_not_treated_as_evidence(self, tmp_path: Path) -> None:
        """A run is what stretches an undated page's recheck to a week, so a
        hand-inflated one would suppress fetches exactly as an invented
        sighting would. The binding covers it too (issue #415)."""
        url = "https://example.invalid/real"
        log = make_log(tmp_path)
        log.append(_observation(url))
        indexed_capture_state(log)
        path = freshness_index_path(log)
        doc = json.loads(path.read_text())
        doc["content"][url]["unchanged"] = 50
        path.write_text(json.dumps(doc))

        state, scan = indexed_capture_state(log)

        assert scan.records_read == 1
        assert state.content[url] == ContentRun("0" * 64, 0)

    def test_an_index_without_content_runs_is_discarded_and_rebuilt(self, tmp_path: Path) -> None:
        """An index written before #415 folded no runs. Reading it as "no
        runs" would be harmless, but it must not be reusable at all: it was
        derived by other fold semantics."""
        log = make_log(tmp_path)
        log.append(_observation("https://example.invalid/a"))
        indexed_capture_state(log)
        path = freshness_index_path(log)
        doc = json.loads(path.read_text())
        del doc["content"]
        path.write_text(json.dumps(doc))

        state, scan = indexed_capture_state(log)

        assert scan.records_read == 1
        assert state == _full_fold(log)

    def test_the_fold_that_learned_content_runs_has_its_own_version(self) -> None:
        assert (
            INDEX_DERIVATION_VERSION >= 2
        )  # 3 since ADR-0015 moved the fold onto the corrected view


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


class TestTheBindingIsPinnedByItsBytes:
    """The binding writer and verifier are one function, so a mutation of
    that function cancels itself out in every round-trip test. Only an
    exact, hand-anchored digest can see it (mutation survivors, PR #233):
    key names, key order, separators and the empty-prefix digest are all
    part of what the recorded value MEANS."""

    def test_the_state_binding_digest_is_exactly_its_specification(self) -> None:
        index = FreshnessIndex(
            derivation_version=1,
            log_offset=844,
            prefix_sha256="a" * 64,
            state_sha256="0" * 64,
            observed={
                "https://example.invalid/b": datetime(2026, 9, 3, 8, 0, tzinfo=UTC),
                "https://example.invalid/a": datetime(2026, 9, 3, 9, 0, tzinfo=UTC),
            },
            holds={
                "https://example.invalid/h": StoredHold(
                    outcome="http_404",
                    consecutive=2,
                    last_failed_at=datetime(2026, 9, 3, 7, 0, tzinfo=UTC),
                )
            },
            content={
                "https://example.invalid/b": StoredRun(sha256="c" * 64, unchanged=0),
                "https://example.invalid/a": StoredRun(sha256="d" * 64, unchanged=3, document=True),
            },
        )

        assert _state_binding(index) == (
            "2e05c1facfe222df8e94a8bcd8f7828cf215e75769726e96986c8c1f1f6b3d50"
        )

    def test_an_empty_log_records_the_empty_prefix_digest(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.log_path.write_bytes(b"")
        indexed_capture_state(log)

        doc = json.loads(freshness_index_path(log).read_text())

        assert doc["prefix_sha256"] == hashlib.sha256(b"").hexdigest()
        assert doc["log_offset"] == 0

    def test_the_prefix_digest_covers_only_the_bytes_the_file_holds(self, tmp_path: Path) -> None:
        """A file shorter than the asked-for offset (a shrink racing the
        stat) digests what exists — a hex answer, never a silent None."""
        log = make_log(tmp_path)
        log.append(_observation("https://example.invalid/a"))
        payload = log.log_path.read_bytes()

        digest = _prefix_digest(log.log_path, len(payload) + 100)

        assert digest == hashlib.sha256(payload).hexdigest()

    def test_the_prefix_digest_covers_bytes_beyond_one_read_chunk(self, tmp_path: Path) -> None:
        """A large archive's anchor must bind every chunk, including the
        bytes immediately beyond the bounded first read."""
        payload = b"a" * (1 << 20) + b"tail"
        path = tmp_path / "large-prefix.jsonl"
        path.write_bytes(payload)

        digest = _prefix_digest(path, len(payload))

        assert digest == hashlib.sha256(payload).hexdigest()
        assert digest != hashlib.sha256(payload[: 1 << 20]).hexdigest()


def _corrected_fold(log: ObservationLog, authority_id: str | None = None) -> CaptureState:
    state = CaptureState.empty()
    assert log.scan_corrected_into(collect_capture_state(state, authority_id)).complete
    return state


def _refile(
    log: ObservationLog, original: ArtifactObservation | FetchFailure, **changes: str
) -> None:
    """Append both halves of a correction that re-files ``original`` with ``changes``.

    Only ``authority_id`` is a legal correction, and the register-wide fold
    does not read it; a changed URL is what makes the fold's use of the
    corrected view observable here. `verify` would refuse such a correction.
    """
    key = record_key(record_to_json_line(original).encode("utf-8"))
    attribution = {"reason": "test", "corrected_by": "owner", "corrected_at": NOW}
    log.append(
        RefiledObservation(
            observation=original.model_copy(update=changes),
            correction=Correction(
                supersedes=key,
                correction_id="c1",
                corrected_fields=("authority_id",),
                previous_values={"authority_id": original.authority_id},
                **attribution,  # type: ignore[arg-type]
            ),
        )
    )
    log.append(RecordTombstone(retracts=key, correction_id="c1", **attribution))  # type: ignore[arg-type]


class TestTheIndexFoldsTheCorrectedView:
    """ADR-0015 §5: the index caches the fold of the corrected view, and an
    index built before a correction landed is rebuilt, not extended."""

    def test_the_derivation_version_moved_past_the_uncorrected_fold(self) -> None:
        assert INDEX_DERIVATION_VERSION >= 3

    def test_a_cold_build_folds_the_corrected_view(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        original = _observation("https://example.invalid/wrong")
        log.append(original)
        _refile(log, original, url="https://example.invalid/right")

        state, _ = indexed_capture_state(log)

        assert set(state.observed) == {"https://example.invalid/right"}
        assert state == _corrected_fold(log)

    def test_a_correction_after_the_index_rebuilds_the_fold(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        original = _observation("https://example.invalid/wrong")
        log.append(original)
        indexed_capture_state(log)
        _refile(log, original, url="https://example.invalid/right")

        state, scan = indexed_capture_state(log)

        assert set(state.observed) == {"https://example.invalid/right"}
        assert scan.clean_through == log.log_path.stat().st_size
        assert state == _corrected_fold(log)

    def test_growth_after_a_correction_still_folds_only_the_tail(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        original = _observation("https://example.invalid/wrong")
        log.append(original)
        _refile(log, original, url="https://example.invalid/right")
        indexed_capture_state(log)
        log.append(_observation("https://example.invalid/b", NOW + timedelta(hours=1)))

        state, scan = indexed_capture_state(log)

        assert scan.records_read == 1
        assert state == _corrected_fold(log)


class TestTheNarrowedFoldFollowsTheCorrection:
    """The per-authority fold is where attribution decides a capture."""

    def _misfiled_then_corrected(self, root: Path) -> ObservationLog:
        log = make_log(root)
        first = _observation("https://example.invalid/a").model_copy(
            update={"authority_id": "4202"}
        )
        failed = _failure("https://example.invalid/b").model_copy(update={"authority_id": "4202"})
        log.append(first)
        log.append(failed)
        log.append(_observation("https://example.invalid/a", NOW + timedelta(hours=1)))
        _refile(log, first, authority_id="4203")
        _refile(log, failed, authority_id="4203")
        return log

    def test_the_target_sees_the_records_and_the_source_does_not(self, tmp_path: Path) -> None:
        log = self._misfiled_then_corrected(tmp_path)

        assert set(_capture_state(log, "4203").observed) == {"https://example.invalid/a"}
        assert set(_capture_state(log, "4203").holds) == {"https://example.invalid/b"}
        assert _capture_state(log, "4202") == CaptureState.empty()

    def test_equals_the_fold_of_a_log_filed_correctly_from_the_start(self, tmp_path: Path) -> None:
        corrected = self._misfiled_then_corrected(tmp_path / "corrected")
        filed_right = make_log(tmp_path / "right")
        for record in (
            _observation("https://example.invalid/a").model_copy(update={"authority_id": "4203"}),
            _failure("https://example.invalid/b").model_copy(update={"authority_id": "4203"}),
            _observation("https://example.invalid/a", NOW + timedelta(hours=1)),
        ):
            filed_right.append(record)

        for authority_id in ("4203", "3201", None):
            assert _capture_state(corrected, authority_id) == _capture_state(
                filed_right, authority_id
            )


class TestTheIndexRemembersDocuments:
    """#507: selection keeps a URL whose latest bytes were a document, so the
    indexed fold must carry that mark exactly as the full fold does."""

    def test_the_fold_that_learned_documents_has_its_own_version(self) -> None:
        assert INDEX_DERIVATION_VERSION == 4

    def test_a_document_mark_survives_the_index(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        url = "https://example.invalid/vedtak/"
        pdf = _observation(url).model_copy(update={"content_type": "application/pdf"})
        log.append(pdf)
        log.append(_observation("https://example.invalid/side"))
        indexed_capture_state(log)

        state, scan = indexed_capture_state(log)

        assert scan.records_read == 0
        assert state.content[url] == ContentRun("0" * 64, 0, document=True)
        assert state.content["https://example.invalid/side"].document is False
        assert state == _full_fold(log)

    def test_an_index_from_the_fold_without_documents_is_rebuilt(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        url = "https://example.invalid/vedtak/"
        log.append(_observation(url).model_copy(update={"content_type": "application/pdf"}))
        indexed_capture_state(log)
        path = freshness_index_path(log)
        doc = json.loads(path.read_text())
        doc["derivation_version"] = 3
        for run in doc["content"].values():
            del run["document"]
        path.write_text(json.dumps(doc))

        state, scan = indexed_capture_state(log)

        assert scan.records_read == 1
        assert state.content[url].document is True
