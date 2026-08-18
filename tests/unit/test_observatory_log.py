"""Tests for lovspor.observatory.log — append-only evidence and its audit."""

import hashlib
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
