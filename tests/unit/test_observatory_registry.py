"""Tests for lovspor.observatory.registry — eligibility is not activation."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.errors import ParseError, SourceNotActivatedError
from lovspor.observatory.registry import (
    AccessPolicyCheck,
    SourceRecord,
    SourceRegistry,
    _host_matches,
    activate,
    authorise_capture,
    capture_host,
    read_access_policy_check,
    read_registry,
    registry_path,
    write_registry,
)
from lovspor.observatory.storage import ObservatoryRoot

CHECKED_AT = datetime(2026, 8, 18, 6, 0, tzinfo=UTC)


def check(**overrides: object) -> AccessPolicyCheck:
    fields: dict[str, object] = {
        "checked_at": CHECKED_AT,
        "robots_txt_url": "https://example.invalid/robots.txt",
        "robots_allows": True,
        "terms_reviewed": True,
        "terms_permit_capture": True,
        "rate_limit_seconds": 2.0,
        "user_agent": "lovspor-observatory/0.1",
        "reviewed_by": "project owner",
    }
    fields.update(overrides)
    return AccessPolicyCheck.model_validate(fields)


def check_document(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "checked_at": "2026-08-18T06:00:00Z",
        "robots_txt_url": "https://example.invalid/robots.txt",
        "robots_allows": True,
        "terms_reviewed": True,
        "terms_permit_capture": True,
        "rate_limit_seconds": 2.0,
        "user_agent": "lovspor-observatory/0.1",
        "reviewed_by": "project owner",
    }
    fields.update(overrides)
    return fields


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

    def test_activation_leaves_the_original_record_untouched(self) -> None:
        source = eligible_source()

        activate(source, check())

        assert source.active is False


class TestListingEntryPointsStayWithinTheClearedDomain:
    def test_a_listing_on_the_canonical_domain_or_a_subdomain_is_accepted(self) -> None:
        source = SourceRecord(
            authority_type="kommune",
            authority_id="9999",
            name="Testby",
            canonical_domain="testby.example.invalid",
            listing_entry_points=(
                "https://testby.example.invalid/notices",
                "https://www.testby.example.invalid/hearings",
            ),
        )

        assert len(source.listing_entry_points) == 2

    @pytest.mark.parametrize(
        "url",
        [
            "https://other.example.invalid/notices",
            "https://testby.example.invalid.evil.invalid/notices",
            "https:///notices",
        ],
    )
    def test_an_off_domain_or_hostless_listing_is_refused(self, url: str) -> None:
        with pytest.raises(ValidationError, match="entry points outside"):
            SourceRecord(
                authority_type="kommune",
                authority_id="9999",
                name="Testby",
                canonical_domain="testby.example.invalid",
                listing_entry_points=(url,),
            )


class TestAccessPolicyCheckFieldConstraints:
    @pytest.mark.parametrize("value", [0, -1.0])
    def test_non_positive_rate_limit_is_refused(self, value: float) -> None:
        with pytest.raises(ValidationError):
            check(rate_limit_seconds=value)

    def test_unattributed_check_is_refused(self) -> None:
        """ "reviewed_by" is mandatory: an unattributed check is not evidence
        that anyone looked."""
        with pytest.raises(ValidationError):
            check(reviewed_by="")


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
    def test_activated_domain_passes(self) -> None:
        registry = SourceRegistry(sources={"9999": activate(eligible_source(), check())})

        record = authorise_capture(registry, "https://testby.example.invalid/forskrift")

        assert record.authority_id == "9999"

    def test_subdomain_of_an_activated_domain_passes(self) -> None:
        registry = SourceRegistry(sources={"9999": activate(eligible_source(), check())})

        assert authorise_capture(registry, "https://www.testby.example.invalid/f").active

    def test_activation_does_not_extend_to_another_host(self) -> None:
        """Clearing an authority is not a licence to fetch anything on its behalf."""
        registry = SourceRegistry(sources={"9999": activate(eligible_source(), check())})

        with pytest.raises(SourceNotActivatedError, match="no registered source covers"):
            authorise_capture(registry, "https://elsewhere.example.invalid/f")

    @pytest.mark.parametrize(
        "url",
        [
            "https://lovdata.no/register/lokaleForskrifter",
            "https://www.lovdata.no/dokument/SF/forskrift",
            "http://LOVDATA.NO/x",
        ],
    )
    def test_lovdata_is_globally_denied(self, url: str) -> None:
        """ADR-0010 §4 forbids it outright; registering the host must not unlock it."""
        denied = SourceRecord(
            authority_type="kommune",
            authority_id="9999",
            name="Testby",
            canonical_domain="lovdata.no",
        )
        registry = SourceRegistry(sources={"9999": activate(denied, check())})

        with pytest.raises(SourceNotActivatedError, match="globally denied"):
            authorise_capture(registry, url)

    def test_lookalike_host_does_not_match_an_activated_domain(self) -> None:
        """Label-wise matching: nottestby.example.invalid is not a subdomain."""
        registry = SourceRegistry(sources={"9999": activate(eligible_source(), check())})

        with pytest.raises(SourceNotActivatedError):
            authorise_capture(registry, "https://nottestby.example.invalid/f")

    def test_suffix_lookalike_does_not_match_either(self) -> None:
        registry = SourceRegistry(sources={"9999": activate(eligible_source(), check())})

        with pytest.raises(SourceNotActivatedError):
            authorise_capture(registry, "https://testby.example.invalid.evil.example/f")

    def test_trailing_dot_on_the_host_is_stripped_before_comparison(self) -> None:
        assert _host_matches("baerum.no.", "baerum.no") is True

    def test_trailing_dot_on_the_domain_is_stripped_before_comparison(self) -> None:
        assert _host_matches("baerum.no", "baerum.no.") is True

    def test_inactive_source_is_refused(self) -> None:
        registry = SourceRegistry(sources={"9999": eligible_source()})

        with pytest.raises(SourceNotActivatedError, match="no recorded access-policy check"):
            authorise_capture(registry, "https://testby.example.invalid/f")

    def test_url_without_a_host_is_refused(self) -> None:
        with pytest.raises(SourceNotActivatedError, match="no host"):
            authorise_capture(SourceRegistry(), "not-a-url")

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


class TestCaptureHost:
    """capture_host() is now the one place both authorise_capture and the
    fetcher's rate-limit key ask for a URL's host — its own return value and
    failure mode need direct coverage, not just through those two callers."""

    def test_returns_the_hostname(self) -> None:
        assert capture_host("https://baerum.kommune.no/forskrift") == "baerum.kommune.no"

    def test_strips_the_port(self) -> None:
        assert capture_host("https://baerum.kommune.no:8443/forskrift") == "baerum.kommune.no"

    def test_lowercases_the_host(self) -> None:
        assert capture_host("https://BAERUM.KOMMUNE.NO/forskrift") == "baerum.kommune.no"

    def test_ignores_the_path_and_query(self) -> None:
        """The rate limiter keys on this return value; if it varied with the
        path, two URLs on the same host would never contend for the same
        rate-limit bucket."""
        assert capture_host("https://baerum.kommune.no/a") == capture_host(
            "https://baerum.kommune.no/b?x=1"
        )

    def test_a_url_without_a_host_is_refused(self) -> None:
        with pytest.raises(SourceNotActivatedError, match="no host"):
            capture_host("not-a-url")


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

    def test_read_registry_decodes_with_explicit_utf8(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The encoding must be passed explicitly, not left to the platform
        default — the archive must read the same on every machine."""
        path = tmp_path / "registry.json"
        path.write_text('{"version": 1, "sources": {}}', encoding="utf-8")
        captured: dict[str, object] = {}
        original_read_text = Path.read_text

        def spy_read_text(self: Path, *args: object, **kwargs: object) -> str:
            captured["encoding"] = kwargs.get("encoding")
            return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "read_text", spy_read_text)

        read_registry(path)

        assert captured["encoding"] == "utf-8"

    def test_write_registry_output_is_sorted_indented_and_literal_utf8(
        self, tmp_path: Path
    ) -> None:
        source = SourceRecord(
            authority_type="kommune",
            authority_id="9999",
            name="Testbæ",
            canonical_domain="testby.example.invalid",
        )
        registry = SourceRegistry(sources={"9999": activate(source, check())})
        path = tmp_path / "registry.json"

        write_registry(registry, path)
        text = path.read_text(encoding="utf-8")

        assert list(json.loads(text).keys()) == ["sources", "version"]
        assert '  "sources": {' in text.splitlines()
        assert "Testbæ" in text
        assert "\\u00e6" not in text


