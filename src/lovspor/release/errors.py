"""Exceptions of the release envelope and its control plane (ADR-0014 Decision 6).

A branch of ``LovsporError`` so the command layer can catch the family
without a bare ``except Exception``. Every error here is a named refusal
the operator reads as one line — never a state the procedure works
around by trusting a file over the running process.
"""

from lovspor.errors import LovsporError


class ReleaseError(LovsporError):
    """The release procedure refuses to continue; the live release is untouched."""


class IncompleteEnvelopeError(ReleaseError):
    """A directory offered as a release lacks a tree, the record or the fragment.

    Nothing under an id name is ever incomplete by construction (the
    build renames a finalized directory in one step), so an incomplete
    envelope is a directory that never earned its name — a foreign copy,
    a manual edit — and is neither reused, staged nor served.
    """


class EnvelopeError(ReleaseError):
    """The final publish-check refuses the envelope; the message names the first defect."""


class ReleaseConflictError(ReleaseError):
    """``<release_content_id>/`` already exists and is not the same finalized release.

    Never overwritten, never merged, never partially replaced: both
    directories are left untouched and the operator decides.
    """


class ControlPlaneError(ReleaseError):
    """The transaction cannot run: a Caddy command failed or a precondition is unmet."""


class UnobservableError(ControlPlaneError):
    """Caddy's admin endpoint cannot be reached, so R is not a release id at all.

    A state of its own, never "unreconciled": nothing can be compared, so
    every mutating command and ``reconcile`` refuse with the named
    precondition *Caddy admin reachable* and print D and M.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"Caddy admin unreachable ({reason}): {detail}")
        self.reason = reason
        self.detail = detail


class UnreconciledError(ControlPlaneError):
    """R, D and M do not name one release; the three values are in the message."""


class CommitRefusedError(ControlPlaneError):
    """The staged fragment did not validate or does not name the candidate."""


class ReloadFailedError(ControlPlaneError):
    """Caddy would not load the new configuration; the previous one was put back."""


class AdminSocketError(ControlPlaneError):
    """One of the admin socket's four facts does not hold (ADR-0014 Decision 6).

    Never a drift verdict and never an unreconciled host: the permission
    model the whole control plane rests on is broken, so the caller that
    asked — the rehearsal, or the drift timer as its first action —
    stops before it observes anything else.
    """


class MigrationRefusedError(ControlPlaneError):
    """The first migration's preflight found its precondition unmet; nothing moved."""


class RehearsalFailedError(ControlPlaneError):
    """An assertion of the staged rehearsal did not hold (ADR-0014 Validation (g)).

    Names the sub-step — ``i`` through ``v``, or one of the negative
    fixtures — and what was read instead. The production migration is
    not authorised while this can be raised: that is the whole purpose
    of running the sequence on a second instance first.
    """

    def __init__(self, step: str, detail: str) -> None:
        super().__init__(f"rehearsal step {step} failed: {detail}")
        self.step = step
        self.detail = detail


class MigrationFailedError(ControlPlaneError):
    """The first migration stopped after a named checkpoint; the message says what to run."""

    def __init__(self, reached: str, detail: str) -> None:
        super().__init__(f"first migration stopped after {reached}: {detail}")
        self.reached = reached
        self.detail = detail
