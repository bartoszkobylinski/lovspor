"""AR5a: control-plane state verified against its witnessed anchors (#738; §9.9 AR5).

:func:`verify` decides whether a family's local chain
(:mod:`~lovspor.forarbeider.control_plane_chains`) can be trusted, by the
anchors an independent witness holds
(:mod:`~lovspor.forarbeider.control_plane_witness`):

1. the witness must be independent: one inside the workspace, or holding it,
   is ``store_not_independent``; one that cannot be read is
   ``store_unavailable``;
2. every anchor must be canonical, for this family, repository and scope, in
   an unbroken chain whose record count never falls, and signed in the anchor
   namespace by a key whose only role is ``anchor_witness`` and which the
   trust root lists, unrevoked, at the check time; else ``anchor_invalid``;
3. a pinned anchor (the newest one the caller has seen) must still be in that
   chain; else ``anchor_rolled_back``;
4. the local chain must verify (``local_invalid``), and then:
   - no anchor at all: ``unanchored``, even for an empty family. A missing
     anchor is never an empty or clean history; :func:`initialize` anchors an
     empty family explicitly, once;
   - fewer records than anchored: ``local_rolled_back`` (truncation, deletion,
     a restored older copy, a deleted stop latch);
   - any anchored head that the local records do not reproduce: ``diverged``
     (substitution, a rewritten chain, a forked writer);
   - exactly the anchored records: ``verified``;
   - one record more: ``pending_anchor`` (a crash after the append, before
     the anchor); more than one: ``unanchored_tail``.

Only ``verified`` is trusted (:func:`verified_state`). :func:`anchor_pending`
anchors exactly one pending record and nothing else, and
:func:`durable_append` writes a workspace record and anchors it under the
workspace lock, so every interruption leaves a state that verification names
exactly: unchanged, ``pending_anchor`` (completed by retrying the same record)
or ``verified`` (the retry is ``already_applied``). An anchored head is never
replaced and committed evidence is never discarded.

**Lock ordering.** :func:`durable_append` holds the workspace lock and, inside
it, the witness's lock; the witness's lock is a leaf. AR4's execution and
recovery hold the workspace lock and never the witness's, so no cycle exists.

Nothing here changes AR1 to AR4: the anchors are evidence for AR5's STOP/RESUME
and budget enforcement, which are not in this module.
"""

from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import ValidationError

from lovspor.forarbeider import publication_attempts, publication_receipts
from lovspor.forarbeider.control_plane_anchor_models import (
    FAMILIES,
    ControlPlaneAnchor,
    Family,
    Status,
    WitnessedAnchor,
    anchor_bytes,
    anchor_digest,
)
from lovspor.forarbeider.control_plane_chains import LocalChain, workspace_chain
from lovspor.forarbeider.control_plane_witness import UNAVAILABLE, AnchorKey, AnchorWitness
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_trust import (
    ANCHOR_NAMESPACE,
    SignedBytes,
    TrustRoot,
    check_signature,
)
from lovspor.forarbeider.publication_execution_models import AttemptRecord, StopLatch
from lovspor.forarbeider.publication_recovery_models import RecoveryReceipt
from lovspor.forarbeider.version_models import canonical_text

#: Families whose chain is one document, compared by its newest anchored head only.
_DOCUMENTS: frozenset[Family] = frozenset({"quarantine_ledger"})


class AnchorPin(NamedTuple):
    """The newest anchor a caller has seen, kept outside the workspace."""

    anchor_sequence: int
    anchor_sha256: str


class AnchorQuery(NamedTuple):
    family: Family
    repository: str
    scope: str
    local: LocalChain


class AnchorAuthority(NamedTuple):
    witness: AnchorWitness
    root: TrustRoot
    checked_at: datetime
    pin: AnchorPin | None = None
    workspace: Path | None = None


class AnchorVerdict(NamedTuple):
    status: Status
    reasons: tuple[str, ...]
    latest: ControlPlaneAnchor | None
    local_count: int


class VerifiedState(NamedTuple):
    """What AR5's STOP/RESUME and budget enforcement may rely on: a verified head."""

    family: Family
    scope: str
    head_sha256: str | None
    record_count: int
    anchor_sequence: int
    anchor_sha256: str


class DurableOutcome(NamedTuple):
    status: Literal["appended", "completed", "already_applied"]
    verdict: AnchorVerdict


Durable = AttemptRecord | RecoveryReceipt | StopLatch


def _refused(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, detail)


# --- the witnessed chain -------------------------------------------------------------------------


