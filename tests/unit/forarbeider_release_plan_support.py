"""Synthetic inventories, selections and artifacts for the AR2 planner tests (#738).

Every key is generated in pytest's temporary directory and dies with it; no
key here stands for the owner's or the bot's, and none is written into the
engine's shipped trust root. Documents are invented (``regjeringen-90000NN``)
and their artifacts are a few synthetic bytes each: no real document text.
"""

from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

from lovspor.forarbeider.identity import DocumentType
from lovspor.forarbeider.policy_trust import (
    CONTROL_NAMESPACE,
    INVENTORY_NAMESPACE,
    LIMITS_NAMESPACE,
    POLICY_NAMESPACE,
    TrustRoot,
)
from lovspor.forarbeider.publication_policy import PublicationPolicy, policy_digest
from lovspor.forarbeider.release_contract_models import DocumentInventory, InventoryScope
from lovspor.forarbeider.release_inventory_models import InventoryEntry, SignedInventory
from lovspor.forarbeider.release_limits_models import ReleaseConstraints
from lovspor.forarbeider.release_plan import PlanContext, Row
from lovspor.forarbeider.release_plan_models import RELEASE_WIDE_BLOCKERS
from lovspor.forarbeider.version_release_models import (
    Blocker,
    DocumentBinding,
    DocumentObservation,
    DocumentSelection,
    PolicyReference,
    ReleaseContent,
    ReleaseManifest,
    counts_of,
    digest_of,
    status_of,
)
from tests.unit.forarbeider_authorization_support import BOT_NAMESPACES, scope, signer_line
from tests.unit.forarbeider_authorization_support import trust_root as make_root
from tests.unit.forarbeider_release_control_support import (
    BOT,
    OWNER,
    SIGNED_AT,
    VALID_FROM,
    VALID_UNTIL,
    Keys,
    constraints,
    policy,
)

#: The owner key holds every owner namespace, the inventory's included.
OWNER_EVERY = ",".join((POLICY_NAMESPACE, LIMITS_NAMESPACE, CONTROL_NAMESPACE, INVENTORY_NAMESPACE))
DISCOVERY = ("d" * 64,)


def document(number: int) -> str:
    return f"regjeringen-{9000000 + number}"


def entry(number: int, document_type: DocumentType = "prop-l") -> InventoryEntry:
    identity = 9000000 + number
    return InventoryEntry(
        document_id=document(number),
        document_type=document_type,
        source_url=f"https://www.regjeringen.no/no/dokumenter/prop.-{number}-l-20252026/id{identity}/",
    )


def every_root(directory: Path, keys: Keys, owner_namespaces: str = OWNER_EVERY) -> TrustRoot:
    lines = [
        signer_line(OWNER, keys.owner, owner_namespaces),
        signer_line(BOT, keys.bot, BOT_NAMESPACES),
    ]
    return make_root(directory, lines)


def inventory_record(
    numbers: Sequence[int] = (1, 2), chosen: PublicationPolicy | None = None, **changes: Any
) -> SignedInventory:
    fields: dict[str, Any] = {
        "inventory_id": "synthetic-inventory-1",
        "inventory_version": 1,
        "scope": scope(chosen or policy()),
        "completeness": "complete",
        "discovery_sha256s": DISCOVERY,
        "entries": tuple(entry(n) for n in sorted(numbers)),
        "signer_principal": OWNER,
        "signed_at": SIGNED_AT,
        "valid_from": VALID_FROM,
        "valid_until": VALID_UNTIL,
    }
    return SignedInventory.model_validate(fields | changes)


# --- the pure planner: selections, bindings and artifacts ------------------------------------


def policy_reference(chosen: PublicationPolicy | None = None) -> PolicyReference:
    chosen = chosen or policy()
    return PolicyReference(
        policy_id=chosen.policy_id,
        policy_version=chosen.policy_version,
        policy_sha256=policy_digest(chosen),
    )


