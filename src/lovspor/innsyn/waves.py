"""Letters for the whole population, a wave at a time (issue #509, point 4).

A wave is the next ``wave_size`` authorities that

* have a recorded recipient — the rest are listed as ``unresolved`` and get
  no letter; and
* have no open request in the ledger — those are listed as ``skipped_open``.
  A request stays open until its outcome is ``full`` or ``refused``.

Authorities with at least one known id still to ask for (part a, after held
and already-asked ids are taken out) come first, then the rest; each group
is in authority-id order (owner decision 2026-10-12).

Part (a) of each letter is the LF and LTII ids the LF ledger saw linked from
the authority's own pages, less the ids whose text is held (``--held``) and
the ids an earlier request to the same authority already asked for. The short
``lovdata.no/forskrift/<id>`` form is left out: it does not say whether the
regulation is local. Part (b), the general request, is in every letter.

The output is one ``<authority>.txt`` per letter (``Til:``, ``Emne:``, body)
and ``manifest.json``. The output directory must be new or empty, so a wave
on disk is exactly one run. Nothing is sent and the ledger is not touched:
``record-sent`` records each letter when the owner has sent it.
"""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lovspor.atomic_io import atomic_replace_bytes
from lovspor.errors import InnsynRefusedError
from lovspor.innsyn.directory import Contact, Maalform
from lovspor.innsyn.ledger import RequestState
from lovspor.innsyn.letters import LetterRequest, render
from lovspor.innsyn.population import Authority, AuthorityKind, PopulationSnapshot
from lovspor.innsyn.sender import Sender
from lovspor.observatory.lf_ledger import LedgerEntry

MANIFEST_FILENAME = "manifest.json"
_REQUESTED_KINDS = frozenset({"LF", "LTII"})


class ManifestEntry(BaseModel):
    """One letter of a wave, as ``record-sent`` will read it back."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority_id: str
    kind: AuthorityKind
    name: str
    recipient: str
    maalform: Maalform
    maalform_recorded: bool
    lf_ids: tuple[str, ...]
    general_clause: Literal[True] = True
    letter_file: str
    letter_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Manifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    generated_at: datetime
    population_valid_at: str
    wave_size: int = Field(ge=1)
    letters: tuple[ManifestEntry, ...]
    unresolved: tuple[str, ...]
    skipped_open: tuple[str, ...]
    remaining: int = Field(ge=0)


class WaveInputs(BaseModel):
    """What a wave is computed from; every part read through a supported path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    population: PopulationSnapshot
    contacts: dict[str, Contact]
    requests: dict[str, RequestState]
    lf_entries: tuple[LedgerEntry, ...]
    held: frozenset[str]
    sender: Sender


def _asked(requests: dict[str, RequestState]) -> dict[str, set[str]]:
    asked: dict[str, set[str]] = {}
    for state in requests.values():
        asked.setdefault(state.sent.authority_id, set()).update(state.sent.lf_ids)
    return asked


def known_ids(inputs: WaveInputs) -> dict[str, tuple[str, ...]]:
    """Part (a) per authority: linked LF/LTII ids, less held and already-asked ones."""
    asked = _asked(inputs.requests)
    found: dict[str, set[str]] = {}
    for entry in inputs.lf_entries:
        if entry.kind in _REQUESTED_KINDS and entry.lf_id not in inputs.held:
            found.setdefault(entry.authority_id, set()).add(entry.lf_id)
    return {a: tuple(sorted(ids - asked.get(a, set()))) for a, ids in found.items()}


def _open(requests: dict[str, RequestState]) -> set[str]:
    return {state.sent.authority_id for state in requests.values() if state.is_open}


class _Plan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chosen: list[Authority]
    unresolved: list[str]
    skipped_open: list[str]
    remaining: int


def plan(inputs: WaveInputs, wave_size: int) -> _Plan:
    """Which authorities this wave writes to, and why the others are not in it."""
    busy = _open(inputs.requests)
    eligible, unresolved, skipped = [], [], []
    for authority in inputs.population.authorities:
        contact = inputs.contacts.get(authority.authority_id, Contact())
        if authority.authority_id in busy:
            skipped.append(authority.authority_id)
        elif contact.recipient is None:
            unresolved.append(authority.authority_id)
        else:
            eligible.append(authority)
    ids = known_ids(inputs)
    eligible.sort(key=lambda a: (not ids.get(a.authority_id), a.authority_id))
    chosen = eligible[:wave_size]
    return _Plan(
        chosen=chosen,
        unresolved=unresolved,
        skipped_open=skipped,
        remaining=len(eligible) - len(chosen),
    )


def _letter(inputs: WaveInputs, authority: Authority, ids: tuple[str, ...]) -> LetterRequest:
    recipient = inputs.contacts[authority.authority_id].recipient
    if recipient is None:
        raise InnsynRefusedError(f"{authority.authority_id} has no recorded recipient")
    contact = inputs.contacts[authority.authority_id]
    return LetterRequest(
        authority=authority,
        recipient=recipient.address,
        maalform=contact.letter_form,
        lf_ids=ids,
        sender=inputs.sender,
    )


def _write_letter(out: Path, request: LetterRequest, recorded: bool) -> ManifestEntry:
    payload = render(request).text().encode("utf-8")
    name = f"{request.authority.authority_id}.txt"
    atomic_replace_bytes(out / name, payload)
    return ManifestEntry(
        authority_id=request.authority.authority_id,
        kind=request.authority.kind,
        name=request.authority.letter_name,
        recipient=request.recipient,
        maalform=request.maalform,
        maalform_recorded=recorded,
        lf_ids=request.lf_ids,
        letter_file=name,
        letter_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _letters(inputs: WaveInputs, chosen: list[Authority], out: Path) -> list[ManifestEntry]:
    ids = known_ids(inputs)
    entries = []
    for authority in chosen:
        request = _letter(inputs, authority, ids.get(authority.authority_id, ()))
        recorded = inputs.contacts[authority.authority_id].maalform is not None
        entries.append(_write_letter(out, request, recorded))
    return entries


def write_wave(inputs: WaveInputs, out: Path, wave_size: int, generated_at: datetime) -> Manifest:
    """Write the next wave's letters and manifest into ``out``.

    Raises:
        InnsynRefusedError: ``out`` exists and is not empty, or its parent is missing.
    """
    _fresh_directory(out)
    chosen = plan(inputs, wave_size)
    entries = _letters(inputs, chosen.chosen, out)
    manifest = Manifest(
        generated_at=generated_at,
        population_valid_at=inputs.population.valid_at.isoformat(),
        wave_size=wave_size,
        letters=tuple(entries),
        unresolved=tuple(chosen.unresolved),
        skipped_open=tuple(chosen.skipped_open),
        remaining=chosen.remaining,
    )
    atomic_replace_bytes(
        out / MANIFEST_FILENAME, (manifest.model_dump_json(indent=2) + "\n").encode()
    )
    return manifest


def _fresh_directory(out: Path) -> None:
    if out.exists() and any(out.iterdir()):
        raise InnsynRefusedError(f"{out} is not empty; a wave is written into a new directory")
    try:
        out.mkdir(exist_ok=True)
    except FileNotFoundError as exc:
        raise InnsynRefusedError(f"{out.parent} is missing; {out} not made") from exc


def read_manifest(path: Path) -> Manifest:
    """Raises: InnsynRefusedError: missing or not a manifest."""
    try:
        return Manifest.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise InnsynRefusedError(f"{path}: not a wave manifest ({exc})") from exc
