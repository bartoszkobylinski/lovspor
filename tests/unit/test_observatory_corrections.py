"""The corrected view of the observation log (ADR-0015 §5, §6)."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import lovspor.observatory.corrections as corrections_module
import lovspor.observatory.log as log_module
from lovspor.observatory.corrections import CorrectionSet, Refiled, fold_corrections
from lovspor.observatory.log import ObservationLog, verify_snapshot
from lovspor.observatory.model import (
    ArtifactObservation,
    Correction,
    FetchFailure,
    ObservationRecord,
    RecordTombstone,
    RefiledObservation,
    RetrievalProvenance,
    Tombstone,
    record_key,
    record_to_json_line,
)
from lovspor.observatory.storage import ObservatoryRoot

START = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
CORRECTED_AT = datetime(2026, 9, 27, 8, 0, tzinfo=UTC)
HOST = "https://www.arendal.kommune.no"


def make_log(root: Path) -> ObservationLog:
    return ObservationLog(ObservatoryRoot(root, []))


def artifact(path: str, authority_id: str = "4202", minute: int = 0) -> ArtifactObservation:
    return ArtifactObservation(
        authority_id=authority_id,
        url=f"{HOST}/{path}",
        observed_at=START + timedelta(minutes=minute),
        provenance=RetrievalProvenance(
            adapter="http",
            channel="http",
            discovery_method="sitemap",
            user_agent="lovspor-observatory/0.1",
            rate_limit_seconds=7.0,
        ),
        sha256=hashlib.sha256(path.encode()).hexdigest(),
        content_type="text/html",
        http_status=200,
    )


def failure(path: str, authority_id: str = "4202") -> FetchFailure:
    return FetchFailure(
        authority_id=authority_id,
        url=f"{HOST}/{path}",
        observed_at=START,
        provenance=artifact(path).provenance,
        outcome="http_404",
    )


def key_of(record: ObservationRecord) -> str:
    return record_key(record_to_json_line(record).encode("utf-8"))


def refiled(
    original: ArtifactObservation | FetchFailure, to: str, correction_id: str = "c1"
) -> RefiledObservation:
    return RefiledObservation(
        observation=original.model_copy(update={"authority_id": to}),
        correction=Correction(
            supersedes=key_of(original),
            correction_id=correction_id,
            corrected_fields=("authority_id",),
            previous_values={"authority_id": original.authority_id},
            reason="misattributed (#221)",
            corrected_by="owner",
            corrected_at=CORRECTED_AT,
        ),
    )


def tombstone(key: str, correction_id: str = "c1") -> RecordTombstone:
    return RecordTombstone(
        retracts=key,
        correction_id=correction_id,
        reason="misattributed (#221)",
        corrected_by="owner",
        corrected_at=CORRECTED_AT,
    )


def correct(
    log: ObservationLog,
    original: ArtifactObservation | FetchFailure,
    to: str = "4203",
    correction_id: str = "c1",
) -> RefiledObservation:
    """Append both halves of one correction, in the writer's order."""
    record = refiled(original, to, correction_id)
    log.append(record)
    log.append(tombstone(key_of(original), correction_id))
    return record


def corrected(log: ObservationLog) -> list[ObservationRecord]:
    seen: list[ObservationRecord] = []
    scan = log.scan_corrected_into(seen.append)
    assert scan.complete
    return seen


