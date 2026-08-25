"""CLI for the source registry: register a source, then activate it.

The two steps are separate commands because ADR-0010 §4 makes them separate
decisions. Registering says *this authority is eligible* — it is an official
municipal or fylkeskommune site, so it is a candidate. Activating says *a
named human read this source's ``robots.txt`` and terms and concluded capture
is permitted*, and only that unlocks traffic against someone else's server.

The access-policy check arrives as a JSON document rather than as flags. It is
the record of a human decision, it has to answer "why was this activated?"
months later, and a conclusion typed into a shell leaves nothing to re-read.

There is no ``--registry`` option. The path comes from
``LOVSPOR_OBSERVATORY_ROOT`` and therefore through the ADR-0010 §5 storage
check; a flag would be a one-word way to write access-policy records into the
engine repository, which is the thing the boundary exists to prevent.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, NamedTuple

import httpx
import typer
from pydantic import ValidationError

from lovspor.errors import (
    ConfigError,
    LogIntegrityError,
    ParseError,
    SourceNotActivatedError,
    StorageBoundaryError,
)
from lovspor.observatory.discovery import Candidate, Discoverer, DiscoveryResult
from lovspor.observatory.fetch import Fetcher
from lovspor.observatory.freshness import latest_observations, worth_capturing
from lovspor.observatory.log import ObservationLog, SnapshotVerification, verify_snapshot
from lovspor.observatory.model import ArtifactObservation
from lovspor.observatory.registry import (
    SourceRecord,
    SourceRegistry,
    activate,
    read_access_policy_check,
    read_registry,
    registry_path,
    write_registry,
)
from lovspor.observatory.storage import ObservatoryRoot, observatory_root
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

observatory_app = typer.Typer(
    name="observatory",
    help="Register and activate local-law capture sources (ADR-0010).",
    no_args_is_help=True,
)

_AuthorityIdOption = Annotated[
    str,
    typer.Option(
        "--id",
        help="Official authority identifier — kommunenummer or fylkesnummer, never a name.",
    ),
]


def _root() -> ObservatoryRoot:
    """Resolve the archive root, or explain why the environment cannot.

    A missing ``LOVSPOR_OBSERVATORY_ROOT`` and a root inside the engine repo
    or the corpus are both ordinary operator mistakes, not bugs, so they read
    as a message rather than a traceback.
    """
    try:
        return observatory_root()
    except (ConfigError, StorageBoundaryError) as exc:
        typer.echo(f"Cannot locate the observatory archive: {exc}", err=True)
        raise typer.Exit(1) from exc


def _registry_file() -> Path:
    return registry_path(_root())


def _load(path: Path) -> SourceRegistry:
    """The registry, or an empty one the first time.

    A registry that exists but does not parse is refused rather than treated
    as absent. Falling back to an empty registry would silently drop every
    recorded access-policy check and let the next `register-source` write a
    fresh file over the evidence that a human cleared those sources.
    """
    if not path.exists():
        return SourceRegistry()
    try:
        return read_registry(path)
    except ParseError as exc:
        typer.echo(f"Refused: {exc}", err=True)
        raise typer.Exit(1) from exc


def _save(sources: dict[str, SourceRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_registry(SourceRegistry(sources=sources), path)


@observatory_app.command("register-source")
def register_source(
    authority_id: _AuthorityIdOption,
    name: Annotated[str, typer.Option("--name", help="Authority name, for humans reading this.")],
    domain: Annotated[
        str, typer.Option("--domain", help="Canonical domain; subdomains are covered.")
    ],
    authority_type: Annotated[
        str, typer.Option("--type", help="kommune or fylkeskommune.")
    ] = "kommune",
) -> None:
    """Record a source as eligible. It is not activated, and nothing may fetch it yet."""
    path = _registry_file()
    registry = _load(path)
    if authority_id in registry.sources:
        typer.echo(f"{authority_id} is already registered; refusing to overwrite it.", err=True)
        raise typer.Exit(1)
    try:
        record = SourceRecord.model_validate(
            {
                "authority_type": authority_type,
                "authority_id": authority_id,
                "name": name,
                "canonical_domain": domain,
            }
        )
    except ValidationError as exc:
        # A blank name or an authority type the model does not know are
        # mistyped arguments, not bugs. The model stays the single place that
        # decides what a source record may look like; this only keeps its
        # verdict from reaching the operator as a traceback.
        typer.echo(f"Refused: {exc}", err=True)
        raise typer.Exit(1) from exc
    _save({**registry.sources, authority_id: record}, path)
    typer.echo(f"Registered {authority_id} ({name}) -> {domain} [inactive]")
    typer.echo("Capture stays refused until an access-policy check is recorded.")


@observatory_app.command("activate-source")
def activate_source(
    authority_id: _AuthorityIdOption,
    check: Annotated[
        Path,
        typer.Option("--check", help="JSON access-policy check: the reviewer's recorded outcome."),
    ],
) -> None:
    """Attach a reviewer's access-policy check and activate the source."""
    path = _registry_file()
    registry = _load(path)
    record = registry.sources.get(authority_id)
    if record is None:
        typer.echo(f"{authority_id} is not registered; run register-source first.", err=True)
        raise typer.Exit(1)
    try:
        activated = activate(record, read_access_policy_check(check))
    except (ParseError, SourceNotActivatedError) as exc:
        # An unreadable check and a check that refuses capture end the same
        # way on purpose: neither is evidence that this source may be fetched.
        typer.echo(f"Refused: {exc}", err=True)
        raise typer.Exit(1) from exc
    except OSError as exc:
        # Absent, a directory, unreadable — every way the filesystem can fail
        # to hand over the document ends the same way. None of them is
        # evidence that this source may be fetched, and a traceback would say
        # "bug" about what is an ordinary mistyped path.
        typer.echo(f"Refused: cannot read the access-policy check at {check}: {exc}", err=True)
        raise typer.Exit(1) from exc
    _save({**registry.sources, authority_id: activated}, path)
    policy = activated.access_policy
    assert policy is not None  # noqa: S101 — activate() cannot return a cleared record without one
    typer.echo(f"Activated {authority_id} ({activated.name}) [{activated.canonical_domain}]")
    typer.echo(f"Reviewed by {policy.reviewed_by}; rate limit {policy.rate_limit_seconds}s")


