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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, NamedTuple

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
)

from lovspor.errors import LovsporError, TemporalDerivationError
from lovspor.temporal import count_source_amendment_notes, derive_temporal_layer

ATTESTATION_NOTES_REF = "refs/notes/temporal-attestations"
"""The git-notes ref holding attestation entries, one note per commit."""

ATTESTATION_FETCH_REFSPEC = f"+{ATTESTATION_NOTES_REF}*:{ATTESTATION_NOTES_REF}*"
"""The fetch refspec ``fetch_corpus`` configures on a consumer clone.

The trailing glob is load-bearing: a *bare* refspec for a ref the remote
does not have yet makes every ``git fetch``/``git pull`` fail with
``couldn't find remote ref`` (verified empirically), which would brick
plain corpus updates against a pre-gate origin. A glob that matches
nothing is silently fine, and transports the registry the moment the
origin gains it."""

EPOCH_NOTES_REF = f"{ATTESTATION_NOTES_REF}-epoch"
"""The gate-epoch ref (ADR-0012 Amendment 1): one immutable record per
parser version, noted on the boundary commit. A sibling, not a second
entry shape inside the attestation ref, whose reader would read it as
corruption; and a name under ``ATTESTATION_FETCH_REFSPEC``'s glob, so
consumer clones fetch it with no refspec change."""

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
    # Impossible values are channel corruption at read time, not data:
    # versions start at 1 and counts cannot be negative, so a note
    # carrying them fails validation and surfaces as AttestationError.
    parser_version: int = Field(ge=1)
    documents_reconciled: int = Field(ge=0)
    notes_total: int = Field(ge=0)
    events_total: int = Field(ge=0)
    attested_at: datetime


def _require_utc(value: datetime) -> datetime:
    # The record format is UTC; an offset elsewhere is a record some
    # other writer produced, not one of ours read back.
    if value.utcoffset() != timedelta(0):
        raise ValueError("must be a UTC instant (offset +00:00 or Z)")
    return value


UtcInstant = Annotated[AwareDatetime, AfterValidator(_require_utc)]
"""A timezone-aware instant whose offset is zero."""


