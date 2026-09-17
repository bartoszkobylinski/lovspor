"""Grouping the register by resolved address (issue #277).

Every resolver here is a dict lookup. A test whose verdict depends on what DNS
answers today is not a test of this module — it is a test of somebody else's
zone file, and it fails for reasons no change in this repository caused.
"""

import socket
from datetime import UTC, datetime

import pytest

from lovspor.observatory.addresses import (
    Resolver,
    hosts_for,
    resolve_register,
    system_resolver,
)
from lovspor.observatory.registry import AccessPolicyCheck, SourceRecord, SourceRegistry

GRIMSTAD = "176.221.90.98"
OSLO = "203.0.113.7"


def cleared(domain: str) -> AccessPolicyCheck:
    """The check the model requires before a source may be active."""
    return AccessPolicyCheck(
        checked_at=datetime(2026, 8, 18, 17, 0, tzinfo=UTC),
        robots_txt_url=f"https://{domain}/robots.txt",
        robots_allows=True,
        terms_reviewed=True,
        terms_permit_capture=True,
        rate_limit_seconds=7.0,
        user_agent="lovspor-observatory/0.1 (+https://lovspor.no/observatory)",
        reviewed_by="Test Reviewer",
    )


def source(
    authority_id: str,
    name: str,
    domain: str,
    *,
    active: bool = True,
    listings: tuple[str, ...] = (),
) -> SourceRecord:
    return SourceRecord(
        authority_type="kommune",
        authority_id=authority_id,
        name=name,
        canonical_domain=domain,
        listing_entry_points=listings,
        access_policy=cleared(domain) if active else None,
        active=active,
    )


def register(*records: SourceRecord) -> SourceRegistry:
    return SourceRegistry(sources={record.authority_id: record for record in records})


def resolver_for(table: dict[str, set[str]]) -> Resolver:
    """A resolver that knows exactly ``table`` and fails like the system one."""

    def resolve(host: str) -> frozenset[str]:
        if host not in table:
            raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided")
        return frozenset(table[host])

    return resolve


class TestWhichHostsASourceImplies:
    def test_the_canonical_domain_and_its_www_form(self) -> None:
        """The fetcher keys on the host it reached, and captures reach both."""
        assert hosts_for(source("4202", "Grimstad", "grimstad.kommune.no")) == (
            "grimstad.kommune.no",
            "www.grimstad.kommune.no",
        )

    def test_listing_entry_points_contribute_their_hosts(self) -> None:
        record = source(
            "9999",
            "Testby",
            "testby.example.invalid",
            listings=("https://kunngjoring.testby.example.invalid/notices",),
        )

        assert hosts_for(record) == (
            "testby.example.invalid",
            "www.testby.example.invalid",
            "kunngjoring.testby.example.invalid",
        )

    def test_a_www_listing_does_not_produce_the_www_host_twice(self) -> None:
        record = source(
            "9999",
            "Testby",
            "testby.example.invalid",
            listings=("https://www.testby.example.invalid/notices",),
        )

        assert hosts_for(record) == (
            "testby.example.invalid",
            "www.testby.example.invalid",
        )

    def test_a_domain_already_carrying_www_gets_no_second_prefix(self) -> None:
        assert hosts_for(source("9999", "Testby", "www.testby.example.invalid")) == (
            "www.testby.example.invalid",
        )


