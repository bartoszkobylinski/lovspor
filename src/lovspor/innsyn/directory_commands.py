"""Recipient, målform and sender commands, and the candidate scan (issue #509, point 2)."""

from typing import Annotated

import typer
from pydantic import ValidationError

from lovspor.errors import LogIntegrityError
from lovspor.innsyn.app import guarded, innsyn_app, instant, now, refuse, workspace
from lovspor.innsyn.candidates import mailbox_candidates, own_domains
from lovspor.innsyn.directory import (
    Contact,
    contacts,
    directory_journal,
    parse_fact,
    record_fact,
)
from lovspor.innsyn.population import Authority, latest_snapshot
from lovspor.innsyn.sender import Sender, write_sender
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.registry_io import _load, _registry_file, _root

_Id = Annotated[str, typer.Option("--id", help="kommunenummer or fylkesnummer.")]
_EvidenceUrl = Annotated[str, typer.Option("--evidence-url", help="The page the fact was read on.")]
_ReadAt = Annotated[
    str,
    typer.Option("--read-at", help="When it was read there (UTC; a capture's ObservedAt)."),
]


def _record(fact: dict[str, object]) -> None:
    try:
        record = parse_fact(fact)
    except ValidationError as exc:
        raise refuse(exc) from exc
    target = workspace()
    guarded(lambda: record_fact(target, latest_snapshot(target), record))


def _fact(kind: str, authority_id: str, evidence_url: str, read_at: str) -> dict[str, object]:
    return {
        "kind": kind,
        "authority_id": authority_id,
        "evidence": {"url": evidence_url, "read_at": instant(read_at, "--read-at")},
        "recorded_at": now(),
    }


@innsyn_app.command("set-recipient")
def set_recipient(
    authority_id: _Id,
    address: Annotated[str, typer.Option("--address", help="The mailbox, e.g. postmottak@…")],
    evidence_url: _EvidenceUrl,
    read_at: _ReadAt,
) -> None:
    """Record the mailbox an authority's request goes to, with where and when it was read."""
    _record({**_fact("recipient", authority_id, evidence_url, read_at), "address": address})
    typer.echo(f"{authority_id}: recipient {address.strip().lower()} (evidence {evidence_url})")


@innsyn_app.command("set-maalform")
def set_maalform(
    authority_id: _Id,
    maalform: Annotated[str, typer.Option("--form", help="nb (bokmål) or nn (nynorsk).")],
    evidence_url: _EvidenceUrl,
    read_at: _ReadAt,
) -> None:
    """Record the written standard an authority's letter uses, with its evidence."""
    _record({**_fact("maalform", authority_id, evidence_url, read_at), "maalform": maalform})
    typer.echo(f"{authority_id}: målform {maalform} (evidence {evidence_url})")


@innsyn_app.command("set-sender")
def set_sender(
    name: Annotated[str, typer.Option("--name", help="The requester's name, as signed.")],
    email: Annotated[str, typer.Option("--email", help="The address answers go to.")],
) -> None:
    """Record whose name the letters are signed with and where answers go."""
    try:
        sender = Sender(name=name, email=email, recorded_at=now())
    except ValidationError as exc:
        raise refuse(exc) from exc
    target = workspace()
    guarded(lambda: write_sender(target, sender))
    typer.echo(f"sender: {sender.name} <{sender.email}>")


def _row(authority: Authority, contact: Contact) -> str:
    recipient = contact.recipient
    form = contact.letter_form if contact.maalform else f"{contact.letter_form} (unset)"
    fields = (
        authority.authority_id,
        authority.kind,
        authority.letter_name,
        recipient.address if recipient else "UNRESOLVED",
        recipient.evidence.url if recipient else "",
        form,
    )
    return "\t".join(fields)


@innsyn_app.command("authorities")
def authorities() -> None:
    """List every authority with its recipient (or UNRESOLVED) and målform, tab-separated.

    Columns: id, kind, name, recipient, evidence URL, målform. Counts go to stderr.
    """
    target = workspace()
    snapshot = guarded(lambda: latest_snapshot(target))
    held = guarded(lambda: contacts(directory_journal(target).records()))
    for authority in snapshot.authorities:
        typer.echo(_row(authority, held.get(authority.authority_id, Contact())))
    ids = [a.authority_id for a in snapshot.authorities]
    resolved = sum(1 for i in ids if i in held and held[i].recipient is not None)
    formed = sum(1 for i in ids if i in held and held[i].maalform is not None)
    typer.echo(
        f"{len(ids)} authorities: {resolved} with a recorded recipient, "
        f"{len(ids) - resolved} unresolved; {formed} with a recorded målform",
        err=True,
    )


@innsyn_app.command("recipient-candidates")
def recipient_candidates(
    authority_ids: Annotated[
        list[str] | None, typer.Option("--id", help="Only these authorities (repeatable).")
    ] = None,
) -> None:
    """Propose own-domain mailboxes found in the archive's captured pages; records nothing.

    Columns: id, address, distinct pages, shortest citing URL, its ObservedAt,
    its blob. Reads the observatory archive (LOVSPOR_OBSERVATORY_ROOT) only.
    """
    wanted = frozenset(authority_ids) if authority_ids else None
    domains = own_domains(_load(_registry_file()))
    try:
        found = mailbox_candidates(ObservationLog(_root()), domains, wanted)
    except LogIntegrityError as exc:
        raise refuse(exc) from exc
    for c in found:
        fields = (c.authority_id, c.address, str(c.pages), c.url, c.observed_at.isoformat())
        typer.echo("\t".join((*fields, c.sha256)))
    authorities_with = len({c.authority_id for c in found})
    typer.echo(f"{len(found)} candidates across {authorities_with} authorities", err=True)
