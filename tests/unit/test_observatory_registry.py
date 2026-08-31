"""Tests for lovspor.observatory.registry — eligibility is not activation."""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.errors import AmbiguousSourceError, ParseError, SourceNotActivatedError
from lovspor.observatory.registry import (
    AccessPolicyCheck,
    CaptureVerdict,
    SourceRecord,
    SourceRegistry,
    _host_matches,
    activate,
    authorise_capture,
    capture_host,
    claimants,
    domains_claimed_twice,
    normalised_domain,
    read_access_policy_check,
    read_capture_verdict,
    read_registry,
    registry_path,
    replace_domain,
    write_registry,
)
from lovspor.observatory.storage import ObservatoryRoot

CHECKED_AT = datetime(2026, 8, 18, 6, 0, tzinfo=UTC)


def check(**overrides: object) -> AccessPolicyCheck:
    fields: dict[str, object] = {
        "checked_at": CHECKED_AT,
        "robots_txt_url": "https://testby.example.invalid/robots.txt",
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
        "robots_txt_url": "https://testby.example.invalid/robots.txt",
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


def other_source(**overrides: object) -> SourceRecord:
    """A second authority, on the same domain unless told otherwise."""
    fields: dict[str, object] = {
        "authority_type": "kommune",
        "authority_id": "8888",
        "name": "Annenby",
        "canonical_domain": "testby.example.invalid",
    }
    return SourceRecord.model_validate({**fields, **overrides})


class TestTwoSourcesCannotShareADomain:
    """Issue #215. `4202 Grimstad` carried `arendal.kommune.no`, the same domain
    as `4203 Arendal`; the gate sorted by id and returned the first, so 5,980
    observations of Arendal's site were filed under Grimstad while Grimstad
    itself was never fetched and every pass over it reported success."""

    def _both_active(self) -> SourceRegistry:
        return SourceRegistry(
            sources={
                "9999": activate(eligible_source(), check()),
                "8888": activate(other_source(), check()),
            }
        )

    def test_the_gate_refuses_rather_than_picking_one(self) -> None:
        with pytest.raises(AmbiguousSourceError, match="more than one activated source"):
            authorise_capture(self._both_active(), "https://testby.example.invalid/f")

    def test_the_gate_refuses_overlapping_parent_and_subdomain_claims(self) -> None:
        """The ambiguity is coverage, not just exact-domain duplication."""
        parent = activate(
            other_source(authority_id="8888", canonical_domain="example.invalid"),
            check(robots_txt_url="https://example.invalid/robots.txt"),
        )
        child = activate(
            eligible_source(),
            check(robots_txt_url="https://testby.example.invalid/robots.txt"),
        )
        registry = SourceRegistry(sources={"8888": parent, "9999": child})

        with pytest.raises(AmbiguousSourceError):
            authorise_capture(registry, "https://testby.example.invalid/f")

    def test_the_refusal_names_both_claimants(self) -> None:
        """An operator has to reconcile two rows, so the message has to say
        which two. A refusal that only says "ambiguous" sends them to grep."""
        with pytest.raises(AmbiguousSourceError) as raised:
            authorise_capture(self._both_active(), "https://testby.example.invalid/f")

        assert "8888 Annenby" in str(raised.value)
        assert "9999 Testby" in str(raised.value)

    def test_the_refusal_message_names_and_separates_both_claimants(self) -> None:
        with pytest.raises(AmbiguousSourceError) as raised:
            authorise_capture(self._both_active(), "https://testby.example.invalid/f")

        assert str(raised.value) == (
            "testby.example.invalid is covered by more than one activated source "
            "(8888 Annenby, 9999 Testby); the register cannot say which authority "
            "publishes it, and picking one would file its pages under the other"
        )

    def test_an_inactive_second_claimant_is_not_an_ambiguity(self) -> None:
        """Only activation authorises a fetch, so only activated sources can
        compete for one. An eligible-but-inactive row claims nothing yet."""
        registry = SourceRegistry(
            sources={"9999": activate(eligible_source(), check()), "8888": other_source()}
        )

        assert (
            authorise_capture(registry, "https://testby.example.invalid/f").authority_id == "9999"
        )

    def test_the_refusal_is_still_a_refusal_to_fetch(self) -> None:
        """It subclasses `SourceNotActivatedError` on purpose: every existing
        handler reads that as "do not fetch this", and that answer stays right
        while the reason changes."""
        with pytest.raises(SourceNotActivatedError):
            authorise_capture(self._both_active(), "https://testby.example.invalid/f")


class TestDomainsClaimedTwice:
    def test_normalisation_removes_only_the_dns_root_dot(self) -> None:
        assert normalised_domain("EXAMPLE.XX.") == "example.xx"

    def test_a_register_with_no_collision_reports_none(self) -> None:
        registry = SourceRegistry(
            sources={
                "9999": eligible_source(),
                "8888": other_source(canonical_domain="annenby.example.invalid"),
            }
        )

        assert domains_claimed_twice(registry) == {}

    def test_a_collision_is_reported_with_both_records(self) -> None:
        registry = SourceRegistry(sources={"9999": eligible_source(), "8888": other_source()})

        contested = domains_claimed_twice(registry)

        assert list(contested) == ["testby.example.invalid"]
        assert [r.authority_id for r in contested["testby.example.invalid"]] == ["8888", "9999"]

    @pytest.mark.parametrize(
        "spelling", ["TESTBY.EXAMPLE.INVALID", "testby.example.invalid.", "Testby.Example.Invalid"]
    )
    def test_equivalent_dns_spellings_are_one_contested_domain(self, spelling: str) -> None:
        """DNS is case-insensitive and the trailing dot is the absolute form of
        the same name. Comparing the field as a string is how the two halves of
        #215 drifted apart: the gate normalised, this did not, so a second
        spelling passed `register-source` and was reported by nothing."""
        registry = SourceRegistry(
            sources={"9999": eligible_source(), "8888": other_source(canonical_domain=spelling)}
        )

        contested = domains_claimed_twice(registry)

        assert list(contested) == ["testby.example.invalid"]
        assert [r.authority_id for r in contested["testby.example.invalid"]] == ["8888", "9999"]

    @pytest.mark.parametrize(
        "spelling", ["TESTBY.EXAMPLE.INVALID", "testby.example.invalid.", "Testby.Example.Invalid"]
    )
    def test_a_claim_is_found_however_the_domain_is_spelled(self, spelling: str) -> None:
        registry = SourceRegistry(sources={"9999": eligible_source()})

        assert claimants(registry, spelling) == ["9999"]

    @pytest.mark.parametrize(
        ("existing", "requested"),
        [
            ("example.invalid", "testby.example.invalid"),
            ("testby.example.invalid", "example.invalid"),
        ],
    )
    def test_a_parent_and_a_subdomain_claim_the_same_ground(
        self, existing: str, requested: str
    ) -> None:
        """Equality is not the question. A source cleared for a parent domain
        covers every host under it, so it and a source on a subdomain compete
        for the same pages — which the capture gate already refuses. Comparing
        the claims for equality would let that pair be registered without a
        word and then refuse every capture with `status` calling the register
        clean. Asked in both directions: which row was registered first
        decides nothing."""
        registry = SourceRegistry(
            sources={"9999": other_source(authority_id="9999", canonical_domain=existing)}
        )

        assert claimants(registry, requested) == ["9999"]

    def test_an_overlap_is_reported_once_under_the_broader_domain(self) -> None:
        """Every member of a group derives the same key from it, so one
        collision is one line in the report rather than one per claimant."""
        registry = SourceRegistry(
            sources={
                "9999": eligible_source(),
                "8888": other_source(canonical_domain="sub.testby.example.invalid"),
            }
        )

        contested = domains_claimed_twice(registry)

        assert list(contested) == ["testby.example.invalid"]
        assert [r.authority_id for r in contested["testby.example.invalid"]] == ["8888", "9999"]

    def test_one_parent_with_two_subdomain_claims_reports_every_claimant(self) -> None:
        """A component, not a neighbourhood. The two subdomains do not overlap
        each other, so collecting what overlaps a single row finds two of the
        three — and, keyed by the same parent, the last row written replaces
        the fuller answer. An operator has to reconcile all three."""
        registry = SourceRegistry(
            sources={
                "9999": other_source(authority_id="9999", canonical_domain="example.invalid"),
                "8888": other_source(canonical_domain="a.example.invalid"),
                "7777": other_source(authority_id="7777", canonical_domain="b.example.invalid"),
            }
        )

        contested = domains_claimed_twice(registry)

        assert list(contested) == ["example.invalid"]
        assert [r.authority_id for r in contested["example.invalid"]] == ["7777", "8888", "9999"]

    def test_two_disjoint_overlap_groups_are_both_reported(self) -> None:
        registry = SourceRegistry(
            sources={
                "1000": other_source(authority_id="1000", canonical_domain="one.invalid"),
                "1001": other_source(authority_id="1001", canonical_domain="sub.one.invalid"),
                "2000": other_source(authority_id="2000", canonical_domain="two.invalid"),
                "2001": other_source(authority_id="2001", canonical_domain="sub.two.invalid"),
            }
        )

        contested = domains_claimed_twice(registry)

        assert list(contested) == ["one.invalid", "two.invalid"]
        assert [[r.authority_id for r in group] for group in contested.values()] == [
            ["1000", "1001"],
            ["2000", "2001"],
        ]

    def test_sibling_subdomains_without_a_parent_do_not_collide(self) -> None:
        """The component only exists because something covers both. Two
        subdomains alone compete for nothing."""
        registry = SourceRegistry(
            sources={
                "8888": other_source(canonical_domain="a.example.invalid"),
                "7777": other_source(authority_id="7777", canonical_domain="b.example.invalid"),
            }
        )

        assert domains_claimed_twice(registry) == {}

    def test_a_group_is_complete_however_the_authority_ids_sort(self) -> None:
        """The component is walked from whichever row the scan reaches first,
        and rows are visited in id order. With the broad domain holding the
        lowest id, a walk that stopped at the first row it had already seen
        would report the parent and one subdomain and drop the other — and,
        keyed by the same parent, the shorter answer overwrites the fuller
        one, so the loss is silent."""
        registry = SourceRegistry(
            sources={
                "1000": other_source(authority_id="1000", canonical_domain="e.invalid"),
                "2000": other_source(authority_id="2000", canonical_domain="a.e.invalid"),
                "3000": other_source(authority_id="3000", canonical_domain="b.e.invalid"),
            }
        )

        contested = domains_claimed_twice(registry)

        assert [r.authority_id for r in contested["e.invalid"]] == ["1000", "2000", "3000"]

    def test_unrelated_domains_are_not_an_overlap(self) -> None:
        """Coverage is label-wise. `notbaerum.no` must not read as a claim on
        `baerum.no` merely because one is a string suffix of the other."""
        registry = SourceRegistry(
            sources={
                "9999": other_source(authority_id="9999", canonical_domain="baerum.no"),
                "8888": other_source(canonical_domain="notbaerum.no"),
            }
        )

        assert domains_claimed_twice(registry) == {}

    def test_an_inactive_row_still_counts_as_a_claim(self) -> None:
        """It is a claim on the register, not on traffic. Reporting only the
        activated ones would hide the collision until somebody activates the
        second row, which is the moment it starts costing observations."""
        registry = SourceRegistry(
            sources={"9999": activate(eligible_source(), check()), "8888": other_source()}
        )

        assert list(domains_claimed_twice(registry)) == ["testby.example.invalid"]


class TestClaimants:
    def test_an_unclaimed_domain_has_none(self) -> None:
        registry = SourceRegistry(sources={"9999": eligible_source()})

        assert claimants(registry, "free.example.invalid") == []

    def test_the_source_itself_can_be_excluded(self) -> None:
        """The write paths ask "who else has this?", so re-declaring a source's
        own current domain must not read as a collision with itself."""
        registry = SourceRegistry(sources={"9999": eligible_source()})

        assert claimants(registry, "testby.example.invalid") == ["9999"]
        assert claimants(registry, "testby.example.invalid", excluding="9999") == []


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
        cleared = check(robots_txt_url="https://lovdata.no/robots.txt")
        registry = SourceRegistry(sources={"9999": activate(denied, cleared)})

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
        "robots_txt_url": "https://testby.example.invalid/robots.txt",
        "terms_permit_capture": true,
        "terms_reviewed": true,
        "terms_url": null,
        "user_agent": "lovspor-observatory/0.1"
      },
      "active": true,
      "authority_id": "9999",
      "authority_type": "kommune",
      "canonical_domain": "testby.example.invalid",
      "capture_verdict": null,
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


