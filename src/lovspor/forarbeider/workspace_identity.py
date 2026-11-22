"""AR5d-1: establishing a workspace's identity, and reading legacy anchors (#738, #1013).

:func:`establish` gives an **empty** workspace its identity
(:mod:`~lovspor.forarbeider.workspace_identity_models`), under the workspace
lock, in three steps, each one an AR5a primitive:

1. anchor the empty ``workspace_identity`` family in the new identity's scope
   (:func:`~lovspor.forarbeider.control_plane_anchor.initialize`);
2. write the identity file once, whole;
3. anchor it as the family's one record
   (:func:`~lovspor.forarbeider.control_plane_anchor.anchor_pending`).

Every interruption leaves a state verification names exactly: before step 2,
no identity (the orphaned empty anchor names a random scope nobody holds, and a
retry draws a new identity); after step 2, ``pending_anchor``, which denies
until a retry completes it (``completed``); after step 3, ``verified``, and a
retry is ``already_established``.

What is refused, so that nothing is adopted silently:

- a workspace holding anything but its lock and leftovers
  (``workspace_not_empty``): a pre-AR5d workspace keeps its records, its latch
  and its legacy anchors untouched, and is denied until an owner-approved
  migration (AR5d-2) exists;
- an identity file the witness never anchored (``unanchored``), or any other
  state but ``pending_anchor`` and ``verified``: an identity is never
  re-registered, so a forged, substituted or rolled-back one stays refused.

:func:`legacy_verdicts` verifies a pre-AR5d workspace's families under the old
literal scope ``workspace``, read-only: it anchors nothing and writes nothing.

**Lock ordering.** The workspace lock, then the witness's lock (a leaf, taken
inside each anchoring step). No ledger lock is taken.
"""

from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple

from lovspor.forarbeider import publication_attempts, workspace_identity_models
from lovspor.forarbeider.control_plane_anchor import (
    AnchorAuthority,
    AnchorQuery,
    anchor_pending,
    initialize,
    verify,
)
from lovspor.forarbeider.control_plane_anchor_models import Family, Status
from lovspor.forarbeider.control_plane_chains import workspace_chain

#: The scope every pre-AR5d workspace family was anchored in, for every workspace.
LEGACY_SCOPE = "workspace"
_LEGACY_FAMILIES: tuple[Family, ...] = ("stop_latch", "recovery_receipts")


class IdentityOutcome(NamedTuple):
    status: Literal["established", "completed", "already_established"]
    scope: str
    identity: workspace_identity_models.WorkspaceIdentity


def _query(workspace: Path, repository: str, scope: str) -> AnchorQuery:
    chain = workspace_chain("workspace_identity", workspace, scope)
    return AnchorQuery("workspace_identity", repository, scope, chain)


def _existing(
    workspace: Path, raw: bytes, repository: str, authority: AnchorAuthority
) -> IdentityOutcome:
    identity = workspace_identity_models.parse_identity(raw, repository)
    scope = workspace_identity_models.scope_of(raw)
    query = _query(workspace, repository, scope)
    verdict = verify(query, authority)
    if verdict.status == "verified":
        return IdentityOutcome("already_established", scope, identity)
    if verdict.status != "pending_anchor":
        raise workspace_identity_models.refused(verdict.status)
    if anchor_pending(query, authority).status != "verified":
        raise workspace_identity_models.refused("not_anchored")
    return IdentityOutcome("completed", scope, identity)


def establish(
    workspace: Path, repository: str, authority: AnchorAuthority, now: datetime
) -> IdentityOutcome:
    """Give an empty workspace its anchored identity; complete or confirm an existing one.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked: workspace``; ``release_not_eligible:
            workspace_identity:<why>`` (``workspace_not_empty:<names>``, a
            malformed identity, or a status but ``pending_anchor``/``verified``);
            ``anchor:<status>`` and the witness's refusals, such as
            ``anchor_store_unavailable``.
    """
    held = authority._replace(workspace=workspace)
    with publication_attempts.workspace_lock(workspace):
        workspace_identity_models.remove_leftovers(workspace)
        raw = workspace_identity_models.read_identity(workspace)
        if raw is not None:
            return _existing(workspace, raw, repository, held)
        found = workspace_identity_models.held_records(workspace)
        if found:
            raise workspace_identity_models.refused(f"workspace_not_empty:{','.join(found)}")
        identity = workspace_identity_models.new_identity(repository, now)
        raw = workspace_identity_models.identity_bytes(identity)
        scope = workspace_identity_models.scope_of(raw)
        initialize(_query(workspace, repository, scope), held)
        workspace_identity_models.write_identity(workspace, raw)
        if anchor_pending(_query(workspace, repository, scope), held).status != "verified":
            raise workspace_identity_models.refused("not_anchored")
    return IdentityOutcome("established", scope, identity)


def legacy_verdicts(
    workspace: Path, repository: str, authority: AnchorAuthority
) -> dict[Family, Status]:
    """Each pre-AR5d workspace family's status under the literal scope; read-only."""
    held = authority._replace(workspace=workspace)
    return {
        family: verify(
            AnchorQuery(
                family, repository, LEGACY_SCOPE, workspace_chain(family, workspace, LEGACY_SCOPE)
            ),
            held,
        ).status
        for family in _LEGACY_FAMILIES
    }
