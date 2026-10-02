"""Observatory commands that work the archive: capture, sweep, verify, report.

The register's own administration -- registering a source, activating it,
recording a verdict, replacing a domain -- lives in ``registry_commands``; the
acts that edit the log itself -- `repair` -- live in ``log_commands``; the
audits of the archive -- `verify`, `composition` -- in ``audit_commands``.
All four share ``app`` (the Typer instance each decorates) and
``registry_io`` (reading and writing the register), so none imports another
and no cycle is possible.
"""

import os
from datetime import UTC, datetime
from typing import Annotated, NamedTuple, NoReturn

import httpx
import typer

from lovspor.errors import (
    AmbiguousSourceError,
    LogIntegrityError,
    StaleSourceError,
)
from lovspor.exclusive_workload import ExclusiveWorkloadHeldError, exclusive_workload

# Imported for their registrations: the modules decorate `observatory_app` with
# the registry, log-editing and audit commands, and nothing here refers to
# their names.
from lovspor.observatory import audit_commands, log_commands, registry_commands  # noqa: F401
from lovspor.observatory.addresses import (
    AddressReport,
    SharedAddress,
    resolve_register,
    system_resolver,
)
from lovspor.observatory.app import _AuthorityIdOption, observatory_app
from lovspor.observatory.capture_pass import CaptureCounts, capture_proposals, capture_summary
from lovspor.observatory.catch_up import skip_catch_up
from lovspor.observatory.discovery import Discoverer, DiscoveryResult
from lovspor.observatory.engine import describe_engine
from lovspor.observatory.fetch import Fetcher
from lovspor.observatory.freshness import CaptureState, collect_capture_state
from lovspor.observatory.freshness_index import indexed_capture_state
from lovspor.observatory.heartbeat import report_run
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.registry import (
    SourceRecord,
    registry_path,
)
from lovspor.observatory.registry_io import (
    _bound_register,
    _load,
    _registry_file,
    _root,
)
from lovspor.observatory.selection import choose, selection_enabled
from lovspor.observatory.status_report import echo_status_report
from lovspor.observatory.storage import ObservatoryRoot
from lovspor.observatory.sweeps import (
    NO_ACTIVE_SOURCES,
    RunContext,
    SweepRun,
    SweepTotals,
    append_sweep_run,
    read_sweep_runs,
    sweeps_path,
)
from lovspor.observatory.triggers import running_sweep

# `observatory_app` is defined in `app` and re-exported here: `cli.py` imports it
# from this module, and both command modules decorate the same instance.
__all__ = ["observatory_app"]


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
    # A declaration is the exception: in the 2026-08-20 sweep (060d4cf) only 25
    # municipalities declared a sitemap, 190 more served one undeclared at the
    # conventional path. The probe is an ordinary gated fetch against the same
    # root the reviewer checked, recorded like any other.
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
    download — which candidates to observe is capture's choice (#348).

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

    A candidate is skipped when the site's own ``lastmod`` predates an
    observation we already hold of it, or, with selection on (#348), when its
    path names no regulation. Both are counted; every other case is fetched.

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
    selection = choose(result, record.listing_entry_points, selection_enabled())
    counts = capture_proposals(fetcher, selection, state, limit)
    typer.echo(capture_summary(counts))
    _refuse_incomplete(record, counts)


def _refuse_incomplete(record: SourceRecord, counts: CaptureCounts) -> None:
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
        scan = log.scan_corrected_into(collect_capture_state(state, authority_id))
    if not scan.complete:
        typer.echo(
            "Refused: the observation log is damaged. Run `observatory verify` first.", err=True
        )
        raise typer.Exit(1)
    return state


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
) -> SweepTotals:
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
        return SweepTotals(refused=1)
    if not result.documents_read:
        typer.echo(
            f"  refused: {record.authority_id} {_no_documents_reason(starts.probed)}", err=True
        )
        return SweepTotals(refused=1)
    selection = choose(result, record.listing_entry_points, selection_enabled())
    counts = capture_proposals(fetcher, selection, state, limit)
    typer.echo(capture_summary(counts))
    if counts.capped:
        # Loud on stderr, like a refusal: a source stopped by the limit was
        # truncated, and the whole point of #172 is that this is otherwise
        # indistinguishable from a source that simply ran out of pages.
        typer.echo(f"  capped: {record.authority_id} stopped at --limit {limit}", err=True)
    return _pass_totals(record, counts)


def _pass_totals(record: SourceRecord, counts: CaptureCounts) -> SweepTotals:
    """One source's pass as the sweep's running counts."""
    return SweepTotals(
        refused=1 if _abandoned(record, counts) else 0,
        captured=counts.captured,
        failed=counts.failed,
        unchanged=counts.unchanged,
        capped=1 if counts.capped else 0,
        deferred=counts.deferred,
        unselected=counts.unselected,
    )


#: Why a pass stopped before the source ran out of candidates. Both leave that
#: source's archive incomplete and both are the register's fault, but they are
#: different repairs: one needs a human to say which authority publishes a host,
#: the other has already had one and needs only another pass.
_CONTESTED = "a candidate's host is claimed by more than one activated source"
_STALE = "the register row it was bound to is not the row on disk any more"


