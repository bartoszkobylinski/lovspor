"""The temporal gate epoch: serving rule and writers (ADR-0012 Amendment 1).

ADR-0012 point 2b lets a state be served ``unattested`` only when its
attestation is unsatisfiable in principle — never a state the gate could
have attested and did not. The gate epoch is where that exception ends:
the start of the first production sync run that ran the reconciliation
gate under the serving parser version, recorded before the gate runs so
the record never depends on the gate's outcome. Storage and immutability
live in :mod:`lovspor.temporal_attestation`; this module decides what a
served state's evidence means, and writes the record through the two
supported paths (the sync run, and the operator backfill).
"""

import subprocess
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from lovspor.errors import LovsporError
from lovspor.snapshot import CorpusStateRef
from lovspor.temporal import TEMPORAL_PARSER_VERSION
from lovspor.temporal_attestation import (
    ATTESTATION_NOTES_REF,
    EPOCH_NOTES_REF,
    AttestationError,
    TemporalGateEpoch,
    UtcInstant,
    attested_commits,
    check_gate_epoch,
    fetch_attestations,
    fetch_gate_epochs,
    push_gate_epochs,
    read_attestation,
    read_gate_epochs,
    registry_synchronised,
    write_gate_epoch,
)

BACKFILL_COMMAND = "lovspor temporal-epoch --corpus-path <writable lovverk clone> backfill"
"""The operator action a missing epoch record names."""


class UnattestedGateStateError(LovsporError):
    """A gate-eligible state has no attestation (Amendment 1, outcome 5).

    Its author date is at or after the serving parser version's gate
    epoch, so the gate refused it, has not reached it yet, or it is an
    intermediate state the gate never attests. Deliberately NOT an
    :class:`AttestationError`: the evidence channel works — it says this
    state is unproven — and a refused or pending proof must not read as
    a broken channel, nor ever as a successful ``unattested``.
    """

    def __init__(self, state: CorpusStateRef, epoch: TemporalGateEpoch) -> None:
        self.corpus_commit = state.sha
        self.parser_version = epoch.parser_version
        self.epoch_at = epoch.epoch_at
        super().__init__(
            f"unattested gate-era state: corpus_commit {state.sha} (author date "
            f"{state.commit_date.isoformat()}) is at or after the temporal parser "
            f"version {epoch.parser_version} gate epoch {epoch.epoch_at.isoformat()} "
            f"({epoch.evidence}) and carries no attestation under that version. The "
            f"gate refused this state, has not reached it yet, or never attests it; "
            f"it is not served as 'unattested' (ADR-0012 Amendment 1).",
        )


def served_reconciliation(repo: Path, state: CorpusStateRef) -> str:
    """The ``reconciliation`` field for one served state, in the order of
    ADR-0012 Amendment 1 point 1d; every non-answer raises.

    1. unsynchronised, unreadable or corrupt registry — epoch records
       included — :class:`AttestationError`;
    2. an attestation for ``(state, version)`` — ``attested``;
    3. no epoch record for the version — :class:`AttestationError`;
    4. author date before the epoch — ``unattested`` (the 2b exception);
    5. otherwise — :class:`UnattestedGateStateError`.
    """
    _require_synchronised(repo)
    epochs = read_gate_epochs(repo)
    if read_attestation(repo, state.sha, TEMPORAL_PARSER_VERSION) is not None:
        return "attested"
    epoch = epochs.get(TEMPORAL_PARSER_VERSION)
    if epoch is None:
        raise AttestationError(
            f"no gate-epoch record for temporal parser version "
            f"{TEMPORAL_PARSER_VERSION} in {EPOCH_NOTES_REF}: the registry cannot "
            f"say where the pre-gate exception ends, so no 'unattested' answer is "
            f"justified. Record it ({BACKFILL_COMMAND}, or the first sync run under "
            f"the version), then refresh this clone with 'lovspor fetch-corpus'.",
        )
    if state.commit_date < epoch.epoch_at:
        return "unattested"
    raise UnattestedGateStateError(state, epoch)


def _require_synchronised(repo: Path) -> None:
    if not registry_synchronised(repo):
        raise AttestationError(
            f"attestation registry is not synchronised in this checkout "
            f"({repo}): plain clones do not fetch {ATTESTATION_NOTES_REF}, so "
            f"a local read cannot tell 'no proof recorded' from 'proof never "
            f"fetched'. Acquire the corpus with 'lovspor fetch-corpus' (which "
            f"configures the notes refspec), or fetch the ref once, and retry.",
        )


