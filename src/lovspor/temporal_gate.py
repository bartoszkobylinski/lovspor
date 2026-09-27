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

from pathlib import Path

from lovspor.errors import LovsporError
from lovspor.snapshot import CorpusStateRef
from lovspor.temporal import TEMPORAL_PARSER_VERSION
from lovspor.temporal_attestation import (
    ATTESTATION_NOTES_REF,
    EPOCH_NOTES_REF,
    AttestationError,
    TemporalGateEpoch,
    read_attestation,
    read_gate_epochs,
    registry_synchronised,
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
