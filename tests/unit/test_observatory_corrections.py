"""The corrected view of the observation log (ADR-0015 §5, §6)."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lovspor.observatory.corrections import CorrectionSet, fold_corrections
from lovspor.observatory.log import ObservationLog
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
