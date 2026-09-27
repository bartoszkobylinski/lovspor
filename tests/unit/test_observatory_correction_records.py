"""The two correction record kinds (ADR-0015 §3, §4) and the record key (§2)."""

import hashlib
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from lovspor.observatory.model import (
    ArtifactObservation,
    Correction,
    FetchFailure,
    ObservationRecord,
    RecordTombstone,
    RefiledObservation,
    RetrievalProvenance,
    record_key,
    record_to_json_line,
)

KEY = "b" * 64
CORRECTED_AT = datetime(2026, 9, 27, 8, 0, tzinfo=UTC)
ADAPTER: TypeAdapter[ObservationRecord] = TypeAdapter(ObservationRecord)


def _artifact(authority_id: str = "4203") -> ArtifactObservation:
    return ArtifactObservation(
        authority_id=authority_id,
        url="https://www.arendal.kommune.no/forskrift",
        observed_at=datetime(2026, 8, 24, 10, 42, tzinfo=UTC),
        provenance=RetrievalProvenance(
            adapter="http",
            channel="http",
            discovery_method="sitemap",
            user_agent="lovspor-observatory/0.1",
            rate_limit_seconds=7.0,
        ),
        sha256="a" * 64,
        content_type="text/html",
        http_status=200,
    )


def _correction(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "supersedes": KEY,
        "correction_id": "c1",
        "corrected_fields": ["authority_id"],
        "previous_values": {"authority_id": "4202"},
        "reason": "misattributed, #221",
        "corrected_by": "owner",
        "corrected_at": CORRECTED_AT,
    }
    fields.update(overrides)
    return fields


def _tombstone(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "kind": "record_tombstone",
        "retracts": KEY,
        "correction_id": "c1",
        "reason": "misattributed, #221",
        "corrected_by": "owner",
        "corrected_at": CORRECTED_AT,
    }
    fields.update(overrides)
    return fields


class TestRecordTombstone:
    def test_parses_through_the_record_union(self) -> None:
        record = ADAPTER.validate_python(_tombstone())
        assert isinstance(record, RecordTombstone)
        assert record.retracts == KEY

    @pytest.mark.parametrize("field", ["reason", "corrected_by", "correction_id"])
    def test_blank_attribution_is_refused(self, field: str) -> None:
        with pytest.raises(ValidationError, match="must not be blank"):
            RecordTombstone.model_validate(_tombstone(**{field: "   "}))

    def test_retracts_must_be_a_sha256(self) -> None:
        with pytest.raises(ValidationError):
            RecordTombstone.model_validate(_tombstone(retracts="not-a-hash"))

    def test_naive_correction_time_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware UTC"):
            RecordTombstone.model_validate(_tombstone(corrected_at=datetime(2026, 9, 27)))

    def test_non_utc_correction_time_is_refused(self) -> None:
        oslo = datetime(2026, 9, 27, 10, 0, tzinfo=timezone(timedelta(hours=2)))
        with pytest.raises(ValidationError, match="must be UTC"):
            RecordTombstone.model_validate(_tombstone(corrected_at=oslo))

    def test_unknown_field_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            RecordTombstone.model_validate(_tombstone(extra="x"))


class TestRefiledObservation:
    def test_parses_through_the_record_union_with_its_inner_kind(self) -> None:
        refiled = RefiledObservation(
            observation=_artifact(), correction=Correction.model_validate(_correction())
        )
        record = ADAPTER.validate_json(record_to_json_line(refiled))
        assert isinstance(record, RefiledObservation)
        assert isinstance(record.observation, ArtifactObservation)
        assert record == refiled

    def test_a_failure_can_be_refiled(self) -> None:
        failure = FetchFailure(
            authority_id="4203",
            url="https://www.arendal.kommune.no/x",
            observed_at=datetime(2026, 8, 24, 10, 42, tzinfo=UTC),
            provenance=_artifact().provenance,
            outcome="http_404",
        )
        refiled = RefiledObservation(
            observation=failure, correction=Correction.model_validate(_correction())
        )
        assert isinstance(ADAPTER.validate_json(record_to_json_line(refiled)), RefiledObservation)

    def test_only_authority_id_is_correctable(self) -> None:
        with pytest.raises(ValidationError):
            Correction.model_validate(
                _correction(corrected_fields=["url"], previous_values={"url": "x"})
            )

    def test_corrected_fields_must_not_be_empty(self) -> None:
        with pytest.raises(ValidationError):
            Correction.model_validate(_correction(corrected_fields=[]))

    def test_previous_values_must_name_exactly_the_corrected_fields(self) -> None:
        with pytest.raises(ValidationError, match="previous_values"):
            Correction.model_validate(_correction(previous_values={}))

    @pytest.mark.parametrize("field", ["reason", "corrected_by", "correction_id"])
    def test_blank_attribution_is_refused(self, field: str) -> None:
        with pytest.raises(ValidationError, match="must not be blank"):
            Correction.model_validate(_correction(**{field: " "}))

    def test_blank_previous_value_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="must not be blank"):
            Correction.model_validate(_correction(previous_values={"authority_id": " "}))

    def test_naive_correction_time_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware UTC"):
            Correction.model_validate(_correction(corrected_at=datetime(2026, 9, 27)))

    def test_a_refiled_record_cannot_nest_a_correction(self) -> None:
        with pytest.raises(ValidationError):
            RefiledObservation.model_validate(
                {"observation": _tombstone(), "correction": _correction()}
            )


class TestRecordKey:
    def test_is_the_sha256_of_the_line_without_its_newline(self) -> None:
        line = record_to_json_line(_artifact()).encode("utf-8")
        assert record_key(line) == hashlib.sha256(line).hexdigest()
        assert record_key(line + b"\n") == hashlib.sha256(line).hexdigest()

    def test_keys_the_bytes_as_stored_not_a_reserialisation(self) -> None:
        # A line written before `redirect_chain` existed re-serialises with it
        # added; the key must name the line in the log, not today's spelling.
        line = record_to_json_line(_artifact()).replace(',"redirect_chain":[]', "")
        assert record_key(line.encode("utf-8")) == hashlib.sha256(line.encode()).hexdigest()
        assert record_key(line.encode("utf-8")) != record_key(
            record_to_json_line(ADAPTER.validate_json(line)).encode("utf-8")
        )