def _independent(authority: AnchorAuthority) -> bool:
    location, workspace = authority.witness.location, authority.workspace
    if location is None or workspace is None:
        return True
    store, held = location.resolve(), workspace.resolve()
    return store != held and held not in store.parents and store not in held.parents


def _signed(witnessed: WitnessedAnchor, authority: AnchorAuthority) -> str | None:
    signed = SignedBytes(anchor_bytes(witnessed.anchor), witnessed.signature.encode())
    check = check_signature(authority.root, signed, ANCHOR_NAMESPACE, authority.checked_at)
    if not check.verified:
        return f"anchor_signature:{check.reason}"
    if check.entry is None or check.entry.role != "anchor_witness":
        return "anchor_signature:not_the_witness"
    return None


def _anchor(raw: bytes, query: AnchorQuery, authority: AnchorAuthority) -> ControlPlaneAnchor | str:
    try:
        witnessed = WitnessedAnchor.model_validate_json(raw)
    except ValidationError:
        return "anchor_unreadable"
    anchor = witnessed.anchor
    if canonical_text(witnessed).encode() != raw:
        return "anchor_not_canonical"
    if (anchor.family, anchor.repository, anchor.scope) != query[:3]:
        return "anchor_foreign"
    return _signed(witnessed, authority) or anchor


def _chain(
    stored: tuple[bytes, ...], query: AnchorQuery, authority: AnchorAuthority
) -> list[ControlPlaneAnchor] | str:
    chain: list[ControlPlaneAnchor] = []
    for raw in stored:
        found = _anchor(raw, query, authority)
        if isinstance(found, str):
            return found
        previous = chain[-1] if chain else None
        link = anchor_digest(previous) if previous else None
        if (found.anchor_sequence, found.previous_anchor_sha256) != (len(chain) + 1, link):
            return "anchor_chain_broken"
        if previous is not None and found.record_count < previous.record_count:
            return "anchor_count_decreased"
        chain.append(found)
    return chain


def _pinned(chain: list[ControlPlaneAnchor], pin: AnchorPin | None) -> bool:
    if pin is None:
        return True
    index = pin.anchor_sequence - 1
    return 0 <= index < len(chain) and anchor_digest(chain[index]) == pin.anchor_sha256


# --- the comparison ------------------------------------------------------------------------------


def _verdict(
    status: Status, query: AnchorQuery, latest: ControlPlaneAnchor | None
) -> AnchorVerdict:
    reasons = query.local.reasons if status == "local_invalid" else ()
    return AnchorVerdict(status, reasons, latest, len(query.local.digests))


def _reproduced(query: AnchorQuery, chain: list[ControlPlaneAnchor]) -> bool:
    digests = query.local.digests
    held = chain[-1:] if query.family in _DOCUMENTS else chain
    return all(a.record_count == 0 or digests[a.record_count - 1] == a.head_sha256 for a in held)


def _compare(query: AnchorQuery, chain: list[ControlPlaneAnchor]) -> AnchorVerdict:
    latest = chain[-1]
    count = len(query.local.digests)
    if count < latest.record_count:
        return _verdict("local_rolled_back", query, latest)
    if not _reproduced(query, chain):
        return _verdict("diverged", query, latest)
    if count == latest.record_count:
        return _verdict("verified", query, latest)
    if count == latest.record_count + 1:
        return _verdict("pending_anchor", query, latest)
    return _verdict("unanchored_tail", query, latest)


def _stored(query: AnchorQuery, authority: AnchorAuthority) -> tuple[bytes, ...] | None:
    try:
        return authority.witness.history(AnchorKey(*query[:3]))
    except ForarbeiderRefusedError as error:
        if error.detail != UNAVAILABLE:
            raise
        return None


def _witnessed(
    query: AnchorQuery, authority: AnchorAuthority
) -> list[ControlPlaneAnchor] | AnchorVerdict:
    """The verified witness chain, or the verdict that refuses it."""
    count = len(query.local.digests)
    if not _independent(authority):
        return _verdict("store_not_independent", query, None)
    stored = _stored(query, authority)
    if stored is None:
        return AnchorVerdict("store_unavailable", (UNAVAILABLE,), None, count)
    chain = _chain(stored, query, authority)
    if isinstance(chain, str):
        return AnchorVerdict("anchor_invalid", (chain,), None, count)
    if not _pinned(chain, authority.pin):
        return _verdict("anchor_rolled_back", query, chain[-1] if chain else None)
    return chain


