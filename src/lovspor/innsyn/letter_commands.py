"""``innsyn letters`` writes a wave; ``innsyn record-sent`` records one letter as sent."""

from pathlib import Path
from typing import Annotated

import typer

from lovspor.errors import InnsynRefusedError, LogIntegrityError
from lovspor.innsyn.app import guarded, innsyn_app, instant, now, refuse, workspace
from lovspor.innsyn.directory import contacts, directory_journal
from lovspor.innsyn.ledger import record_events, requests
from lovspor.innsyn.population import latest_snapshot
from lovspor.innsyn.sender import read_sender
from lovspor.innsyn.sent import letter_digest, manifest_entry, sent_event
from lovspor.innsyn.waves import WaveInputs, write_wave
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.observatory.lf_ledger import LedgerEntry, ledger_path, read_ledger_file
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.registry_io import _root


def _held_ids(path: Path | None) -> frozenset[str]:
    """LF ids whose text is held: one per line, ``#`` starts a comment."""
    if path is None:
        return frozenset()
    try:
        lines = path.read_bytes().decode().splitlines()
    except FileNotFoundError as exc:
        raise refuse(InnsynRefusedError(f"{path}: no such --held file")) from exc
    except UnicodeDecodeError as exc:
        raise refuse(InnsynRefusedError(f"{path}: not UTF-8 at byte {exc.start}")) from exc
    ids = (line.partition("#")[0].strip() for line in lines)
    return frozenset(lf_id for lf_id in ids if lf_id)


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


def _sent(manifest_path: Path, authority_id: str, sent_at: str, message_id: str | None) -> None:
    entry = manifest_entry(manifest_path, authority_id)
    moment = instant(sent_at, "--sent-at")
    event = sent_event(entry, letter_digest(manifest_path, entry), moment, message_id)
    record_events(workspace(), [event])
    typer.echo(f"{event.request_id}: sent to {entry.recipient} recorded")


@innsyn_app.command("record-sent")
def record_sent(
    manifest: Annotated[Path, typer.Option("--manifest", help="The wave's manifest.json.")],
    authority_id: Annotated[str, typer.Option("--id", help="The authority the letter went to.")],
    sent_at: Annotated[str, typer.Option("--sent-at", help="When it was sent (UTC).")],
    message_id: Annotated[
        str | None,
        typer.Option("--message-id", help="The Message-ID it went out with, e.g. <…@lovspor.no>."),
    ] = None,
) -> None:
    """Record a letter of a wave as sent, with the SHA-256 of the letter as sent."""
    try:
        guarded(lambda: _sent(manifest, authority_id, sent_at, message_id))
    except (OSError, ValueError) as exc:
        raise refuse(exc) from exc
