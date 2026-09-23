"""What `observatory status` prints, section by section.

Split out of commands.py so the operator report can grow — the dead-man
switch and the engine commit joined it under issues #347 and #219 — without
the command module growing with it.
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import typer

from lovspor.observatory.heartbeat import heartbeat_url
from lovspor.observatory.registry import SourceRegistry, domains_claimed_twice
from lovspor.observatory.sweeps import OBSERVATION_SLA, SWEEP_DEADLINE, CadenceState, SweepRun


def _hm(delta: timedelta) -> str:
    """A duration as hours and minutes, e.g. ``1h16m``."""
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes // 60}h{minutes % 60:02d}m"


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
    typer.echo(f"  engine:     {run.engine_commit or 'unknown'}")


def _echo_switch() -> None:
    """Is the dead-man switch armed? Answered here, not in a 6 MB sweep log.

    An unarmed switch was how an 8-day outage went unnoticed (issue #347):
    the sweep said so on stderr every night, into a file nobody reads. The
    URL is a credential — anyone holding it can forge a sign of life — so
    only its host is shown.
    """
    base = heartbeat_url()
    typer.echo("\nDead-man switch")
    if base is None:
        typer.echo("  NOT ARMED — set LOVSPOR_OBSERVATORY_HEARTBEAT_URL in the job's environment")
        return
    typer.echo(f"  armed:      reports to {urlsplit(base).netloc}")


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