@observatory_app.command("sources")
def list_sources() -> None:
    """List registered sources and whether capture is permitted."""
    registry = _load(_registry_file())
    if not registry.sources:
        typer.echo("No sources registered.")
        return
    for authority_id, record in sorted(registry.sources.items()):
        state = "active" if record.active else "inactive"
        typer.echo(f"{authority_id}  {record.name}  {record.canonical_domain}  [{state}]")
        policy = record.access_policy
        if policy is not None:
            typer.echo(
                f"    checked {policy.checked_at.date().isoformat()} by {policy.reviewed_by}; "
                f"rate limit {policy.rate_limit_seconds}s; UA {policy.user_agent}"
            )


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
    typer.echo(f"records read: {result.artifacts_checked}")
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
    declared = fetcher.declared_sitemaps(policy.robots_txt_url)
    if declared:
        return _Starts(declared, probed=False)
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
    fetcher = Fetcher(_load(_registry_file()), ObservationLog(_root()), httpx.Client())
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
    scan = log.scan()
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
    typer.echo(f"unfinished final record: {len(unfinished)} bytes, {len(scan.records)} intact")
    if not apply:
        typer.echo("dry run — nothing written. Re-run with --apply to remove it.")
        return
    _remove_unfinished_record(log, raw)


class _CaptureCounts(NamedTuple):
    """What one source's pass did, and whether the limit cut it short.

    ``capped`` is the point of the type: the three counters cannot express the
    difference between a sitemap that ran out and a pass that was stopped, and
    that difference is what makes a truncated source read as finished (#172).
    """

    captured: int
    failed: int
    unchanged: int
    capped: bool


