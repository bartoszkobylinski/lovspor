"""CLI for the source registry: register a source, then activate it.

The two steps are separate commands because ADR-0010 4 makes them separate
decisions. Registering says *this authority is eligible* -- it is an official
municipal or fylkeskommune site, so it is a candidate. Activating says *a
named human read this source's ``robots.txt`` and terms and concluded capture
is permitted*, and only that unlocks traffic against someone else's server.

The access-policy check arrives as a JSON document rather than as flags. It is
the record of a human decision, it has to answer "why was this activated?"
months later, and a conclusion typed into a shell leaves nothing to re-read.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from lovspor.errors import ParseError, SourceNotActivatedError
from lovspor.observatory.app import _AuthorityIdOption, observatory_app
from lovspor.observatory.events import (
    SourceDomainReplaced,
    append_source_event,
    domain_replacement,
)
from lovspor.observatory.registry import (
    CaptureVerdict,
    SourceRecord,
    activate,
    read_access_policy_check,
    read_capture_verdict,
    registry_path,
    replace_domain,
)
from lovspor.observatory.registry_io import (
    _load,
    _refuse_a_claimed_domain,
    _refuse_a_registered_id,
    _registry_file,
    _root,
    _save,
)
from lovspor.observatory.storage import ObservatoryRoot


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
    _refuse_a_registered_id(registry, authority_id)
    _refuse_a_claimed_domain(registry, domain, excluding=authority_id)
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


def _next_listings(current: tuple[str, ...], add: list[str], remove: list[str]) -> tuple[str, ...]:
    """The entry points after this update, or a refusal.

    A removal that matched nothing is an error rather than a no-op. The
    operator's intent is to stop sending traffic to a page; reporting success
    while the entry they actually declared stays live is the one outcome this
    command must not produce. An addition already present is not the same
    case — it ends in exactly the state that was asked for, so it is
    idempotent, and it stays idempotent within one invocation: each addition
    is checked against what this command has already added, not only against
    what the registry held on entry.

    A URL given as both an addition and a removal is refused. Applying
    removals first would let the addition win and report success to an
    operator who asked for the entry to go; neither order is more correct
    than the other, so the instruction is declined rather than resolved.
    """
    both = list(dict.fromkeys(url for url in add if url in remove))
    if both:
        typer.echo(f"Refused: {', '.join(both)} is both added and removed.", err=True)
        raise typer.Exit(1)
    missing = [url for url in remove if url not in current]
    if missing:
        typer.echo(f"Refused: {', '.join(missing)} not declared on this source.", err=True)
        raise typer.Exit(1)
    listings = [url for url in current if url not in remove]
    for url in add:
        if url not in listings:
            listings.append(url)
    return tuple(listings)


def _with_listings(record: SourceRecord, listings: tuple[str, ...]) -> SourceRecord:
    """The same source with new entry points, rebuilt through the model.

    ``model_copy`` would be shorter and would skip every validator, so the
    domain check that refuses an entry point outside the cleared domain would
    never run — which is precisely the hole that hand-editing the registry
    opened. The record is therefore revalidated as a whole, and a refusal
    happens before anything is written.
    """
    return SourceRecord.model_validate({**record.model_dump(), "listing_entry_points": listings})


@observatory_app.command("update-source")
def update_source(
    authority_id: _AuthorityIdOption,
    add_listing: Annotated[
        list[str] | None,
        typer.Option("--add-listing", help="Declare an overview page as an entry. Repeatable."),
    ] = None,
    remove_listing: Annotated[
        list[str] | None,
        typer.Option("--remove-listing", help="Withdraw a declared entry. Repeatable."),
    ] = None,
) -> None:
    """Change the mutable properties of a source that is already registered.

    Listing entry points are declared here rather than at registration
    because most sources are registered long before anyone reads their site
    for an overview page, and because the 116 municipalities that need one are
    already in the registry (#151, #184). Editing ``sources.json`` by hand
    reaches the same field while skipping the model's domain validation, so
    the supported route has to exist for the guarantee to mean anything.
    """
    if not add_listing and not remove_listing:
        typer.echo("Refused: nothing to change; pass --add-listing or --remove-listing.", err=True)
        raise typer.Exit(1)
    path = _registry_file()
    registry = _load(path)
    record = registry.sources.get(authority_id)
    if record is None:
        typer.echo(f"{authority_id} is not registered; run register-source first.", err=True)
        raise typer.Exit(1)
    listings = _next_listings(record.listing_entry_points, add_listing or [], remove_listing or [])
    try:
        updated = _with_listings(record, listings)
    except ValidationError as exc:
        typer.echo(f"Refused: {exc}", err=True)
        raise typer.Exit(1) from exc
    _save({**registry.sources, authority_id: updated}, path)
    typer.echo(f"{authority_id}: {len(listings)} listing entry point(s) declared.")
    for url in listings:
        typer.echo(f"  listing  {url}")


def _with_verdict(record: SourceRecord, verdict: CaptureVerdict) -> SourceRecord:
    """The same source carrying its verdict, rebuilt through the model.

    ``model_copy`` would skip every validator, which is the hole a hand-edited
    registry opens (#184). Revalidating the whole record also carries the
    access-policy evidence back through the model rather than around it.
    """
    return SourceRecord.model_validate({**record.model_dump(), "capture_verdict": verdict})


@observatory_app.command("record-verdict")
def record_verdict(
    authority_id: _AuthorityIdOption,
    verdict: Annotated[
        Path,
        typer.Option("--verdict", help="JSON capture verdict: what was concluded, and on what."),
    ],
) -> None:
    """Record what an investigation concluded about capturing this source.

    The twin of ``activate-source``. That one attaches a human's conclusion
    that a source may be fetched; this attaches the conclusion that fetching
    it yields nothing, so the next sweep does not re-derive it and reach the
    same silent zero (#195). It arrives as a document rather than as flags for
    the reason the access-policy check does: it carries the routes that were
    checked, and a conclusion typed into a shell leaves nothing to re-read.

    Recording a verdict does not deactivate the source. The two are separate
    decisions, and the re-check the verdict schedules depends on the source
    still being cleared to fetch.
    """
    path = _registry_file()
    registry = _load(path)
    record = registry.sources.get(authority_id)
    if record is None:
        typer.echo(f"{authority_id} is not registered; run register-source first.", err=True)
        raise typer.Exit(1)
    recorded = _read_verdict(verdict)
    try:
        updated = _with_verdict(record, recorded)
    except ValidationError as exc:
        typer.echo(f"Refused: {exc}", err=True)
        raise typer.Exit(1) from exc
    _save({**registry.sources, authority_id: updated}, path)
    typer.echo(f"Recorded {recorded.outcome} for {authority_id} ({record.name})")
    typer.echo(f"Re-check after {recorded.recheck_after.isoformat(timespec='seconds')}")


def _read_verdict(path: Path) -> CaptureVerdict:
    """The verdict document, or an operator-legible refusal.

    A mistyped path and a document the model rejects are both ordinary
    mistakes rather than bugs, so neither reaches the operator as a traceback.
    """
    try:
        return read_capture_verdict(path)
    except ParseError as exc:
        typer.echo(f"Refused: {exc}", err=True)
        raise typer.Exit(1) from exc
    except OSError as exc:
        typer.echo(f"Refused: cannot read the capture verdict at {path}: {exc}", err=True)
        raise typer.Exit(1) from exc


def _plan_replacement(
    record: SourceRecord, domain: str, reason: str, changed_by: str
) -> tuple[SourceRecord, SourceDomainReplaced]:
    """The record after the move and the event describing it, or a refusal.

    Both are built before anything is written, and the event fingerprints the
    record as it stands — after the write it would identify the record that
    replaced it, which is the one question the fingerprint is not for.
    """
    # Stripped once, here, so the registry and the event cannot disagree about
    # the domain: the event model normalises its own fields, and a record built
    # from the raw argument would keep whitespace the event had removed.
    domain = domain.strip()
    try:
        replaced = replace_domain(record, domain)
    except (SourceNotActivatedError, ValidationError) as exc:
        typer.echo(f"Refused: {exc}", err=True)
        raise typer.Exit(1) from exc
    try:
        event = domain_replacement(
            record=record,
            to_domain=domain,
            reason=reason,
            changed_at=datetime.now(UTC),
            changed_by=changed_by,
        )
    except ParseError as exc:
        typer.echo(f"Refused: {exc}", err=True)
        raise typer.Exit(1) from exc
    return replaced, event


def _record_replacement(root: ObservatoryRoot, event: SourceDomainReplaced) -> None:
    """Append the decision, and say plainly when the archive would not take it.

    Written after the registry, not before. The registry change only ever
    *removes* clearance, so a change that lands without its event leaves the
    engine more restrictive than the record claims — visibly, since the source
    now reads inactive. The other order would let an event describe a
    withdrawal that never happened while the source kept crawling the old
    domain under its old clearance, which is the failure worth avoiding.
    """
    try:
        append_source_event(root, event)
    except OSError as exc:
        typer.echo(
            f"Refused: {event.authority_id} was moved to {event.to_domain} and deactivated, "
            f"but the decision could not be recorded: {exc}. The source is safe — it has no "
            "clearance — but the history is incomplete; record it before re-activating.",
            err=True,
        )
        raise typer.Exit(1) from exc


# The event is appended only after the register moves, so a failed write
# leaves no decision behind either; the operator should not have to infer it.
_NOTHING_DECIDED = "The register was not changed, and no decision was recorded."


@observatory_app.command("replace-source-domain")
def replace_source_domain(
    authority_id: _AuthorityIdOption,
    domain: Annotated[
        str, typer.Option("--domain", help="The domain this authority publishes on now.")
    ],
    reason: Annotated[
        str, typer.Option("--reason", help="Why it moved. Recorded verbatim, not paraphrased.")
    ],
    changed_by: Annotated[
        str, typer.Option("--by", help="Who decided. Never synthesised from the environment.")
    ],
) -> None:
    """Move an authority to a different domain, withdrawing its clearance.

    A municipality that starts redirecting to another domain cannot simply
    have `canonical_domain` edited: the access-policy check answers about the
    old host, and leaving it in place would let a clearance obtained for one
    server authorise traffic to another (issue #166). So the move is one
    operation — new domain, no policy, inactive, entry points dropped — and
    capture resumes only after a fresh `activate-source` on a fresh review.

    The decision is appended to `source-events.jsonl` with the fingerprint of
    the record it replaced, because `sources.json` is current state and cannot
    say what was withdrawn.
    """
    root = _root()
    path = registry_path(root)
    registry = _load(path)
    record = registry.sources.get(authority_id)
    if record is None:
        typer.echo(f"{authority_id} is not registered; run register-source first.", err=True)
        raise typer.Exit(1)
    _refuse_a_claimed_domain(registry, domain, excluding=authority_id)
    replaced, event = _plan_replacement(record, domain, reason, changed_by)
    _save({**registry.sources, authority_id: replaced}, path, _NOTHING_DECIDED)
    _record_replacement(root, event)
    _echo_replacement(replaced, event)


def _echo_replacement(replaced: SourceRecord, event: SourceDomainReplaced) -> None:
    """What changed, and the one step that makes the source fetchable again."""
    typer.echo(f"{event.authority_id} ({replaced.name}): {event.from_domain} -> {event.to_domain}")
    typer.echo("  clearance withdrawn; the source is inactive and will not be swept")
    typer.echo(f"  replaced record {event.previous_record_sha256[:12]} recorded in the event log")
    typer.echo(
        f"Next: review {event.to_domain} and run\n"
        f"  lovspor observatory activate-source --id {event.authority_id} --check <check>.json"
    )


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
