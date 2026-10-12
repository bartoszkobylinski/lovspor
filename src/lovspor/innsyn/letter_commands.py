"""``innsyn letters`` writes a wave; ``innsyn record-sent`` records one letter as sent."""

import hashlib
from pathlib import Path
from typing import Annotated

import typer

from lovspor.errors import InnsynRefusedError, LogIntegrityError
from lovspor.innsyn.app import guarded, innsyn_app, instant, now, refuse, workspace
from lovspor.innsyn.directory import contacts, directory_journal
from lovspor.innsyn.ledger import parse_event, record_events, request_id, requests
from lovspor.innsyn.population import latest_snapshot
from lovspor.innsyn.sender import read_sender
from lovspor.innsyn.waves import ManifestEntry, WaveInputs, read_manifest, write_wave
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.observatory.lf_ledger import LedgerEntry, ledger_path, read_ledger_file
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.registry_io import _root


def _held_ids(path: Path | None) -> frozenset[str]:
    """LF ids whose text is held: one per line, ``#`` starts a comment."""
    if path is None:
        return frozenset()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise refuse(InnsynRefusedError(f"{path}: no such --held file")) from exc
    except UnicodeDecodeError as exc:
        raise refuse(InnsynRefusedError(f"{path}: not UTF-8 at byte {exc.start}")) from exc
    return frozenset(line.split("#", 1)[0].strip() for line in lines) - {""}


def _lf_entries(path: Path | None) -> tuple[LedgerEntry, ...]:
    source = ledger_path(ObservationLog(_root())) if path is None else path
    if not source.exists():
        raise refuse(InnsynRefusedError(f"{source}: no LF ledger; run `observatory lf-ledger`"))
    try:
        return tuple(read_ledger_file(source))
    except LogIntegrityError as exc:
        raise refuse(exc) from exc
    except UnicodeDecodeError as exc:
        raise refuse(InnsynRefusedError(f"{source}: not UTF-8 at byte {exc.start}")) from exc


def _inputs(
    target: InnsynWorkspace, lf_entries: tuple[LedgerEntry, ...], held: frozenset[str]
) -> WaveInputs:
    return WaveInputs(
        population=latest_snapshot(target),
        contacts=contacts(directory_journal(target).records()),
        requests=requests(target),
        lf_entries=lf_entries,
        held=held,
        sender=read_sender(target),
    )


@innsyn_app.command("letters")
def letters(
    out: Annotated[Path, typer.Option("--out", help="A new or empty directory for the wave.")],
    wave_size: Annotated[
        int, typer.Option("--wave-size", min=1, help="How many letters this wave writes.")
    ] = 25,
    held: Annotated[
        Path | None,
        typer.Option("--held", help="LF ids whose text is held, one per line; left out."),
    ] = None,
    lf_ledger: Annotated[
        Path | None,
        typer.Option("--lf-ledger", help="lf-ledger.jsonl (default: the observatory's)."),
    ] = None,
) -> None:
    """Write the next wave of innsynskrav letters and a manifest. Sends nothing."""
    target = workspace()
    entries, held_ids = _lf_entries(lf_ledger), _held_ids(held)
    inputs = guarded(lambda: _inputs(target, entries, held_ids))
    manifest = guarded(lambda: write_wave(inputs, out, wave_size, now()))
    with_ids = sum(1 for letter in manifest.letters if letter.lf_ids)
    typer.echo(
        f"letters: {len(manifest.letters)} written to {out} ({with_ids} with known LF ids); "
        f"{len(manifest.unresolved)} unresolved, {len(manifest.skipped_open)} with an open "
        f"request, {manifest.remaining} left for later waves"
    )


def _entry(manifest_path: Path, authority_id: str) -> ManifestEntry:
    manifest = read_manifest(manifest_path)
    for entry in manifest.letters:
        if entry.authority_id == authority_id:
            return entry
    raise InnsynRefusedError(f"{manifest_path} has no letter for {authority_id}")


def _letter_digest(manifest_path: Path, entry: ManifestEntry) -> str:
    """The SHA-256 of the letter as it stands, after its ``Til:`` is checked."""
    letter = manifest_path.parent / entry.letter_file
    payload = letter.read_bytes()
    first = payload.decode("utf-8").split("\n", 1)[0]
    if first != f"Til: {entry.recipient}":
        raise InnsynRefusedError(f"{letter}: {first!r} is not the manifest's recipient")
    return hashlib.sha256(payload).hexdigest()


def _sent(manifest_path: Path, authority_id: str, sent_at: str) -> None:
    entry = _entry(manifest_path, authority_id)
    moment = instant(sent_at, "--sent-at")
    data = {
        "event": "sent",
        "request_id": request_id(authority_id, moment),
        "recorded_at": now(),
        "authority_id": authority_id,
        "recipient": entry.recipient,
        "sent_at": moment,
        "lf_ids": entry.lf_ids,
        "general_clause": entry.general_clause,
        "letter_sha256": _letter_digest(manifest_path, entry),
    }
    record_events(workspace(), [parse_event(data)])
    typer.echo(f"{data['request_id']}: sent to {entry.recipient} recorded")


@innsyn_app.command("record-sent")
def record_sent(
    manifest: Annotated[Path, typer.Option("--manifest", help="The wave's manifest.json.")],
    authority_id: Annotated[str, typer.Option("--id", help="The authority the letter went to.")],
    sent_at: Annotated[str, typer.Option("--sent-at", help="When it was sent (UTC).")],
) -> None:
    """Record a letter of a wave as sent, with the SHA-256 of the letter as sent."""
    try:
        guarded(lambda: _sent(manifest, authority_id, sent_at))
    except (OSError, ValueError) as exc:
        raise refuse(exc) from exc