REACHED_AT = datetime(2026, 8, 26, 18, 0, tzinfo=UTC)
RECHECK_AFTER = datetime(2026, 11, 26, 18, 0, tzinfo=UTC)


def verdict_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "outcome": "no_machine_reachable_source",
        "routes_checked": [
            "lovdata public data (avdeling I only; local regulations are avdeling II)",
            "sitemap and sitemap index",
            "atom and rss at conventional paths",
            "server-rendered index",
        ],
        "evidence": "https://github.com/bartoszkobylinski/lovspor/issues/194",
        "reached_at": REACHED_AT.isoformat().replace("+00:00", "Z"),
        "reviewed_by": "Bartosz Kobyliński",
        "recheck_after": RECHECK_AFTER.isoformat().replace("+00:00", "Z"),
    }
    document.update(overrides)
    return document


def verdict(**overrides: object) -> CaptureVerdict:
    return CaptureVerdict.model_validate(verdict_document(**overrides))


class TestCaptureVerdict:
    """What was concluded about a source, and on what evidence (#195).

    The twin of :class:`AccessPolicyCheck`. That one records a human's
    conclusion that a source *may* be fetched; this one records a conclusion
    that fetching it produces nothing — reached for twelve municipalities that
    publish no sitemap, no feed, and no server-rendered index, and whose
    regulations are absent from the only source this project may use.
    """

    def test_a_verdict_carries_what_was_checked_not_only_what_was_concluded(self) -> None:
        """An unsupported conclusion is the thing this field must not become.

        A bare "unreachable" would have to be re-trusted by every later reader;
        the routes make it re-readable instead.
        """
        with pytest.raises(ValidationError):
            CaptureVerdict.model_validate(verdict_document(routes_checked=[]))

    def test_a_verdict_is_attributed(self) -> None:
        """Someone concluded this. An unattributed verdict is not evidence
        that anyone looked — the same rule `reviewed_by` already carries."""
        with pytest.raises(ValidationError):
            CaptureVerdict.model_validate(verdict_document(reviewed_by=""))

    def test_a_verdict_must_expire(self) -> None:
        """A verdict without a re-check date is the silence this exists to
        prevent, one level up: a source that stops being asked looks like a
        source that has nothing to say."""
        document = verdict_document()
        del document["recheck_after"]

        with pytest.raises(ValidationError):
            CaptureVerdict.model_validate(document)

    def test_the_recheck_cannot_precede_the_verdict(self) -> None:
        with pytest.raises(ValidationError, match="recheck_after"):
            CaptureVerdict.model_validate(
                verdict_document(recheck_after=REACHED_AT.isoformat().replace("+00:00", "Z"))
            )

    def test_both_timestamps_must_be_utc(self) -> None:
        """The same axis rule the observations keep: a naive stamp is
        ambiguous the moment the machine moves."""
        for field in ("reached_at", "recheck_after"):
            with pytest.raises(ValidationError):
                CaptureVerdict.model_validate(verdict_document(**{field: "2026-08-26T18:00:00"}))

    def test_an_unknown_outcome_is_refused(self) -> None:
        """The vocabulary is closed on purpose: a free-text outcome cannot be
        counted, and `observatory status` has to count these."""
        with pytest.raises(ValidationError):
            CaptureVerdict.model_validate(verdict_document(outcome="probably-fine"))

    def test_a_verdict_is_due_once_its_recheck_date_has_passed(self) -> None:
        held = verdict()

        assert held.due(RECHECK_AFTER - timedelta(seconds=1)) is False
        assert held.due(RECHECK_AFTER) is True
        assert held.due(RECHECK_AFTER + timedelta(days=1)) is True


