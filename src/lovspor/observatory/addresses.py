"""Which registered sources share a server (issue #277).

The politeness budget is a promise to whoever operates a machine, but the
fetcher enforces it per *host*: ``RateLimiter`` keys on the hostname a URL
targets. Two municipalities on one server therefore hold two independent
budgets and the sweep hits that machine at twice the rate it believes it is
holding to — `grimstad.kommune.no` and `arendal.kommune.no` are one such pair.

Issue #277 asks for the keying to change. This module deliberately does not
change it. It answers the question the issue puts *before* that decision —
how many registered sources share an address today — because keying the
budget on the resolved address without knowing the answer can be far worse
than the defect: Norwegian municipalities sit behind shared vendor platforms
(#194 counts twelve on ACOS alone), and one budget per address would collapse
a whole vendor's population into a single queue and turn a 20-hour sweep into
a week of them.

So this reports, and a human decides. Three things shape what it reports:

**A source is more than its canonical domain.** The fetcher rate-limits on the
host it actually reached, and captures routinely reach ``www.X`` for a source
registered as ``X``. Reading only the canonical domain would therefore measure
a host the sweep may never hit. The hosts here are the ones the register
itself implies: the canonical domain, its ``www.`` form, and the host of every
declared listing entry point. Discovery can still reach another subdomain
inside the cleared domain, and this cannot see those — the register does not
record them, and inventing them would be worse than naming the limit.

**Resolution failure is data, not an error.** A host that does not resolve is
reported as unresolved with the reason, never raised past. A register pass
that aborts on the first dead domain measures nothing, and a dead domain is
itself worth seeing next to a source the sweep still tries every night.

**Sharing is any overlap, not equal sets.** Round-robin DNS gives one host
several addresses and two hosts may overlap on one of them. One shared address
is one shared machine, which is the whole question, so grouping is by address
and a source appears in every group it has an address in.
"""

import socket
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

from lovspor.observatory.registry import SourceRecord, SourceRegistry

#: Resolve one hostname to its addresses. Injected rather than called directly
#: so the report is testable without a network and without a live DNS answer
#: deciding whether a test passes — the same reason ``CaptureSettings`` takes
#: ``monotonic`` and ``sleep``.
Resolver = Callable[[str], frozenset[str]]

WWW_PREFIX = "www."


def system_resolver(host: str) -> frozenset[str]:
    """Every address the system resolver returns for ``host``.

    Both families: a source reachable over IPv6 shares a server with anything
    else on that address just as much, and asking only for A records would
    report a dual-stacked pair as unrelated.

    Raises:
        OSError: resolution failed. The caller records the reason; see
            :func:`resolve_register`.
    """
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    # sockaddr is ``(address, port)`` for IPv4 and ``(address, port, flowinfo,
    # scope_id)`` for IPv6, so its first element types as ``str | int``. Keeping
    # only the strings is narrower than coercing: ``str(...)`` on a malformed
    # entry would turn a port number into an "address" and quietly put it in a
    # sharing group, which is the one thing this report must not invent.
    return frozenset(address for *_, sockaddr in infos if isinstance(address := sockaddr[0], str))


@dataclass(frozen=True)
class HostResolution:
    """One hostname, and what the resolver said about it."""

    host: str
    addresses: frozenset[str] = frozenset()
    #: Empty when resolution succeeded. The resolver's own message otherwise,
    #: because "which failure" is the difference between a retired domain and
    #: a resolver that was not reachable when the pass ran.
    error: str = ""

    @property
    def resolved(self) -> bool:
        return not self.error


@dataclass(frozen=True)
class SourceAddresses:
    """A registered source, the hosts it implies, and where they point."""

    authority_id: str
    name: str
    canonical_domain: str
    active: bool
    hosts: tuple[HostResolution, ...]

    @property
    def addresses(self) -> frozenset[str]:
        """Every address any of this source's hosts resolved to."""
        if not self.hosts:
            return frozenset()
        return frozenset().union(*(host.addresses for host in self.hosts))

    @property
    def unresolved(self) -> tuple[HostResolution, ...]:
        return tuple(host for host in self.hosts if not host.resolved)


