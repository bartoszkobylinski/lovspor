"""AR5b: the anchored control state a publication authorization requires (#738; §9.9 AR5).

Before a simulated publication is authorized, the verifier itself reads and
verifies, against the independent witness (AR5a), three families:

- ``control_history`` for the plan's exact policy: the owner's control records
  the caller offers must be exactly the anchored ones. A withheld newest STOP
  is ``local_rolled_back``; another history is ``diverged``;
- ``stop_latch``: the workspace's latch transitions, so a deleted latch is not
  a cleared one; since AR5d-1 anchored in the scope of the workspace's own
  anchored identity, and denied (``workspace_identity:<why>``) without one;
- ``publication_attempts`` for the transaction.

Anything but ``verified`` (missing, stale, rolled back, divergent, unavailable,
not independent) fails the ``ar5_control_anchor`` check, which no simulation
may waive. Nothing a caller passes is taken as "verified": the verdicts are
computed here, from the bytes and the witness.

Anchoring new records is narrow:

- :func:`sync_control` anchors owner control records beyond the anchored head
  only when :func:`~lovspor.forarbeider.release_control.verify_control` accepts
  the whole chain (owner signatures, order, links, scopes), and only when they
  extend the anchored head;
- :func:`settle` anchors the one record an append just wrote. A witness that
  fails leaves the family ``pending_anchor``, which denies until completed.
"""

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from lovspor.forarbeider.control_plane_anchor import (
    AnchorAuthority,
    AnchorQuery,
    AnchorVerdict,
    anchor_pending,
    verify,
    workspace_scope,
)
from lovspor.forarbeider.control_plane_anchor_models import Family
from lovspor.forarbeider.control_plane_chains import control_chain, workspace_chain
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_control import ControlRequest, verify_control

if TYPE_CHECKING:
    from lovspor.forarbeider.publication_authorize import AuthorizationInputs
    from lovspor.forarbeider.publication_execute import ExecutionRequest

REQUIRED: tuple[Family, ...] = ("control_history", "stop_latch", "publication_attempts")
#: A lapsed resume lease leaves the chain intact (it only stops again).
_INTACT = frozenset({"resume_expired"})


def _refused(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, detail)


def authority_of(request: "ExecutionRequest") -> AnchorAuthority | None:
    """The request's witness, bound to the request's own workspace."""
    found = request.anchoring
    return None if found is None else found._replace(workspace=request.workspace)


def control_query(
    inputs: "AuthorizationInputs", policy_id: str, count: int | None = None
) -> AnchorQuery:
    records = inputs.control if count is None else inputs.control[:count]
    chain = control_chain(records, policy_id)
    return AnchorQuery("control_history", inputs.settings.repository, policy_id, chain)


def scope_of(request: "ExecutionRequest") -> str:
    """The request workspace's verified identity scope (AR5d-1).

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible``: ``anchor_authority_missing``
            or ``workspace_identity:<why>``.
    """
    authority = authority_of(request)
    if authority is None:
        raise _refused("anchor_authority_missing")
    return workspace_scope(request.workspace, request.inputs.settings.repository, authority)


def workspace_query(
    inputs: "AuthorizationInputs", workspace: Path, family: Family, scope: str
) -> AnchorQuery:
    chain = workspace_chain(family, workspace, scope)
    return AnchorQuery(family, inputs.settings.repository, scope, chain)


def anchor_reasons(
    authority: AnchorAuthority | None,
    workspace: Path | None,
    inputs: "AuthorizationInputs",
    policy_id: str | None,
) -> tuple[str, ...]:
    """Why the required families are not all verified; empty when they are."""
    if authority is None or workspace is None:
        return ("anchor_authority_missing",)
    if policy_id is None:
        return ("anchor_policy_unknown",)
    held = authority._replace(workspace=workspace)
    tid = inputs.transaction.transaction_id
    queries = [
        control_query(inputs, policy_id),
        workspace_query(inputs, workspace, "publication_attempts", tid),
    ]
    identity: tuple[str, ...] = ()
    try:
        scope = workspace_scope(workspace, inputs.settings.repository, held)
        queries.insert(1, workspace_query(inputs, workspace, "stop_latch", scope))
    except ForarbeiderRefusedError as error:
        identity = (error.detail,)
    found = ((query.family, verify(query, held).status) for query in queries)
    return identity + tuple(f"{f}:{status}" for f, status in found if status != "verified")


def _owner_chain_intact(request: "ExecutionRequest", policy_id: str, now: datetime) -> bool:
    evidence = verify_control(ControlRequest(request.inputs.control, policy_id, now), request.root)
    return evidence.status == "verified" and set(evidence.reasons) <= _INTACT


def sync_control(request: "ExecutionRequest", policy_id: str, now: datetime) -> AnchorVerdict:
    """Anchor the owner control records the witness has not seen yet, one by one.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible``: no witness
            (``anchor_authority_missing``), a chain the owner's signatures do not
            carry (``control_chain_not_verified``), or one that does not extend
            the anchored head (``anchor:control_history:<status>``).
    """
    authority = authority_of(request)
    if authority is None:
        raise _refused("anchor_authority_missing")
    if not _owner_chain_intact(request, policy_id, now):
        raise _refused("control_chain_not_verified")
    full = control_query(request.inputs, policy_id)
    verdict = verify(full, authority)
    if verdict.status == "verified":
        return verdict
    if verdict.status not in ("pending_anchor", "unanchored_tail") or verdict.latest is None:
        raise _refused(f"anchor:control_history:{verdict.status}")
    for count in range(verdict.latest.record_count + 1, len(request.inputs.control) + 1):
        anchor_pending(control_query(request.inputs, policy_id, count), authority)
    return verify(full, authority)


def settle(
    request: "ExecutionRequest", family: Family, scope: str | None = None
) -> AnchorVerdict | None:
    """Anchor the record an append just wrote; ``None`` when that cannot be done now.

    Without ``scope`` the family is a workspace family, anchored in the
    workspace's verified identity scope; an unverified identity anchors nothing.
    """
    authority = authority_of(request)
    if authority is None:
        return None
    if scope is None:
        try:
            scope = scope_of(request)
        except ForarbeiderRefusedError:
            return None
    query = workspace_query(request.inputs, request.workspace, family, scope)
    try:
        return anchor_pending(query, authority)
    except ForarbeiderRefusedError:
        return None