class TestASourceCarriesItsVerdict:
    def test_a_source_without_a_verdict_is_unchanged(self) -> None:
        """The field is additive: every source recorded before it still loads,
        and carries no verdict rather than an empty one."""
        record = SourceRecord.model_validate(
            {
                "authority_type": "kommune",
                "authority_id": "1860",
                "name": "Vestvågøy",
                "canonical_domain": "vestvagoy.kommune.no",
            }
        )

        assert record.capture_verdict is None

    def test_a_verdict_survives_a_write_and_a_read(self, tmp_path: Path) -> None:
        """The registry holds operator decisions, and this is one of them."""
        record = SourceRecord.model_validate(
            {
                "authority_type": "kommune",
                "authority_id": "1860",
                "name": "Vestvågøy",
                "canonical_domain": "vestvagoy.kommune.no",
                "capture_verdict": verdict_document(),
            }
        )
        path = tmp_path / "sources.json"
        write_registry(SourceRegistry(sources={"1860": record}), path)

        loaded = read_registry(path).sources["1860"].capture_verdict

        assert loaded is not None
        assert loaded.outcome == "no_machine_reachable_source"
        assert loaded.reviewed_by == "Bartosz Kobyliński"
        assert len(loaded.routes_checked) == 4

    def test_a_verdict_does_not_deactivate_the_source(self) -> None:
        """Recording that a source publishes nothing reachable is not a
        withdrawal of permission to fetch it. The two are separate decisions,
        and the re-check depends on the source still being activated."""
        record = SourceRecord.model_validate(
            {
                "authority_type": "kommune",
                "authority_id": "1860",
                "name": "Vestvågøy",
                "canonical_domain": "vestvagoy.kommune.no",
                "access_policy": check_document(
                    robots_txt_url="https://www.vestvagoy.kommune.no/robots.txt"
                ),
                "active": True,
                "capture_verdict": verdict_document(),
            }
        )

        assert record.active is True
        assert record.capture_verdict is not None