class TestSharedAddressesAreFound:
    def test_two_sources_on_one_address_are_reported_together(self) -> None:
        """The pair from the issue: one machine, two authorities, two budgets."""
        registry = register(
            source("4202", "Grimstad", "grimstad.kommune.no"),
            source("4203", "Arendal", "arendal.kommune.no"),
        )
        resolve = resolver_for(
            {
                "grimstad.kommune.no": {GRIMSTAD},
                "www.grimstad.kommune.no": {GRIMSTAD},
                "arendal.kommune.no": {GRIMSTAD},
                "www.arendal.kommune.no": {GRIMSTAD},
            }
        )

        report = resolve_register(registry, resolve)

        assert [group.address for group in report.shared] == [GRIMSTAD]
        assert [s.authority_id for s in report.shared[0].sources] == ["4202", "4203"]

    def test_an_address_only_one_source_holds_is_not_reported(self) -> None:
        registry = register(
            source("4202", "Grimstad", "grimstad.kommune.no"),
            source("0301", "Oslo", "oslo.kommune.no"),
        )
        resolve = resolver_for(
            {
                "grimstad.kommune.no": {GRIMSTAD},
                "www.grimstad.kommune.no": {GRIMSTAD},
                "oslo.kommune.no": {OSLO},
                "www.oslo.kommune.no": {OSLO},
            }
        )

        assert resolve_register(registry, resolve).shared == ()

    def test_overlap_on_one_of_several_addresses_still_counts(self) -> None:
        """Round-robin DNS: one shared address is one shared machine."""
        registry = register(
            source("4202", "Grimstad", "grimstad.kommune.no"),
            source("0301", "Oslo", "oslo.kommune.no"),
        )
        resolve = resolver_for(
            {
                "grimstad.kommune.no": {GRIMSTAD, OSLO},
                "www.grimstad.kommune.no": {GRIMSTAD},
                "oslo.kommune.no": {OSLO},
                "www.oslo.kommune.no": {OSLO},
            }
        )

        report = resolve_register(registry, resolve)

        assert [group.address for group in report.shared] == [OSLO]

    def test_the_busiest_address_is_reported_first(self) -> None:
        """A vendor platform and a neighbouring pair are different findings."""
        vendor = "198.51.100.10"
        registry = register(
            source("1", "A", "a.example.invalid"),
            source("2", "B", "b.example.invalid"),
            source("3", "C", "c.example.invalid"),
            source("4", "D", "d.example.invalid"),
            source("5", "E", "e.example.invalid"),
        )
        table = {
            "a.example.invalid": {vendor},
            "b.example.invalid": {vendor},
            "c.example.invalid": {vendor},
            "d.example.invalid": {GRIMSTAD},
            "e.example.invalid": {GRIMSTAD},
        }
        table.update({f"www.{host}": addrs for host, addrs in table.items()})

        report = resolve_register(registry, resolver_for(table))

        assert [(g.address, len(g.sources)) for g in report.shared] == [
            (vendor, 3),
            (GRIMSTAD, 2),
        ]


class TestTheHeadlineCountsOnlyActiveSources:
    def test_an_inactive_neighbour_does_not_make_a_source_share_a_budget(self) -> None:
        """An inactive source is never fetched, so it costs the server nothing."""
        registry = register(
            source("4202", "Grimstad", "grimstad.kommune.no"),
            source("4203", "Arendal", "arendal.kommune.no", active=False),
        )
        table = {
            "grimstad.kommune.no": {GRIMSTAD},
            "www.grimstad.kommune.no": {GRIMSTAD},
            "arendal.kommune.no": {GRIMSTAD},
            "www.arendal.kommune.no": {GRIMSTAD},
        }

        report = resolve_register(registry, resolver_for(table))

        assert [group.address for group in report.shared] == [GRIMSTAD]
        assert report.active_sources_sharing == ()

    def test_two_active_sources_on_one_address_are_both_counted(self) -> None:
        registry = register(
            source("4202", "Grimstad", "grimstad.kommune.no"),
            source("4203", "Arendal", "arendal.kommune.no"),
        )
        table = {
            "grimstad.kommune.no": {GRIMSTAD},
            "www.grimstad.kommune.no": {GRIMSTAD},
            "arendal.kommune.no": {GRIMSTAD},
            "www.arendal.kommune.no": {GRIMSTAD},
        }

        report = resolve_register(registry, resolver_for(table))

        assert [s.authority_id for s in report.active_sources_sharing] == ["4202", "4203"]

    def test_a_source_is_counted_once_however_many_addresses_it_shares(self) -> None:
        registry = register(
            source("4202", "Grimstad", "grimstad.kommune.no"),
            source("4203", "Arendal", "arendal.kommune.no"),
        )
        table = {
            "grimstad.kommune.no": {GRIMSTAD, OSLO},
            "www.grimstad.kommune.no": {GRIMSTAD},
            "arendal.kommune.no": {GRIMSTAD, OSLO},
            "www.arendal.kommune.no": {GRIMSTAD},
        }

        report = resolve_register(registry, resolver_for(table))

        assert len(report.shared) == 2
        assert [s.authority_id for s in report.active_sources_sharing] == ["4202", "4203"]