class TestTheCorrectedView:
    def test_without_corrections_the_view_is_the_log(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        records = [artifact("a"), failure("b"), artifact("c")]
        for record in records:
            log.append(record)

        assert corrected(log) == records

    def test_without_corrections_the_view_does_not_hash_lines(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = make_log(tmp_path)
        original = artifact("a")
        log.append(original)

        def unexpected_hash(_line: bytes) -> str:
            pytest.fail("an empty correction set must not hash observation lines")

        monkeypatch.setattr(corrections_module, "record_key", unexpected_hash)

        assert corrected(log) == [original]

    def test_a_corrected_original_is_replaced_at_its_own_position(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        first, middle, last = artifact("a", "4203"), artifact("b"), artifact("c", "4203")
        for record in (first, middle, last):
            log.append(record)

        correct(log, middle)

        assert corrected(log) == [first, middle.model_copy(update={"authority_id": "4203"}), last]

    def test_a_failure_is_corrected_the_same_way(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        original = failure("x")
        log.append(original)
        correct(log, original)

        assert [record.authority_id for record in corrected(log)] == ["4203"]  # type: ignore[union-attr]

    def test_a_refiled_half_without_its_tombstone_is_not_in_force(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        original = artifact("a")
        log.append(original)
        log.append(refiled(original, "4203"))

        assert corrected(log) == [original]

    def test_a_tombstone_without_its_refiled_half_is_not_in_force(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        original = artifact("a")
        log.append(original)
        log.append(tombstone(key_of(original)))

        assert corrected(log) == [original]

    def test_halves_of_different_corrections_do_not_pair(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        original = artifact("a")
        log.append(original)
        log.append(refiled(original, "4203", correction_id="c1"))
        log.append(tombstone(key_of(original), correction_id="c2"))

        assert corrected(log) == [original]

    def test_a_wrong_correction_is_corrected_through_its_refiled_record(
        self, tmp_path: Path
    ) -> None:
        log = make_log(tmp_path)
        original = artifact("a")
        log.append(original)
        wrong = correct(log, original, to="9999", correction_id="c1")
        log.append(
            RefiledObservation(
                observation=original.model_copy(update={"authority_id": "4203"}),
                correction=refiled(original, "4203", "c2").correction.model_copy(
                    update={
                        "supersedes": key_of(wrong),
                        "previous_values": {"authority_id": "9999"},
                    }
                ),
            )
        )
        log.append(tombstone(key_of(wrong), correction_id="c2"))

        assert [record.authority_id for record in corrected(log)] == ["4203"]  # type: ignore[union-attr]

    def test_a_cycle_in_the_correction_chain_stops_at_the_first_repeated_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = artifact("a")
        replacement = refiled(original, "4203")
        key = key_of(original)
        calls = 0

        def cyclic(_corrections: CorrectionSet, candidate: str) -> Refiled:
            nonlocal calls
            calls += 1
            assert calls == 1, "the repeated key must stop resolution before another lookup"
            return Refiled(replacement, candidate)

        monkeypatch.setattr(CorrectionSet, "in_force", cyclic)

        assert CorrectionSet().resolve(original, key) == replacement.observation

    def test_the_first_completed_correction_stands(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        original = artifact("a")
        log.append(original)
        correct(log, original, to="4203", correction_id="c1")
        correct(log, original, to="9999", correction_id="c2")

        assert [record.authority_id for record in corrected(log)] == ["4203"]  # type: ignore[union-attr]

    def test_a_blob_tombstone_passes_through(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        removal = Tombstone(
            sha256="a" * 64, removed_at=CORRECTED_AT, basis="privacy", authorised_by="owner"
        )
        log.append(removal)

        assert corrected(log) == [removal]

    def test_the_key_is_the_stored_line_even_when_it_reserialises_differently(
        self, tmp_path: Path
    ) -> None:
        log = make_log(tmp_path)
        original = artifact("a")
        legacy = record_to_json_line(original).replace(',"redirect_chain":[]', "")
        log.log_path.write_text(legacy + "\n", encoding="utf-8")
        log.append(
            RefiledObservation(
                observation=original.model_copy(update={"authority_id": "4203"}),
                correction=refiled(original, "4203").correction.model_copy(
                    update={"supersedes": record_key(legacy.encode())}
                ),
            )
        )
        log.append(tombstone(record_key(legacy.encode())))

        assert [record.authority_id for record in corrected(log)] == ["4203"]  # type: ignore[union-attr]


class TestTheCorrectionFold:
    def test_a_fresh_reader_folds_a_correction_in_the_first_line(self, tmp_path: Path) -> None:
        writer = make_log(tmp_path)
        original = artifact("a")
        writer.append(refiled(original, "4203"))
        writer.append(tombstone(key_of(original)))

        corrections = make_log(tmp_path).corrections()

        assert corrections.in_force(key_of(original)) is not None

    def test_is_extended_from_where_it_stopped(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        original = artifact("a")
        log.append(original)
        assert not log.corrections()

        correct(log, original)

        assert log.corrections().in_force(key_of(original)) is not None
        assert log.corrections().last_offset == log.log_path.stat().st_size

    def test_is_discarded_when_the_log_gets_shorter(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        original = artifact("a")
        log.append(original)
        prefix = log.log_path.read_bytes()
        correct(log, original)
        assert log.corrections()

        log.log_path.write_bytes(prefix)

        assert not log.corrections()

    def test_a_cached_fold_is_not_reapplied_when_the_size_is_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = make_log(tmp_path)
        original = artifact("a")
        log.append(original)
        correct(log, original)

        cached = log.corrections()

        def unexpected_fold(_path: Path, _into: CorrectionSet, _start: int) -> int:
            pytest.fail("an unchanged log must not be folded again")

        monkeypatch.setattr(log_module, "fold_corrections", unexpected_fold)

        assert log.corrections() is cached

    def test_an_empty_truncation_restarts_the_next_fold_at_byte_zero(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        original = artifact("a")
        log.append(original)
        correct(log, original)
        assert log.corrections()

        log.log_path.write_bytes(b"")
        assert not log.corrections()
        log.append(original)
        correct(log, original)

        assert log.corrections().in_force(key_of(original)) is not None

    def test_a_correction_after_empty_truncation_is_read_from_byte_zero(
        self, tmp_path: Path
    ) -> None:
        log = make_log(tmp_path)
        original = artifact("a")
        correct(log, original)
        assert log.corrections()

        log.log_path.write_bytes(b"")
        assert not log.corrections()
        correct(log, original)

        assert log.corrections().in_force(key_of(original)) is not None

    def test_an_absent_log_has_no_corrections(self, tmp_path: Path) -> None:
        assert not make_log(tmp_path).corrections()

    def test_stops_before_an_unfinished_line(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        original = artifact("a")
        log.append(original)
        log.append(refiled(original, "4203"))
        whole = log.log_path.stat().st_size
        line = record_to_json_line(tombstone(key_of(original))).encode()
        with log.log_path.open("ab") as handle:
            handle.write(line)

        into = CorrectionSet()
        assert fold_corrections(log.log_path, into, 0) == whole
        assert into.tombstones == {}

    def test_an_unreadable_candidate_is_skipped_and_left_to_the_scan(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append(artifact("a"))
        with log.log_path.open("ab") as handle:
            handle.write(b'{"kind":"record_tombstone","broken":\n')

        assert not log.corrections()
        assert not log.scan_corrected_into(lambda _record: None).complete

    def test_a_url_naming_a_correction_kind_is_not_a_correction(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append(artifact("record_tombstone"))

        assert not log.corrections()

    def test_only_correction_candidate_lines_are_parsed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = make_log(tmp_path)
        log.append(artifact("ordinary"))
        parsed: list[bytes] = []
        original_fold_line = corrections_module._fold_line

        def track_candidate(into: CorrectionSet, line: bytes, end: int) -> None:
            parsed.append(line)
            original_fold_line(into, line, end)

        monkeypatch.setattr(corrections_module, "_fold_line", track_candidate)

        fold_corrections(log.log_path, CorrectionSet(), 0)

        assert parsed == []


class TestTheBlobTombstoneIsUntouched:
    def test_a_correction_never_enters_the_tombstoned_hashes(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        original = artifact("a")
        log.append_artifact(original, b"a")
        correct(log, original)

        assert log.tombstoned_hashes() == frozenset()

    def test_recapturing_a_corrected_records_bytes_is_not_refused(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        original = artifact("a")
        log.append_artifact(original, b"a")
        correct(log, original)

        log.append_artifact(original.model_copy(update={"authority_id": "4203"}), b"a")

        assert log.blob_path(original.sha256).read_bytes() == b"a"


class TestVerifyAuditsCorrections:
    """ADR-0015 §5: `verify` reads the raw log and fails a correction that
    does not account for itself."""

    def _stored(self, root: Path) -> tuple[ObservationLog, ArtifactObservation]:
        log = make_log(root)
        original = artifact("a")
        log.append_artifact(original, b"a")
        return log, original

    def test_a_complete_correction_is_clean(self, tmp_path: Path) -> None:
        log, original = self._stored(tmp_path)
        correct(log, original)

        report = verify_snapshot(log)

        assert report.ok, report
        assert report.artifacts_checked == 1

    def test_a_refiled_half_alone_is_an_incomplete_correction(self, tmp_path: Path) -> None:
        log, original = self._stored(tmp_path)
        log.append(refiled(original, "4203"))

        report = verify_snapshot(log)

        assert report.incomplete_corrections == (key_of(original),)
        assert report.corrections_without_record == ()
        assert not report.ok

    def test_a_tombstone_half_alone_is_an_incomplete_correction(self, tmp_path: Path) -> None:
        log, original = self._stored(tmp_path)
        log.append(tombstone(key_of(original)))

        report = verify_snapshot(log)

        assert report.incomplete_corrections == (key_of(original),)
        assert report.corrections_without_record == ()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("reason", "a different reason"),
            ("corrected_by", "someone else"),
            ("corrected_at", CORRECTED_AT + timedelta(seconds=1)),
        ],
    )
    def test_halves_disagreeing_on_attribution_are_not_one_correction(
        self, tmp_path: Path, field: str, value: object
    ) -> None:
        log, original = self._stored(tmp_path)
        log.append(refiled(original, "4203"))
        log.append(tombstone(key_of(original)).model_copy(update={field: value}))

        report = verify_snapshot(log)

        assert report.incomplete_corrections == (key_of(original),)
        assert corrected(log) == [original]

    def test_halves_naming_different_records_complete_neither(self, tmp_path: Path) -> None:
        log, original = self._stored(tmp_path)
        other = artifact("b")
        log.append_artifact(other, b"b")
        log.append(refiled(original, "4203"))
        log.append(tombstone(key_of(other)))

        report = verify_snapshot(log)

        assert report.incomplete_corrections == tuple(sorted((key_of(original), key_of(other))))
        assert corrected(log) == [original, other]

    def test_a_correction_whose_original_was_under_another_authority_is_reported(
        self, tmp_path: Path
    ) -> None:
        log = make_log(tmp_path)
        original = artifact("a", authority_id="3201")
        log.append_artifact(original, b"a")
        forged = refiled(original, "4203").model_copy(
            update={
                "correction": refiled(original, "4203").correction.model_copy(
                    update={"previous_values": {"authority_id": "4202"}}
                )
            }
        )
        log.append(forged)
        log.append(tombstone(key_of(original)))

        assert verify_snapshot(log).refiled_mismatches == (key_of(original),)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("reason", "a different reason"),
            ("corrected_by", "a different operator"),
            ("corrected_at", CORRECTED_AT + timedelta(seconds=1)),
        ],
    )
    def test_halves_with_mismatched_attribution_are_rejected(
        self, tmp_path: Path, field: str, value: str | datetime
    ) -> None:
        log, original = self._stored(tmp_path)
        log.append(refiled(original, "4203"))
        log.append(tombstone(key_of(original)).model_copy(update={field: value}))

        report = verify_snapshot(log)

        assert not report.ok, report

    def test_a_tombstone_naming_no_line_is_reported(self, tmp_path: Path) -> None:
        log, _ = self._stored(tmp_path)
        log.append(tombstone("f" * 64))

        report = verify_snapshot(log)

        assert report.corrections_without_record == ("f" * 64,)
        assert not report.ok

    def test_a_refiled_record_changing_an_uncorrected_field_is_reported(
        self, tmp_path: Path
    ) -> None:
        log, original = self._stored(tmp_path)
        forged = refiled(original, "4203")
        log.append(
            forged.model_copy(
                update={"observation": forged.observation.model_copy(update={"url": "x"})}
            )
        )
        log.append(tombstone(key_of(original)))

        report = verify_snapshot(log)

        assert report.refiled_mismatches == (key_of(original),)
        assert not report.ok

    def test_wrong_previous_values_are_reported(self, tmp_path: Path) -> None:
        log, original = self._stored(tmp_path)
        forged = refiled(original, "4203")
        log.append(
            forged.model_copy(
                update={
                    "correction": forged.correction.model_copy(
                        update={"previous_values": {"authority_id": "3201"}}
                    )
                }
            )
        )
        log.append(tombstone(key_of(original)))

        assert verify_snapshot(log).refiled_mismatches == (key_of(original),)

    def test_a_refiled_record_of_another_kind_is_reported(self, tmp_path: Path) -> None:
        log, original = self._stored(tmp_path)
        other = failure("a")
        record = refiled(other, "4203")
        log.append(
            record.model_copy(
                update={
                    "correction": record.correction.model_copy(
                        update={"supersedes": key_of(original)}
                    )
                }
            )
        )
        log.append(tombstone(key_of(original)))

        assert verify_snapshot(log).refiled_mismatches == (key_of(original),)

    def test_a_correction_of_a_blob_tombstone_is_reported(self, tmp_path: Path) -> None:
        log, original = self._stored(tmp_path)
        removal = Tombstone(sha256="e" * 64, removed_at=CORRECTED_AT, basis="x", authorised_by="y")
        log.append(removal)
        record = refiled(original, "4203")
        log.append(
            record.model_copy(
                update={
                    "correction": record.correction.model_copy(
                        update={"supersedes": key_of(removal)}
                    )
                }
            )
        )
        log.append(tombstone(key_of(removal)))

        assert key_of(removal) in verify_snapshot(log).refiled_mismatches

    def test_an_original_corrected_twice_is_reported(self, tmp_path: Path) -> None:
        log, original = self._stored(tmp_path)
        correct(log, original, correction_id="c1")
        correct(log, original, correction_id="c2")

        report = verify_snapshot(log)

        assert report.multiply_corrected == (key_of(original),)
        assert not report.ok

    def test_a_chained_correction_is_audited_against_its_refiled_original(
        self, tmp_path: Path
    ) -> None:
        log, original = self._stored(tmp_path)
        wrong = correct(log, original, to="9999", correction_id="c1")
        second = refiled(original, "4203", "c2")
        log.append(
            second.model_copy(
                update={
                    "correction": second.correction.model_copy(
                        update={
                            "supersedes": key_of(wrong),
                            "previous_values": {"authority_id": "9999"},
                        }
                    )
                }
            )
        )
        log.append(tombstone(key_of(wrong), correction_id="c2"))

        assert verify_snapshot(log).ok

    def test_a_damaged_log_is_not_audited_for_corrections(self, tmp_path: Path) -> None:
        log, _ = self._stored(tmp_path)
        log.append(tombstone("f" * 64))
        with log.log_path.open("ab") as handle:
            handle.write(b"{torn")

        report = verify_snapshot(log)

        assert report.incomplete_final_record is True
        assert report.corrections_without_record == ()