def _capture_candidates(
    fetcher: Fetcher, candidates: tuple[Candidate, ...], observed: dict[str, datetime], limit: int
) -> _CaptureCounts:
    """Fetch what has changed, in order, and report each outcome as it happens.

    A run over a municipal site is hours of politely-spaced requests, so the
    per-URL line is not noise: it is the only way an operator can tell a slow
    run from a stuck one.
    """
    captured = failed = skipped = 0
    for candidate in candidates:
        if not worth_capturing(candidate, observed):
            skipped += 1
            continue
        if limit and captured + failed >= limit:
            typer.echo(f"stopping at --limit {limit}")
            return _CaptureCounts(captured, failed, skipped, capped=True)
        record = fetcher.capture(candidate.url, candidate.discovery_method)
        if isinstance(record, ArtifactObservation):
            captured += 1
            typer.echo(f"  {record.http_status}  {candidate.url}")
        else:
            failed += 1
            typer.echo(f"  {record.outcome}  {candidate.url}")
    return _CaptureCounts(captured, failed, skipped, capped=False)


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
    scan = log.scan()
    if not scan.complete:
        typer.echo(
            "Refused: the observation log is damaged. Run `observatory verify` first.", err=True
        )
        raise typer.Exit(1)
    fetcher = Fetcher(_load(_registry_file()), log, httpx.Client())
    starts = _entry_points(fetcher, record, None)
    result = Discoverer(fetcher, log).discover(record, starts.urls)
    _require_documents(record, result, starts.probed)
    typer.echo(f"candidates: {len(result.candidates)}")
    counts = _capture_candidates(
        fetcher, result.candidates, latest_observations(scan.records), limit
    )
    typer.echo(
        f"captured: {counts.captured} | failed: {counts.failed} "
        f"| unchanged since last seen: {counts.unchanged}"
    )


class _SweepTotals(NamedTuple):
    """Running counts over a whole sweep, so the run can describe itself."""

    refused: int = 0
    captured: int = 0
    failed: int = 0
    unchanged: int = 0
    capped: int = 0

    def plus(self, other: "_SweepTotals") -> "_SweepTotals":
        return _SweepTotals(
            refused=self.refused + other.refused,
            captured=self.captured + other.captured,
            failed=self.failed + other.failed,
            unchanged=self.unchanged + other.unchanged,
            capped=self.capped + other.capped,
        )


def _sweep_one(
    fetcher: Fetcher,
    log: ObservationLog,
    record: SourceRecord,
    observed: dict[str, datetime],
    limit: int,
) -> _SweepTotals:
    """One source of a sweep, as counts; ``refused`` is 1 when it refused.

    The sweep continues either way — one municipality's missing sitemap must
    not cost the other two hundred their day's observations, and each host
    waits out only its own rate limit — but the refusal stays on stderr and
    moves the sweep's exit code.
    """
    starts = _entry_points(fetcher, record, None)
    result = Discoverer(fetcher, log).discover(record, starts.urls)
    if not result.documents_read:
        typer.echo(
            f"  refused: {record.authority_id} {_no_documents_reason(starts.probed)}", err=True
        )
        return _SweepTotals(refused=1)
    typer.echo(f"candidates: {len(result.candidates)}")
    counts = _capture_candidates(fetcher, result.candidates, observed, limit)
    typer.echo(
        f"captured: {counts.captured} | failed: {counts.failed} "
        f"| unchanged since last seen: {counts.unchanged}"
    )
    if counts.capped:
        # Loud on stderr, like a refusal: a source stopped by the limit was
        # truncated, and the whole point of #172 is that this is otherwise
        # indistinguishable from a source that simply ran out of pages.
        typer.echo(f"  capped: {record.authority_id} stopped at --limit {limit}", err=True)
    return _SweepTotals(
        captured=counts.captured,
        failed=counts.failed,
        unchanged=counts.unchanged,
        capped=1 if counts.capped else 0,
    )


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
    started_at = datetime.now(UTC)
    root = _root()
    active = _active_sources()
    log, observed = _sweep_inputs(root)
    fetcher = Fetcher(_load(_registry_file()), log, httpx.Client())
    totals = _SweepTotals()
    for record in active:
        typer.echo(f"== {record.authority_id} {record.name}")
        totals = totals.plus(_sweep_one(fetcher, log, record, observed, limit))
    _record_sweep(root, started_at, len(active), totals)
    if totals.refused:
        typer.echo(f"sources refused: {totals.refused} of {len(active)}", err=True)
    if totals.capped:
        typer.echo(f"sources capped: {totals.capped} of {len(active)}", err=True)
    if totals.refused or totals.capped:
        # The exit code follows the recorded status, and a capped source makes
        # that status degraded: the sweep ran but did not finish observing.
        raise typer.Exit(1)


