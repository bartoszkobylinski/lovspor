"""Observatory commands that work the archive: capture, sweep, verify, report.

The register's own administration -- registering a source, activating it,
recording a verdict, replacing a domain -- lives in ``registry_commands``.
The two halves share ``app`` (the Typer instance both decorate) and
``registry_io`` (reading and writing the register), so neither imports the
other and no cycle is possible.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, NamedTuple

import httpx
import typer

from lovspor.errors import (
    AmbiguousSourceError,
    LogIntegrityError,
    StaleSourceError,
)
from lovspor.exclusive_workload import ExclusiveWorkloadHeldError, exclusive_workload

# Imported for its registrations: the module decorates `observatory_app` with
# the registry-administration commands, and nothing here refers to its names.
from lovspor.observatory import registry_commands  # noqa: F401
from lovspor.observatory.addresses import (
    AddressReport,
    SharedAddress,
    resolve_register,
    system_resolver,
)
from lovspor.observatory.app import _AuthorityIdOption, observatory_app
from lovspor.observatory.discovery import Candidate, Discoverer, DiscoveryResult
from lovspor.observatory.fetch import Fetcher
from lovspor.observatory.freshness import (
    CaptureState,
    collect_capture_state,
    worth_capturing,
)
from lovspor.observatory.freshness_index import indexed_capture_state
from lovspor.observatory.heartbeat import heartbeat_url, send_heartbeat
from lovspor.observatory.log import ObservationLog, SnapshotVerification, verify_snapshot
from lovspor.observatory.model import ArtifactObservation
from lovspor.observatory.outcomes import ArchiveComposition, collect_composition
from lovspor.observatory.registry import (
    SourceRecord,
    SourceRegistry,
    domains_claimed_twice,
    registry_path,
)
from lovspor.observatory.registry_io import (
    _bound_register,
    _load,
    _registry_file,
    _root,
)
from lovspor.observatory.storage import ObservatoryRoot
from lovspor.observatory.sweeps import (
    OBSERVATION_SLA,
    SWEEP_DEADLINE,
    CadenceState,
    SweepRun,
    append_sweep_run,
    cadence_state,
    latest_sweep_run,
    sweep_status,
    sweeps_path,
)

# `observatory_app` is defined in `app` and re-exported here: `cli.py` imports it
# from this module, and both command modules decorate the same instance.
__all__ = ["observatory_app"]


# Each pairs a defect list with what its presence means. Kept together so the
# report cannot drift from the model: a field added to SnapshotVerification and
# not added here would be counted by `ok` and never explained to anyone.
def _defects(result: SnapshotVerification) -> list[str]:
    counted = (
        (result.missing_blobs, "blobs gone with no tombstone"),
        (result.hash_mismatches, "blobs that no longer hash to their record"),
        (result.orphan_blobs, "blobs no record mentions"),
        (result.unremoved_tombstones, "tombstoned blobs still on disk"),
        (result.tombstones_without_observation, "tombstones for hashes never observed"),
        (result.observations_after_tombstone, "observations appended after their tombstone"),
    )
    return [f"{len(found)} {label}" for found, label in counted if found]


def _log_damage(result: SnapshotVerification) -> list[str]:
    """The two log defects, each with the action it calls for.

    They call for opposite actions, which is the whole reason the audit
    separates them: one line may be dropped, the other must not be touched.
    """
    damage = []
    if result.incomplete_final_record:
        damage.append(
            "the final record was never finished — an interrupted run leaves exactly this, "
            "and the fetch it describes was never recorded. `observatory repair` removes that "
            "one line and keeps the log as it stood."
        )
    if result.malformed_lines:
        numbers = ", ".join(str(number) for number in result.malformed_lines)
        damage.append(
            f"line(s) {numbers} are corrupted — an interrupted append cannot produce this, "
            "so the storage itself is suspect. Do not truncate: restore from backup."
        )
    return damage


@observatory_app.command("verify")
def verify() -> None:
    """Audit the snapshot: the log and the stored bytes must account for each other.

    Exits non-zero when they do not, so a scheduled run can act on it. A
    tombstoned blob is not a defect — a recorded, explained removal is the
    sanctioned way for bytes to disappear.
    """
    result = verify_snapshot(ObservationLog(_root()))
    typer.echo(f"artifacts checked: {result.artifacts_checked}")
    if result.tombstoned:
        typer.echo(f"removed under a tombstone: {len(result.tombstoned)} (sanctioned)")
    for line in _log_damage(result):
        typer.echo(f"  {line}")
    for line in _defects(result):
        typer.echo(f"  {line}")
    if result.ok:
        typer.echo("snapshot ok")
        return
    typer.echo("snapshot NOT ok")
    raise typer.Exit(1)


@observatory_app.command("composition")
def composition() -> None:
    """What the archive is made of, and the failure rate it actually has.

    The log files a followed redirect as a `fetch_failure`, because that hop
    returned no bytes — and 75% of everything it calls a failure is one. Read
    by `kind` alone the archive reports a failure rate four times its real one
    (issue #188), and until this command there was nowhere to read it any other
    way: every summary the engine prints is derived from what a fetch returned,
    not from the log, so an auditor opening the archive wrote their own query
    and got the wrong number.

    One streamed pass. The counts are the answer, not the archive, so the
    memory this needs is the size of the report (issue #199).
    """
    log = ObservationLog(_root())
    found = ArchiveComposition()
    if not log.scan_into(collect_composition(found)).complete:
        typer.echo(
            "Refused: the observation log is damaged. Run `observatory verify` first.", err=True
        )
        raise typer.Exit(1)
    _echo_composition(found)


def _echo_composition(found: ArchiveComposition) -> None:
    """The composition, with both rates side by side.

    The wrong figure is printed next to the right one on purpose: somebody has
    already quoted it, and a report that silently replaces it leaves them
    unable to tell which number they had.
    """
    typer.echo(f"records:          {found.records}")
    typer.echo(f"  artifacts:      {found.artifacts}")
    typer.echo(f"  redirect hops:  {found.hops}  (recorded as fetch_failure, not failures)")
    typer.echo(f"  lost documents: {found.lost}")
    typer.echo(f"  tombstones:     {found.tombstones}")
    typer.echo(f"\nlost documents:   {found.loss_rate:.2%} of all records")
    typer.echo(f"counting kind alone: {found.naive_failure_rate:.2%} — the #188 figure, overstated")
    if found.by_outcome:
        typer.echo("\nfailures by outcome")
        for outcome, count in found.by_outcome.most_common():
            typer.echo(f"  {outcome:32} {count}")


@observatory_app.command("addresses")
def addresses() -> None:
    """Which registered sources share a server (issue #277).

    The budget is enforced per host, so two municipalities on one machine hold
    two budgets and the sweep hits that machine twice as hard as it believes.
    Read this before deciding what the budget should key on instead: keying on
    the resolved address is only an improvement if the register does not turn
    out to be one vendor platform (#194), and that is a measurement, not a
    guess.

    Reports rather than judges, and exits 0 either way. A shared address is a
    fact about the register, not a defect in it — several municipalities on one
    supplier is the normal shape of Norwegian municipal hosting.
    """
    registry = _load(_registry_file())
    if not registry.sources:
        typer.echo("No sources registered.")
        return
    _echo_addresses(resolve_register(registry, system_resolver))


def _echo_addresses(report: AddressReport) -> None:
    """The counts, then every shared address, then what would not resolve.

    Shared groups are printed in full rather than summarised: the decision is
    about which sources those are — a pair of neighbouring kommuner and twelve
    sites on one CMS are the same count and different findings.
    """
    active = [source for source in report.sources if source.active]
    typer.echo(f"registered sources: {len(report.sources)}  (active: {len(active)})")
    typer.echo(f"addresses shared by more than one source: {len(report.shared)}")
    typer.echo(
        f"active sources sharing an address with another active source: "
        f"{len(report.active_sources_sharing)} of {len(active)}"
    )
    for group in report.shared:
        _echo_shared_group(group)
    _echo_unresolved_hosts(report)


def _echo_shared_group(group: SharedAddress) -> None:
    """One address and its members, each named rather than counted."""
    typer.echo(
        f"\n  {group.address}  {len(group.sources)} sources ({len(group.active_sources)} active)"
    )
    for source in group.sources:
        state = "" if source.active else "  [inactive]"
        typer.echo(f"    {source.authority_id}  {source.name}  {source.canonical_domain}{state}")


def _echo_unresolved_hosts(report: AddressReport) -> None:
    """Hosts the resolver could not answer for, with the reason it gave."""
    unresolved = report.unresolved_sources
    if not unresolved:
        return
    typer.echo(f"\nsources with a host that did not resolve: {len(unresolved)}")
    for source in unresolved:
        for host in source.unresolved:
            typer.echo(f"    {source.authority_id}  {host.host}: {host.error}")


class _Starts(NamedTuple):
    """Where discovery starts, and whether that is a guess or a declaration."""

    urls: tuple[str, ...]
    probed: bool


def _require_documents(record: SourceRecord, result: DiscoveryResult, probed: bool) -> None:
    """Refuse loudly when discovery read nothing — a verdict, not a result.

    Zero documents means zero candidates, and `captured: 0` with exit code 0
    is indistinguishable from a healthy no-change run in a cron job
    (issue #151). The message says which thing failed: a probe that found
    nothing, or a declaration that could not be read.
    """
    if result.documents_read:
        return
    typer.echo(
        f"Refused: {record.authority_id} {_no_documents_reason(probed)}. "
        "Discovery read no documents, so there is nothing to capture.",
        err=True,
    )
    raise typer.Exit(1)


def _no_documents_reason(probed: bool) -> str:
    return (
        "declares no sitemap in its robots.txt, and nothing readable answered "
        "at the conventional /sitemap.xml"
        if probed
        else "declares sitemaps that could not be read"
    )


def _entry_points(fetcher: Fetcher, record: SourceRecord, given: list[str] | None) -> _Starts:
    """Where discovery starts: what was asked for, or what the source declares.

    The fallback reads the sitemaps out of the very ``robots.txt`` the
    reviewer checked when the source was activated — its URL is in the
    access-policy record, so nothing here has to guess a host. That keeps the
    entry points current when the site moves them, and keeps the crawl
    following what the source publishes rather than what someone once copied
    into a runbook.
    """
    if given:
        return _Starts(tuple(given), probed=False)
    policy = record.access_policy
    if policy is None:
        return _Starts((), probed=False)
    declared = fetcher.declared_sitemaps(policy.robots_txt_url, policy.user_agent)
    if declared:
        return _Starts(declared + record.listing_entry_points, probed=False)
    # Listings are the entry for the 116 municipalities that publish no sitemap
    # at all (#151), where discovery otherwise has none and a capture is a
    # structural no-op. They are added rather than substituted when a sitemap
    # does exist: a source can publish both, and the sitemap is the machine
    # index while a listing is the page a person reads — neither is a fallback
    # for the other.
    if record.listing_entry_points:
        return _Starts(record.listing_entry_points, probed=False)
    # A declaration is the exception, not the rule: 190 of 358 municipalities
    # serve a sitemap at the conventional path without declaring it (Phase A
    # sweep, 2026-08-20). The probe is an ordinary gated fetch against the
    # same root the reviewer checked, recorded like any other.
    return _Starts((policy.robots_txt_url.removesuffix("robots.txt") + "sitemap.xml",), probed=True)


def _activated_source(authority_id: str) -> SourceRecord:
    """The source, if it is cleared for capture. Refuse before any request.

    Unregistered and registered-but-not-activated end the same way on purpose:
    neither is permission to send traffic to someone else's server, and the
    difference is a detail of our bookkeeping, not of what we are allowed to do.
    """
    record = _load(_registry_file()).sources.get(authority_id)
    if record is None or not record.active:
        typer.echo(f"Refused: {authority_id} is not an activated source.", err=True)
        raise typer.Exit(1)
    return record


def _report_discovery(result: DiscoveryResult) -> None:
    """Everything found and everything declined, in full.

    No truncation: a listing that quietly stopped at the first N would read as
    "this is what the source publishes", which is the one claim the
    observatory must never make loosely.
    """
    typer.echo(f"documents read: {len(result.documents_read)}")
    for url in result.documents_read:
        typer.echo(f"  read {url}")
    typer.echo(f"candidates: {len(result.candidates)}")
    for candidate in result.candidates:
        typer.echo(f"  {candidate.discovery_method}  {candidate.url}")
    if result.skipped:
        typer.echo(f"skipped: {len(result.skipped)}")
        for skipped in result.skipped:
            typer.echo(f"  {skipped.reason}  {skipped.url}")


@observatory_app.command("discover")
def discover(
    authority_id: _AuthorityIdOption,
    entry_point: Annotated[
        list[str] | None,
        typer.Option(
            "--entry-point",
            help="Start here instead of the sitemaps robots.txt declares. Repeatable.",
        ),
    ] = None,
) -> None:
    """Read a source's sitemaps and feeds, and report the URLs worth observing.

    Discovery proposes; it never captures a candidate. That separation is what
    keeps a sitemap of 40,000 entries from turning one command into a mass
    download — deciding which candidates to observe is a later step, and a
    deliberate one.

    The documents discovery reads are themselves fetched through every gate
    and recorded in the log, because what a source listed on a given day is
    exactly the evidence this archive exists to keep.
    """
    record = _activated_source(authority_id)
    fetcher = Fetcher(_bound_register(), ObservationLog(_root()), httpx.Client())
    starts = _entry_points(fetcher, record, entry_point)
    result = Discoverer(fetcher, ObservationLog(_root())).discover(record, starts.urls)
    if not entry_point:
        _require_documents(record, result, starts.probed)
    _report_discovery(result)


def _remove_unfinished_record(log: ObservationLog, raw: bytes) -> None:
    """Back the log up, then drop its unfinished final record.

    The backup is written before anything is truncated, so an interruption
    here leaves the original intact rather than half-repaired. It refuses to
    overwrite an existing backup: a second repair silently clobbering the
    evidence from the first is the failure this command exists to avoid.
    """
    backup = log.log_path.with_name(log.log_path.name + ".bak")
    if backup.exists():
        typer.echo(f"Refused: {backup} already exists — move it aside first.", err=True)
        raise typer.Exit(1)
    backup.write_bytes(raw)
    body, separator, _ = raw.rpartition(b"\n")
    log.log_path.write_bytes(body + separator)
    typer.echo(f"removed. The log as it stood is kept at {backup}")


@observatory_app.command("repair")
def repair(
    apply: Annotated[
        bool, typer.Option("--apply", help="Actually remove it. Without this, nothing is written.")
    ] = False,
) -> None:
    """Drop an unfinished final record left by an interrupted run.

    Only that. The command refuses every other kind of damage, because only
    this one is safe to fix by deleting: an append that never finished
    describes a fetch that was never recorded, so the line carries nothing a
    reader could otherwise recover. A corrupted line anywhere else was written
    in full and then damaged — cutting it would destroy a record nobody has
    been told about, and it means the storage is failing rather than a run
    being interrupted.

    Reports without writing unless ``--apply`` is given. This edits evidence;
    it should take two deliberate steps, and a dry run has to be possible on a
    machine where the answer is "do not touch this".
    """
    log = ObservationLog(_root())
    scan = log.scan_damage()
    if scan.malformed_lines:
        numbers = ", ".join(str(number) for number in scan.malformed_lines)
        typer.echo(
            f"Refused: line(s) {numbers} are corrupted, which an interrupted append cannot "
            "produce. The storage is suspect — restore from backup rather than truncating.",
            err=True,
        )
        raise typer.Exit(1)
    if not scan.incomplete_final_record:
        typer.echo("nothing to repair")
        return
    raw = log.log_path.read_bytes()
    unfinished = raw.rpartition(b"\n")[2]
    typer.echo(f"unfinished final record: {len(unfinished)} bytes, {scan.records_read} intact")
    if not apply:
        typer.echo("dry run — nothing written. Re-run with --apply to remove it.")
        return
    _remove_unfinished_record(log, raw)


class _CaptureCounts(NamedTuple):
    """What one source's pass did, and whether the limit cut it short.

    ``capped`` is the point of the type: the three counters cannot express the
    difference between a sitemap that ran out and a pass that was stopped, and
    that difference is what makes a truncated source read as finished (#172).

    ``deferred`` is counted apart from ``unchanged`` because the two are
    different answers. A URL is unchanged when the site says so; it is deferred
    when it has refused us the same way and we are waiting before asking again
    (#204). Folding them together would hide a source whose candidate list has
    quietly become a list of dead ends.
    """

    captured: int
    failed: int
    unchanged: int
    capped: bool
    #: The pass stopped because a candidate's host is claimed by more than one
    #: activated source. Its own field rather than a kind of `capped`: both
    #: leave the source's archive incomplete, but a limit was our choice and
    #: this is a register that cannot name a publisher (#215).
    contested: bool = False
    deferred: int = 0
    #: Redirect hops followed on the way to the records above. Reported rather
    #: than left implicit: they are 75% of everything the log files as a
    #: failure (#188), and the pass that stopped counting them as failures
    #: must not be the pass that stopped mentioning them at all.
    redirects: int = 0
    #: The pass stopped because the register stopped filing a candidate under
    #: the row this run bound itself to. Apart from `contested` because the
    #: repair differs: that one needs a human to say which authority publishes
    #: a host, this one is already repaired and needs only a re-run (#221).
    stale: bool = False


def _capture_candidates(
    fetcher: Fetcher, candidates: tuple[Candidate, ...], state: CaptureState, limit: int
) -> _CaptureCounts:
    """Fetch what has changed, in order, and report each outcome as it happens.

    A run over a municipal site is hours of politely-spaced requests, so the
    per-URL line is not noise: it is the only way an operator can tell a slow
    run from a stuck one.
    """
    captured = failed = skipped = deferred = hops = 0
    # One instant for the whole pass: a clock read per candidate would let two
    # candidates observed at the same moment fall on opposite sides of the
    # re-check window, for no reason a reader could reconstruct later.
    now = datetime.now(UTC)
    for candidate in candidates:
        if not worth_capturing(candidate, state, now):
            if candidate.url in state.observed:
                skipped += 1
            else:
                deferred += 1
            continue
        if limit and captured + failed >= limit:
            typer.echo(f"stopping at --limit {limit}")
            return _CaptureCounts(captured, failed, skipped, True, False, deferred, hops)
        try:
            record = fetcher.capture(candidate.url, candidate.discovery_method)
        except (AmbiguousSourceError, StaleSourceError) as exc:
            # A refusal about the register, not about the page. Either it cannot
            # name one authority for this host — discovery cleared the source's
            # own host, but a candidate may sit on a subdomain a second source
            # also claims (#215) — or it no longer names the one this run bound
            # itself to (#221). Reaching either as a traceback would end the
            # pass with an empty stderr and the records already appended
            # unexplained (#208's shape).
            typer.echo(f"Refused: {exc}", err=True)
            stale = isinstance(exc, StaleSourceError)
            return _CaptureCounts(
                captured, failed, skipped, False, not stale, deferred, hops, stale
            )
        hops += len(record.provenance.redirect_chain)
        if isinstance(record, ArtifactObservation):
            captured += 1
            typer.echo(f"  {record.http_status}  {candidate.url}")
        else:
            failed += 1
            typer.echo(f"  {record.outcome}  {candidate.url}")
    return _CaptureCounts(captured, failed, skipped, False, False, deferred, hops)


@observatory_app.command("capture")
def capture(
    authority_id: _AuthorityIdOption,
    limit: Annotated[
        int,
        typer.Option("--limit", min=0, help="Stop after this many fetches. 0 means no bound."),
    ] = 0,
) -> None:
    """Observe what discovery proposes, skipping what has not changed since.

    Discovery runs first, every time, so the candidate list is the one the
    source publishes now rather than one cached from an earlier day.

    A candidate is skipped only when the site's own ``lastmod`` predates an
    observation we already hold of that URL. Every other case is fetched:
    declining to look is the one mistake this archive cannot undo later.

    An interrupted run needs no resuming. Each observation is appended as it
    happens, so running the command again picks up where it stopped — the
    pages already captured now fail that same freshness test.
    """
    record = _activated_source(authority_id)
    log = ObservationLog(_root())
    state = _capture_state(log, record.authority_id)
    fetcher = Fetcher(_bound_register(), log, httpx.Client())
    try:
        starts = _entry_points(fetcher, record, None)
        result = Discoverer(fetcher, log).discover(record, starts.urls)
    except (AmbiguousSourceError, StaleSourceError) as exc:
        typer.echo(f"Refused: {exc}", err=True)
        raise typer.Exit(1) from exc
    _require_documents(record, result, starts.probed)
    typer.echo(f"candidates: {len(result.candidates)}")
    counts = _capture_candidates(fetcher, result.candidates, state, limit)
    typer.echo(_capture_summary(counts))
    _refuse_incomplete(record, counts)


def _refuse_incomplete(record: SourceRecord, counts: _CaptureCounts) -> None:
    """Name what is missing from this source's archive, then exit 1.

    The records already appended stay; what stopped is the rest of the pass.
    Saying so is the difference between an incomplete archive an operator knows
    about and one that reads as finished.
    """
    if counts.contested:
        remedy = "the register names one authority for that host"
    elif counts.stale:
        remedy = "it is captured again under the row the register holds now"
    else:
        return
    typer.echo(
        f"  abandoned: {record.authority_id} partway — the archive for this source "
        f"is incomplete until {remedy}",
        err=True,
    )
    raise typer.Exit(1)


def _capture_summary(counts: _CaptureCounts) -> str:
    """The line a whole pass is read from, and the one the fleet greps.

    ``deferred`` is appended rather than inserted: the prefix is what an
    operator's scripts match on to tell a finished source from a running one,
    and a new counter must not move it.
    """
    return (
        f"captured: {counts.captured} | failed: {counts.failed} "
        f"| unchanged since last seen: {counts.unchanged} "
        f"| deferred after repeated failure: {counts.deferred} "
        f"| redirect hops: {counts.redirects}"
    )


def _capture_state(log: ObservationLog, authority_id: str | None) -> CaptureState:
    """What the log already knows about this source, or refuse a damaged log.

    Folded out of the log rather than materialised from it. The map is the
    whole reason the log is read here, and it is smaller than the log by
    orders of magnitude — 81,408 URLs against 610,850 records when this was
    written, which `capture` re-read in full, every round, at 2.13 GB a time
    (issue #199).

    ``authority_id`` narrows the fold to the source being captured; None asks
    about every source, which is what a sweep over the whole register needs.

    The damaged-log refusal lives here because both callers must make it, and
    must make it identically: a fold that quietly skipped unreadable lines
    would answer "never seen" for pages the archive holds, and every one of
    them would be re-fetched as though the observation had never happened.

    The register-wide fold (None) goes through the derived freshness index
    (issue #201) so a sweep's cost stops growing with the archive; the
    index path returns the identical fold and the identical damage verdict,
    or rebuilds. A narrowed fold is a different function of the log and
    stays on the direct read.
    """
    if authority_id is None:
        state, scan = indexed_capture_state(log)
    else:
        state = CaptureState.empty()
        scan = log.scan_into(collect_capture_state(state, authority_id))
    if not scan.complete:
        typer.echo(
            "Refused: the observation log is damaged. Run `observatory verify` first.", err=True
        )
        raise typer.Exit(1)
    return state


class _SweepTotals(NamedTuple):
    """Running counts over a whole sweep, so the run can describe itself."""

    refused: int = 0
    captured: int = 0
    failed: int = 0
    unchanged: int = 0
    capped: int = 0
    held: int = 0
    deferred: int = 0
    withdrawn: int = 0

    def plus(self, other: "_SweepTotals") -> "_SweepTotals":
        return _SweepTotals(
            refused=self.refused + other.refused,
            captured=self.captured + other.captured,
            failed=self.failed + other.failed,
            unchanged=self.unchanged + other.unchanged,
            capped=self.capped + other.capped,
            held=self.held + other.held,
            deferred=self.deferred + other.deferred,
            withdrawn=self.withdrawn + other.withdrawn,
        )


class _Lane(NamedTuple):
    """What every source's pass shares, so re-binding stays a per-source act."""

    log: ObservationLog
    state: CaptureState
    client: httpx.Client
    limit: int


def _sweep_one(
    fetcher: Fetcher,
    log: ObservationLog,
    record: SourceRecord,
    state: CaptureState,
    limit: int,
) -> _SweepTotals:
    """One source of a sweep, as counts; ``refused`` is 1 when it refused.

    The sweep continues either way — one municipality's missing sitemap must
    not cost the other two hundred their day's observations, and each host
    waits out only its own rate limit — but the refusal stays on stderr and
    moves the sweep's exit code.
    """
    try:
        starts = _entry_points(fetcher, record, None)
        result = Discoverer(fetcher, log).discover(record, starts.urls)
    except (AmbiguousSourceError, StaleSourceError) as exc:
        # A refusal, not a crash. One unreconcilable row — or one repaired
        # under this run — must not cost the other two hundred municipalities
        # their night's observations, and it must not pass quietly either, so
        # it moves the sweep's exit code the way every other refusal does.
        typer.echo(f"  refused: {record.authority_id} {exc}", err=True)
        return _SweepTotals(refused=1)
    if not result.documents_read:
        typer.echo(
            f"  refused: {record.authority_id} {_no_documents_reason(starts.probed)}", err=True
        )
        return _SweepTotals(refused=1)
    typer.echo(f"candidates: {len(result.candidates)}")
    counts = _capture_candidates(fetcher, result.candidates, state, limit)
    typer.echo(_capture_summary(counts))
    if counts.capped:
        # Loud on stderr, like a refusal: a source stopped by the limit was
        # truncated, and the whole point of #172 is that this is otherwise
        # indistinguishable from a source that simply ran out of pages.
        typer.echo(f"  capped: {record.authority_id} stopped at --limit {limit}", err=True)
    return _SweepTotals(
        refused=1 if _abandoned(record, counts) else 0,
        captured=counts.captured,
        failed=counts.failed,
        unchanged=counts.unchanged,
        capped=1 if counts.capped else 0,
        deferred=counts.deferred,
    )


#: Why a pass stopped before the source ran out of candidates. Both leave that
#: source's archive incomplete and both are the register's fault, but they are
#: different repairs: one needs a human to say which authority publishes a host,
#: the other has already had one and needs only another pass.
_CONTESTED = "a candidate's host is claimed by more than one activated source"
_STALE = "the register row it was bound to is not the row on disk any more"


def _abandoned(record: SourceRecord, counts: _CaptureCounts) -> bool:
    """Report a pass that stopped early, and say whether it was a refusal.

    Counted as a refusal so the sweep degrades, but the counts it did collect
    are kept: those pages are in the archive whatever the register says, and
    reporting zero would be a second untruth.
    """
    if counts.contested:
        reason = _CONTESTED
    elif counts.stale:
        reason = _STALE
    else:
        return False
    typer.echo(f"  refused: {record.authority_id} abandoned partway — {reason}", err=True)
    return True


@observatory_app.command("capture-all")
def capture_all(
    limit: Annotated[
        int,
        typer.Option(
            "--limit", min=0, help="Stop after this many fetches per source. 0 means no bound."
        ),
    ] = 0,
) -> None:
    """Sweep every activated source once, in authority-id order.

    The steady-state cycle: after a source's bootstrap, a sweep is one
    sitemap read plus whatever changed since — minutes per source — so a
    sequential pass over the whole register fits a nightly cron. Exit code 0
    means every source was observed; any refusal makes it 1, because a sweep
    that quietly skipped a source would be the silent zero of issue #151 at
    fleet scale.
    """
    root = _root()
    try:
        with exclusive_workload(OBSERVATORY_WORKLOAD):
            run = _sweep(root, limit)
    except ExclusiveWorkloadHeldError as exc:
        # A hand-run sweep defers exactly like the scheduled one; it just has
        # no run record to leave, because it never had one for failures.
        typer.echo(f"OBSERVATORY SWEEP DEFERRED\nreason: {_EXCLUSIVE_WORKLOAD}\n{exc}", err=True)
        raise typer.Exit(1) from exc
    if run.status != "success":
        # The exit code follows the recorded status, and a capped source makes
        # that status degraded: the sweep ran but did not finish observing.
        raise typer.Exit(1)


def _sweep(root: ObservatoryRoot, limit: int) -> SweepRun:
    """Sweep every activated source once, and return the run it recorded.

    Returning the record rather than leaving the caller to find it is the whole
    point: a caller that reads back "the latest run" is inferring identity from
    a timestamp, and a timestamp cannot say which invocation wrote something.
    Two sweeps overlapping — an operator running one by hand while the nightly
    fires — is enough to make one report the other's outcome as its own.

    The register is bound once per source rather than once per run. A pass over
    two hundred municipalities takes days — 141 hours on 2026-09-03 — and the
    register loaded at the start is not the one an operator is looking at by
    the end (issue #221).
    """
    started_at = datetime.now(UTC)
    active = [record.authority_id for record in _active_sources()]
    log, state = _sweep_inputs(root)
    lane = _Lane(log, state, httpx.Client(), limit)
    totals = _SweepTotals()
    for authority_id in active:
        totals = totals.plus(_sweep_source(authority_id, lane, started_at))
    _echo_sweep_outcome(totals, len(active))
    return _record_sweep(root, started_at, len(active), totals)


def _sweep_source(authority_id: str, lane: _Lane, now: datetime) -> _SweepTotals:
    """One source's pass, bound to the register as it stands at its turn.

    The scope of the run is the ids the register held when it began; which row
    each id names is read again here. Growing the list under the loop would
    make "197 of 198" a number nothing could check, and a source activated
    mid-run loses nothing: the next sweep begins with it.
    """
    register = _bound_register()
    record = register.activated(authority_id)
    if record is None:
        typer.echo(f"== {authority_id}")
        typer.echo(f"  withdrawn: {authority_id} is no longer an activated source")
        return _SweepTotals(withdrawn=1)
    typer.echo(f"== {record.authority_id} {record.name}")
    if _held(record, now):
        return _SweepTotals(held=1)
    fetcher = Fetcher(register, lane.log, lane.client)
    return _sweep_one(fetcher, lane.log, record, lane.state, lane.limit)


def _echo_sweep_outcome(totals: _SweepTotals, active: int) -> None:
    """Every tally the run did not end clean on, each on its own stream.

    A withdrawal is stdout rather than stderr: the operator asked for it, so it
    is a fact about the run and not a fault in it. It is still said aloud — a
    source that quietly stopped being swept reads as an archive with nothing
    missing, which is #151's silent zero (issue #221).
    """
    if totals.refused:
        typer.echo(f"sources refused: {totals.refused} of {active}", err=True)
    if totals.capped:
        typer.echo(f"sources capped: {totals.capped} of {active}", err=True)
    if totals.held:
        typer.echo(f"sources held under a verdict: {totals.held} of {active}")
    if totals.withdrawn:
        typer.echo(f"sources withdrawn mid-sweep: {totals.withdrawn} of {active}")


def _held(record: SourceRecord, now: datetime) -> bool:
    """Whether a recorded verdict spares this source the sweep, said aloud.

    A verdict that has not reached its re-check date is the record of an
    investigation already done; sending the source down the same dead path
    nightly would re-derive it and reach the same silent zero (#195). One that
    is due is not a skip: the re-check is the deliberate act the expiry
    exists for, and a source that refuses again refuses loudly.
    """
    verdict = record.capture_verdict
    if verdict is None or verdict.due(now):
        return False
    typer.echo(
        f"  held: {record.authority_id} under {verdict.outcome} "
        f"until {verdict.recheck_after.isoformat(timespec='seconds')}"
    )
    return True


def _active_sources() -> list[SourceRecord]:
    active = [r for _, r in sorted(_load(_registry_file()).sources.items()) if r.active]
    if not active:
        typer.echo("Refused: no activated sources.", err=True)
        raise typer.Exit(1)
    return active


def _sweep_inputs(root: ObservatoryRoot) -> tuple[ObservationLog, CaptureState]:
    """The log to append to and what has already been seen.

    A damaged log refuses here, before anything is fetched, and therefore
    before a sweep run is recorded. That path is the nightly wrapper's to
    report as FAILED: it runs `observatory verify` in preflight, which is the
    one place that can tell "the archive is unreadable" from "the archive is
    not even mounted".
    """
    log = ObservationLog(root)
    return log, _capture_state(log, None)


def _record_sweep(
    root: ObservatoryRoot, started_at: datetime, active: int, totals: _SweepTotals
) -> SweepRun:
    """Record that this sweep happened, and how completely.

    Written before the exit code is raised, so a degraded sweep leaves the same
    evidence a clean one does — the run that refused a source is exactly the
    run somebody will want to read tomorrow.
    """
    run = SweepRun(
        run_id=started_at.isoformat(),
        started_at=started_at,
        finished_at=datetime.now(UTC),
        active_sources=active,
        sources_completed=active - totals.refused - totals.held - totals.withdrawn,
        sources_refused=totals.refused,
        sources_capped=totals.capped,
        sources_held=totals.held,
        sources_withdrawn=totals.withdrawn,
        captured=totals.captured,
        failed_fetches=totals.failed,
        unchanged=totals.unchanged,
        deferred=totals.deferred,
        status=sweep_status(active=active, refused=totals.refused, capped=totals.capped),
        # A register with nothing to sweep is a failure with a nameable
        # cause, not a green run over an empty list. `capture-all` refuses
        # before reaching here, but the recorder must stay total: the one
        # caller that does produce it must not produce a reasonless failure.
        failure_reason=_NO_ACTIVE_SOURCES if active == 0 else None,
    )
    append_sweep_run(root, run)
    return run


def _hm(delta: timedelta) -> str:
    """A duration as hours and minutes, e.g. ``1h16m``."""
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes // 60}h{minutes % 60:02d}m"


def _latest_sweep(root: ObservatoryRoot) -> SweepRun | None:
    try:
        return latest_sweep_run(sweeps_path(root))
    except LogIntegrityError as exc:
        typer.echo(f"Refused: {exc}", err=True)
        raise typer.Exit(1) from exc


def _echo_sources(registry: SourceRegistry) -> None:
    active = sum(1 for record in registry.sources.values() if record.active)
    typer.echo("Sources")
    typer.echo(f"  registered: {len(registry.sources)}")
    typer.echo(f"  active:     {active}")
    _echo_contested_domains(registry)
    _echo_verdicts(registry)


def _echo_contested_domains(registry: SourceRegistry) -> None:
    """Domains claimed by more than one source, named rather than counted.

    Nothing else looks at the register as a whole. A sweep meets this state one
    source at a time and only when it reaches a claimant, which in #215 meant
    five nights of filing one municipality's pages under another's name while
    every report said 201 registered, 201 active.
    """
    contested = domains_claimed_twice(registry)
    if not contested:
        return
    typer.echo(f"  domains claimed twice: {len(contested)}  (capture refused on each)")
    for domain, records in sorted(contested.items()):
        named = ", ".join(f"{r.authority_id} {r.name}" for r in records)
        typer.echo(f"    {domain}: {named}")


def _echo_verdicts(registry: SourceRegistry) -> None:
    """Held sources stay on the report, and say when they are due again.

    A verdict that removed its source from view would be #151's silent zero
    one level up: the archive reads as complete because the sources that
    produce nothing have stopped being counted (#195).
    """
    held = [r.capture_verdict for r in registry.sources.values() if r.capture_verdict is not None]
    if not held:
        return
    now = datetime.now(UTC)
    typer.echo(f"  held under a verdict: {len(held)}")
    typer.echo(f"  due for re-check: {sum(1 for verdict in held if verdict.due(now))}")


def _echo_last_sweep(run: SweepRun | None) -> None:
    typer.echo("\nLast sweep")
    if run is None:
        typer.echo("  never")
        return
    typer.echo(f"  started:    {run.started_at.isoformat(timespec='seconds')}")
    typer.echo(f"  finished:   {run.finished_at.isoformat(timespec='seconds')}")
    typer.echo(f"  duration:   {_hm(run.finished_at - run.started_at)}")
    typer.echo(f"  completed:  {run.sources_completed} / {run.active_sources}")
    typer.echo(f"  refused:    {run.sources_refused}")
    typer.echo(f"  capped:     {run.sources_capped}")
    typer.echo(f"  held:       {run.sources_held}")
    typer.echo(f"  withdrawn:  {run.sources_withdrawn}")
    typer.echo(
        f"  captured:   {run.captured} | unchanged: {run.unchanged} | deferred: {run.deferred}"
    )
    typer.echo(f"  status:     {run.status.upper()}")


def _echo_cadence(state: CadenceState, run: SweepRun | None) -> None:
    """Render the cadence, distinguishing the two ways an age can be missing.

    "Never swept" beside a printed last sweep would contradict itself; the
    other case is a run stamped ahead of the clock, which is worth naming
    because it is the one that would otherwise have read as fresh.
    """
    unknown = "never swept" if run is None else "unknown — last sweep is stamped ahead of the clock"
    typer.echo("\nCadence")
    typer.echo(f"  target:     {_hm(OBSERVATION_SLA)}")
    typer.echo(f"  age:        {_hm(state.age) if state.age is not None else unknown}")
    typer.echo(f"  deadline:   {_hm(SWEEP_DEADLINE)}")
    typer.echo(f"  state:      {'OVERDUE' if state.overdue else 'OK'}")


#: Preflight verdicts. Strings rather than an enum because they are written
#: into the run record and read by a human at 03:00, not branched on.
_STORAGE_UNAVAILABLE = "storage_unavailable"
_REGISTRY_MISSING = "registry_missing"
_LOG_DAMAGED = "observation_log_damaged"
_NO_ACTIVE_SOURCES = "no_active_sources"
#: Not a preflight verdict: the ground was fine, the host was reserved. The
#: sweep did not start and says so (issue #169).
_EXCLUSIVE_WORKLOAD = "deferred_exclusive_workload"
#: The name a sweep writes into the host lock, so a refused benchmark can say
#: who held it.
OBSERVATORY_WORKLOAD = "observatory-sweep"


def _preflight(root: ObservatoryRoot) -> str | None:
    """What stops the sweep before it starts, or None to proceed.

    Ordered by how early the failure is: a missing archive is not the same
    problem as a damaged log, and answering "why is it red" with the wrong one
    sends the operator to the wrong place.

    A register with nothing activated is checked here rather than left to
    `capture-all`, which refuses before it can record anything. A scheduled run
    that observed nothing must leave telemetry saying why — otherwise the night
    simply vanishes from the sweep history, and `no_active_sources` would be a
    name for a state nothing could ever write.
    """
    if not root.path.exists():
        return _STORAGE_UNAVAILABLE
    if not registry_path(root).exists():
        return _REGISTRY_MISSING
    if not any(record.active for record in _load(registry_path(root)).sources.values()):
        return _NO_ACTIVE_SOURCES
    if not ObservationLog(root).scan_damage().complete:
        return _LOG_DAMAGED
    return None


def _failed_run(started_at: datetime, reason: str) -> SweepRun:
    """A run that could not sweep anything, as a record.

    Built before it is stored, because the case that most needs reporting —
    the archive is not mounted — is exactly the case with nowhere to store it.
    The dead-man switch does not need the archive to speak.
    """
    return SweepRun(
        run_id=started_at.isoformat(),
        started_at=started_at,
        finished_at=datetime.now(UTC),
        active_sources=0,
        sources_completed=0,
        sources_refused=0,
        captured=0,
        failed_fetches=0,
        unchanged=0,
        status="failed",
        failure_reason=reason,
    )


def _report(run: SweepRun) -> None:
    """Send the outbound heartbeat, loudly enough to notice when it fails.

    Never fatal: a monitoring endpoint being unreachable must not turn a
    completed sweep into a failed command. Never silent either — a switch that
    quietly stopped reporting looks exactly like a dead machine, and the
    operator should learn that from this line rather than from a false alarm.
    """
    base = heartbeat_url()
    if base is None:
        typer.echo("heartbeat: not configured; no dead-man switch is armed", err=True)
        return
    with httpx.Client() as client:
        if send_heartbeat(base, run, client):
            typer.echo(f"heartbeat: reported {run.status}")
        else:
            typer.echo(f"heartbeat: NOT DELIVERED (run was {run.status})", err=True)


@observatory_app.command("nightly")
def nightly(
    limit: Annotated[
        int,
        typer.Option(
            "--limit", min=0, help="Stop after this many fetches per source. 0 means no bound."
        ),
    ] = 0,
) -> None:
    """The scheduled entry point: check the ground, then sweep.

    Everything checkable without touching a server is checked first, because a
    sweep that starts on a half-present archive is worse than one that refuses:
    it produces records nobody can trust.

    There is deliberately **no fallback** when the archive is absent. Quietly
    creating a second observatory on the internal disk is the most damaging
    thing this command could do to be helpful — two archives, each partial,
    neither knowing about the other.

    That case is also the one it cannot record: with nowhere to write, the only
    output is this message and the exit code, and the remote dead-man switch is
    what turns the resulting silence into an alarm.
    """
    started_at = datetime.now(UTC)
    root = _root()
    reason = _preflight(root)
    if reason is not None:
        typer.echo(f"OBSERVATORY SWEEP FAILED\nreason: {reason}", err=True)
        typer.echo(f"expected: {root.path}", err=True)
        failed = _failed_run(started_at, reason)
        if reason != _STORAGE_UNAVAILABLE:
            append_sweep_run(root, failed)
        _report(failed)
        raise typer.Exit(1)
    # After preflight, not before: the deferral record needs an archive to
    # land in, and preflight is what establishes there is one. Held across the
    # whole sweep — a benchmark starting mid-sweep is the overlap the lock is
    # for (issue #169). Held by the benchmark -> defer: record it, exit 1, the
    # next scheduled sweep picks up. Never wait.
    try:
        with exclusive_workload(OBSERVATORY_WORKLOAD):
            run = _sweep(root, limit)
    except ExclusiveWorkloadHeldError as exc:
        typer.echo(f"OBSERVATORY SWEEP DEFERRED\nreason: {_EXCLUSIVE_WORKLOAD}\n{exc}", err=True)
        deferred = _failed_run(started_at, _EXCLUSIVE_WORKLOAD)
        append_sweep_run(root, deferred)
        _report(deferred)
        raise typer.Exit(1) from exc
    # Reported from the record this invocation holds, never from whatever the
    # log happens to end with. A degraded sweep still reports: it ran, and
    # liveness is what the switch guards.
    _report(run)
    if run.status != "success":
        raise typer.Exit(1)


@observatory_app.command("status")
def status() -> None:
    """Is the archive actually being observed?

    Exists so that question never again means grepping a 12 GB log. The exit
    code answers it too — 1 when no sweep has begun inside the deadline —
    because the same command then serves a monitor, and a health check nobody
    can script is a health check nobody runs.
    """
    root = _root()
    latest = _latest_sweep(root)
    state = cadence_state(latest)
    _echo_sources(_load(_registry_file()))
    _echo_last_sweep(latest)
    _echo_cadence(state, latest)
    if state.overdue:
        raise typer.Exit(1)
