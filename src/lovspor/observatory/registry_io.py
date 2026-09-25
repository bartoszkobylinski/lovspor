"""Reading and writing the source register, and refusing what it may not hold.

Shared by every observatory command module. It lives apart from them because
both sides need it: splitting the commands without splitting these helpers
would leave one command module importing the other.
"""

from pathlib import Path

import typer

from lovspor.errors import ConfigError, ParseError, StorageBoundaryError
from lovspor.observatory.registry import (
    Register,
    SourceRecord,
    SourceRegistry,
    claimants,
    read_registry,
    registry_path,
    write_registry,
)
from lovspor.observatory.storage import ObservatoryRoot, observatory_root


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


def _bound_register() -> Register:
    """The register this run works from, and the file it must keep agreeing with.

    Bound rather than snapshotted: a capture over one municipality is hours of
    politely-spaced requests and a sweep over the register is days, so the file
    the run started from is not the file an operator is looking at (issue #221).
    """
    path = _registry_file()
    return Register(_load(path), path)


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


def _refuse_a_registered_id(registry: SourceRegistry, authority_id: str) -> None:
    """Refuse an id the register already holds rather than overwrite it.

    Sibling of :func:`_refuse_a_claimed_domain`: both answer "may the register
    hold this?" at the point the state would be written, and both answer by
    refusing rather than by letting a second record silently replace a first.
    """
    if authority_id not in registry.sources:
        return
    typer.echo(f"{authority_id} is already registered; refusing to overwrite it.", err=True)
    raise typer.Exit(1)


def _refuse_a_claimed_domain(
    registry: SourceRegistry, domain: str, excluding: str | None = None
) -> None:
    """Refuse a domain another source already claims (issue #215).

    Checked where the state is written rather than where the register is read.
    A load-time refusal would be stricter and would also be a trap: the only
    supported way out of the state is `replace-source-domain`, which has to
    read the register first, so a register that refuses to load is a register
    nobody can repair through an interface this engine offers.
    """
    taken = claimants(registry, domain, excluding=excluding)
    if not taken:
        return
    typer.echo(
        f"Refused: {domain} is already claimed by {', '.join(taken)}. Two sources on "
        "one domain make an observation unable to name the authority that published "
        "it, so the register may not hold that state.",
        err=True,
    )
    raise typer.Exit(1)


def _save(
    sources: dict[str, SourceRecord], path: Path, consequence: str = "The register was not changed."
) -> None:
    """Write the register, or refuse the way an unreadable one is refused (#208).

    The archive sits on external storage by design (ADR-0010 §5), so an
    unmounted or read-only disk is its ordinary failure, and a traceback would
    call that a bug. The write is atomic, so a refusal here always means the
    file on disk is the one the command read — ``consequence`` says so, and a
    command whose failure has a further meaning states that too.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_registry(SourceRegistry(sources=sources), path)
    except OSError as exc:
        typer.echo(
            f"Refused: cannot write the source register at {path}: {exc}. "
            f"Is the archive mounted and writable? {consequence}",
            err=True,
        )
        raise typer.Exit(1) from exc
