"""Tests for lovspor.observatory.log — append-only evidence and its audit."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.errors import LogIntegrityError
from lovspor.observatory.log import ObservationLog, verify_snapshot
from lovspor.observatory.model import (
    ArtifactObservation,
    FetchFailure,
    RetrievalProvenance,
    Tombstone,
)

OBSERVED_AT = datetime(2026, 8, 18, 6, 30, tzinfo=UTC)


def provenance() -> RetrievalProvenance:
    return RetrievalProvenance(
        adapter="generic-html",
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
        log = ObservationLog(tmp_path)
        log.append_artifact(observation(b"first"), b"first")
        before = log.log_path.read_bytes()

        log.append_artifact(observation(b"second"), b"second")

        assert log.log_path.read_bytes().startswith(before)

    def test_recrawl_of_unchanged_content_appends_a_second_record(self, tmp_path: Path) -> None:
        """A re-crawl is always an addition: two records, one deduplicated blob."""
        log = ObservationLog(tmp_path)
        payload = b"unchanged"
        log.append_artifact(observation(payload), payload)
        log.append_artifact(observation(payload), payload)

        records = list(log.records())
        blobs = list((tmp_path / "blobs").rglob("*"))

        assert len(records) == 2
        assert [path for path in blobs if path.is_file()] == [log.blob_path(records[0].sha256)]

    def test_records_read_back_in_append_order(self, tmp_path: Path) -> None:
        log = ObservationLog(tmp_path)
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
        assert list(ObservationLog(tmp_path).records()) == []


class TestHashIntegrityAtTheDoor:
    def test_payload_that_contradicts_the_record_is_refused(self, tmp_path: Path) -> None:
        log = ObservationLog(tmp_path)

        with pytest.raises(LogIntegrityError, match="hashes to"):
            log.append_artifact(observation(b"declared"), b"actual")

    def test_refused_payload_writes_neither_blob_nor_line(self, tmp_path: Path) -> None:
        log = ObservationLog(tmp_path)

        with pytest.raises(LogIntegrityError):
            log.append_artifact(observation(b"declared"), b"actual")

        assert not log.log_path.exists()
        assert not (tmp_path / "blobs").exists()

    def test_stored_bytes_are_returned_verbatim(self, tmp_path: Path) -> None:
        log = ObservationLog(tmp_path)
        payload = b"\x00\xff not text \xc3\xb8"
        record = observation(payload)
        log.append_artifact(record, payload)

        assert log.read_blob(record.sha256) == payload


class TestTornOrUnknownLines:
    def test_truncated_line_fails_loudly(self, tmp_path: Path) -> None:
        log = ObservationLog(tmp_path)
        log.append_artifact(observation(b"a"), b"a")
        with log.log_path.open("a", encoding="utf-8") as handle:
            handle.write('{"kind":"artifact","url":"https://exa\n')

        with pytest.raises(LogIntegrityError, match=":2:"):
            list(log.records())

    def test_unrecognised_record_shape_fails_loudly(self, tmp_path: Path) -> None:
        """Skipping it would shrink the evidence silently — the one forbidden outcome."""
        log = ObservationLog(tmp_path)
        with log.log_path.open("a", encoding="utf-8") as handle:
            handle.write('{"kind":"promotion","authority_id":"9999"}\n')

        with pytest.raises(LogIntegrityError):
            list(log.records())

    def test_blank_lines_are_tolerated(self, tmp_path: Path) -> None:
        log = ObservationLog(tmp_path)
        log.append_artifact(observation(b"a"), b"a")
        with log.log_path.open("a", encoding="utf-8") as handle:
            handle.write("\n")

        assert len(list(log.records())) == 1


class TestSnapshotVerification:
    def test_intact_snapshot_verifies(self, tmp_path: Path) -> None:
        log = ObservationLog(tmp_path)
        log.append_artifact(observation(b"a"), b"a")
        log.append_artifact(observation(b"b", url="https://example.invalid/2"), b"b")

        report = verify_snapshot(log)

        assert report.ok
        assert report.artifacts_checked == 2

    def test_blob_deleted_without_a_tombstone_is_reported(self, tmp_path: Path) -> None:
        log = ObservationLog(tmp_path)
        record = observation(b"a")
        log.append_artifact(record, b"a")
        log.blob_path(record.sha256).unlink()

        report = verify_snapshot(log)

        assert not report.ok
        assert report.missing_blobs == (record.sha256,)

    def test_tombstoned_removal_keeps_the_snapshot_valid(self, tmp_path: Path) -> None:
        log = ObservationLog(tmp_path)
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
        log = ObservationLog(tmp_path)
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
        log = ObservationLog(tmp_path)
        record = observation(b"a")
        log.append_artifact(record, b"a")
        log.blob_path(record.sha256).write_bytes(b"tampered")

        report = verify_snapshot(log)

        assert not report.ok
        assert report.hash_mismatches == (record.sha256,)

    def test_tombstone_whose_blob_is_still_present_is_reported(self, tmp_path: Path) -> None:
        """A recorded removal that never happened is its own integrity signal."""
        log = ObservationLog(tmp_path)
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
        log = ObservationLog(tmp_path)
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