class TestReadingAVerdictDocument:
    """A verdict arrives as a document for the same reason an access-policy
    check does: it is the record of a human decision, it has to answer "why
    does this source never produce anything?" months later, and a conclusion
    typed into a shell leaves nothing to re-read."""

    def test_a_well_formed_document_loads(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.json"
        path.write_text(json.dumps(verdict_document()), encoding="utf-8")

        assert read_capture_verdict(path).outcome == "no_machine_reachable_source"

    def test_document_is_read_with_an_explicit_utf8_encoding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verdicts are portable records, not documents in the host locale."""
        path = tmp_path / "verdict.json"
        document = json.dumps(verdict_document())
        encodings: list[str | None] = []
        original_read_text = Path.read_text

        def recording_read_text(
            target: Path, encoding: str | None = None, errors: str | None = None
        ) -> str:
            encodings.append(encoding)
            return original_read_text(target, encoding=encoding, errors=errors)

        path.write_text(document, encoding="utf-8")
        monkeypatch.setattr(Path, "read_text", recording_read_text)

        assert read_capture_verdict(path).outcome == "no_machine_reachable_source"
        assert encodings == ["utf-8"]

    def test_a_schema_violation_is_a_parse_error(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.json"
        path.write_text(json.dumps(verdict_document(routes_checked=[])), encoding="utf-8")

        with pytest.raises(ParseError, match="invalid capture verdict"):
            read_capture_verdict(path)

    def test_unreadable_bytes_are_a_parse_error(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.json"
        path.write_bytes(json.dumps(verdict_document()).encode("utf-16"))

        with pytest.raises(ParseError, match="unreadable capture verdict"):
            read_capture_verdict(path)


class TestTheClearanceBelongsToItsDomain:
    """Issue #166. An access-policy check answers about one host — the one
    whose robots.txt the reviewer read. Nothing tied the two together, so
    editing `canonical_domain` let a clearance obtained for haugesund.no
    authorise traffic to haugesund.kommune.no."""

    def _record(self, domain: str, robots: str) -> SourceRecord:
        return SourceRecord.model_validate(
            {
                "authority_type": "kommune",
                "authority_id": "1106",
                "name": "Haugesund",
                "canonical_domain": domain,
                "access_policy": check_document(robots_txt_url=robots),
                "active": True,
            }
        )

    def test_a_check_performed_against_the_domain_itself_is_accepted(self) -> None:
        record = self._record("haugesund.no", "https://haugesund.no/robots.txt")

        assert record.active is True

    def test_a_check_performed_against_a_subdomain_is_accepted(self) -> None:
        """`www.X` is inside `X` — the commonest hosting shape among Norwegian
        municipalities, and the one the redirect rule already allows."""
        record = self._record("haugesund.no", "https://www.haugesund.no/robots.txt")

        assert record.active is True

    def test_a_check_performed_against_another_domain_is_refused(self) -> None:
        with pytest.raises(ValidationError, match=re.escape("outside haugesund.kommune.no")):
            self._record("haugesund.kommune.no", "https://www.haugesund.no/robots.txt")

    def test_a_check_performed_against_the_parent_domain_is_refused(self) -> None:
        """The parent is not the child: clearing `example.invalid` says
        nothing about what `testby.example.invalid` serves or permits."""
        with pytest.raises(ValidationError, match=re.escape("outside testby.example.invalid")):
            self._record("testby.example.invalid", "https://example.invalid/robots.txt")

    def test_a_lookalike_host_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            self._record("haugesund.no", "https://nothaugesund.no/robots.txt")

    def test_a_robots_url_with_no_host_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            self._record("haugesund.no", "robots.txt")

    def test_an_inactive_record_is_held_to_it_too(self) -> None:
        """The check is on the record, not on activation: a record that keeps
        a foreign clearance while inactive is one `activate` away from using
        it, and the registry should not be able to hold that state at all."""
        with pytest.raises(ValidationError, match=re.escape("outside haugesund.kommune.no")):
            SourceRecord.model_validate(
                {
                    "authority_type": "kommune",
                    "authority_id": "1106",
                    "name": "Haugesund",
                    "canonical_domain": "haugesund.kommune.no",
                    "access_policy": check_document(
                        robots_txt_url="https://www.haugesund.no/robots.txt"
                    ),
                    "active": False,
                }
            )

    def test_a_record_without_a_check_is_unaffected(self) -> None:
        assert eligible_source().access_policy is None

    def test_a_hand_edited_registry_gets_the_same_answer(self, tmp_path: Path) -> None:
        """The whole point of putting it in the type: editing sources.json is
        exactly the route that skips every other guard."""
        path = tmp_path / "sources.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "sources": {
                        "1106": {
                            "authority_type": "kommune",
                            "authority_id": "1106",
                            "name": "Haugesund",
                            "canonical_domain": "haugesund.kommune.no",
                            "access_policy": check_document(
                                robots_txt_url="https://www.haugesund.no/robots.txt"
                            ),
                            "active": True,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ParseError, match="invalid source registry"):
            read_registry(path)


class TestReplacingTheDomain:
    """One operation, because the three parts are not independently safe: the
    review, the decision to send traffic and the declared entry points were
    all obtained for a host that is no longer the one being crawled."""

    def _activated(self) -> SourceRecord:
        record = SourceRecord(
            authority_type="kommune",
            authority_id="1106",
            name="Haugesund",
            canonical_domain="haugesund.no",
            listing_entry_points=("https://www.haugesund.no/kunngjoringer",),
        )
        return activate(record, check(robots_txt_url="https://www.haugesund.no/robots.txt"))

    def test_the_clearance_is_withdrawn_and_the_source_deactivated(self) -> None:
        replaced = replace_domain(self._activated(), "haugesund.kommune.no")

        assert replaced.canonical_domain == "haugesund.kommune.no"
        assert replaced.access_policy is None
        assert replaced.active is False

    def test_entry_points_declared_for_the_old_domain_are_dropped(self) -> None:
        """They are pages someone confirmed list documents — on a host that is
        no longer this source's. The record would not even validate with them."""
        assert replace_domain(self._activated(), "haugesund.kommune.no").listing_entry_points == ()

    def test_a_verdict_reached_about_the_old_domain_is_dropped(self) -> None:
        """ "Publishes nothing a machine can reach" was concluded from routes
        checked on the old host. Carried across it would hold a server nobody
        has looked at out of every sweep (issue #195)."""
        held = SourceRecord.model_validate(
            {**self._activated().model_dump(), "capture_verdict": verdict_document()}
        )

        assert replace_domain(held, "haugesund.kommune.no").capture_verdict is None

    def test_the_authority_keeps_its_identity(self) -> None:
        replaced = replace_domain(self._activated(), "haugesund.kommune.no")

        assert (replaced.authority_id, replaced.name, replaced.authority_type) == (
            "1106",
            "Haugesund",
            "kommune",
        )

    def test_replacing_a_domain_with_itself_is_refused(self) -> None:
        """A replacement that replaces nothing would withdraw a live clearance
        and report success — worse than refusing a typo."""
        with pytest.raises(SourceNotActivatedError, match=re.escape("already on haugesund.no")):
            replace_domain(self._activated(), "haugesund.no")

    def test_the_refusal_survives_a_difference_of_case(self) -> None:
        with pytest.raises(SourceNotActivatedError):
            replace_domain(self._activated(), "HAUGESUND.NO")

    def test_moving_to_a_subdomain_is_a_real_migration(self) -> None:
        """A subdomain is a different domain. `sub.X` -> `X` widens what the
        source covers and `X` -> `sub.X` narrows it; refusing either as "already
        on" would leave an operator no supported way to make the change."""
        replaced = replace_domain(self._activated(), "www.haugesund.no")

        assert replaced.canonical_domain == "www.haugesund.no"
        assert replaced.access_policy is None

    def test_a_trailing_dot_does_not_make_it_a_different_domain(self) -> None:
        """The root dot is DNS syntax for the same name, so `X.` and `X` are
        one domain — whichever side it is typed on."""
        with pytest.raises(SourceNotActivatedError):
            replace_domain(self._activated(), "haugesund.no.")

    def test_a_trailing_dot_on_the_registered_side_is_normalised_too(self) -> None:
        """The mirror of the case above. Normalising only the argument would
        let a source registered as `X.` be "migrated" to `X` — a replacement
        that replaces nothing, withdrawing a live clearance for a typo."""
        record = SourceRecord(
            authority_type="kommune",
            authority_id="1106",
            name="Haugesund",
            canonical_domain="haugesund.no.",
        )
        activated = activate(record, check(robots_txt_url="https://www.haugesund.no/robots.txt"))

        with pytest.raises(SourceNotActivatedError):
            replace_domain(activated, "haugesund.no")

    def test_case_is_normalised_on_the_registered_side_too(self) -> None:
        """Domains are case-insensitive, and the registry can hold either
        spelling. Folding only the argument would let a source registered as
        `X` in capitals be "migrated" to the same name in lower case, which
        withdraws a live clearance and changes nothing."""
        record = SourceRecord(
            authority_type="kommune",
            authority_id="1106",
            name="Haugesund",
            canonical_domain="HAUGESUND.NO",
        )
        activated = activate(record, check(robots_txt_url="https://www.haugesund.no/robots.txt"))

        with pytest.raises(SourceNotActivatedError):
            replace_domain(activated, "haugesund.no")

    def test_a_leading_dot_is_a_different_domain(self) -> None:
        """Only the trailing root dot is syntax. A leading one is malformed,
        and stripping it would silently equate two spellings that name
        different things."""
        replaced = replace_domain(self._activated(), ".haugesund.no")

        assert replaced.canonical_domain == ".haugesund.no"

    def test_the_replaced_record_is_a_valid_one(self) -> None:
        """It has to load back out of the registry it is about to be written
        to, or the migration leaves the archive unreadable."""
        replaced = replace_domain(self._activated(), "haugesund.kommune.no")

        assert SourceRecord.model_validate(replaced.model_dump()) == replaced