class TestTermsDecisionIsNotTermsReview:
    def test_reviewed_terms_that_prohibit_capture_block_activation(self) -> None:
        """ "I read the terms and they forbid crawling" must not clear a source."""
        with pytest.raises(SourceNotActivatedError, match="terms_permit_capture=False"):
            activate(eligible_source(), check(terms_permit_capture=False))

    def test_unreviewed_terms_block_activation(self) -> None:
        with pytest.raises(SourceNotActivatedError, match="terms_reviewed=False"):
            activate(
                eligible_source(),
                check(terms_reviewed=False, terms_permit_capture=False),
            )

    def test_a_verdict_without_a_review_is_incoherent(self) -> None:
        with pytest.raises(ValidationError, match="terms_permit_capture cannot be true"):
            check(terms_reviewed=False, terms_permit_capture=True)

    def test_active_record_with_prohibiting_terms_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="ADR-0010"):
            SourceRecord(
                authority_type="kommune",
                authority_id="9999",
                name="Testby",
                canonical_domain="testby.example.invalid",
                access_policy=check(terms_permit_capture=False),
                active=True,
            )


class TestRegistrySchemaIsPinned:
    def test_unknown_field_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            SourceRecord.model_validate(
                {
                    "authority_type": "kommune",
                    "authority_id": "9999",
                    "name": "Testby",
                    "canonical_domain": "testby.example.invalid",
                    "crawl_budget": 100,
                },
            )

    def test_future_schema_version_is_refused(self, tmp_path: Path) -> None:
        """A v1 reader must not consume v2 semantics by ignoring the version."""
        path = tmp_path / "registry.json"
        path.write_text('{"version": 2, "sources": {}}', encoding="utf-8")

        with pytest.raises(ParseError, match="invalid source registry"):
            read_registry(path)


