"""Tests for lovspor.observatory.log — append-only evidence and its audit."""

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.errors import LogIntegrityError, StorageBoundaryError, TombstonedArtifactError
from lovspor.observatory.log import ObservationLog, verify_snapshot
from lovspor.observatory.model import (
    ArtifactObservation,
    FetchFailure,
    RetrievalProvenance,
    Tombstone,
)
from lovspor.observatory.storage import (
    ENV_OBSERVATORY_ROOT,
    ObservatoryRoot,
    engine_root,
    observatory_root,
)

OBSERVED_AT = datetime(2026, 8, 18, 6, 30, tzinfo=UTC)


def make_log(root: Path) -> ObservationLog:
    """A log under a validated root — the only way to construct one."""
    return ObservationLog(ObservatoryRoot(root, []))


def provenance() -> RetrievalProvenance:
    return RetrievalProvenance(
        adapter="generic-html",
        channel="http",
        discovery_method="sitemap",
        user_agent="lovspor-observatory/0.1",
        rate_limit_seconds=2.0,
    )


def observation(payload: bytes, *, url: str = "https://example.invalid/f") -> ArtifactObservation:
    return ArtifactObservation(
        authority_id="9999",
        url=url,
        observed_at=OBSERVED_AT,
        provenance=provenance(),
        sha256=hashlib.sha256(payload).hexdigest(),
        content_type="text/html",
        http_status=200,
    )