def _active_sources() -> list[SourceRecord]:
    active = [r for _, r in sorted(_load(_registry_file()).sources.items()) if r.active]
    if not active:
        typer.echo("Refused: no activated sources.", err=True)
        raise typer.Exit(1)
    return active


def _sweep_inputs(root: ObservatoryRoot) -> tuple[ObservationLog, dict[str, datetime]]:
    """The log to append to and what has already been seen.

    A damaged log refuses here, before anything is fetched, and therefore
    before a sweep run is recorded. That path is the nightly wrapper's to
    report as FAILED: it runs `observatory verify` in preflight, which is the
    one place that can tell "the archive is unreadable" from "the archive is
    not even mounted".
    """
    log = ObservationLog(root)
    scan = log.scan()
    if not scan.complete:
        typer.echo(
            "Refused: the observation log is damaged. Run `observatory verify` first.", err=True
        )
        raise typer.Exit(1)
    return log, latest_observations(scan.records)


def _record_sweep(
    root: ObservatoryRoot, started_at: datetime, active: int, totals: _SweepTotals
) -> None:
    """Record that this sweep happened, and how completely.

    Written before the exit code is raised, so a degraded sweep leaves the same
    evidence a clean one does — the run that refused a source is exactly the
    run somebody will want to read tomorrow.
    """
    append_sweep_run(
        root,
        SweepRun(
            run_id=started_at.isoformat(),
            started_at=started_at,
            finished_at=datetime.now(UTC),
            active_sources=active,
            sources_completed=active - totals.refused,
            sources_refused=totals.refused,
            sources_capped=totals.capped,
            captured=totals.captured,
            failed_fetches=totals.failed,
            unchanged=totals.unchanged,
            status=sweep_status(active=active, refused=totals.refused, capped=totals.capped),
            # A register with nothing to sweep is a failure with a nameable
            # cause, not a green run over an empty list. `capture-all` refuses
            # before reaching here, but the recorder must stay total: the one
            # caller that does produce it must not produce a reasonless failure.
            failure_reason=_NO_ACTIVE_SOURCES if active == 0 else None,
        ),
    )


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
    typer.echo(f"  captured:   {run.captured} | unchanged: {run.unchanged}")
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


def _preflight(root: ObservatoryRoot) -> str | None:
    """What stops the sweep before it starts, or None to proceed.

    Ordered by how early the failure is: a missing archive is not the same
    problem as a damaged log, and answering "why is it red" with the wrong one
    sends the operator to the wrong place.
    """
    if not root.path.exists():
        return _STORAGE_UNAVAILABLE
    if not registry_path(root).exists():
        return _REGISTRY_MISSING
    if not ObservationLog(root).scan().complete:
        return _LOG_DAMAGED
    return None


def _record_failed_sweep(root: ObservatoryRoot, started_at: datetime, reason: str) -> None:
    """A run that could not sweep anything, recorded so the gap has a cause."""
    append_sweep_run(
        root,
        SweepRun(
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
        ),
    )


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
        if reason != _STORAGE_UNAVAILABLE:
            _record_failed_sweep(root, started_at, reason)
        raise typer.Exit(1)
    capture_all(limit)


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
