"""Synthetic fixtures for the AR3 quarantine tests (#738).

Invented documents, a few synthetic bytes each, FC7 decisions built by hand
and throwaway keys in pytest's temporary directory. No text, no network.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from lovspor.forarbeider.policy_authorization import SignedRecord
from lovspor.forarbeider.policy_evaluation import (
    AttachmentDecision,
    DocumentDecision,
    EntryDecision,
    PolicyDecision,
    decision_text,
)
from lovspor.forarbeider.policy_trust import QUARANTINE_NAMESPACE, TrustRoot
from lovspor.forarbeider.publication_policy import policy_digest
from lovspor.forarbeider.release_plan import Row, build_plan
from lovspor.forarbeider.release_plan_models import ReleasePlan
from lovspor.forarbeider.release_quarantine_models import (
    QuarantineException,
    QuarantineRecord,
    QuarantineSet,
)
from lovspor.forarbeider.version_models import SourceState
from lovspor.forarbeider.version_release_models import Blocker
from tests.unit.forarbeider_authorization_support import BOT_NAMESPACES, scope, signer_line
from tests.unit.forarbeider_authorization_support import trust_root as make_root
from tests.unit.forarbeider_release_control_support import (
    BOT,
    CHECKED_AT,
    OWNER,
    SIGNED_AT,
    Keys,
    constraints,
    policy,
    signed_by,
)
from tests.unit.forarbeider_release_plan_support import (
    OWNER_EVERY,
    Store,
    context,
    release_wide,
    row,
)

#: The owner key holds every owner namespace, the quarantine's included.
OWNER_QUARANTINE = f"{OWNER_EVERY},{QUARANTINE_NAMESPACE}"
LATER = CHECKED_AT + timedelta(days=1)


def quarantine_root(
    directory: Path,
    keys: Keys,
    owner_namespaces: str = OWNER_QUARANTINE,
    revoked: tuple[Any, ...] = (),
) -> TrustRoot:
    lines = [
        signer_line(OWNER, keys.owner, owner_namespaces),
        signer_line(BOT, keys.bot, BOT_NAMESPACES),
    ]
    return make_root(directory, lines, revoked)


def blocked_row(store: Store, number: int, *extra: tuple[str, str], **changes: Any) -> Row:
    """A row whose blockers are the release-wide ones plus ``extra`` ``(category, code)``."""
    found = [
        *release_wide(),
        *(Blocker.model_validate({"category": c, "code": k}) for c, k in extra),
    ]
    return row(store, number, blockers=found, **changes)


def entry(entry_id: str, decision: str, rule: str, part_id: str | None = None) -> EntryDecision:
    return EntryDecision(
        entry_id=entry_id,
        part_id=part_id,
        source_sha256=None,
        decision=decision,  # type: ignore[arg-type]
        rule=rule,
        evidence_entries=(),
        reasons=("synthetic",),
    )


def attachment(label: str, decision: str = "deny") -> AttachmentDecision:
    return AttachmentDecision(
        url=f"https://www.regjeringen.no/contentassets/{label}.pdf",
        kind="embedded",
        raw_sha256=None,
        decision=decision,  # type: ignore[arg-type]
        rule="attachment:embedded",
        reasons=("synthetic",),
    )


def decided_row(
    store: Store,
    number: int,
    entries: Sequence[EntryDecision] = (),
    attachments: Sequence[AttachmentDecision] = (),
    counts: dict[str, int] | None = None,
) -> Row:
    """A row whose FC7 blockers name these decisions, with the decision in the store."""
    counted = counts or _counts(entries, attachments)
    codes = [("policy", f"fc7:{name}:{n}") for name, n in sorted(counted.items())]
    codes.append(("policy", "fc7_rules_not_satisfied"))
    base = blocked_row(store, number, *codes)
    chosen = policy()
    decision = PolicyDecision(
        document_id=base.selection.document_id,
        version_id=base.selection.version_id or "0" * 64,
        assessment_sha256="a" * 64,
        source_sha256s=base.selection.source_sha256s,
        policy_id=chosen.policy_id,
        policy_version=chosen.policy_version,
        policy_sha256=policy_digest(chosen),
        source_state=SourceState.SOURCE_COMPLETE,
        attachment_discovery="verified",
        entries=tuple(entries),
        attachments=tuple(attachments),
        document=DocumentDecision(
            rules_satisfied=False, blockers=tuple(f"{k}:{n}" for k, n in sorted(counted.items()))
        ),
    )
    key = store.put(decision_text(decision).encode())
    return Row(base.selection, base.binding.model_copy(update={"policy_decision_sha256": key}))


def _counts(
    entries: Sequence[EntryDecision], attachments: Sequence[AttachmentDecision]
) -> dict[str, int]:
    found: dict[str, int] = {}
    for item in entries:
        if item.decision == "allow":
            continue
        found[f"entries_{item.decision}"] = found.get(f"entries_{item.decision}", 0) + 1
    for other in attachments:
        if other.decision == "allow":
            continue
        found[f"attachments_{other.decision}"] = found.get(f"attachments_{other.decision}", 0) + 1
    return found


def planned(
    store: Store,
    rows: Sequence[Row],
    numbers: Sequence[int],
    quarantine: QuarantineSet | None = None,
    **chosen: Any,
) -> ReleasePlan:
    limits = constraints(**chosen) if chosen else None
    return build_plan(context(numbers, rows, limits), rows, store.artifacts, quarantine)


def exception(record: QuarantineRecord, **changes: Any) -> QuarantineException:
    fields: dict[str, Any] = {
        "exception_id": "synthetic-clearance-1",
        "action": "clearance",
        "target": "finding",
        "scope": scope(policy()),
        "document_id": record.document_id,
        "version_id": record.version_id,
        "quarantine_id": record.quarantine_id,
        "evidence_sha256s": record.evidence_sha256s,
        "reason_code": "owner_reviewed",
        "signer_principal": OWNER,
        "signed_at": SIGNED_AT,
        "valid_from": SIGNED_AT - timedelta(hours=1),
        "valid_until": CHECKED_AT + timedelta(days=30),
    }
    return QuarantineException.model_validate(fields | changes)


def owner_signed(keys: Keys, record: QuarantineException) -> SignedRecord:
    return signed_by(record, keys.owner, QUARANTINE_NAMESPACE)


def at(hours: int) -> datetime:
    return CHECKED_AT + timedelta(hours=hours)