def blob(label: str, size: int) -> bytes:
    """``size`` synthetic bytes that differ per label."""
    seed = label.encode()
    return (seed * (size // len(seed) + 1))[:size]


def key_of(data: bytes) -> str:
    return sha256(data).hexdigest()


class Store:
    """A synthetic private artifact store: bytes keyed by their SHA-256."""

    def __init__(self) -> None:
        self.artifacts: dict[str, bytes] = {}

    def put(self, data: bytes) -> str:
        key = key_of(data)
        self.artifacts[key] = data
        return key


def release_wide() -> tuple[Blocker, ...]:
    found = sorted(RELEASE_WIDE_BLOCKERS)
    return tuple(Blocker.model_validate({"category": c, "code": code}) for c, code in found)


def row(
    store: Store,
    number: int,
    sizes: Sequence[int] = (100, 200),
    blockers: Sequence[Blocker] | None = None,
    **changes: Any,
) -> Row:
    """One document's FC8b selection and binding over synthetic artifacts.

    The first size is the FC6 manifest, the rest are raw source pages.
    """
    keys = [store.put(blob(f"{number}-{index}", size)) for index, size in enumerate(sizes)]
    found = tuple(sorted(set(release_wide() if blockers is None else blockers), key=_blocker_key))
    fields: dict[str, Any] = {
        "document_id": document(number),
        "status": status_of(found),
        "version_id": key_of(f"version-{number}".encode()),
        "source_sha256s": tuple(sorted(keys[1:])),
        "policy": policy_reference(),
        "processing": None,
        "source_state": None,
        "publication_state": None,
        "blockers": found,
    }
    selection = DocumentSelection.model_validate(fields | changes)
    binding = DocumentBinding(
        document_id=document(number),
        lineage_status="verified",
        lineage_entries=1,
        claimed_head_sha256=None,
        lineage_head_sha256=None,
        manifest_sha256=keys[0],
        assessment_sha256=None,
        policy_decision_sha256=None,
        authorization_sha256=None,
    )
    return Row(selection, binding)


def _blocker_key(item: Blocker) -> tuple[str, str]:
    return (item.category, item.code)


def manifest_of(rows: Sequence[Row]) -> ReleaseManifest:
    """The FC8b manifest of exactly these rows, digests recomputed."""
    ordered = sorted(rows, key=lambda item: item.selection.document_id)
    content = ReleaseContent(documents=tuple(item.selection for item in ordered))
    bindings = tuple(item.binding for item in ordered)
    return ReleaseManifest(
        release_id=digest_of(content),
        content=content,
        counts=counts_of(content.documents),
        bindings_sha256=digest_of(*bindings),
        bindings=bindings,
        observations=tuple(
            DocumentObservation(
                document_id=item.selection.document_id,
                observed_at=None,
                observations=1,
                content_versions=1,
            )
            for item in ordered
        ),
    )


def context(
    numbers: Sequence[int],
    rows: Sequence[Row] = (),
    chosen: ReleaseConstraints | None = None,
    **changes: Any,
) -> PlanContext:
    manifest = manifest_of(rows) if rows else None
    fields: dict[str, Any] = {
        "inventory": DocumentInventory(
            inventory_id="synthetic-inventory-1",
            scope=InventoryScope(document_types=("prop-l",)),
            document_ids=tuple(sorted(document(n) for n in numbers)),
        ),
        "inventory_version": 1,
        "inventory_sha256": "e" * 64,
        "policy": policy_reference(),
        "limits_sha256": "a" * 64,
        "constraints": chosen or constraints(),
        "selection_release_id": "0" * 64 if manifest is None else manifest.release_id,
        "selection_bindings_sha256": "0" * 64 if manifest is None else manifest.bindings_sha256,
    }
    return PlanContext(**(fields | changes))


def total(artifacts: Mapping[str, bytes], keys: Sequence[str]) -> int:
    return sum(len(artifacts[key]) for key in set(keys))
