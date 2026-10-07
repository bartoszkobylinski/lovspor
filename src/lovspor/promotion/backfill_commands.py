"""``lovspor promote backfill``, ``backfill-preview`` and ``observe`` (ADR-0016 S6).

``backfill-preview``
    Every version observed at the artifact's URL since the first capture,
    with its interval, its standing approval or its hold, and what the next
    run would write. Writes nothing and records nothing.
``backfill``
    Write the next version the corpus does not hold (:mod:`.backfill`), record
    it in the decision log, and print the commit to make, followed by the
    ordered commands for every further approved version. Never commits.
``observe``
    The observation refresh: re-read every promoted version's interval from
    the log (:mod:`.intervals`) and rewrite only the observations files that
    changed. No version is added, no document or manifest is touched. Run it
    weekly — ADR-0016 Decision 3 refreshes ``observed_at_last`` at most weekly
    per document — and commit under the subject it prints, ``observe: …``,
    which the history derivation counts as no event.

The commands are added to :data:`~lovspor.promotion.commands.promote_app`
here, and the CLI imports ``promote_app`` from this module, so the group it
mounts always carries them. Like ``promote local``, the commands only call
``*_impl`` functions so their bodies stay in the mutation gate (#292).
"""

from __future__ import annotations

import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from lovspor.atomic_io import atomic_write_text
from lovspor.errors import PromotionRefusedError
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.registry_io import _root
from lovspor.observatory.storage import engine_root
from lovspor.promotion.archive import Fetch, authority_fetches
from lovspor.promotion.backfill import (
    BackfillPlan,
    Inputs,
    NextVersion,
    VersionHold,
    next_version,
    plan_backfill,
)
from lovspor.promotion.commands import (
    HELD_EXIT_CODE,
    Request,
    _Artifact,
    _Authority,
    _context,
    _Corpus,
    _echo_commit,
    _KlassVersion,
    _refusing,
    promote_app,
)
from lovspor.promotion.corpus import LOCAL_DIR, CorpusCheckout, LocalRecord
from lovspor.promotion.decisions import (
    DecisionLog,
    HeldRecord,
    HumanDecision,
    PromotedRecord,
    utc_text,
)
from lovspor.promotion.intervals import with_intervals
from lovspor.promotion.versions import read_primary
from lovspor.promotion.writer import ObservationsFile, apply, observations_text

OBSERVE_SUBJECT = "observe: refresh observation intervals ({count} documents)"


def _plan(request: Request) -> tuple[Inputs, BackfillPlan, DecisionLog]:
    context = _context(request)
    inputs = Inputs(context.log, context.fetches, context.authority, context.corpus)
    history = read_primary(context.log, context.fetches, context.key.source_url, None)
    decisions = DecisionLog(context.root)
    human = [r for r in decisions.records() if isinstance(r, HumanDecision)]
    return inputs, plan_backfill(history, human), decisions


def backfill_preview_impl(request: Request) -> None:
    """Print the versions, their approvals and holds, and the next write; write nothing."""
    inputs, plan, _ = _plan(request)
    _echo_plan(plan)
    step = next_version(inputs, plan)
    if isinstance(step, NextVersion):
        typer.echo(f"Next: v{step.prepared.version} -> {step.prepared.markdown_path}")
        typer.echo(f"  commit subject: {step.prepared.commit_subject}")
    elif isinstance(step, VersionHold):
        _echo_hold(step)
    else:
        typer.echo("Next: nothing to write.")


def backfill_impl(request: Request, now: datetime) -> None:
    """Write the next approved version into the checkout, or report why none is written."""
    inputs, plan, decisions = _plan(request)
    _echo_plan(plan)
    step = next_version(inputs, plan)
    if isinstance(step, VersionHold):
        _record_hold(decisions, step, now)
        raise typer.Exit(HELD_EXIT_CODE)
    if step is None:
        typer.echo("Nothing to write: the corpus holds every approved version.")
        if plan.holds:
            raise typer.Exit(HELD_EXIT_CODE)
        return
    written = apply(inputs.corpus, step.writes)
    decisions.record_outcome(_promoted(step, now))
    for path in written:
        typer.echo(f"wrote {path}")
    _echo_commit(inputs.corpus, step.prepared.commit_subject)
    _echo_remaining(request, plan, step)


def _promoted(step: NextVersion, now: datetime) -> PromotedRecord:
    prepared = step.prepared
    return PromotedRecord(
        artifact=step.key,
        recorded_at=now,
        doc_id=prepared.identity.doc_id,
        version=prepared.version,
        markdown_path=prepared.markdown_path,
        content_hash=prepared.identity.content_hash,
        commit_subject=prepared.commit_subject,
        audit=step.writes.audit,
    )


def _record_hold(decisions: DecisionLog, hold: VersionHold, now: datetime) -> None:
    """An extraction or identity hold of the next version is recorded, as `local` records it."""
    _echo_hold(hold)
    if hold.held is None or hold.key is None:
        return
    record = HeldRecord(
        artifact=hold.key,
        recorded_at=now,
        stage=hold.held.stage,
        reason=hold.held.reason,
        detail=hold.held.detail,
        personal_data=hold.held.personal_data,
    )
    decisions.record_outcome(record)