@dataclass(frozen=True)
class SharedAddress:
    """One address, and every registered source that resolves to it."""

    address: str
    sources: tuple[SourceAddresses, ...]

    @property
    def active_sources(self) -> tuple[SourceAddresses, ...]:
        """The members the sweep actually fetches — the ones that share a budget."""
        return tuple(source for source in self.sources if source.active)


@dataclass(frozen=True)
class AddressReport:
    """The whole register, resolved, with the shared addresses called out."""

    sources: tuple[SourceAddresses, ...]
    shared: tuple[SharedAddress, ...]

    @property
    def unresolved_sources(self) -> tuple[SourceAddresses, ...]:
        return tuple(source for source in self.sources if source.unresolved)

    @property
    def active_sources_sharing(self) -> tuple[SourceAddresses, ...]:
        """Active sources that share an address with another *active* source.

        The headline number issue #277 asks for, and it counts active sources
        only on both sides: an inactive source is not fetched, so it consumes
        none of the server's patience and sharing an address with one costs
        nothing today. Counting it would inflate the very figure the keying
        decision rests on.
        """
        sharing = {
            source.authority_id: source
            for group in self.shared
            if len(group.active_sources) > 1
            for source in group.active_sources
        }
        return tuple(sharing[key] for key in sorted(sharing))


def hosts_for(record: SourceRecord) -> tuple[str, ...]:
    """The hosts the register implies for one source, in a stable order.

    The canonical domain first, then its ``www.`` form, then the host of every
    declared listing entry point. Duplicates are dropped and the canonical
    domain's own ``www.`` form is not added twice when a listing already names
    it, so a source with a ``www.`` entry point does not resolve it twice and
    read as though it had more hosts than it has.
    """
    ordered = [record.canonical_domain]
    if not record.canonical_domain.startswith(WWW_PREFIX):
        ordered.append(f"{WWW_PREFIX}{record.canonical_domain}")
    for url in record.listing_entry_points:
        host = urlsplit(url).hostname
        if host:
            ordered.append(host)
    return tuple(dict.fromkeys(ordered))


def resolve_register(registry: SourceRegistry, resolver: Resolver) -> AddressReport:
    """Resolve every registered source and group the register by address.

    One resolver call per host, and no cache across sources: two sources
    cannot share a hostname. The register refuses a second source on a claimed
    domain, and a listing entry point must live inside its own source's
    domain — so a cache keyed on hostname would never hit, and a cache that
    never hits is machinery whose correctness nobody can observe.
    """

    def lookup(host: str) -> HostResolution:
        try:
            return HostResolution(host, resolver(host))
        except OSError as exc:
            return HostResolution(host, error=str(exc) or exc.__class__.__name__)

    sources = tuple(
        SourceAddresses(
            authority_id=authority_id,
            name=record.name,
            canonical_domain=record.canonical_domain,
            active=record.active,
            hosts=tuple(lookup(host) for host in hosts_for(record)),
        )
        for authority_id, record in sorted(registry.sources.items())
    )
    return AddressReport(sources=sources, shared=_group_by_address(sources))


def _group_by_address(sources: Iterable[SourceAddresses]) -> tuple[SharedAddress, ...]:
    """Addresses more than one source resolves to, busiest first.

    Sorted by how many sources share the address because the shape of the
    answer is the finding: a group of two is a pair on one box, a group of
    twelve is a vendor platform (#194), and those call for opposite decisions
    about what the budget should key on.
    """
    by_address: dict[str, list[SourceAddresses]] = defaultdict(list)
    for source in sources:
        for address in source.addresses:
            by_address[address].append(source)
    shared = [
        SharedAddress(address, tuple(members))
        for address, members in by_address.items()
        if len(members) > 1
    ]
    shared.sort(key=lambda group: (-len(group.sources), group.address))
    return tuple(shared)
