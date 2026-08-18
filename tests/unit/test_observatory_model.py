"""Tests for lovspor.observatory.model — the ADR-0010 capture model."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from lovspor.observatory.model import (
    ArtifactObservation,
    FetchFailure,
    ObservationRecord,
    RetrievalProvenance,
    Tombstone,
    record_to_json_line,
)

SHA = "a" * 64
OBSERVED_AT = datetime(2026, 8, 18, 6, 30, tzinfo=UTC)


def provenance() -> RetrievalProvenance:
    return RetrievalProvenance(
        adapter="generic-html",
        channel="http",
        discovery_method="sitemap",
        user_agent="lovspor-observatory/0.1 (+https://lovspor.no)",
        rate_limit_seconds=2.0,
    )


def artifact(**overrides: object) -> ArtifactObservation:
    fields: dict[str, object] = {
        "authority_id": "9999",
        "url": "https://example.invalid/forskrift.pdf",
        "observed_at": OBSERVED_AT,
        "provenance": provenance(),
        "sha256": SHA,
        "content_type": "application/pdf",
        "http_status": 200,
    }
    fields.update(overrides)
    return ArtifactObservation.model_validate(fields)


class TestObservedAtIsAnAxis:
    def test_naive_timestamp_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware UTC"):
            artifact(observed_at=datetime(2026, 8, 18, 6, 30))

    def test_non_utc_offset_is_refused(self) -> None:
        oslo = timezone(timedelta(hours=2))
        with pytest.raises(ValidationError, match="must be UTC"):
            artifact(observed_at=datetime(2026, 8, 18, 8, 30, tzinfo=oslo))

    def test_tombstone_removal_time_is_also_utc_only(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware UTC"):
            Tombstone.model_validate(
                {
                    "sha256": SHA,
                    "removed_at": datetime(2026, 8, 18, 6, 30),
                    "basis": "privacy request",
                    "authorised_by": "project owner",
                },
            )


class TestMandatoryFields:
    @pytest.mark.parametrize(
        "missing",
        [
            "authority_id",
            "url",
            "observed_at",
            "provenance",
            "sha256",
            "content_type",
            "http_status",
        ],
    )
    def test_artifact_observation_requires_every_capture_field(self, missing: str) -> None:
        fields = {
            "authority_id": "9999",
            "url": "https://example.invalid/f.pdf",
            "observed_at": OBSERVED_AT,
            "provenance": provenance().model_dump(),
            "sha256": SHA,
            "content_type": "application/pdf",
            "http_status": 200,
        }
        del fields[missing]

        with pytest.raises(ValidationError):
            ArtifactObservation.model_validate(fields)

    def test_fetch_failure_requires_an_outcome_but_no_hash(self) -> None:
        failure = FetchFailure(
            authority_id="9999",
            url="https://example.invalid/missing",
            observed_at=OBSERVED_AT,
            provenance=provenance(),
            outcome="timeout",
        )

        assert failure.http_status is None
        assert not hasattr(failure, "sha256")

    def test_fetch_failure_without_outcome_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            FetchFailure.model_validate(
                {
                    "authority_id": "9999",
                    "url": "https://example.invalid/missing",
                    "observed_at": OBSERVED_AT,
                    "provenance": provenance().model_dump(),
                },
            )

    def test_politeness_parameters_travel_with_the_record(self) -> None:
        record = artifact()

        assert record.provenance.rate_limit_seconds == 2.0
        assert record.provenance.user_agent.startswith("lovspor-observatory/")

    def test_non_positive_rate_limit_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            RetrievalProvenance(
                adapter="generic-html",
                channel="http",
                discovery_method="sitemap",
                user_agent="ua",
                rate_limit_seconds=0,
            )

    @pytest.mark.parametrize("bad", ["", "xyz", "A" * 64, "a" * 63])
    def test_malformed_sha256_is_refused(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            artifact(sha256=bad)


class TestRecordsAreImmutable:
    def test_an_observation_cannot_be_edited_in_place(self) -> None:
        record = artifact()

        with pytest.raises(ValidationError):
            record.sha256 = "b" * 64  # type: ignore[misc]


class TestDiscriminatedUnion:
    def test_artifact_kind_selects_the_artifact_type(self) -> None:
        parsed = TypeAdapter(ObservationRecord).validate_python(
            {
                "kind": "artifact",
                "authority_id": "9999",
                "url": "https://example.invalid/f",
                "observed_at": OBSERVED_AT,
                "provenance": provenance().model_dump(),
                "sha256": SHA,
                "content_type": "text/html",
                "http_status": 200,
            },
        )

        assert isinstance(parsed, ArtifactObservation)

    def test_failure_kind_selects_the_failure_type(self) -> None:
        parsed = TypeAdapter(ObservationRecord).validate_python(
            {
                "kind": "fetch_failure",
                "authority_id": "9999",
                "url": "https://example.invalid/f",
                "observed_at": OBSERVED_AT,
                "provenance": provenance().model_dump(),
                "outcome": "timeout",
            },
        )

        assert isinstance(parsed, FetchFailure)

    def test_a_failure_carrying_artifact_fields_is_refused(self) -> None:
        """The two kinds are not interchangeable shapes with optional halves."""
        with pytest.raises(ValidationError):
            TypeAdapter(ObservationRecord).validate_python(
                {
                    "kind": "fetch_failure",
                    "authority_id": "9999",
                    "url": "https://example.invalid/f",
                    "observed_at": OBSERVED_AT,
                    "provenance": provenance().model_dump(),
                    "outcome": "timeout",
                    "sha256": SHA,
                },
            )

    def test_unknown_kind_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            TypeAdapter(ObservationRecord).validate_python({"kind": "promotion"})


class TestDeterministicSerialisation:
    def test_same_record_serialises_byte_identically(self) -> None:
        assert record_to_json_line(artifact()) == record_to_json_line(artifact())

    def test_field_order_does_not_change_the_line(self) -> None:
        forward = artifact()
        reordered = ArtifactObservation.model_validate(
            {
                "http_status": 200,
                "content_type": "application/pdf",
                "sha256": SHA,
                "provenance": provenance().model_dump(),
                "observed_at": OBSERVED_AT,
                "url": "https://example.invalid/forskrift.pdf",
                "authority_id": "9999",
            },
        )

        assert record_to_json_line(forward) == record_to_json_line(reordered)

    def test_line_is_single_line_and_keeps_norwegian_characters(self) -> None:
        line = record_to_json_line(artifact(url="https://example.invalid/høring-æøå"))

        assert "\n" not in line
        assert "høring-æøå" in line


class TestProvenanceCompleteness:
    def test_channel_is_required_and_distinct_from_discovery_method(self) -> None:
        """ADR-0010 §2 lists channel; how bytes arrived is not how the URL was found."""
        with pytest.raises(ValidationError):
            RetrievalProvenance(
                adapter="generic-html",
                discovery_method="sitemap",
                user_agent="ua",
                rate_limit_seconds=2.0,
            )

    def test_channel_and_discovery_method_are_recorded_separately(self) -> None:
        record = artifact()

        assert record.provenance.channel == "http"
        assert record.provenance.discovery_method == "sitemap"


class TestEvidenceSchemaIsClosed:
    def test_unknown_field_on_an_observation_is_refused(self) -> None:
        """Silently dropping part of an evidence record is the failure mode here."""
        with pytest.raises(ValidationError):
            artifact(classification="forskrift")

    def test_unknown_field_on_a_tombstone_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            Tombstone.model_validate(
                {
                    "sha256": SHA,
                    "removed_at": OBSERVED_AT,
                    "basis": "privacy request",
                    "authorised_by": "project owner",
                    "reinstated": True,
                },
            )
