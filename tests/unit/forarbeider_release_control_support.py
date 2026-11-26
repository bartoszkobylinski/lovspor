"""Throwaway keys and records for the AR1b signed limits, ledger and stop/resume tests (#738).

Every key is generated in pytest's temporary directory and dies with it. None
stands for the owner's or the bot's key, and none is ever written into the
engine's shipped trust root (``src/lovspor/forarbeider/trust``).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from lovspor.forarbeider.policy_authorization import SignedRecord
from lovspor.forarbeider.policy_trust import (
    CONTROL_NAMESPACE,
    LIMITS_NAMESPACE,
    POLICY_NAMESPACE,
    TrustRoot,
)
from lovspor.forarbeider.publication_policy import PublicationPolicy, policy_text
from lovspor.forarbeider.release_contract_models import BudgetUsage
from lovspor.forarbeider.release_control_models import ControlScope, ReleaseControl
from lovspor.forarbeider.release_ledger_models import LedgerOperation
from lovspor.forarbeider.release_limits_models import ReleaseConstraints, ReleaseLimits
from tests.unit.forarbeider_authorization_support import (
    BOT_NAMESPACES,
    Key,
    new_key,
    raw,
    scope,
    sign,
    signer_line,
    trust_root,
)
from tests.unit.forarbeider_authorization_support import policy as synthetic_policy

OWNER = "test-owner"
BOT = "test-bot"
#: The owner key holds every owner namespace: policy, limits and control.
OWNER_ALL = f"{POLICY_NAMESPACE},{LIMITS_NAMESPACE},{CONTROL_NAMESPACE}"
SIGNED_AT = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
CHECKED_AT = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
DAY = date(2026, 11, 14)
VALID_FROM = datetime(2026, 11, 14, 0, 0, tzinfo=UTC)
VALID_UNTIL = datetime(2027, 11, 14, 0, 0, tzinfo=UTC)
RELEASE_A = "a" * 64
RELEASE_B = "b" * 64


@dataclass(frozen=True)
class Keys:
    owner: Key
    bot: Key
    other: Key


def make_keys(directory: Path) -> Keys:
    return Keys(
        owner=new_key(directory, "owner"),
        bot=new_key(directory, "bot"),
        other=new_key(directory, "other"),
    )


def root_of(
    directory: Path, keys: Keys, owner_namespaces: str = OWNER_ALL, revoked: tuple[Key, ...] = ()
) -> TrustRoot:
    lines = [
        signer_line(OWNER, keys.owner, owner_namespaces),
        signer_line(BOT, keys.bot, BOT_NAMESPACES),
    ]
    return trust_root(directory, lines, revoked)


def policy() -> PublicationPolicy:
    return synthetic_policy()


def policy_bytes(chosen: PublicationPolicy | None = None) -> bytes:
    return policy_text(chosen or policy()).encode()


def constraints(**changes: Any) -> ReleaseConstraints:
    fields: dict[str, Any] = {
        "max_documents_per_batch": 15,
        "max_files_per_batch": 100,
        "max_bytes_per_batch": 25_000_000,
        "max_documents_per_day": 20,
        "max_files_per_day": 200,
        "max_bytes_per_day": 50_000_000,
        "max_commits_per_day": 3,
    }
    return ReleaseConstraints(**(fields | changes))


def limits(chosen: PublicationPolicy | None = None, **changes: Any) -> ReleaseLimits:
    fields: dict[str, Any] = {
        "limits_id": "synthetic-limits-1",
        "scope": scope(chosen or policy()),
        "constraints": constraints(),
        "signer_principal": OWNER,
        "signed_at": SIGNED_AT,
        "valid_from": VALID_FROM,
        "valid_until": VALID_UNTIL,
    }
    return ReleaseLimits.model_validate(fields | changes)


def signed_by(record: Any, key: Key, namespace: str) -> SignedRecord:
    data = record if isinstance(record, bytes) else raw(record)
    return SignedRecord(data, sign(key, data, namespace))


def control(
    action: str, sequence: int, previous: SignedRecord | None, **changes: Any
) -> ReleaseControl:
    fields: dict[str, Any] = {
        "control_id": f"synthetic-control-{sequence}",
        "action": action,
        "scope": ControlScope(policy_id=None),
        "reason": f"synthetic {action}",
        "sequence": sequence,
        "previous_control_sha256": None if previous is None else digest(previous),
        "resumes_control_sha256": None,
        "signer_principal": OWNER,
        "signed_at": SIGNED_AT + timedelta(minutes=sequence),
        "valid_until": (SIGNED_AT + timedelta(days=1) if action == "resume" else None),
    }
    return ReleaseControl.model_validate(fields | changes)


def digest(record: SignedRecord) -> str:
    return sha256(record.record).hexdigest()


def usage(documents: int = 2, files: int = 4, size: int = 20_000, commits: int = 1) -> BudgetUsage:
    return BudgetUsage(documents=documents, files=files, bytes=size, commits=commits)


def operation(kind: str, release: str | None = RELEASE_A, **changes: Any) -> LedgerOperation:
    fields: dict[str, Any] = {
        "operation": kind,
        "ledger_id": "synthetic-ledger",
        "day": DAY,
        "release_id": release,
        "batch_id": None if release is None else "batch-001",
        "amounts": usage(),
        "recorded_at": CHECKED_AT,
    }
    return LedgerOperation.model_validate(fields | changes)