def _abandoned(record: SourceRecord, counts: CaptureCounts) -> bool:
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
    totals = SweepTotals()
    for authority_id in active:
        totals = totals.plus(_sweep_source(authority_id, lane, started_at))
    _echo_sweep_outcome(totals, len(active))
    return _record_sweep(root, started_at, len(active), totals)


def _sweep_source(authority_id: str, lane: _Lane, now: datetime) -> SweepTotals:
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
        return SweepTotals(withdrawn=1)
    typer.echo(f"== {record.authority_id} {record.name}")
    if _held(record, now):
        return SweepTotals(held=1)
    fetcher = Fetcher(register, lane.log, lane.client)
    return _sweep_one(fetcher, lane.log, record, lane.state, lane.limit)


def _echo_sweep_outcome(totals: SweepTotals, active: int) -> None:
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
    if selection_enabled():
        typer.echo(f"candidates not selected by path: {totals.unselected} (selection on)")


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
    root: ObservatoryRoot, started_at: datetime, active: int, totals: SweepTotals
) -> SweepRun:
    """Record that this sweep happened, and how completely.

    Written before the exit code is raised, so a degraded sweep leaves the same
    evidence a clean one does — the run that refused a source is exactly the
    run somebody will want to read tomorrow.
    """
    context = RunContext(engine_commit=describe_engine().commit, selection=selection_enabled())
    run = totals.as_run(started_at, datetime.now(UTC), active, context)
    append_sweep_run(root, run)
    return run


def _sweep_runs(root: ObservatoryRoot) -> list[SweepRun]:
    try:
        return read_sweep_runs(sweeps_path(root))
    except LogIntegrityError as exc:
        typer.echo(f"Refused: {exc}", err=True)
        raise typer.Exit(1) from exc


#: Preflight verdicts. Strings rather than an enum because they are written
#: into the run record and read by a human at 03:00, not branched on.
_STORAGE_UNAVAILABLE = "storage_unavailable"
_REGISTRY_MISSING = "registry_missing"
_LOG_DAMAGED = "observation_log_damaged"
_ENGINE_NOT_PINNED = "engine_not_pinned"
#: Set to 1 in the scheduled job's environment: the sweep then refuses to run
#: from an engine checkout that is on a branch or dirty (issue #219). Opt-in,
#: because every developer checkout is on a branch and `nightly` must stay
#: runnable there; the launchd template arms it.
ENV_REQUIRE_PINNED_ENGINE = "LOVSPOR_OBSERVATORY_REQUIRE_PINNED_ENGINE"
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
        return NO_ACTIVE_SOURCES
    if not ObservationLog(root).scan_damage().complete:
        return _LOG_DAMAGED
    return _engine_pin_verdict()


def _engine_pin_verdict() -> str | None:
    """`engine_not_pinned` when the job demands a pinned engine and has none.

    A branch can move under a running job and local edits are unreviewed
    code; the lane worker refuses both and, since #219, so does the nightly.
    The refusal is recorded like any other preflight verdict, with the
    reason on stderr, so a stray `git checkout -b` reads as a red run and
    not as a night that never happened.
    """
    if os.environ.get(ENV_REQUIRE_PINNED_ENGINE, "").strip() != "1":
        return None
    checkout = describe_engine()
    if checkout.pinned is False:
        typer.echo(f"engine: {checkout.reason} ({checkout.commit})", err=True)
        return _ENGINE_NOT_PINNED
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
        engine_commit=describe_engine().commit,
    )


def _refuse_sweep(root: ObservatoryRoot, started_at: datetime, reason: str) -> NoReturn:
    typer.echo(f"OBSERVATORY SWEEP FAILED\nreason: {reason}", err=True)
    typer.echo(f"expected: {root.path}", err=True)
    failed = _failed_run(started_at, reason)
    if reason != _STORAGE_UNAVAILABLE:
        append_sweep_run(root, failed)
    report_run(failed)
    raise typer.Exit(1)


@observatory_app.command("nightly")
def nightly(
    limit: Annotated[
        int,
        typer.Option(
            "--limit", min=0, help="Stop after this many fetches per source. 0 means no bound."
        ),
    ] = 0,
    catch_up: Annotated[
        bool,
        typer.Option(
            "--catch-up", help="Off the 03:00 trigger, skip if a sweep started in the last 24h."
        ),
    ] = False,
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
    if catch_up and skip_catch_up(root, OBSERVATORY_WORKLOAD, started_at):
        return
    reason = _preflight(root)
    if reason is not None:
        _refuse_sweep(root, started_at, reason)
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
        report_run(deferred)
        raise typer.Exit(1) from exc
    # Reported from the record this invocation holds, never from whatever the
    # log happens to end with. A degraded sweep still reports: it ran, and
    # liveness is what the switch guards.
    report_run(run)
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
    runs = _sweep_runs(_root())
    running = running_sweep(OBSERVATORY_WORKLOAD)
    if echo_status_report(_load(_registry_file()), runs, running).overdue:
        raise typer.Exit(1)
