"""``lovspor ops``: the droplet's operator commands (issue #478).

``alert`` is what ``lovspor-alert@.service`` runs for a failed unit, and what
the operator runs with ``--test`` to prove the target receives it. Exit codes:
0 sent, or no webhook configured (a logged no-op — an unconfigured alert must
never be a second failed unit); 1 the configuration or the delivery failed;
2 the unit name is malformed.
"""

import os
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer
from pydantic import ValidationError

from lovspor.ops.alert import (
    DEFAULT_ALERT_ENV,
    WEBHOOK_VAR,
    Alert,
    AlertConfig,
    load_alert_config,
    redact_url,
    send_alert,
)
from lovspor.ops.errors import AlertConfigError, AlertDeliveryError
from lovspor.ops.unit_facts import Runner, UnitFacts, UnitName, read_unit_facts, run_command

ops_app = typer.Typer(
    name="ops",
    help="Operate the droplet: alerts for failed lovspor systemd units.",
    no_args_is_help=True,
)

_TEST_FACTS = UnitFacts(
    result="test",
    exit_status=None,
    journal=("This is a test alert from `lovspor ops alert --test`; nothing failed.",),
)


@dataclass(frozen=True)
class AlertDeps:
    runner: Runner
    client: httpx.Client
    clock: Callable[[], datetime]
    environ: dict[str, str]
    hostname: str


@ops_app.command(name="alert")
def alert_command(
    unit: Annotated[str, typer.Option("--unit", help="The failed unit, e.g. %i in a template.")],
    env_file: Annotated[
        Path, typer.Option("--env-file", help="Root-only file holding LOVSPOR_ALERT_WEBHOOK.")
    ] = DEFAULT_ALERT_ENV,
    test: Annotated[
        bool, typer.Option("--test", help="Send a test alert; read nothing from systemd.")
    ] = False,
) -> None:
    """POST a short failure report for UNIT to the operator's webhook."""
    with httpx.Client() as client:
        alert_impl(unit, env_file, _real_deps(client), test=test)


def _real_deps(client: httpx.Client) -> AlertDeps:
    return AlertDeps(
        runner=run_command,
        client=client,
        clock=lambda: datetime.now(UTC),
        environ=dict(os.environ),
        hostname=socket.gethostname(),
    )


def alert_impl(unit: str, env_file: Path, deps: AlertDeps, *, test: bool = False) -> None:
    name = _unit_name(unit)
    config = _config(deps, env_file)
    if config.webhook is None:
        typer.echo(
            f"alert webhook not configured ({WEBHOOK_VAR} unset in the environment and "
            f"in {env_file}); no alert sent for {name}",
            err=True,
        )
        return
    facts = _TEST_FACTS if test else read_unit_facts(deps.runner, name)
    _deliver(_alert(name, facts, deps, test=test), config, deps.client)


def _unit_name(unit: str) -> str:
    try:
        return UnitName.model_validate({"unit": unit}).unit
    except ValidationError as error:
        typer.echo(f"error: --unit {unit!r} is not a systemd unit name", err=True)
        raise typer.Exit(code=2) from error


def _config(deps: AlertDeps, env_file: Path) -> AlertConfig:
    try:
        return load_alert_config(deps.environ, env_file)
    except AlertConfigError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error


def _alert(unit: str, facts: UnitFacts, deps: AlertDeps, *, test: bool) -> Alert:
    return Alert(
        unit=unit,
        host=deps.hostname,
        at=deps.clock(),
        result=facts.result,
        exit_status=facts.exit_status,
        journal=facts.journal,
        test=test,
    )


def _deliver(alert: Alert, config: AlertConfig, client: httpx.Client) -> None:
    target = redact_url(config.webhook.get_secret_value()) if config.webhook else "<unset>"
    try:
        status = send_alert(alert, config, client)
    except AlertDeliveryError as error:
        typer.echo(f"error: alert for {alert.unit} not delivered to {target}: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"alert sent for {alert.unit} to {target} ({config.format}, HTTP {status})")