class TestRegistryFileIsPinnedToBytes:
    """The roundtrip and determinism tests above cannot see the file's shape.

    Both compare the writer against itself, so sort_keys, indent and
    ensure_ascii are invisible to them. A registry is access-control data read
    by humans during an audit; its layout is pinned here.
    """

    EXPECTED = """{
  "sources": {
    "9999": {
      "access_policy": {
        "checked_at": "2026-08-18T06:00:00Z",
        "note": "",
        "rate_limit_seconds": 2.0,
        "reviewed_by": "project owner",
        "robots_allows": true,
        "robots_txt_url": "https://example.invalid/robots.txt",
        "terms_permit_capture": true,
        "terms_reviewed": true,
        "terms_url": null,
        "user_agent": "lovspor-observatory/0.1"
      },
      "active": true,
      "authority_id": "9999",
      "authority_type": "kommune",
      "canonical_domain": "testby.example.invalid",
      "listing_entry_points": [],
      "name": "Testbø"
    }
  },
  "version": 1
}
"""

    def written(self, tmp_path: Path) -> str:
        source = SourceRecord(
            authority_type="kommune",
            authority_id="9999",
            name="Testbø",
            canonical_domain="testby.example.invalid",
        )
        registry = SourceRegistry(sources={"9999": activate(source, check())})
        path = tmp_path / "registry.json"
        write_registry(registry, path)
        return path.read_text(encoding="utf-8")

    def test_file_matches_the_pinned_layout(self, tmp_path: Path) -> None:
        assert self.written(tmp_path) == self.EXPECTED

    def test_a_registry_written_before_listings_still_loads(self, tmp_path: Path) -> None:
        """The file on disk outlives the schema. `listing_entry_points` arrived
        with #151, and every record written before it has to keep loading —
        with the empty default, not with a refusal, because a registry that
        stops parsing takes every recorded access-policy decision with it."""
        path = tmp_path / "registry.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "sources": {
                        "9999": {
                            "authority_type": "kommune",
                            "authority_id": "9999",
                            "name": "Testbø",
                            "canonical_domain": "testby.example.invalid",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        assert read_registry(path).sources["9999"].listing_entry_points == ()

    def test_keys_are_sorted_at_every_level(self, tmp_path: Path) -> None:
        def assert_sorted(pairs: list[tuple[str, object]]) -> dict[str, object]:
            keys = [key for key, _ in pairs]
            assert keys == sorted(keys)
            return dict(pairs)

        json.loads(self.written(tmp_path), object_pairs_hook=assert_sorted)

    def test_file_is_indented_for_human_review(self, tmp_path: Path) -> None:
        assert '\n  "sources": {' in self.written(tmp_path)

    def test_norwegian_characters_are_written_literally(self, tmp_path: Path) -> None:
        text = self.written(tmp_path)

        assert "Testbø" in text
        assert "\\u00f8" not in text

    def test_file_ends_with_exactly_one_newline(self, tmp_path: Path) -> None:
        text = self.written(tmp_path)

        assert text.endswith("}\n")
        assert not text.endswith("\n\n")


class TestHostMatchingNormalisation:
    """Host comparison must not depend on how the URL happened to be spelled."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://TESTBY.EXAMPLE.INVALID/f",
            "https://Testby.Example.Invalid/f",
            "https://testby.example.invalid./f",
            "https://WWW.TESTBY.EXAMPLE.INVALID/f",
        ],
    )
    def test_case_and_trailing_dot_do_not_defeat_the_gate(self, url: str) -> None:
        registry = SourceRegistry(sources={"9999": activate(eligible_source(), check())})

        assert authorise_capture(registry, url).authority_id == "9999"

    def test_uppercase_canonical_domain_still_matches(self) -> None:
        """The registry side is normalised too, not only the URL side."""
        source = SourceRecord(
            authority_type="kommune",
            authority_id="9999",
            name="Testby",
            canonical_domain="TESTBY.EXAMPLE.INVALID",
        )
        registry = SourceRegistry(sources={"9999": activate(source, check())})

        assert authorise_capture(registry, "https://testby.example.invalid/f").active

    def test_trailing_dot_does_not_smuggle_past_the_lovdata_deny(self) -> None:
        with pytest.raises(SourceNotActivatedError, match="globally denied"):
            authorise_capture(SourceRegistry(), "https://lovdata.no./register")

    def test_trailing_dot_in_the_registered_domain_is_normalised_too(self) -> None:
        """Normalisation applies to both sides; stripping only the URL side would
        make a registry entry written with a fully-qualified trailing dot
        silently stop matching its own authority."""
        source = SourceRecord(
            authority_type="kommune",
            authority_id="9999",
            name="Testby",
            canonical_domain="testby.example.invalid.",
        )
        registry = SourceRegistry(sources={"9999": activate(source, check())})

        assert authorise_capture(registry, "https://testby.example.invalid/f").active


class TestRegistryPath:
    """registry_path() is the one place the CLI learns the registry's
    filename; a change here would silently move access-policy records that
    operators expect at ``sources.json``."""

    def test_returns_sources_json_under_the_root(self, tmp_path: Path) -> None:
        root = ObservatoryRoot(str(tmp_path), forbidden=[])

        path = registry_path(root)

        assert path == root.path / "sources.json"
        assert path.name == "sources.json"


class TestReadAccessPolicyCheck:
    """read_access_policy_check() is the CLI's only door for a reviewer's
    check; it is exercised end-to-end via the CLI tests, but its own failure
    modes deserve direct coverage rather than only through that caller."""

    def test_loads_a_valid_check(self, tmp_path: Path) -> None:
        path = tmp_path / "check.json"
        path.write_text(json.dumps(check_document()), encoding="utf-8")

        result = read_access_policy_check(path)

        assert result == check()

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_access_policy_check(tmp_path / "absent.json")

    def test_malformed_json_is_a_parse_error(self, tmp_path: Path) -> None:
        path = tmp_path / "check.json"
        path.write_text("{ not json", encoding="utf-8")

        with pytest.raises(ParseError, match="unreadable access-policy check"):
            read_access_policy_check(path)

    def test_the_check_document_encoding_is_explicit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The encoding must be passed explicitly, not left to the platform
        default. A reviewer's name is routinely non-ASCII, and a check that
        reads differently on a differently-configured machine is not the
        record it claims to be."""
        path = tmp_path / "check.json"
        path.write_text(json.dumps(check_document()), encoding="utf-8")
        captured: dict[str, object] = {}
        original_read_text = Path.read_text

        def spy_read_text(self: Path, *args: object, **kwargs: object) -> str:
            captured["encoding"] = kwargs.get("encoding")
            return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "read_text", spy_read_text)

        read_access_policy_check(path)

        assert captured["encoding"] == "utf-8"

    def test_non_utf8_bytes_are_a_parse_error(self, tmp_path: Path) -> None:
        path = tmp_path / "check.json"
        path.write_bytes(json.dumps(check_document()).encode("utf-16"))

        with pytest.raises(ParseError, match="unreadable access-policy check"):
            read_access_policy_check(path)

    def test_schema_violation_is_a_parse_error_not_a_bare_validation_error(
        self, tmp_path: Path
    ) -> None:
        """A syntactically valid document that fails the model's own rules —
        here, a verdict recorded without the review that would justify it —
        must surface as ParseError like any other bad input, not leak
        pydantic's ValidationError past this boundary."""
        path = tmp_path / "check.json"
        path.write_text(
            json.dumps(check_document(terms_reviewed=False, terms_permit_capture=True)),
            encoding="utf-8",
        )

        with pytest.raises(ParseError, match="invalid access-policy check"):
            read_access_policy_check(path)

    def test_a_missing_required_field_is_a_parse_error(self, tmp_path: Path) -> None:
        document = check_document()
        del document["reviewed_by"]
        path = tmp_path / "check.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(ParseError, match="invalid access-policy check"):
            read_access_policy_check(path)