class TemporalGateEpoch(BaseModel):
    """When the reconciliation gate began for one parser version.

    ``epoch_at`` is the start of the first production sync run that runs
    the gate under ``parser_version``; ``boundary_commit`` is the corpus
    HEAD when the record was written — the last state before the epoch,
    and the commit the note is attached to.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    parser_version: int = Field(ge=1)
    epoch_at: UtcInstant
    boundary_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    recorded_at: UtcInstant
    source: Literal["sync-run", "backfill"]
    evidence: str = Field(min_length=1)


def registry_synchronised(repo: Path) -> bool:
    """True when local registry reads are backed by the acquisition contract.

    A local read of the registry is only meaningful when this checkout
    actually carries it. Three ways it can (any one suffices):

    * the repo has no ``origin`` remote — it IS the registry's home (the
      sync engine's working repo, a test fixture): nothing exists to fetch;
    * an ``origin`` fetch refspec genuinely transports the notes ref —
      source AND destination side — so every ``git fetch``/``git pull``
      carries the registry; what ``fetch_corpus`` configures;
    * the notes ref exists locally (a one-shot ``fetch_attestations``).

    Anything else is an unsynchronised clone: a proof may exist on the
    remote that this checkout never fetched, and ADR-0012 point 2c forbids
    reading that as ``unattested`` — absence of the evidence channel must
    never impersonate absence of evidence. Callers fail closed instead.
    """

    def _read(args: list[str]) -> subprocess.CompletedProcess[str]:
        # S603/S607: trusted git command, list args, no shell.
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )

    # Pure config read: `git remote get-url` fatals on a syntactically
    # invalid fetch refspec anywhere in the remote's config, which would
    # make a poisoned clone read as "no origin" — its own registry home.
    if _read(["config", "--get", "remote.origin.url"]).returncode != 0:
        return True
    refspecs = _read(["config", "--get-all", "remote.origin.fetch"])
    if refspecs.returncode == 0 and any(
        refspec_transports_registry(line) for line in refspecs.stdout.splitlines()
    ):
        return True
    return _read(["rev-parse", "--quiet", "--verify", ATTESTATION_NOTES_REF]).returncode == 0


def refspec_transports_registry(spec: str) -> bool:
    """True when one fetch refspec really transports the attestation ref.

    Both sides must cover it: the SOURCE side is what gets fetched from
    the remote, the DESTINATION side is what ``read_attestation`` reads
    locally. A refspec that merely mentions the ref on one side — e.g.
    mapping an unrelated branch INTO the notes namespace, or the notes
    ref out to a branch — fetches something else and would recreate the
    false ``unattested`` this guard exists to prevent (codex-tests,
    PR #230). A side covers the ref exactly, or via a trailing-glob
    prefix (``refs/notes/*``, the canonical glob form both included).
    """

    def covers(side: str) -> bool:
        if side.endswith("*"):
            return ATTESTATION_NOTES_REF.startswith(side[:-1])
        return side == ATTESTATION_NOTES_REF

    source, colon, destination = spec.strip().removeprefix("+").partition(":")
    if not colon or source.endswith("*") != destination.endswith("*"):
        # A wildcard on only one side is not a lesser refspec — git refuses
        # it outright (`fatal: invalid refspec`, verified empirically), and
        # while it sits in the config it poisons `git remote get-url` and
        # every fetch/pull. It transports nothing; the repair path removes
        # it (codex-tests round 5, PR #230).
        return False
    return covers(source) and covers(destination)


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
    _fetch_notes_ref(repo, remote, ATTESTATION_NOTES_REF)


def fetch_gate_epochs(repo: Path, remote: str = "origin") -> None:
    """Fetch the gate-epoch ref from ``remote``; same contract as
    :func:`fetch_attestations` (absent on the remote is the bootstrap)."""
    _fetch_notes_ref(repo, remote, EPOCH_NOTES_REF)


def _fetch_notes_ref(repo: Path, remote: str, ref: str) -> None:
    if _remote_ref(repo, remote, ref) is None:
        return
    result = _git(repo, ["fetch", remote, f"+{ref}:{ref}"])
    if result.returncode != 0:
        raise AttestationError(
            f"failed to fetch attestation notes {ref} from {remote}: {result.stderr.strip()}",
        )


def _remote_ref(repo: Path, remote: str, ref: str) -> str | None:
    """The object id ``remote`` holds at exactly ``ref``; None when absent.

    ``git ls-remote`` matches its pattern against ref-name TAILS, so it also
    lists e.g. ``refs/x/refs/notes/temporal-attestations-epoch``, sorted
    before the real ref. Reading its first line would take that bystander's
    id for the notes ref's — only the line naming ``ref`` itself counts.
    """
    listed = _git(repo, ["ls-remote", remote, ref])
    if listed.returncode != 0:
        raise AttestationError(
            f"cannot reach {remote} to check the attestation ref {ref}: {listed.stderr.strip()}",
        )
    suffix = f"\t{ref}"
    for line in listed.stdout.splitlines():
        if line.endswith(suffix):
            return line.removesuffix(suffix)
    return None


def _git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    # S603/S607: trusted git command, list args, no shell.
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
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
    _require_consistent_entries(resolved, entries)
    return entries


def _require_consistent_entries(annotated: str, entries: list[TemporalAttestation]) -> None:
    """Refuse a note whose entries contradict the commit carrying it.

    A syntactically valid note anchored to the wrong commit claims a state
    it is not attached to; two entries under one ``(commit,
    parser_version)`` key cannot both be the immutable record, and picking
    either would silently prefer one. Both are a corrupt channel — for
    every version the note answers, not only the one a caller asked about.
    """
    seen_versions: set[int] = set()
    for entry in entries:
        if entry.corpus_commit != annotated:
            raise AttestationError(
                f"attestation note on {annotated} carries an entry for "
                f"{entry.corpus_commit} — the evidence channel is corrupt",
            )
        if entry.parser_version in seen_versions:
            raise AttestationError(
                f"attestation note on {annotated} carries duplicate entries "
                f"for parser version {entry.parser_version} — the evidence "
                f"channel is corrupt",
            )
        seen_versions.add(entry.parser_version)


def read_gate_epochs(repo: Path) -> dict[int, TemporalGateEpoch]:
    """Every gate-epoch record, keyed by parser version; ``{}`` when the
    ref is absent.

    Lists the notes tree and reads each note blob directly, so it never
    needs the boundary commit's object — a ``--depth 1`` clone reads the
    records too. An unparseable record, one anchored to a commit other
    than its own ``boundary_commit``, or two records for one version are
    a corrupt channel: :class:`AttestationError`, never a guessed epoch.
    """
    epochs: dict[int, TemporalGateEpoch] = {}
    notes = _note_objects(repo, EPOCH_NOTES_REF)
    _require_commit_anchors(repo, [annotated for _, annotated in notes])
    for blob, annotated in notes:
        for record in _epoch_records(repo, blob):
            if record.boundary_commit != annotated:
                raise AttestationError(
                    f"gate-epoch note on {annotated} carries a record for "
                    f"{record.boundary_commit} — the evidence channel is corrupt",
                )
            if record.parser_version in epochs:
                raise AttestationError(
                    f"duplicate gate-epoch records for parser version "
                    f"{record.parser_version} — the evidence channel is corrupt",
                )
            epochs[record.parser_version] = record
    return epochs


def attested_commits(repo: Path, parser_version: int) -> list[str]:
    """Every commit carrying an attestation under ``parser_version``.

    Every note is validated whole, whatever version is asked for: a note on
    a blob, tree or tag, an entry claiming another commit, or a duplicated
    key raises
    :class:`AttestationError` rather than bounding an epoch on it.
    """
    notes = _note_objects(repo, ATTESTATION_NOTES_REF)
    _require_commit_anchors(repo, [annotated for _, annotated in notes], "attestation")
    commits = []
    for blob, annotated in notes:
        entries = _attestation_entries(repo, blob)
        _require_consistent_entries(annotated, entries)
        if any(entry.parser_version == parser_version for entry in entries):
            commits.append(annotated)
    return commits


def check_gate_epoch(repo: Path, record: TemporalGateEpoch) -> bool:
    """Validate ``record`` for writing: True when it may be written,
    False when an identical record already exists (the idempotent retry).

    ``recorded_at`` is the write stamp, not the epoch, so a record that
    differs only there is identical. Refused, as :class:`AttestationError`:
    a different record for an existing version (records are immutable), a
    boundary commit whose author date is not before ``epoch_at``, and an
    ``epoch_at`` later than the earliest attested state under the version
    — the gate cannot have attested a state before it began.
    """
    existing = read_gate_epochs(repo).get(record.parser_version)
    if existing is not None:
        if _epoch_content(existing) == _epoch_content(record):
            return False
        raise AttestationError(
            f"a different gate-epoch record for parser version "
            f"{record.parser_version} already exists ({existing.epoch_at.isoformat()}, "
            f"{existing.evidence}); records are immutable",
        )
    _check_epoch_bounds(repo, record)
    return True


def write_gate_epoch(repo: Path, record: TemporalGateEpoch) -> bool:
    """Record one gate epoch; True when written, False for an identical
    record already present. Validation is :func:`check_gate_epoch`."""
    if not check_gate_epoch(repo, record):
        return False
    siblings = [
        e for e in read_gate_epochs(repo).values() if e.boundary_commit == record.boundary_commit
    ]
    payload = [e.model_dump(mode="json") for e in [*siblings, record]]
    message = json.dumps(payload, sort_keys=True)
    result = _git(
        repo,
        ["notes", f"--ref={EPOCH_NOTES_REF}", "add", "-f", "-m", message, record.boundary_commit],
    )
    if result.returncode != 0:
        raise AttestationError(
            f"failed to record the gate epoch on {record.boundary_commit}: {result.stderr.strip()}",
        )
    return True


def publish_gate_epochs(repo: Path, remote: str = "origin") -> bool:
    """Push the local epoch ref when ``remote`` does not hold it; True when
    pushed.

    A write whose push was rejected leaves the immutable note only in this
    clone, and a retry reads it back as already recorded — so "recorded"
    is not "published" until the remote's ref equals the local one. The
    push is never forced: a remote holding a different history rejects
    it, and that rejection raises instead of overwriting a remote record.
    """
    local = _git(repo, ["rev-parse", "--quiet", "--verify", EPOCH_NOTES_REF])
    if local.returncode != 0:
        return False
    if _remote_ref(repo, remote, EPOCH_NOTES_REF) == local.stdout.strip():
        return False
    push_gate_epochs(repo, remote)
    return True


def push_gate_epochs(repo: Path, remote: str = "origin") -> None:
    """Push the gate-epoch ref to ``remote``; a rejected push raises."""
    result = _git(repo, ["push", remote, EPOCH_NOTES_REF])
    if result.returncode != 0:
        raise AttestationError(
            f"failed to push {EPOCH_NOTES_REF} to {remote}: {result.stderr.strip()}",
        )


def _epoch_content(record: TemporalGateEpoch) -> dict[str, object]:
    return record.model_dump(exclude={"recorded_at"})


def _check_epoch_bounds(repo: Path, record: TemporalGateEpoch) -> None:
    boundary_date = _author_date(repo, record.boundary_commit)
    if boundary_date >= record.epoch_at:
        raise AttestationError(
            f"boundary commit {record.boundary_commit} has author date "
            f"{boundary_date.isoformat()}, not before the epoch "
            f"{record.epoch_at.isoformat()}",
        )
    attested = [_author_date(repo, c) for c in attested_commits(repo, record.parser_version)]
    if attested and record.epoch_at > min(attested):
        raise AttestationError(
            f"epoch {record.epoch_at.isoformat()} is later than the earliest attested "
            f"state under parser version {record.parser_version} "
            f"({min(attested).isoformat()}): the gate cannot attest before it begins",
        )


def _author_date(repo: Path, commit: str) -> datetime:
    result = _git(repo, ["log", "-1", "--format=%aI", f"{commit}^{{commit}}", "--"])
    if result.returncode != 0:
        raise AttestationError(
            f"cannot resolve commit {commit} in {repo}: {result.stderr.strip()}",
        )
    return datetime.fromisoformat(result.stdout.strip())


def _note_objects(repo: Path, ref: str) -> list[tuple[str, str]]:
    """``(note blob, annotated object)`` for every note under ``ref``;
    ``[]`` when the ref does not exist.

    ``git notes list`` answers an empty list for a ref that points at a
    non-commit, which would read a damaged ref as "no records" — so a
    ref that exists must first prove it is a notes history."""
    exists = _git(repo, ["rev-parse", "--quiet", "--verify", ref]).returncode == 0
    if exists and _git(repo, ["rev-parse", "--quiet", "--verify", f"{ref}^{{commit}}"]).returncode:
        raise AttestationError(f"notes ref {ref} unreadable: it does not point at a commit")
    result = _git(repo, ["notes", f"--ref={ref}", "list"])
    if result.returncode != 0:
        raise AttestationError(f"notes ref {ref} unreadable: {result.stderr.strip()}")
    return [
        (blob, annotated)
        for blob, _, annotated in (line.partition(" ") for line in result.stdout.splitlines())
    ]


def _require_commit_anchors(repo: Path, objects: list[str], channel: str = "gate-epoch") -> None:
    """Every present annotated object must be a commit.

    Epochs and attestations are defined on a corpus state, so a note on a
    blob, tree or tag is corruption; ``channel`` names the registry. An
    object this clone does not hold is accepted: a ``--depth 1`` clone
    lacks older anchors, and the reader must still work there (the id
    match against the record stays enforced).
    """
    if not objects:
        return
    result = subprocess.run(
        ["git", "cat-file", "--batch-check=%(objectname) %(objecttype)"],  # noqa: S607
        cwd=repo,
        input="".join(f"{obj}\n" for obj in objects),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AttestationError(f"{channel} anchors unreadable: {result.stderr.strip()}")
    for line in result.stdout.splitlines():
        name, _, kind = line.partition(" ")
        if kind not in ("commit", "missing"):
            raise AttestationError(
                f"{channel} note is attached to {name}, a {kind} and not a commit — "
                f"the evidence channel is corrupt",
            )


def _epoch_records(repo: Path, blob: str) -> list[TemporalGateEpoch]:
    try:
        return _EPOCH_RECORDS.validate_json(_note_text(repo, blob))
    except ValidationError as exc:
        raise _unparseable_note(blob, exc) from exc


def _attestation_entries(repo: Path, blob: str) -> list[TemporalAttestation]:
    try:
        return _ATTESTATION_ENTRIES.validate_json(_note_text(repo, blob))
    except ValidationError as exc:
        raise _unparseable_note(blob, exc) from exc


def _note_text(repo: Path, blob: str) -> str:
    result = _git(repo, ["cat-file", "blob", blob])
    if result.returncode != 0:
        raise AttestationError(f"note blob {blob} unreadable: {result.stderr.strip()}")
    return result.stdout


def _unparseable_note(blob: str, exc: ValidationError) -> AttestationError:
    return AttestationError(f"note {blob} is unparseable — a broken evidence channel: {exc}")


_EPOCH_RECORDS = TypeAdapter(list[TemporalGateEpoch])
_ATTESTATION_ENTRIES = TypeAdapter(list[TemporalAttestation])