class EpochReport(BaseModel):
    """What one epoch command did: the record it judged, and whether it
    was written. ``warning`` marks a refusal the operator must act on."""

    model_config = ConfigDict(frozen=True)

    message: str
    record: TemporalGateEpoch | None = None
    written: bool = False
    warning: bool = False


class BackfillRequest(BaseModel):
    """An operator's backfill of the serving parser version's epoch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    epoch_at: UtcInstant
    boundary_commit: str = Field(min_length=4)
    sync_run: str = Field(pattern=r"^[0-9]+$")
    apply: bool = False


def record_sync_run_epoch(repo: Path, sync_run: str, now: datetime) -> EpochReport:
    """The sync's mechanical write, at run start and before the gate.

    Writes and pushes a ``sync-run`` record only when none exists for the
    running version AND no attestation under it exists: the first run
    under a new parser version. With attestations but no record it writes
    nothing — it never invents a ship date after the fact; that record is
    the operator backfill.
    """
    _fetch_registry(repo)
    existing = read_gate_epochs(repo).get(TEMPORAL_PARSER_VERSION)
    if existing is not None:
        return EpochReport(message="gate epoch already recorded; nothing to do", record=existing)
    if attested_commits(repo, TEMPORAL_PARSER_VERSION):
        return EpochReport(
            message=(
                f"attestations under temporal parser version {TEMPORAL_PARSER_VERSION} "
                f"exist without a gate-epoch record; not inventing a ship date — "
                f"the record is an operator backfill ({BACKFILL_COMMAND})"
            ),
            warning=True,
        )
    record = _sync_run_record(repo, sync_run, now)
    write_gate_epoch(repo, record)
    push_gate_epochs(repo)
    return EpochReport(message="gate epoch recorded and pushed", record=record, written=True)


def backfill_epoch(repo: Path, request: BackfillRequest, now: datetime) -> EpochReport:
    """The operator's record of an epoch that predates this mechanism.

    Dry run by default: fetches the registry refs from ``origin``,
    validates the record exactly as the write would, and writes nothing.
    With ``apply`` it writes the note and pushes the epoch ref.
    """
    _fetch_registry(repo)
    record = TemporalGateEpoch(
        parser_version=TEMPORAL_PARSER_VERSION,
        epoch_at=request.epoch_at,
        boundary_commit=_resolve_commit(repo, request.boundary_commit),
        recorded_at=now,
        source="backfill",
        evidence=_evidence(request.sync_run),
    )
    if not check_gate_epoch(repo, record):
        return EpochReport(message="identical gate epoch already recorded", record=record)
    if not request.apply:
        return EpochReport(message="dry run: valid; nothing written (pass --apply)", record=record)
    write_gate_epoch(repo, record)
    push_gate_epochs(repo)
    return EpochReport(message="gate epoch recorded and pushed", record=record, written=True)


def _fetch_registry(repo: Path) -> None:
    fetch_attestations(repo)
    fetch_gate_epochs(repo)


def _sync_run_record(repo: Path, sync_run: str, now: datetime) -> TemporalGateEpoch:
    # Author dates have whole-second resolution: an epoch carrying
    # microseconds would read a commit made in the same second as
    # pre-epoch. Flooring errs toward gate-era, which fails closed.
    epoch_at = now.replace(microsecond=0)
    return TemporalGateEpoch(
        parser_version=TEMPORAL_PARSER_VERSION,
        epoch_at=epoch_at,
        boundary_commit=_resolve_commit(repo, "HEAD"),
        recorded_at=now,
        source="sync-run",
        evidence=_evidence(sync_run),
    )


def _evidence(sync_run: str) -> str:
    return f"lovspor sync run {sync_run}"


def _resolve_commit(repo: Path, rev: str) -> str:
    result = subprocess.run(  # noqa: S603 — trusted git, list args, no shell
        ["git", "rev-parse", "--verify", "--end-of-options", f"{rev}^{{commit}}"],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AttestationError(f"cannot resolve commit {rev!r} in {repo}: {result.stderr.strip()}")
    return result.stdout.strip()