class TestResolutionFailureIsRecordedNotRaised:
    def test_a_dead_host_is_reported_with_its_reason(self) -> None:
        registry = register(source("5612", "Nowhere", "gone.example.invalid"))

        report = resolve_register(registry, resolver_for({}))

        assert [s.authority_id for s in report.unresolved_sources] == ["5612"]
        assert "nodename" in report.sources[0].unresolved[0].error

    def test_a_dead_host_does_not_stop_the_pass(self) -> None:
        """A register pass that aborts on the first retired domain measures nothing."""
        registry = register(
            source("5612", "Nowhere", "gone.example.invalid"),
            source("4202", "Grimstad", "grimstad.kommune.no"),
            source("4203", "Arendal", "arendal.kommune.no"),
        )
        table = {
            "grimstad.kommune.no": {GRIMSTAD},
            "www.grimstad.kommune.no": {GRIMSTAD},
            "arendal.kommune.no": {GRIMSTAD},
            "www.arendal.kommune.no": {GRIMSTAD},
        }

        report = resolve_register(registry, resolver_for(table))

        assert [group.address for group in report.shared] == [GRIMSTAD]

    def test_one_dead_host_does_not_discard_the_source_s_other_addresses(self) -> None:
        registry = register(
            source("4202", "Grimstad", "grimstad.kommune.no"),
            source("4203", "Arendal", "arendal.kommune.no"),
        )
        table = {
            "grimstad.kommune.no": {GRIMSTAD},
            "arendal.kommune.no": {GRIMSTAD},
        }

        report = resolve_register(registry, resolver_for(table))

        assert [group.address for group in report.shared] == [GRIMSTAD]
        assert len(report.unresolved_sources) == 2


class TestEachHostIsAskedOnce:
    def test_a_listing_on_the_www_host_does_not_resolve_it_twice(self) -> None:
        """Deduplication happens in `hosts_for`, so no cache has to."""
        asked: list[str] = []

        def counting(host: str) -> frozenset[str]:
            asked.append(host)
            return frozenset({GRIMSTAD})

        registry = register(
            source(
                "4202",
                "Grimstad",
                "grimstad.kommune.no",
                listings=("https://www.grimstad.kommune.no/kunngjoringer",),
            )
        )

        resolve_register(registry, counting)

        assert asked == ["grimstad.kommune.no", "www.grimstad.kommune.no"]

    def test_two_sources_cannot_share_a_host_so_none_is_asked_twice(self) -> None:
        """The register refuses a second source on a claimed domain."""
        asked: list[str] = []

        def counting(host: str) -> frozenset[str]:
            asked.append(host)
            return frozenset({GRIMSTAD})

        registry = register(
            source("4202", "Grimstad", "grimstad.kommune.no"),
            source("4203", "Arendal", "arendal.kommune.no"),
        )

        resolve_register(registry, counting)

        assert len(asked) == len(set(asked))


class TestTheSystemResolver:
    def test_it_asks_for_both_families(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A dual-stacked pair sharing an address must not read as unrelated."""
        captured: dict[str, object] = {}

        def fake_getaddrinfo(host: str, port: object, **kwargs: object) -> list[tuple]:
            captured["host"] = host
            captured["family"] = kwargs.get("family")
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (GRIMSTAD, 0)),
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 0, 0, 0)),
            ]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

        assert system_resolver("grimstad.kommune.no") == frozenset({GRIMSTAD, "2001:db8::1"})
        assert captured["family"] is None
