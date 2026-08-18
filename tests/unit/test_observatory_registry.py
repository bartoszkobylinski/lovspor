"""Tests for lovspor.observatory.registry — eligibility is not activation."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.errors import ParseError, SourceNotActivatedError
from lovspor.observatory.registry import (
    AccessPolicyCheck,
    SourceRecord,
    SourceRegistry,
    activate,
    read_registry,
    require_active,
    write_registry,
)

CHECKED_AT = datetime(2026, 8, 18, 6, 0, tzinfo=UTC)


def check(**overrides: object) -> AccessPolicyCheck:
    fields: dict[str, object] = {
        "checked_at": CHECKED_AT,
        "robots_txt_url": "https://example.invalid/robots.txt",
        "robots_allows": True,
        "terms_reviewed": True,
        "rate_limit_seconds": 2.0,
        "user_agent": "lovspor-observatory/0.1",
        "reviewed_by": "project owner",
    }
    fields.update(overrides)
    return AccessPolicyCheck.model_validate(fields)


def eligible_source() -> SourceRecord:
    return SourceRecord(
        authority_type="kommune",
        authority_id="9999",
        name="Testby",
        canonical_domain="testby.example.invalid",
    )


class TestEligibilityIsNotActivation:
    def test_a_registered_source_starts_inactive(self) -> None:
        assert eligible_source().active is False

    def test_activation_records_the_access_policy_check(self) -> None:
        activated = activate(eligible_source(), check())

        assert activated.active is True
        assert activated.access_policy == check()

    def test_robots_disallow_blocks_activation(self) -> None:
        with pytest.raises(SourceNotActivatedError, match="robots_allows=False"):
            activate(eligible_source(), check(robots_allows=False))

    def test_unreviewed_terms_block_activation(self) -> None:
        with pytest.raises(SourceNotActivatedError, match="terms_reviewed=False"):
            activate(eligible_source(), check(terms_reviewed=False))

    def test_activation_leaves_the_original_record_untouched(self) -> None:
        source = eligible_source()

        activate(source, check())

        assert source.active is False


class TestActivationCannotBeForged:
    def test_active_without_any_check_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="ADR-0010"):
            SourceRecord(
                authority_type="kommune",
                authority_id="9999",
                name="Testby",
                canonical_domain="testby.example.invalid",
                active=True,
            )

    def test_active_with_a_disallowing_check_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="ADR-0010"):
            SourceRecord(
                authority_type="kommune",
                authority_id="9999",
                name="Testby",
                canonical_domain="testby.example.invalid",
                access_policy=check(robots_allows=False),
                active=True,
            )

    def test_hand_edited_registry_file_gets_the_same_gate(self, tmp_path: Path) -> None:
        """Activation must not be grantable by editing JSON."""
        path = tmp_path / "registry.json"
        path.write_text(
            '{"version": 1, "sources": {"9999": {"authority_type": "kommune", '
            '"authority_id": "9999", "name": "Testby", '
            '"canonical_domain": "testby.example.invalid", "active": true}}}',
            encoding="utf-8",
        )

        with pytest.raises(ParseError, match="invalid source registry"):
            read_registry(path)


class TestCaptureGate:
    def test_active_source_passes(self) -> None:
        registry = SourceRegistry(sources={"9999": activate(eligible_source(), check())})

        assert require_active(registry, "9999").authority_id == "9999"

    def test_inactive_source_is_refused(self) -> None:
        registry = SourceRegistry(sources={"9999": eligible_source()})

        with pytest.raises(SourceNotActivatedError, match="no recorded access-policy check"):
            require_active(registry, "9999")

    def test_unknown_authority_is_refused(self) -> None:
        with pytest.raises(SourceNotActivatedError, match="not in the source registry"):
            require_active(SourceRegistry(), "9999")

    def test_active_listing_excludes_inactive_sources(self) -> None:
        other = SourceRecord(
            authority_type="fylkeskommune",
            authority_id="88",
            name="Testfylke",
            canonical_domain="testfylke.example.invalid",
        )
        registry = SourceRegistry(
            sources={"9999": activate(eligible_source(), check()), "88": other},
        )

        assert [record.authority_id for record in registry.active()] == ["9999"]


class TestRegistryFile:
    def test_roundtrip_preserves_the_record(self, tmp_path: Path) -> None:
        path = tmp_path / "registry.json"
        registry = SourceRegistry(sources={"9999": activate(eligible_source(), check())})

        write_registry(registry, path)

        assert read_registry(path) == registry

    def test_write_is_deterministic(self, tmp_path: Path) -> None:
        registry = SourceRegistry(sources={"9999": activate(eligible_source(), check())})
        first = tmp_path / "a.json"
        second = tmp_path / "b.json"

        write_registry(registry, first)
        write_registry(registry, second)

        assert first.read_bytes() == second.read_bytes()

    def test_key_must_match_the_authority_id(self) -> None:
        with pytest.raises(ValidationError, match="does not match authority_id"):
            SourceRegistry(sources={"0000": eligible_source()})

    def test_malformed_json_is_a_parse_error(self, tmp_path: Path) -> None:
        path = tmp_path / "registry.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(ParseError, match="unreadable source registry"):
            read_registry(path)

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_registry(tmp_path / "absent.json")
