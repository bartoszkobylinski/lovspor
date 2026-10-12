"""``innsyn send``: deliver a wave's letters over SMTP, recording each as it is accepted.

Without ``--send`` it is a dry run: it prints what would go and touches
nothing. The transport and the pause are module attributes so a test can
put a fake server and a clock-free pause in their place; nothing else of the
run is replaceable.
"""

import os
import time
from collections.abc import Callable
from email.headerregistry import Address
from pathlib import Path
from typing import Annotated

import typer

from lovspor.errors import ConfigError
from lovspor.innsyn.app import guarded, innsyn_app, now, refuse, workspace
from lovspor.innsyn.dispatch import (
    Failure,
    Prepared,
    SendPlan,
    SendReport,
    Wire,
    compose,
    prepare,
    send_letters,
)
from lovspor.innsyn.mailer import SmtpSettings, SmtpTransport, Transport, smtp_settings
from lovspor.innsyn.sender import Sender, read_sender

transport_for: Callable[[SmtpSettings], Transport] = SmtpTransport
pause: Callable[[float], None] = time.sleep


def _dry_run(prepared: Prepared, plan: SendPlan) -> None:
    sender = plan.sender
    origin = str(Address(sender.name, addr_spec=sender.email))
    for ready in prepared.ready[: plan.limit]:
        size = len(compose(ready, sender, now()).email().as_bytes())
        fields = (ready.entry.authority_id, f"To: {ready.entry.recipient}", f"From: {origin}")
        typer.echo("\t".join((*fields, f"Subject: {ready.subject}", f"{size} bytes")))
    try:
        settings = smtp_settings(os.environ)
    except ConfigError as exc:
        typer.echo(f"SMTP not configured: {exc}", err=True)
    else:
        typer.echo(f"SMTP: {settings.host}:{settings.port} as {settings.user}", err=True)
    typer.echo("dry run: nothing sent; add --send to send", err=True)


def _failure(failure: Failure, manifest: Path) -> None:
    typer.echo(f"FAILED {failure.authority_id}: {failure.reason}", err=True)
    if failure.accepted and failure.sent_at is not None:
        typer.echo(
            "record it now: lovspor observatory innsyn record-sent "
            f"--manifest {manifest} --id {failure.authority_id} "
            f"--sent-at {failure.sent_at.isoformat()} --message-id '{failure.message_id}'",
            err=True,
        )
        return
    typer.echo(
        "nothing recorded for it; the run stopped and later letters were not tried. "
        "Check Gmail's Sent folder for it before running again.",
        err=True,
    )


def _report(report: SendReport, manifest: Path) -> None:
    for event in report.sent:
        typer.echo(f"{event.request_id}: sent to {event.recipient} {event.message_id}")
    if report.failure is not None:
        _failure(report.failure, manifest)


def _summary(prepared: Prepared, sent: int, remaining: int) -> None:
    for refusal in prepared.refused:
        typer.echo(f"refused {refusal.authority_id}: {refusal.reason}", err=True)
    typer.echo(
        f"send: sent {sent}, skipped (already sent) {len(prepared.skipped)}, "
        f"refused {len(prepared.refused)}, remaining {remaining}",
        err=True,
    )


def _deliver(prepared: Prepared, plan: SendPlan, manifest: Path) -> SendReport:
    try:
        settings = smtp_settings(os.environ)
    except ConfigError as exc:
        raise refuse(exc) from exc
    wire = Wire(transport=transport_for(settings), clock=now, pause=pause)
    target = workspace()
    report = send_letters(target, prepared, plan, wire)
    _report(report, manifest)
    return report


def _plan(sender: Sender, limit: int, pause_seconds: float) -> SendPlan:
    return SendPlan(sender=sender, limit=limit, pause_seconds=pause_seconds)


@innsyn_app.command("send")
def send(
    manifest: Annotated[Path, typer.Option("--manifest", help="The wave's manifest.json.")],
    limit: Annotated[
        int, typer.Option("--limit", min=1, help="At most this many letters this run.")
    ] = 25,
    pause_seconds: Annotated[
        float, typer.Option("--pause-seconds", min=0, help="Pause between two letters.")
    ] = 20.0,
    really: Annotated[
        bool, typer.Option("--send", help="Send. Without it, a dry run that sends nothing.")
    ] = False,
) -> None:
    """Send a wave's letters by SMTP (STARTTLS), in manifest order, recording each one sent."""
    target = workspace()
    prepared = guarded(lambda: prepare(target, manifest))
    plan = _plan(guarded(lambda: read_sender(target)), limit, pause_seconds)
    if not really:
        _dry_run(prepared, plan)
        _summary(prepared, 0, len(prepared.ready))
        return
    report = _deliver(prepared, plan, manifest)
    _summary(prepared, len(report.sent), report.remaining)
    if report.failure is not None:
        raise typer.Exit(1)
