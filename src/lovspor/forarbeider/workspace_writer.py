"""AR5d-1: one writer per workspace identity, fenced by the witness (#738, #1013; §9.9 AR5).

A workspace identity names **one exclusive writer at a time**, not a lineage
that independent writers may share. A copy of a workspace, or of its identity
file, holds the same anchored identity but another workspace lock, and its
attempt records are scoped per transaction; so neither the local lock nor the
exactly-once attempt chain stops two copies from sending two different
transactions. The witness does.

Before every send, :func:`claim` appends a ``claim`` to the workspace's
``workspace_writer`` chain (:mod:`~lovspor.forarbeider.workspace_writer_models`)
and anchors it, in the identity's scope, through the AR5a primitives:

- the chain must first be exactly what the witness anchored (``verified``). A
  copy behind the witness is ``local_rolled_back``; a copy whose own claim lost
  the race is ``diverged``, for good: its chain can never be anchored again,
  whatever transaction or ledger it is given;
- the newest claim must be released, or its lease run out (``claim_held``);
- the claim is anchored by the witness's compare-and-swap on the chain head:
  of two copies claiming at once, one is anchored and the other's submit is
  refused (``anchor_head_moved``). The anchored head is the fencing token.

:func:`require_current`, immediately before the send, proves the claim is
still the anchored head and has at least ``SEND_MARGIN`` of its lease left. A
takeover is only possible once the lease has run out, so a writer that was
taken over never begins a send. :func:`release` ends the claim after the
outcome is recorded, or when a check refuses before the send.

**Crashes.** A claim appended but not anchored is ``pending_anchor``: the next
claim anchors it first (it is this chain's own record; if a copy anchored
another one meanwhile, the chain is ``diverged`` and refused). A claim anchored
and never released holds every writer of the identity off until its lease ends;
then exactly one claim is anchored, and the previous holder is fenced. A
release that could not be anchored is completed the same way. No identity is
ever regenerated.

**Lock ordering.** Called under the workspace lock (and the ledger's); the
witness's lock is taken only inside each anchoring step, as a leaf.
"""

import secrets
from datetime import datetime
from typing import TYPE_CHECKING

from lovspor.forarbeider import publication_anchors, workspace_writer_models
from lovspor.forarbeider.control_plane_anchor import (
    AnchorAuthority,
    AnchorQuery,
    AnchorVerdict,
    anchor_pending,
    verify,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.workspace_writer_models import LEASE, SEND_MARGIN, WriterEvent

if TYPE_CHECKING:
    from lovspor.forarbeider.publication_execute import ExecutionRequest


def _refused(why: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, f"workspace_writer:{why}")


def _held(request: "ExecutionRequest") -> tuple[AnchorAuthority, str]:
    authority = publication_anchors.authority_of(request)
    if authority is None:
        raise _refused("anchor_authority_missing")
    return authority, publication_anchors.scope_of(request)


def _query(request: "ExecutionRequest", scope: str) -> AnchorQuery:
    return publication_anchors.workspace_query(
        request.inputs, request.workspace, "workspace_writer", scope
    )


def _anchored(query: AnchorQuery, authority: AnchorAuthority) -> AnchorVerdict:
    try:
        return anchor_pending(query, authority)
    except ForarbeiderRefusedError as error:
        raise _refused(error.detail) from error


def _settled(request: "ExecutionRequest", authority: AnchorAuthority, scope: str) -> None:
    """The chain is exactly the anchored one; this chain's own pending record is anchored first."""
    query = _query(request, scope)
    verdict = verify(query, authority)
    if verdict.status == "pending_anchor":
        verdict = _anchored(query, authority)
    if verdict.status != "verified":
        raise _refused(verdict.status)


def _free(newest: WriterEvent | None, now: datetime) -> None:
    """The slot is free at ``now``: the newest claim is released or its lease over."""
    if newest is None:
        return
    if now < newest.at:
        raise _refused("clock_regressed")
    if newest.kind == "claim" and newest.lease_until is not None and now < newest.lease_until:
        raise _refused("claim_held")


def claim(request: "ExecutionRequest", attempt_id: str, now: datetime) -> WriterEvent:
    """Claim the identity's one writer slot for this attempt, anchored before anything is sent.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible``: ``workspace_identity:<why>``;
            ``workspace_writer:`` ``<status>`` (``local_rolled_back``,
            ``diverged``, ``store_unavailable``, ...), ``clock_regressed``,
            ``claim_held``, ``anchor_head_moved`` (another copy claimed first)
            or ``anchor:<status>``.
    """
    authority, scope = _held(request)
    _settled(request, authority, scope)
    sequence, previous, newest = workspace_writer_models.head(request.workspace)
    _free(newest, now)
    event = WriterEvent(
        sequence=sequence,
        previous_sha256=previous,
        kind="claim",
        claim_id=secrets.token_hex(32),
        transaction_id=request.inputs.transaction.transaction_id,
        attempt_id=attempt_id,
        at=now,
        lease_until=now + LEASE,
    )
    workspace_writer_models.append(request.workspace, event)
    if _anchored(_query(request, scope), authority).status != "verified":
        raise _refused("not_anchored")
    return event


def require_current(request: "ExecutionRequest", held: WriterEvent, now: datetime) -> None:
    """Right before the send: ``held`` is still the anchored head, with lease enough to send.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible: workspace_writer:``
            ``claim_lost:<status>`` or ``lease_expiring``.
    """
    authority, scope = _held(request)
    query = _query(request, scope)
    status = verify(query, authority).status
    if status != "verified" or query.local.last != workspace_writer_models.event_bytes(held):
        raise _refused(f"claim_lost:{status}")
    if held.lease_until is None or now > held.lease_until - SEND_MARGIN:
        raise _refused("lease_expiring")


def _ended(request: "ExecutionRequest", held: WriterEvent, now: datetime) -> WriterEvent:
    sequence, previous, _ = workspace_writer_models.head(request.workspace)
    return WriterEvent.model_validate(
        held.model_dump()
        | {
            "sequence": sequence,
            "previous_sha256": previous,
            "kind": "release",
            "at": max(now, held.at),
            "lease_until": None,
        }
    )


def release(request: "ExecutionRequest", held: WriterEvent, now: datetime) -> bool:
    """End ``held`` if it is still the anchored head; ``False`` when that cannot be done now.

    A release that is appended but not anchored is completed by the next
    :func:`claim`; one that is not appended leaves the claim to its lease.
    """
    try:
        authority, scope = _held(request)
        query = _query(request, scope)
        current = verify(query, authority).status == "verified"
        if not current or query.local.last != workspace_writer_models.event_bytes(held):
            return False
        workspace_writer_models.append(request.workspace, _ended(request, held, now))
        return anchor_pending(_query(request, scope), authority).status == "verified"
    except ForarbeiderRefusedError:
        return False
