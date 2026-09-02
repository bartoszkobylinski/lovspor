"""Attestation registry for temporal counted-conformance (ADR-0012 point 2).

An attestation records that the build-time reconciliation gate proved
ADR-0009's counted conformance — the parser-visible amendment-note count
equals the source XML's ``changesToParent`` count, corpus-wide — for one
``(corpus_commit, temporal parser version)`` pair. It attests exactly
that check and nothing broader.

Storage is git notes on the corpus repository
(``refs/notes/temporal-attestations``): the record travels with every
corpus clone, keys directly on the commit it attests, and adds no commit
to the corpus history — so attesting a state never creates a new
resolvable corpus state. The evidence-channel contract of ADR-0012
point 2c maps onto three distinguishable answers:

* a readable ref with no note for the commit → **absent** (``None``);
* a note whose entry list carries the key → the entry;
* an unreadable repository or an unparseable note → a typed
  :class:`AttestationError` — a broken evidence channel must never
  impersonate absence of evidence.

Entries are immutable: writing an identical entry again is an idempotent
no-op (workflow retries must not fail), writing a DIFFERENT entry under
an existing key is refused. Correcting a gate result means bumping
``TEMPORAL_PARSER_VERSION`` and attesting under the new key.
"""

import json
import subprocess
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, ValidationError

from lovspor.errors import LovsporError, TemporalDerivationError
from lovspor.temporal import count_source_amendment_notes, derive_temporal_layer

ATTESTATION_NOTES_REF = "refs/notes/temporal-attestations"
"""The git-notes ref holding attestation entries, one note per commit."""

_NO_NOTE_MARKERS = ("no note found", "No note found")
"""Git's message when a commit has no note — the ABSENT answer, which is
the only non-zero outcome allowed to read as anything but an error."""


class AttestationError(LovsporError):
    """The attestation evidence channel failed — unreadable repository,
    unparseable note, or an attempted rewrite of an existing entry.

    Deliberately distinct from an absent attestation (``None``): absence
    is evidence of nothing recorded; this is a broken or misused channel
    and must surface as an operational failure, never as ``unattested``.
    """