def _echo_plan(plan: BackfillPlan) -> None:
    history = plan.history
    typer.echo(f"Primary URL: {history.primary_url}")
    held = {hold.version: hold.reason for hold in plan.holds}
    for version in history.versions:
        status = held.get(version.version, "approved")
        first, last = utc_text(version.observed_at_first), utc_text(version.observed_at_last)
        count = len(version.sightings)
        typer.echo(f"  v{version.version}  {first} .. {last}  ({count} observations)  {status}")
    for excluded in history.excluded:
        typer.echo(
            f"  excluded: {excluded.observed_at} {excluded.sha256} ({excluded.reason.value})"
        )
    typer.echo(
        f"Source status: {history.source_status.outcome} at {history.source_status.observed_at}"
    )
    reasons = plan.holds_by_reason()
    summary = ", ".join(f"{reason}: {count}" for reason, count in reasons.items()) or "none"
    typer.echo(f"Holds by reason: {summary}")
    for hold in plan.holds:
        typer.echo(f"  v{hold.version} {hold.reason}: {hold.detail}")


def _echo_hold(hold: VersionHold) -> None:
    typer.echo(f"Held: v{hold.version} {hold.reason} - {hold.detail}")
    if hold.held is not None:
        for hit in hold.held.personal_data:
            typer.echo(f"  personal data: {hit.kind.value} on line {hit.line}")


def _echo_remaining(request: Request, plan: BackfillPlan, step: NextVersion) -> None:
    """The ordered commands for every further approved version, one commit each."""
    remaining = [
        a.version.version for a in plan.approved if a.version.version > step.prepared.version
    ]
    if not remaining:
        return
    where = shlex.quote(str(request.corpus))
    command = " ".join(
        shlex.quote(part)
        for part in (
            *("lovspor", "promote", "backfill", "--authority", request.authority_id),
            *("--artifact", request.artifact, "--corpus", str(request.corpus)),
            *("--klass-version", request.klass_version),
        )
    )
    typer.echo("After that commit, the further versions, in this order:")
    for number in remaining:
        subject = step.prepared.commit_subject.removesuffix(f" v{step.prepared.version}")
        typer.echo(f"  {command}")
        typer.echo(f"  git -C {where} add -- {LOCAL_DIR}")
        typer.echo(f"  git -C {where} commit -m {shlex.quote(f'{subject} v{number}')}")


def observe_impl(corpus_path: Path, authority_id: str | None) -> None:
    """Refresh every current local document's observation intervals from the log."""
    root = _root()
    log = ObservationLog(root)
    forbidden = [engine_root(), root.path]
    corpus = CorpusCheckout(corpus_path, forbidden)
    written: list[str] = []
    fetches: dict[str, tuple[Fetch, ...]] = {}
    for doc_id, record in sorted(corpus.local_manifest().documents.items()):
        if record.status != "current" or authority_id not in (None, record.authority_id):
            continue
        if record.authority_id not in fetches:
            fetches[record.authority_id] = authority_fetches(log, record.authority_id)
        outcome = _refresh(corpus, log, fetches[record.authority_id], record)
        typer.echo(f"{doc_id}: {outcome or 'unchanged'}")
        if outcome == "refreshed":
            written.append(f"{LOCAL_DIR}/{record.authority_id}/observations/{record.slug}.json")
    _echo_refreshed(corpus, written)


def _refresh(
    corpus: CorpusCheckout, log: ObservationLog, fetches: tuple[Fetch, ...], record: LocalRecord
) -> str | None:
    """``refreshed``, ``None`` when the file already says so, or why it was skipped."""
    path = corpus.inside(f"{record.authority_id}/observations/{record.slug}.json")
    try:
        on_disk = path.read_bytes()
        existing = ObservationsFile.model_validate_json(on_disk)
        urls = {entry.primary_url for entry in existing.versions}
        if len(urls) != 1:
            return "skipped: its versions name more than one primary URL"
        history = read_primary(log, fetches, urls.pop(), None)
        text = observations_text(with_intervals(existing, history))
    except (OSError, ValidationError) as exc:
        return f"skipped: {path} does not read ({type(exc).__name__})"
    except PromotionRefusedError as exc:
        return f"skipped: {exc}"
    if on_disk == text.encode():
        return None
    atomic_write_text(path, text)
    return "refreshed"


def _echo_refreshed(corpus: CorpusCheckout, written: list[str]) -> None:
    if not written:
        typer.echo("Observation intervals are current; nothing written, nothing to commit.")
        return
    for path in written:
        typer.echo(f"wrote {path}")
    _echo_commit(corpus, OBSERVE_SUBJECT.format(count=len(written)))


def backfill_preview(
    authority: _Authority, artifact: _Artifact, corpus: _Corpus, klass_version: _KlassVersion
) -> None:
    """Show every observed version of one document, its approval or hold. Writes nothing."""
    request = Request(authority, artifact, corpus, klass_version)
    _refusing(lambda: backfill_preview_impl(request))


def backfill(
    authority: _Authority, artifact: _Artifact, corpus: _Corpus, klass_version: _KlassVersion
) -> None:
    """Write the next approved observed version into a lovverk checkout. Never commits."""
    request = Request(authority, artifact, corpus, klass_version)
    _refusing(lambda: backfill_impl(request, datetime.now(UTC)))


def observe(
    corpus: _Corpus,
    authority: Annotated[
        str | None, typer.Option("--authority", help="Only this authority's documents.")
    ] = None,
) -> None:
    """Refresh observation intervals of promoted local documents (weekly). Never commits."""
    _refusing(lambda: observe_impl(corpus, authority))


def register_backfill(app: typer.Typer) -> None:
    """Add the S6 commands to ``lovspor promote``."""
    app.command("backfill-preview")(backfill_preview)
    app.command("backfill")(backfill)
    app.command("observe")(observe)


register_backfill(promote_app)

__all__ = ["promote_app", "register_backfill"]