def verify(query: AnchorQuery, authority: AnchorAuthority) -> AnchorVerdict:
    """The family's status against the witness; only ``verified`` may be trusted."""
    chain = _witnessed(query, authority)
    if isinstance(chain, AnchorVerdict):
        return chain
    if query.local.reasons:
        return _verdict("local_invalid", query, chain[-1] if chain else None)
    if not chain:
        return AnchorVerdict("unanchored", ("anchor_missing",), None, len(query.local.digests))
    return _compare(query, chain)


# --- anchoring -----------------------------------------------------------------------------------


def _next(query: AnchorQuery, latest: ControlPlaneAnchor | None) -> ControlPlaneAnchor:
    schema, kind = FAMILIES[query.family]
    count = len(query.local.digests)
    return ControlPlaneAnchor(
        family=query.family,
        family_schema=schema,
        repository=query.repository,
        scope_kind=kind,
        scope=query.scope,
        head_sha256=query.local.digests[-1] if count else None,
        record_count=count,
        last_sequence=count,
        anchor_sequence=latest.anchor_sequence + 1 if latest else 1,
        previous_anchor_sha256=anchor_digest(latest) if latest else None,
    )


def initialize(query: AnchorQuery, authority: AnchorAuthority) -> AnchorVerdict:
    """Anchor an empty family once; existing records without an anchor are refused.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible: already_anchored`` or
            ``anchor:<status>``; the witness's ``anchor_head_moved``.
    """
    verdict = verify(query, authority)
    if verdict.status == "verified":
        raise _refused("already_anchored")
    if verdict.status != "unanchored" or query.local.digests:
        raise _refused(f"anchor:{verdict.status}")
    authority.witness.submit(_next(query, None))
    return verify(query, authority)


def anchor_pending(query: AnchorQuery, authority: AnchorAuthority) -> AnchorVerdict:
    """Anchor the one record a crash left unanchored; a verified family is left alone.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible: anchor:<status>`` for
            any other status; the witness's ``anchor_head_moved``.
    """
    verdict = verify(query, authority)
    if verdict.status == "verified":
        return verdict
    if verdict.status != "pending_anchor":
        raise _refused(f"anchor:{verdict.status}")
    authority.witness.submit(_next(query, verdict.latest))
    return verify(query, authority)


def verified_state(query: AnchorQuery, authority: AnchorAuthority) -> VerifiedState:
    """The verified head, for AR5; anything but ``verified`` is refused.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible: anchor:<status>``.
    """
    verdict = verify(query, authority)
    latest = verdict.latest
    if verdict.status != "verified" or latest is None:
        raise _refused(f"anchor:{verdict.status}")
    return VerifiedState(
        query.family,
        query.scope,
        latest.head_sha256,
        latest.record_count,
        latest.anchor_sequence,
        anchor_digest(latest),
    )


# --- durable workspace appends -------------------------------------------------------------------


def _family_of(record: Durable) -> tuple[Family, str]:
    if isinstance(record, AttemptRecord):
        return "publication_attempts", record.transaction_id
    if isinstance(record, RecoveryReceipt):
        return "recovery_receipts", "workspace"
    return "stop_latch", "workspace"


def _write(workspace: Path, record: Durable) -> None:
    if isinstance(record, AttemptRecord):
        publication_attempts.append(workspace, record)
    elif isinstance(record, RecoveryReceipt):
        publication_receipts.append(workspace, record)
    else:
        publication_attempts.latch(workspace, record.reasons, record.set_at, record.transaction_id)


def durable_append(
    workspace: Path, record: Durable, repository: str, authority: AnchorAuthority
) -> DurableOutcome:
    """Append ``record`` to its workspace family and anchor it, under the workspace lock.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked: workspace``; ``release_not_eligible:
            anchor:<status>`` unless the family verifies (or holds exactly this
            record pending); ``record_not_appended``; the witness's refusals.
    """
    family, scope = _family_of(record)
    raw = canonical_text(record).encode()
    held = authority._replace(workspace=workspace)
    with publication_attempts.workspace_lock(workspace):
        query = AnchorQuery(family, repository, scope, workspace_chain(family, workspace, scope))
        verdict = verify(query, held)
        if verdict.status == "verified" and query.local.last == raw:
            return DurableOutcome("already_applied", verdict)
        if verdict.status == "pending_anchor" and query.local.last == raw:
            return DurableOutcome("completed", anchor_pending(query, held))
        if verdict.status != "verified":
            raise _refused(f"anchor:{verdict.status}")
        _write(workspace, record)
        after = AnchorQuery(family, repository, scope, workspace_chain(family, workspace, scope))
        if after.local.last != raw or len(after.local.digests) != verdict.local_count + 1:
            raise _refused("record_not_appended")
        return DurableOutcome("appended", anchor_pending(after, held))