class TemporalAttestation(BaseModel):
    """One recorded counted-conformance result for one (commit, parser)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    corpus_commit: str
    parser_version: int
    documents_reconciled: int
    notes_total: int
    events_total: int
    attested_at: datetime


def fetch_attestations(repo: Path, remote: str = "origin") -> None:
    """Fetch the attestation notes ref from ``remote`` into this clone.

    A plain ``git clone`` does not fetch ``refs/notes/*``, so a fresh
    clone would read every remote attestation as a false local absence —
    and a writer starting from such a clone would create a second notes
    history and fail the push non-fast-forward. Every reader and writer
    calls this (or the equivalent fetch) before touching the registry.

    A remote that has no attestation ref yet (bootstrap) is fine; any
    other fetch failure is a channel failure and raises.
    """
    present = subprocess.run(  # noqa: S603
        ["git", "ls-remote", "--exit-code", remote, ATTESTATION_NOTES_REF],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if present.returncode == 2:  # noqa: PLR2004 — git: ref not found on remote
        return
    if present.returncode != 0:
        raise AttestationError(
            f"cannot reach {remote} to check the attestation ref: "
            f"{present.stderr.strip()}",
        )
    result = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "git",
            "fetch",
            remote,
            f"+{ATTESTATION_NOTES_REF}:{ATTESTATION_NOTES_REF}",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AttestationError(
            f"failed to fetch attestation notes from {remote}: "
            f"{result.stderr.strip()}",
        )


class ReconciliationTotals(NamedTuple):
    """Corpus-wide evidence a passing gate records."""

    documents: int
    notes: int
    events: int


def reconcile_corpus(
    repo: Path,
    docs: Iterable[tuple[str, str, bytes]],
) -> ReconciliationTotals:
    """Prove ADR-0009 counted conformance for every given document, or raise.

    ``docs`` yields ``(doc_id, markdown_path, xml_bytes)``; the rendered
    Markdown is read from the repository — the committed state is what
    gets attested. Derivation runs ``strict=False``: the attestation
    proves COUNTS, not marker recognisability, which stays the serving
    path's own strict contract (ADR-0012 point 3 vs point 2). A count
    mismatch or an unparseable date raises ``TemporalDerivationError``
    naming the document, which must abort the build before anything is
    pushed.
    """
    documents = notes = events = 0
    for doc_id, markdown_path, xml_bytes in docs:
        expected = count_source_amendment_notes(xml_bytes)
        markdown = (repo / markdown_path).read_text(encoding="utf-8")
        try:
            layer = derive_temporal_layer(
                markdown,
                document_ref=doc_id,
                expected_note_count=expected,
                strict=False,
            )
        except TemporalDerivationError as exc:
            raise TemporalDerivationError(f"{doc_id}: {exc}") from exc
        documents += 1
        notes += layer.notes_seen
        events += len(layer.events)
    return ReconciliationTotals(documents, notes, events)


def read_attestation(
    repo: Path,
    corpus_commit: str,
    parser_version: int,
) -> TemporalAttestation | None:
    """The recorded attestation for ``(corpus_commit, parser_version)``,
    or ``None`` when the readable registry holds no such entry."""
    entries = _read_entries(repo, corpus_commit)
    for entry in entries:
        if entry.parser_version == parser_version:
            return entry
    return None


def write_attestation(repo: Path, attestation: TemporalAttestation) -> None:
    """Record one attestation; idempotent for an identical re-write.

    A DIFFERENT entry under an existing ``(commit, parser_version)`` key
    is refused — entries are immutable, and a correction is a parser
    version bump, never an edit.
    """
    entries = _read_entries(repo, attestation.corpus_commit)
    for entry in entries:
        if entry.parser_version != attestation.parser_version:
            continue
        if entry == attestation:
            return
        raise AttestationError(
            f"attestation for {attestation.corpus_commit} at parser version "
            f"{attestation.parser_version} already exists with different "
            f"content; entries are immutable — bump TEMPORAL_PARSER_VERSION "
            f"instead of rewriting",
        )
    payload = [e.model_dump(mode="json") for e in [*entries, attestation]]
    result = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "git",
            "notes",
            f"--ref={ATTESTATION_NOTES_REF}",
            "add",
            "-f",
            "-m",
            json.dumps(payload, sort_keys=True),
            attestation.corpus_commit,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AttestationError(
            f"failed to record attestation for {attestation.corpus_commit}: "
            f"{result.stderr.strip()}",
        )


def _read_entries(repo: Path, corpus_commit: str) -> list[TemporalAttestation]:
    """Every entry noted on ``corpus_commit``; [] when the note is absent.

    Any failure other than git's own "no note found" is a channel
    failure and raises.
    """
    probe = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "--verify", f"{corpus_commit}^{{commit}}"],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        # git reports "no note found" even for a commit this clone has
        # never seen — which would collapse "channel cannot judge" into
        # "absent". Prove the commit first, so ABSENT is only ever said
        # about a commit the registry could actually speak to.
        raise AttestationError(
            f"attestation registry unreadable: {corpus_commit} is not a "
            f"commit this repository can resolve",
        )
    resolved = probe.stdout.strip()
    result = subprocess.run(  # noqa: S603
        ["git", "notes", f"--ref={ATTESTATION_NOTES_REF}", "show", corpus_commit],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if any(marker in stderr for marker in _NO_NOTE_MARKERS):
            return []
        raise AttestationError(
            f"attestation registry unreadable for {corpus_commit}: {stderr}",
        )
    try:
        raw = json.loads(result.stdout)
        entries = [TemporalAttestation.model_validate(item) for item in raw]
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise AttestationError(
            f"attestation note on {corpus_commit} is unparseable — a broken "
            f"evidence channel, not an absent attestation: {exc}",
        ) from exc
    for entry in entries:
        if entry.corpus_commit != resolved:
            # A syntactically valid note anchored to the wrong commit is
            # semantic corruption, not evidence: the entry claims a state
            # it is not attached to.
            raise AttestationError(
                f"attestation note on {resolved} carries an entry for "
                f"{entry.corpus_commit} — the evidence channel is corrupt",
            )
    return entries