class TestAppendOnly:
    def test_appending_preserves_earlier_lines_byte_for_byte(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append_artifact(observation(b"first"), b"first")
        before = log.log_path.read_bytes()

        log.append_artifact(observation(b"second"), b"second")

        assert log.log_path.read_bytes().startswith(before)

    def test_recrawl_of_unchanged_content_appends_a_second_record(self, tmp_path: Path) -> None:
        """A re-crawl is always an addition: two records, one deduplicated blob."""
        log = make_log(tmp_path)
        payload = b"unchanged"
        log.append_artifact(observation(payload), payload)
        log.append_artifact(observation(payload), payload)

        records = list(log.records())
        blobs = list((tmp_path / "blobs").rglob("*"))

        assert len(records) == 2
        assert [path for path in blobs if path.is_file()] == [log.blob_path(records[0].sha256)]

    def test_records_read_back_in_append_order(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append_artifact(observation(b"a", url="https://example.invalid/1"), b"a")
        log.append(
            FetchFailure(
                authority_id="9999",
                url="https://example.invalid/2",
                observed_at=OBSERVED_AT,
                provenance=provenance(),
                outcome="timeout",
            ),
        )

        kinds = [record.kind for record in log.records()]

        assert kinds == ["artifact", "fetch_failure"]

    def test_empty_log_reads_as_no_records(self, tmp_path: Path) -> None:
        assert list(make_log(tmp_path).records()) == []

    def test_snapshot_paths_hang_off_the_configured_root(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)

        assert log.root == tmp_path
        assert log.log_path.parent == tmp_path
        assert log.blob_path("ab" + "c" * 62).parent == tmp_path / "blobs" / "ab"

    def test_append_creates_missing_nested_parent_directories(self, tmp_path: Path) -> None:
        """The root itself may not exist yet — only its own parents (mkdir
        without ``parents=True`` would stop at the first missing level)."""
        log = make_log(tmp_path / "a" / "b" / "c")

        log.append(
            FetchFailure(
                authority_id="9999",
                url="https://example.invalid/f",
                observed_at=OBSERVED_AT,
                provenance=provenance(),
                outcome="timeout",
            ),
        )

        assert log.log_path.exists()

    def test_append_writes_with_explicit_utf8_encoding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = make_log(tmp_path)
        captured: dict[str, object] = {}
        original_open = Path.open

        def spy_open(self: Path, *args: object, **kwargs: object) -> object:
            captured["encoding"] = kwargs.get("encoding")
            return original_open(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "open", spy_open)

        log.append(
            FetchFailure(
                authority_id="9999",
                url="https://example.invalid/f",
                observed_at=OBSERVED_AT,
                provenance=provenance(),
                outcome="timeout",
            ),
        )

        assert captured["encoding"] == "utf-8"

    def test_blank_line_in_the_middle_does_not_truncate_the_log(self, tmp_path: Path) -> None:
        """A blank line must be skipped, not treated as the end of the log."""
        log = make_log(tmp_path)
        log.append_artifact(observation(b"a"), b"a")
        with log.log_path.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        log.append_artifact(observation(b"b", url="https://example.invalid/2"), b"b")

        assert len(list(log.records())) == 2

    def test_records_reads_with_explicit_utf8_encoding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = make_log(tmp_path)
        log.append_artifact(observation(b"a"), b"a")
        captured: dict[str, object] = {}
        original_open = Path.open

        def spy_open(self: Path, *args: object, **kwargs: object) -> object:
            captured["encoding"] = kwargs.get("encoding")
            return original_open(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "open", spy_open)

        list(log.records())

        assert captured["encoding"] == "utf-8"


class TestHashIntegrityAtTheDoor:
    def test_payload_that_contradicts_the_record_is_refused(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)

        with pytest.raises(LogIntegrityError, match="hashes to"):
            log.append_artifact(observation(b"declared"), b"actual")

    def test_refused_payload_writes_neither_blob_nor_line(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)

        with pytest.raises(LogIntegrityError):
            log.append_artifact(observation(b"declared"), b"actual")

        assert not log.log_path.exists()
        assert not (tmp_path / "blobs").exists()

    def test_stored_bytes_are_returned_verbatim(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        payload = b"\x00\xff not text \xc3\xb8"
        record = observation(payload)
        log.append_artifact(record, payload)

        assert log.read_blob(record.sha256) == payload

    def test_reading_an_absent_blob_raises_file_not_found(self, tmp_path: Path) -> None:
        """Absence may be a tombstoned removal, not corruption — the caller decides
        what it means, but the log must not mask it as anything else."""
        log = make_log(tmp_path)

        with pytest.raises(FileNotFoundError):
            log.read_blob("a" * 64)


class TestTornOrUnknownLines:
    def test_truncated_line_fails_loudly(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append_artifact(observation(b"a"), b"a")
        with log.log_path.open("a", encoding="utf-8") as handle:
            handle.write('{"kind":"artifact","url":"https://exa\n')

        with pytest.raises(LogIntegrityError, match=":2:"):
            list(log.records())

    def test_unrecognised_record_shape_fails_loudly(self, tmp_path: Path) -> None:
        """Skipping it would shrink the evidence silently — the one forbidden outcome."""
        log = make_log(tmp_path)
        with log.log_path.open("a", encoding="utf-8") as handle:
            handle.write('{"kind":"promotion","authority_id":"9999"}\n')

        with pytest.raises(LogIntegrityError):
            list(log.records())

    def test_blank_lines_are_tolerated(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append_artifact(observation(b"a"), b"a")
        with log.log_path.open("a", encoding="utf-8") as handle:
            handle.write("\n")

        assert len(list(log.records())) == 1


class TestSnapshotVerification:
    def test_intact_snapshot_verifies(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append_artifact(observation(b"a"), b"a")
        log.append_artifact(observation(b"b", url="https://example.invalid/2"), b"b")

        report = verify_snapshot(log)

        assert report.ok
        assert report.artifacts_checked == 2

    def test_blob_deleted_without_a_tombstone_is_reported(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        record = observation(b"a")
        log.append_artifact(record, b"a")
        log.blob_path(record.sha256).unlink()

        report = verify_snapshot(log)

        assert not report.ok
        assert report.missing_blobs == (record.sha256,)

    def test_tombstoned_removal_keeps_the_snapshot_valid(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        record = observation(b"a")
        log.append_artifact(record, b"a")
        log.blob_path(record.sha256).unlink()
        log.append(
            Tombstone(
                sha256=record.sha256,
                removed_at=OBSERVED_AT,
                basis="privacy request from the source authority",
                authorised_by="project owner",
            ),
        )

        report = verify_snapshot(log)

        assert report.ok
        assert report.tombstoned == (record.sha256,)
        assert report.missing_blobs == ()

    def test_tombstone_history_is_kept_not_erased(self, tmp_path: Path) -> None:
        """The original observation stays in the log; only the bytes go."""
        log = make_log(tmp_path)
        record = observation(b"a")
        log.append_artifact(record, b"a")
        log.blob_path(record.sha256).unlink()
        log.append(
            Tombstone(
                sha256=record.sha256,
                removed_at=OBSERVED_AT,
                basis="legal demand",
                authorised_by="project owner",
            ),
        )

        kinds = [item.kind for item in log.records()]

        assert kinds == ["artifact", "tombstone"]

    def test_altered_blob_is_caught_by_rehashing(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        record = observation(b"a")
        log.append_artifact(record, b"a")
        log.blob_path(record.sha256).write_bytes(b"tampered")

        report = verify_snapshot(log)

        assert not report.ok
        assert report.hash_mismatches == (record.sha256,)

    def test_tombstone_whose_blob_is_still_present_is_reported(self, tmp_path: Path) -> None:
        """A recorded removal that never happened is its own integrity signal."""
        log = make_log(tmp_path)
        record = observation(b"a")
        log.append_artifact(record, b"a")
        log.append(
            Tombstone(
                sha256=record.sha256,
                removed_at=OBSERVED_AT,
                basis="privacy request",
                authorised_by="project owner",
            ),
        )

        report = verify_snapshot(log)

        assert not report.ok
        assert report.unremoved_tombstones == (record.sha256,)

    def test_fetch_failures_are_not_counted_as_artifacts(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append(
            FetchFailure(
                authority_id="9999",
                url="https://example.invalid/missing",
                observed_at=OBSERVED_AT,
                provenance=provenance(),
                outcome="http_error",
                http_status=503,
            ),
        )

        report = verify_snapshot(log)

        assert report.ok
        assert report.artifacts_checked == 0


class TestTombstoneIsNotSilentlyReversed:
    def test_recapture_of_tombstoned_bytes_is_refused(self, tmp_path: Path) -> None:
        """The source still serves them; putting them back must be deliberate."""
        log = make_log(tmp_path)
        record = observation(b"a")
        log.append_artifact(record, b"a")
        log.blob_path(record.sha256).unlink()
        log.append(
            Tombstone(
                sha256=record.sha256,
                removed_at=OBSERVED_AT,
                basis="privacy request",
                authorised_by="project owner",
            ),
        )

        with pytest.raises(TombstonedArtifactError, match="sanctioned removal"):
            log.append_artifact(observation(b"a"), b"a")

    def test_recapture_refusal_message_is_exact(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        record = observation(b"a")
        digest = record.sha256
        log.append_artifact(record, b"a")
        log.blob_path(digest).unlink()
        log.append(
            Tombstone(
                sha256=digest,
                removed_at=OBSERVED_AT,
                basis="privacy request",
                authorised_by="project owner",
            ),
        )

        with pytest.raises(TombstonedArtifactError) as exc_info:
            log.append_artifact(observation(b"a"), b"a")

        assert str(exc_info.value) == (
            f"{digest} was removed under a tombstone; re-storing it would reverse a "
            "sanctioned removal (ADR-0010 §7)"
        )

    def test_refused_recapture_restores_nothing(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        record = observation(b"a")
        log.append_artifact(record, b"a")
        log.blob_path(record.sha256).unlink()
        log.append(
            Tombstone(
                sha256=record.sha256,
                removed_at=OBSERVED_AT,
                basis="legal demand",
                authorised_by="project owner",
            ),
        )
        before = log.log_path.read_bytes()

        with pytest.raises(TombstonedArtifactError):
            log.append_artifact(observation(b"a"), b"a")

        assert not log.blob_path(record.sha256).exists()
        assert log.log_path.read_bytes() == before

    def test_different_bytes_from_the_same_source_still_capture(self, tmp_path: Path) -> None:
        """A tombstone retires specific bytes, not the endpoint."""
        log = make_log(tmp_path)
        retired = observation(b"a")
        log.append_artifact(retired, b"a")
        log.blob_path(retired.sha256).unlink()
        log.append(
            Tombstone(
                sha256=retired.sha256,
                removed_at=OBSERVED_AT,
                basis="privacy request",
                authorised_by="project owner",
            ),
        )

        log.append_artifact(observation(b"revised"), b"revised")

        assert verify_snapshot(log).ok


class TestOrphanAndUnexplainedRecords:
    def test_blob_with_no_log_record_fails_the_audit(self, tmp_path: Path) -> None:
        """A crash between writing bytes and appending the record leaves raw material
        with no provenance; walking only log-referenced hashes would never see it."""
        log = make_log(tmp_path)
        log.append_artifact(observation(b"a"), b"a")
        orphan = hashlib.sha256(b"orphan").hexdigest()
        path = log.blob_path(orphan)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"orphan")

        report = verify_snapshot(log)

        assert not report.ok
        assert report.orphan_blobs == (orphan,)

    def test_tombstone_for_never_observed_bytes_fails_the_audit(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        never = hashlib.sha256(b"never seen").hexdigest()
        log.append(
            Tombstone(
                sha256=never,
                removed_at=OBSERVED_AT,
                basis="privacy request",
                authorised_by="project owner",
            ),
        )

        report = verify_snapshot(log)

        assert not report.ok
        assert report.tombstones_without_observation == (never,)

    def test_observation_appended_after_its_tombstone_fails_the_audit(self, tmp_path: Path) -> None:
        """The API refuses this; a hand-edited log can still contain it."""
        log = make_log(tmp_path)
        record = observation(b"a")
        log.append_artifact(record, b"a")
        log.blob_path(record.sha256).unlink()
        log.append(
            Tombstone(
                sha256=record.sha256,
                removed_at=OBSERVED_AT,
                basis="legal demand",
                authorised_by="project owner",
            ),
        )
        log.append(record)

        report = verify_snapshot(log)

        assert not report.ok
        assert report.observations_after_tombstone == (record.sha256,)


class TestRootMustBeValidated:
    def test_log_rejects_an_unvalidated_path(self, tmp_path: Path) -> None:
        """A bare Path has not passed the §5 boundary check and is not accepted."""
        with pytest.raises(StorageBoundaryError, match="requires an ObservatoryRoot"):
            ObservationLog(tmp_path)  # type: ignore[arg-type]

    def test_unvalidated_argument_error_message_is_exact(self) -> None:
        with pytest.raises(StorageBoundaryError) as exc_info:
            ObservationLog(5)  # type: ignore[arg-type]

        assert str(exc_info.value) == (
            "ObservationLog requires an ObservatoryRoot, got int; "
            "resolve the root through observatory_root() so ADR-0010 §5 is checked"
        )

    def test_log_cannot_be_pointed_inside_the_engine_repository(self) -> None:
        """The boundary reaches the log because the log will not take a raw path."""
        with pytest.raises(StorageBoundaryError):
            ObservationLog(observatory_root({ENV_OBSERVATORY_ROOT: str(engine_root() / "data")}))


class TestReadingNeverStopsEarly:
    """The blank-line guard is also covered in TestAppendOnly; this adds the
    consequence a duplicate name would not: the audit must count records that
    sit after the blank line, not stop at it."""

    def test_verification_sees_records_after_a_blank_line(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append_artifact(observation(b"first", url="https://example.invalid/1"), b"first")
        with log.log_path.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        log.append_artifact(observation(b"second", url="https://example.invalid/2"), b"second")

        assert verify_snapshot(log).artifacts_checked == 2


class TestLogCreatesItsOwnRoot:
    def test_appending_a_record_creates_a_root_several_levels_deep(self, tmp_path: Path) -> None:
        """The observatory root normally does not exist before the first capture.

        Exercised through `append`, not `append_artifact`: the latter writes a blob
        first, which creates the root as a side effect and hides whether `append`
        can stand up its own directory."""
        log = ObservationLog(ObservatoryRoot(tmp_path / "a" / "b" / "observatory", []))

        log.append(
            FetchFailure(
                authority_id="9999",
                url="https://example.invalid/missing",
                observed_at=OBSERVED_AT,
                provenance=provenance(),
                outcome="timeout",
            ),
        )

        assert log.log_path.exists()
        assert len(list(log.records())) == 1

    def test_first_capture_creates_a_root_several_levels_deep(self, tmp_path: Path) -> None:
        log = ObservationLog(ObservatoryRoot(tmp_path / "c" / "d" / "observatory", []))

        log.append_artifact(observation(b"a"), b"a")

        assert log.log_path.exists()


class TestCrashRecovery:
    """A run cut short by a lost disk or lost power (ADR-0010 §5 puts the
    archive on storage that can go away mid-write). The audit has to answer
    "how bad is it?" precisely when the log is damaged."""

    def _torn(self, tmp_path: Path, tail: bytes) -> ObservationLog:
        """A log with one intact record and an unfinished write after it."""
        log = make_log(tmp_path)
        log.append_artifact(observation(b"first"), b"first")
        with log.log_path.open("ab") as handle:
            handle.write(tail)
        return log

    def test_an_unfinished_final_write_is_reported_not_raised(self, tmp_path: Path) -> None:
        line = make_log(tmp_path).log_path
        log = self._torn(tmp_path, b'{"kind":"artifact","authority_id":"99')
        assert line == log.log_path

        result = verify_snapshot(log)

        assert result.incomplete_final_record is True
        assert result.malformed_lines == ()
        assert result.ok is False

    def test_the_records_before_the_tear_are_still_counted(self, tmp_path: Path) -> None:
        log = self._torn(tmp_path, b'{"kind":"artifact","authority_id":"99')

        assert verify_snapshot(log).artifacts_checked == 1
        assert len(log.scan().records) == 1

    def test_a_write_cut_mid_character_is_still_readable_up_to_the_tear(
        self, tmp_path: Path
    ) -> None:
        """Norwegian text makes this the common case, not the exotic one: a
        tear inside 'æ' leaves bytes that are not valid UTF-8 at all, and
        reading the file as text would lose the intact records too."""
        log = self._torn(tmp_path, '{"name":"Bær'.encode()[:-1])

        result = verify_snapshot(log)

        assert result.incomplete_final_record is True
        assert result.artifacts_checked == 1

    def test_a_malformed_line_mid_file_is_not_a_torn_tail(self, tmp_path: Path) -> None:
        """An interrupted append can only ever damage the last line. Damage
        anywhere else is corruption, and calling it a crash would invite an
        operator to truncate a file whose problem is elsewhere."""
        log = make_log(tmp_path)
        log.append_artifact(observation(b"first"), b"first")
        log.append_artifact(observation(b"second", url="https://example.invalid/g"), b"second")
        lines = log.log_path.read_bytes().split(b"\n")
        log.log_path.write_bytes(b"\n".join([lines[0], b"{ truncated", lines[1], b""]))

        result = verify_snapshot(log)

        assert result.incomplete_final_record is False
        assert result.malformed_lines == (2,)
        assert result.ok is False

    def test_a_complete_but_invalid_last_line_is_corruption_not_a_tear(
        self, tmp_path: Path
    ) -> None:
        """The newline is what distinguishes them. A last line that is invalid
        yet properly terminated was written in full — so whatever damaged it
        was not an interrupted append, and truncating it would throw away a
        record the operator has not been told about."""
        log = make_log(tmp_path)
        log.append_artifact(observation(b"first"), b"first")
        with log.log_path.open("ab") as handle:
            handle.write(b"{ truncated\n")

        result = verify_snapshot(log)

        assert result.incomplete_final_record is False
        assert result.malformed_lines == (2,)

    def test_a_tear_after_earlier_corruption_is_still_a_tear(self, tmp_path: Path) -> None:
        """Both defects are real and they are different defects. Reporting only
        the first would let an operator truncate the tail and believe the log
        was whole."""
        log = make_log(tmp_path)
        log.append_artifact(observation(b"first"), b"first")
        log.append_artifact(observation(b"second", url="https://example.invalid/g"), b"second")
        lines = log.log_path.read_bytes().split(b"\n")
        log.log_path.write_bytes(
            b"\n".join([lines[0], b"{ corrupted", lines[1]]) + b'\n{"kind":"artif'
        )

        result = verify_snapshot(log)

        assert result.incomplete_final_record is True
        assert result.malformed_lines == (2,)

    def test_a_corrupted_byte_never_decodes_into_a_plausible_record(self, tmp_path: Path) -> None:
        """A lenient decode would substitute U+FFFD and hand back a record that
        parses — one whose URL is not the URL that was observed. A refused line
        is visible; a silently altered one reads as genuine forever."""
        log = make_log(tmp_path)
        log.append_artifact(observation(b"first"), b"first")
        raw = log.log_path.read_bytes()
        target = raw.index(b"example.invalid")
        log.log_path.write_bytes(raw[:target] + b"\xff" + raw[target + 1 :])

        scan = log.scan()

        assert scan.records == ()
        assert scan.malformed_lines == (1,)
        assert scan.incomplete_final_record is False

    def test_an_unreadable_log_suppresses_blob_findings(self, tmp_path: Path) -> None:
        """Every blob the unread lines account for would otherwise be reported
        as an orphan — a list of invented defects burying the real one."""
        log = self._torn(tmp_path, b'{"kind":"artifact","authority_id":"99')
        orphan = tmp_path / "blobs" / "aa" / ("aa" * 32)
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"unreferenced")

        result = verify_snapshot(log)

        assert result.orphan_blobs == ()
        assert result.missing_blobs == ()
        assert result.incomplete_final_record is True

    def test_an_unreadable_log_counts_only_artifacts_and_suppresses_tombstone_findings(
        self, tmp_path: Path
    ) -> None:
        """The blob-classification short-circuit is not orphan/missing-only:
        every field ``_classify_blobs`` and the tombstone bookkeeping would
        otherwise populate must stay at its default, and ``artifacts_checked``
        must count the ``ArtifactObservation`` before the tear, not the
        ``Tombstone`` that followed it."""
        log = make_log(tmp_path)
        record = observation(b"first")
        log.append_artifact(record, b"first")
        log.append(
            Tombstone(
                sha256=record.sha256,
                removed_at=OBSERVED_AT,
                basis="privacy request from the source authority",
                authorised_by="project owner",
            ),
        )
        with log.log_path.open("ab") as handle:
            handle.write(b'{"kind":"artifact","authority_id":"99')

        result = verify_snapshot(log)

        assert result.incomplete_final_record is True
        assert result.artifacts_checked == 1
        assert result.tombstoned == ()
        assert result.hash_mismatches == ()
        assert result.unremoved_tombstones == ()
        assert result.tombstones_without_observation == ()
        assert result.observations_after_tombstone == ()

    def test_an_intact_log_reports_no_damage(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append_artifact(observation(b"first"), b"first")

        result = verify_snapshot(log)

        assert result.ok is True
        assert result.incomplete_final_record is False
        assert result.malformed_lines == ()
        assert log.scan().complete is True

    def test_an_absent_log_scans_as_empty_and_intact(self, tmp_path: Path) -> None:
        scan = make_log(tmp_path).scan()

        assert scan.records == ()
        assert scan.complete is True

    def test_a_present_but_empty_log_file_scans_as_empty_and_intact(self, tmp_path: Path) -> None:
        """Distinct code path from the absent case: this exercises
        ``read_bytes()`` on a zero-byte file rather than the existence guard."""
        log = make_log(tmp_path)
        log.log_path.touch()

        scan = log.scan()

        assert scan.records == ()
        assert scan.complete is True

    def test_a_torn_write_with_no_prior_record_reports_zero_records(self, tmp_path: Path) -> None:
        """The crash can land on the very first append, not just a later one."""
        log = make_log(tmp_path)
        with log.log_path.open("ab") as handle:
            handle.write(b'{"kind":"artifact","authority_id":"99')

        result = verify_snapshot(log)

        assert result.incomplete_final_record is True
        assert result.malformed_lines == ()
        assert result.artifacts_checked == 0

    def test_a_complete_last_line_missing_only_its_trailing_newline_is_not_flagged(
        self, tmp_path: Path
    ) -> None:
        """The write is one call of json + "\\n"; a crash could in principle
        land between the two without touching the JSON bytes at all. Since the
        line still parses, it is data, not damage — the newline's absence
        alone must not trip ``incomplete_final_record``."""
        log = make_log(tmp_path)
        log.append_artifact(observation(b"first"), b"first")
        raw = log.log_path.read_bytes()
        assert raw.endswith(b"\n")
        log.log_path.write_bytes(raw[:-1])

        result = verify_snapshot(log)

        assert result.incomplete_final_record is False
        assert result.malformed_lines == ()
        assert result.artifacts_checked == 1
        assert result.ok is True

    def test_scan_is_incomplete_when_only_malformed_lines_exist(self, tmp_path: Path) -> None:
        """``complete`` must catch mid-file corruption too, not only a torn tail."""
        log = make_log(tmp_path)
        log.append_artifact(observation(b"first"), b"first")
        log.append_artifact(observation(b"second", url="https://example.invalid/g"), b"second")
        lines = log.log_path.read_bytes().split(b"\n")
        log.log_path.write_bytes(b"\n".join([lines[0], b"{ truncated", lines[1], b""]))

        assert log.scan().complete is False

    def test_reading_records_stays_strict(self, tmp_path: Path) -> None:
        """scan() is the tolerant reading, added for the audit. The evidence
        reader itself must keep refusing to read past a line it cannot parse."""
        log = self._torn(tmp_path, b'{"kind":"artifact","authority_id":"99')

        with pytest.raises(LogIntegrityError):
            list(log.records())


class TestDurability:
    def test_each_appended_record_is_fsynced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The window this closes is the one that produces a torn tail. On an
        archive that lives on removable storage, leaving the record in the OS
        cache is the difference between an interrupted run costing one fetch
        and costing the readability of the whole log."""
        synced: list[int] = []
        original_fsync = os.fsync

        def spy_fsync(fd: int) -> None:
            synced.append(fd)
            original_fsync(fd)

        monkeypatch.setattr(os, "fsync", spy_fsync)
        log = make_log(tmp_path)

        log.append_artifact(observation(b"first"), b"first")

        assert len(synced) == 1

    def test_the_write_is_flushed_before_fsync_is_called(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """fsync only pushes what the OS already holds. Without an explicit
        flush first, the just-written bytes could still be sitting in
        Python's own buffer, and fsync would durably persist nothing."""
        sizes_at_fsync: list[int] = []
        original_fsync = os.fsync

        def spy_fsync(fd: int) -> None:
            sizes_at_fsync.append(os.fstat(fd).st_size)
            original_fsync(fd)

        monkeypatch.setattr(os, "fsync", spy_fsync)
        log = make_log(tmp_path)

        log.append_artifact(observation(b"first"), b"first")

        assert sizes_at_fsync == [log.log_path.stat().st_size]
        assert sizes_at_fsync[0] > 0
