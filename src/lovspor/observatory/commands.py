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

from pathlib import Path
from typing import Annotated

import typer

from lovspor.errors import ConfigError, ParseError, SourceNotActivatedError, StorageBoundaryError
from lovspor.observatory.registry import (
    SourceRecord,
    SourceRegistry,
    activate,
    read_access_policy_check,
    read_registry,
    registry_path,
    write_registry,
)
from lovspor.observatory.storage import observatory_root

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


def _registry_file() -> Path:
    """Resolve the registry path, or explain why the environment cannot.

    A missing ``LOVSPOR_OBSERVATORY_ROOT`` and a root inside the engine repo
    or the corpus are both ordinary operator mistakes, not bugs, so they read
    as a message rather than a traceback.
    """
    try:
        return registry_path(observatory_root())
    except (ConfigError, StorageBoundaryError) as exc:
        typer.echo(f"Cannot locate the source registry: {exc}", err=True)
        raise typer.Exit(1) from exc


def _load(path: Path) -> SourceRegistry:
    """The registry, or an empty one the first time."""
    return read_registry(path) if path.exists() else SourceRegistry()


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
    record = SourceRecord.model_validate(
        {
            "authority_type": authority_type,
            "authority_id": authority_id,
            "name": name,
            "canonical_domain": domain,
        }
    )
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
    except FileNotFoundError as exc:
        typer.echo(f"Refused: no access-policy check at {check}", err=True)
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
