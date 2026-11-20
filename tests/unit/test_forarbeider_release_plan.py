"""AR2: a verified document inventory and a deterministic batch planner (#738; §9.2, §9.9).

Synthetic only: invented documents, a few bytes of synthetic artifacts each,
and throwaway keys in pytest's temporary directory. Nothing here fetches,
signs with a real key, writes a plan or a ledger, or publishes.
"""

import ast
import json
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider import release_sources
from lovspor.forarbeider.models import JSON_LAYOUT
from lovspor.forarbeider.policy_authorization import SignedRecord
from lovspor.forarbeider.policy_trust import (
    INVENTORY_NAMESPACE,
    LIMITS_NAMESPACE,
    OWNER_NAMESPACES,
    POLICY_NAMESPACE,
    TrustRoot,
)
from lovspor.forarbeider.release_contract_models import (
    REFUSING_CODES,
    BatchSize,
    InventoryEvidence,
    LimitsEvidence,
    PlanEvidence,
    ReproductionEvidence,
    trusted,
    verified,
)
from lovspor.forarbeider.release_gates import evaluate_release
from lovspor.forarbeider.release_inventory import (
    InventoryMaterial,
    InventoryRequest,
    verify_inventory,
)
from lovspor.forarbeider.release_limits import LimitsMaterial, LimitsRequest, verify_limits
from lovspor.forarbeider.release_plan import (
    PlanRequest,
    Row,
    build_plan,
    parse_plan,
    plan_release,
    plan_text,
    verify_plan,
)
from lovspor.forarbeider.release_plan_models import (
    PLANNER_VERSION,
    RELEASE_WIDE_BLOCKERS,
    PlannedBatch,
    ReleasePlan,
)
from lovspor.forarbeider.release_provenance import authentic, issue_provenance
from lovspor.forarbeider.release_sources import (
    ReleaseRequest,
    ReleaseSources,
    collect_evidence,
)
from lovspor.forarbeider.version_lineage import Observation
from lovspor.forarbeider.version_release import build_release, release_text
from lovspor.forarbeider.version_release_models import Blocker, ReleaseVerification, digest_of
from tests.unit.forarbeider_authorization_support import (
    full_request,
    raw,
    revocation,
    sign,
)
from tests.unit.forarbeider_authorization_support import policy as synthetic_policy
from tests.unit.forarbeider_lineage_support import Chain, changed, original
from tests.unit.forarbeider_release_control_support import (
    CHECKED_AT,
    Keys,
    constraints,
    limits,
    make_keys,
    policy,
    policy_bytes,
    signed_by,
)
from tests.unit.forarbeider_release_plan_support import (
    OWNER_EVERY,
    Store,
    context,
    document,
    every_root,
    inventory_record,
    manifest_of,
    row,
    total,
)
from tests.unit.test_forarbeider_release_gates import baseline, codes, intent
from tests.unit.test_forarbeider_version_release import FIRST, offer, renamed

NEW_MODULES = (
    "release_inventory",
    "release_inventory_models",
    "release_plan",
    "release_plan_models",
)


@pytest.fixture(scope="module")
def keys(tmp_path_factory: pytest.TempPathFactory) -> Keys:
    return make_keys(tmp_path_factory.mktemp("keys"))


@pytest.fixture
def root(tmp_path: Path, keys: Keys) -> TrustRoot:
    return every_root(tmp_path / "root", keys)


def owner_inventory(keys: Keys, *numbers: int, **changes: Any) -> SignedRecord:
    return signed_by(
        inventory_record(numbers or (1, 2), **changes), keys.owner, INVENTORY_NAMESPACE
    )


def inventory_of(
    root: TrustRoot, *records: SignedRecord, revocations: tuple[SignedRecord, ...] = ()
) -> InventoryEvidence:
    material = InventoryMaterial(records=records, revocations=revocations)
    return verify_inventory(InventoryRequest(policy_bytes(), material, CHECKED_AT), root)


def limits_of(root: TrustRoot, keys: Keys, **changes: Any) -> LimitsEvidence:
    record = signed_by(limits(**changes), keys.owner, LIMITS_NAMESPACE)
    request = LimitsRequest(policy_bytes(), LimitsMaterial(records=(record,)), CHECKED_AT)
    return verify_limits(request, root)


def reasons(evidence: InventoryEvidence | PlanEvidence) -> set[str]:
    return set(evidence.reasons)


# --- the owner-signed inventory ----------------------------------------------------------


def test_the_inventory_namespace_is_owner_only() -> None:
    assert INVENTORY_NAMESPACE == "lovspor-forarbeider-inventory"
    assert INVENTORY_NAMESPACE in OWNER_NAMESPACES


def test_an_owner_signed_inventory_verifies_and_is_sealed(root: TrustRoot, keys: Keys) -> None:
    found = inventory_of(root, owner_inventory(keys, 2, 1, 3))
    assert found.status == "verified", found.reasons
    assert found.source == "owner_signed_inventory"
    assert found.inventory is not None
    assert found.inventory.document_ids == (document(1), document(2), document(3))
    assert found.inventory_version == 1
    assert found.discovery_sha256s == ("d" * 64,)
    assert found.trust_root_sha256 == root.sha256
    assert found.policy is not None and found.policy.policy_id == policy().policy_id
    assert found.signer_role == "owner_policy"
    assert authentic(found, "document_inventory")
    assert verified("document_inventory", found)


def test_inventory_evidence_binds_the_exact_signed_bytes(root: TrustRoot, keys: Keys) -> None:
    offered = owner_inventory(keys)
    found = inventory_of(root, offered)
    assert found.inventory_sha256 == digest_bytes(offered.record)


def digest_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def test_no_inventory_is_missing_never_an_empty_inventory(root: TrustRoot) -> None:
    found = inventory_of(root)
    assert found.status == "missing"
    assert found.inventory is None
    assert reasons(found) == {"signed_inventory_missing"}


def test_an_unsigned_inventory_is_missing(root: TrustRoot) -> None:
    found = inventory_of(root, SignedRecord(raw(inventory_record()), None))
    assert (found.status, reasons(found)) == ("missing", {"signature_missing"})


def test_a_forged_inventory_is_invalid(root: TrustRoot, keys: Keys) -> None:
    genuine = owner_inventory(keys, 1, 2)
    swollen = raw(inventory_record((1, 2, 3)))
    found = inventory_of(root, SignedRecord(swollen, genuine.signature))
    assert found.status == "invalid"
    assert found.inventory is None


def test_a_bot_signed_inventory_is_invalid(tmp_path: Path, keys: Keys) -> None:
    root = every_root(tmp_path / "root", keys)
    data = raw(inventory_record(signer_principal="test-bot"))
    found = inventory_of(root, SignedRecord(data, sign(keys.bot, data, INVENTORY_NAMESPACE)))
    assert found.status == "invalid"


def test_an_inventory_signed_in_another_namespace_is_invalid(root: TrustRoot, keys: Keys) -> None:
    data = raw(inventory_record())
    for namespace in (LIMITS_NAMESPACE, POLICY_NAMESPACE):
        found = inventory_of(root, SignedRecord(data, sign(keys.owner, data, namespace)))
        assert found.status == "invalid", namespace


def test_an_owner_key_without_the_inventory_namespace_cannot_sign_one(
    tmp_path: Path, keys: Keys
) -> None:
    limited = every_root(tmp_path / "root", keys, POLICY_NAMESPACE)
    assert inventory_of(limited, owner_inventory(keys)).status == "invalid"


def test_an_inventory_for_another_policy_or_scope_is_invalid(root: TrustRoot, keys: Keys) -> None:
    other = synthetic_policy(policy_version=2)
    found = inventory_of(root, owner_inventory(keys, chosen=other))
    assert found.status == "invalid"
    assert reasons(found) & {"policy_version_mismatch", "policy_digest_mismatch"}


def test_an_expired_or_premature_inventory_is_invalid(root: TrustRoot, keys: Keys) -> None:
    expired = owner_inventory(
        keys, valid_until=CHECKED_AT, signed_at=CHECKED_AT - timedelta(hours=3)
    )
    assert "inventory_expired" in reasons(inventory_of(root, expired))
    premature = owner_inventory(keys, valid_from=CHECKED_AT + timedelta(hours=1))
    assert "inventory_not_yet_valid" in reasons(inventory_of(root, premature))


def test_two_valid_inventories_conflict_and_none_is_picked(root: TrustRoot, keys: Keys) -> None:
    first = owner_inventory(keys, 1, 2)
    second = owner_inventory(keys, 1, 2, 3, inventory_id="synthetic-inventory-2")
    found = inventory_of(root, first, second)
    assert (found.status, found.inventory) == ("invalid", None)
    assert "inventory_conflict" in reasons(found)


def test_a_revoked_inventory_gives_way_to_its_successor(root: TrustRoot, keys: Keys) -> None:
    first = owner_inventory(keys, 1, 2)
    second = owner_inventory(
        keys, 1, 2, 3, inventory_id="synthetic-inventory-2", inventory_version=2
    )
    withdrawn = signed_by(revocation("synthetic-inventory-1"), keys.owner, POLICY_NAMESPACE)
    found = inventory_of(root, first, second, revocations=(withdrawn,))
    assert found.status == "verified", found.reasons
    assert found.inventory_version == 2
    alone = inventory_of(root, first, revocations=(withdrawn,))
    assert "inventory_revoked" in reasons(alone)


def test_an_unverifiable_revocation_fails_closed(root: TrustRoot, keys: Keys) -> None:
    forged = SignedRecord(raw(revocation("synthetic-inventory-9")), b"not a signature")
    found = inventory_of(root, owner_inventory(keys), revocations=(forged,))
    assert found.status == "invalid"
    assert "revocation_not_verified" in reasons(found)


def test_a_revoked_inventory_does_not_hide_an_invalid_successor(
    root: TrustRoot, keys: Keys
) -> None:
    first = owner_inventory(keys, 1)
    successor = owner_inventory(keys, 2, inventory_id="synthetic-inventory-2")
    forged = SignedRecord(successor.record + b" ", successor.signature)
    withdrawn = signed_by(revocation("synthetic-inventory-1"), keys.owner, POLICY_NAMESPACE)

    found = inventory_of(root, first, forged, revocations=(withdrawn,))

    assert found.status == "invalid"
    assert found.inventory is None


def malformed(record: dict[str, Any], root: TrustRoot, keys: Keys) -> InventoryEvidence:
    data = (json.dumps(record, **JSON_LAYOUT) + "\n").encode()
    found = inventory_of(root, SignedRecord(data, sign(keys.owner, data, INVENTORY_NAMESPACE)))
    assert "record_malformed" in found.reasons
    return found


def as_dict() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(raw(inventory_record((1, 2))))
    return loaded


@pytest.mark.parametrize(
    "change",
    [
        "duplicate",
        "unsorted",
        "verified_flag",
        "partial",
        "foreign_type",
        "url_mismatch",
        "no_discovery",
        "empty",
    ],
)
def test_a_malformed_inventory_is_refused(change: str, root: TrustRoot, keys: Keys) -> None:
    record = as_dict()
    entries = record["entries"]
    if change == "duplicate":
        record["entries"] = [entries[0], entries[0]]
    elif change == "unsorted":
        record["entries"] = list(reversed(entries))
    elif change == "verified_flag":
        record["verified"] = True
    elif change == "partial":
        record["completeness"] = "partial"
    elif change == "foreign_type":
        entries[0]["document_type"] = "prop-ls"
    elif change == "url_mismatch":
        entries[0]["source_url"] = entries[1]["source_url"]
    elif change == "no_discovery":
        record["discovery_sha256s"] = []
    else:
        record["entries"] = []
    found = malformed(record, root, keys)
    assert found.status == "invalid"
    assert found.inventory is None


def test_a_catalogue_without_a_verifier_never_becomes_an_inventory(root: TrustRoot) -> None:
    material = InventoryMaterial(catalogue=(b"<html>synthetic catalogue</html>",))
    found = verify_inventory(InventoryRequest(policy_bytes(), material, CHECKED_AT), root)
    assert found.status == "missing"
    assert found.inventory is None
    assert reasons(found) == {"catalogue_verifier_not_implemented"}
    assert not trusted("document_inventory", "verified", "official_catalogue")


def test_an_inventory_without_its_policy_is_missing(root: TrustRoot, keys: Keys) -> None:
    material = InventoryMaterial(records=(owner_inventory(keys),))
    found = verify_inventory(InventoryRequest(None, material, CHECKED_AT), root)
    assert (found.status, reasons(found)) == ("missing", {"inventory_policy_unavailable"})


def test_a_caller_built_inventory_is_never_trusted() -> None:
    claimed = baseline().inventory
    assert claimed.status == "verified"
    assert not verified("document_inventory", claimed)
    assert "inventory_source_untrusted" in codes(evaluate_release(baseline()), "inventory")


# --- the pure planner --------------------------------------------------------------------


def test_every_expected_document_is_accounted_for() -> None:
    store = Store()
    blocked = row(
        store, 3, blockers=(Blocker(category="publication", code="publication_incomplete"),)
    )
    rows = [row(store, 1), row(store, 2), blocked, row(store, 5)]
    plan = build_plan(context((1, 2, 3, 4), rows), rows, store.artifacts)
    dispositions = {item.document_id: item.disposition for item in plan.documents}
    assert dispositions == {
        document(1): "planned",
        document(2): "planned",
        document(3): "blocked",
        document(4): "missing",
        document(5): "outside_inventory",
    }
    by_id = {item.document_id: item for item in plan.documents}
    assert by_id[document(3)].reasons == ("publication:publication_incomplete",)
    assert by_id[document(4)].reasons == ("inventory_document_without_evidence",)
    assert by_id[document(5)].reasons == ("document_outside_inventory",)
    coverage = plan.coverage
    assert (coverage.expected, coverage.planned, coverage.blocked) == (4, 2, 1)
    assert (coverage.missing, coverage.outside_inventory, coverage.state) == (1, 1, "incomplete")
    assert [b.document_ids for b in plan.batches] == [(document(1), document(2))]


def test_documents_are_ordered_and_listed_once() -> None:
    store = Store()
    rows = [row(store, n) for n in (4, 1, 3, 2)]
    plan = build_plan(context((1, 2, 3, 4), rows), rows, store.artifacts)
    ids = [item.document_id for item in plan.documents]
    assert ids == sorted(set(ids))


def test_sizes_are_counted_from_the_actual_artifacts() -> None:
    store = Store()
    first = row(store, 1, sizes=(1_000, 2_345, 17))
    plan = build_plan(context((1,), [first]), [first], store.artifacts)
    planned = plan.documents[0]
    assert planned.files == 3
    assert planned.bytes == 1_000 + 2_345 + 17
    assert planned.artifact_sha256s == tuple(sorted(planned.artifact_sha256s))
    assert planned.bytes == total(store.artifacts, planned.artifact_sha256s)
    (batch,) = plan.batches
    assert (batch.documents, batch.files, batch.bytes) == (1, 3, 3_362)


def test_an_artifact_shared_by_two_parts_of_a_document_counts_once() -> None:
    store = Store()
    first = row(store, 1, sizes=(100, 100))
    shared = first.binding.model_copy(update={"assessment_sha256": first.binding.manifest_sha256})
    plan = build_plan(context((1,), [first]), [first._replace(binding=shared)], store.artifacts)
    assert (plan.documents[0].files, plan.documents[0].bytes) == (2, 200)


def test_an_altered_artifact_excludes_its_document() -> None:
    store = Store()
    first, second = row(store, 1), row(store, 2)
    key = first.selection.source_sha256s[0]
    store.artifacts[key] = store.artifacts[key] + b"tampered"
    plan = build_plan(context((1, 2), [first, second]), [first, second], store.artifacts)
    by_id = {item.document_id: item for item in plan.documents}
    assert by_id[document(1)].disposition == "excluded"
    assert "artifact_altered" in by_id[document(1)].reasons
    assert by_id[document(2)].disposition == "planned"


def test_missing_and_altered_artifacts_are_both_reported() -> None:
    store = Store()
    first = row(store, 1)
    missing = first.binding.manifest_sha256
    altered = first.selection.source_sha256s[0]
    assert missing is not None and missing != altered
    del store.artifacts[missing]
    store.artifacts[altered] += b"tampered"

    plan = build_plan(context((1,), [first]), [first], store.artifacts)

    assert plan.documents[0].disposition == "excluded"
    assert set(plan.documents[0].reasons) == {"artifact_missing", "artifact_altered"}
    assert plan.batches == ()


def test_a_missing_artifact_excludes_its_document() -> None:
    store = Store()
    first = row(store, 1)
    del store.artifacts[first.binding.manifest_sha256 or ""]
    plan = build_plan(context((1,), [first]), [first], store.artifacts)
    assert plan.documents[0].disposition == "excluded"
    assert plan.documents[0].reasons == ("artifact_missing",)
    assert plan.batches == ()


def test_an_excluded_selection_stays_visible_with_its_blockers() -> None:
    store = Store()
    broken = row(
        store,
        1,
        blockers=(Blocker(category="integrity", code="lineage_invalid"),),
        version_id=None,
    )
    plan = build_plan(context((1,), [broken]), [broken], store.artifacts)
    assert plan.documents[0].disposition == "excluded"
    assert plan.documents[0].reasons == ("integrity:lineage_invalid",)
    assert (plan.documents[0].files, plan.documents[0].bytes) == (0, 0)


def test_an_fc7_or_fc7b_blocker_is_never_planned() -> None:
    store = Store()
    found = [
        row(store, 1, blockers=(Blocker(category="policy", code="fc7_deny"),)),
        row(store, 2, blockers=(Blocker(category="authorization", code="fc7b_invalid"),)),
        row(store, 3, blockers=(Blocker(category="trust", code="head_missing"),)),
    ]
    plan = build_plan(context((1, 2, 3), found), found, store.artifacts)
    assert {item.disposition for item in plan.documents} == {"blocked"}
    assert plan.batches == ()


def test_only_the_named_release_wide_blockers_are_left_to_the_gates() -> None:
    assert (
        frozenset(
            {
                ("trust", "head_unanchored"),
                ("governance", "d10_legal_assessment_unresolved"),
                ("governance", "d11_automated_publication_not_accepted"),
                ("authorization", "fc7b:d10_legal_assessment_not_verifiable"),
                ("authorization", "fc7b:d11_automated_publication_not_accepted"),
            }
        )
        == RELEASE_WIDE_BLOCKERS
    )
    store = Store()
    first = row(store, 1)
    plan = build_plan(context((1,), [first]), [first], store.artifacts)
    assert plan.basis.pending_release_blockers == tuple(
        f"{category}:{code}" for category, code in sorted(RELEASE_WIDE_BLOCKERS)
    )


def test_a_document_under_another_policy_is_blocked() -> None:
    store = Store()
    other = row(store, 1)
    foreign = other.selection.policy.model_copy(update={"policy_sha256": "9" * 64})  # type: ignore[union-attr]
    moved = other._replace(selection=other.selection.model_copy(update={"policy": foreign}))
    plan = build_plan(context((1,), [moved]), [moved], store.artifacts)
    assert plan.documents[0].disposition == "blocked"
    assert "policy_mismatch" in plan.documents[0].reasons


def test_a_101_file_release_splits_at_the_hard_cap() -> None:
    store = Store()
    found = [row(store, n, sizes=(10,)) for n in range(1, 102)]
    chosen = constraints(max_documents_per_batch=100)
    plan = build_plan(context(range(1, 102), found, chosen), found, store.artifacts)
    assert [b.files for b in plan.batches] == [100, 1]
    assert [b.sequence for b in plan.batches] == [1, 2]


@pytest.mark.parametrize("excess", [0, 1])
def test_a_batch_fills_exactly_to_its_signed_file_cap(excess: int) -> None:
    store = Store()
    found = [row(store, n, sizes=(10, 10, 10)) for n in range(1, 5)]
    chosen = constraints(max_files_per_batch=9 - excess)
    plan = build_plan(context(range(1, 5), found, chosen), found, store.artifacts)
    expected = [9, 3] if not excess else [6, 6]
    assert [b.files for b in plan.batches] == expected


@pytest.mark.parametrize("excess", [0, 1])
def test_a_batch_fills_exactly_to_its_signed_byte_cap(excess: int) -> None:
    store = Store()
    found = [row(store, n, sizes=(500,)) for n in range(1, 4)]
    chosen = constraints(max_bytes_per_batch=1_000 - excess)
    plan = build_plan(context(range(1, 4), found, chosen), found, store.artifacts)
    expected = [1_000, 500] if not excess else [500, 500, 500]
    assert [b.bytes for b in plan.batches] == expected


def test_the_signed_document_cap_bounds_each_batch() -> None:
    store = Store()
    found = [row(store, n, sizes=(1,)) for n in range(1, 8)]
    chosen = constraints(max_documents_per_batch=3)
    plan = build_plan(context(range(1, 8), found, chosen), found, store.artifacts)
    assert [b.documents for b in plan.batches] == [3, 3, 1]


def test_an_oversized_document_is_blocked_and_the_rest_still_planned() -> None:
    store = Store()
    found = [
        row(store, 1, sizes=(400,)),
        row(store, 2, sizes=(1_200,)),
        row(store, 3, sizes=(400,)),
    ]
    chosen = constraints(max_bytes_per_batch=1_000)
    plan = build_plan(context((1, 2, 3), found, chosen), found, store.artifacts)
    by_id = {item.document_id: item for item in plan.documents}
    assert by_id[document(2)].disposition == "blocked"
    assert by_id[document(2)].reasons == ("document_exceeds_batch_cap",)
    assert [b.document_ids for b in plan.batches] == [(document(1), document(3))]


def test_the_plan_replays_byte_for_byte_whatever_the_input_order() -> None:
    store = Store()
    found = [row(store, n, sizes=(n * 10, 7)) for n in range(1, 6)]
    first = build_plan(context(range(1, 6), found), found, store.artifacts)
    again = build_plan(context(range(1, 6), found), list(reversed(found)), store.artifacts)
    assert plan_text(first) == plan_text(again)
    assert [b.batch_id for b in first.batches] == [b.batch_id for b in again.batches]
    assert parse_plan(plan_text(first).encode()) == first


@pytest.mark.parametrize(
    "change",
    [
        {"limits_sha256": "b" * 64},
        {"inventory_sha256": "f" * 64},
        {"inventory_version": 2},
        {"selection_release_id": "1" * 64},
    ],
)
def test_a_changed_basis_changes_the_plan_and_every_batch_id(change: dict[str, Any]) -> None:
    store = Store()
    found = [row(store, 1), row(store, 2)]
    before = build_plan(context((1, 2), found), found, store.artifacts)
    after = build_plan(context((1, 2), found, **change), found, store.artifacts)
    assert before.plan_id != after.plan_id
    assert {b.batch_id for b in before.batches}.isdisjoint(b.batch_id for b in after.batches)


def test_a_new_version_changes_the_batch_of_its_document() -> None:
    store = Store()
    found = [row(store, 1), row(store, 2)]
    newer = found[0]._replace(
        selection=found[0].selection.model_copy(update={"version_id": "7" * 64})
    )
    before = build_plan(context((1, 2), found), found, store.artifacts)
    after = build_plan(context((1, 2), found), [newer, found[1]], store.artifacts)
    assert before.batches[0].batch_id != after.batches[0].batch_id


def test_batch_ids_fit_the_ledger_and_name_no_text() -> None:
    store = Store()
    found = [row(store, 1)]
    (batch,) = build_plan(context((1,), found), found, store.artifacts).batches
    assert batch.batch_id.startswith("batch-")
    assert len(batch.batch_id) <= 63


def test_a_plan_is_never_eligible_nor_published() -> None:
    store = Store()
    found = [row(store, 1)]
    plan = build_plan(context((1,), found), found, store.artifacts)
    assert (plan.release_eligible, plan.published) == (False, False)
    assert plan.basis.planner_version == PLANNER_VERSION
    with pytest.raises(ValidationError):
        ReleasePlan.model_validate(plan.model_dump() | {"release_eligible": True})


def forged(plan: ReleasePlan, **changes: Any) -> dict[str, Any]:
    return plan.model_dump(mode="json") | changes


def test_a_hand_edited_plan_is_refused() -> None:
    store = Store()
    found = [row(store, n, sizes=(1,)) for n in (1, 2, 3)]
    plan = build_plan(
        context((1, 2, 3), found, constraints(max_documents_per_batch=2)), found, store.artifacts
    )
    assert len(plan.batches) == 2
    first, second = plan.batches
    duplicated = first.model_copy(
        update={"document_ids": (*first.document_ids, *second.document_ids)}
    )
    attempts = [
        forged(plan, plan_id="0" * 64),
        forged(plan, batches=[duplicated.model_dump(), second.model_dump()]),
        forged(plan, batches=[first.model_dump()]),
        forged(plan, documents=list(reversed(forged(plan)["documents"]))),
    ]
    for attempt in attempts:
        with pytest.raises(ValidationError):
            ReleasePlan.model_validate(attempt)


def test_a_batch_states_its_own_counts() -> None:
    with pytest.raises(ValidationError):
        PlannedBatch(
            batch_id="batch-" + "0" * 56,
            sequence=1,
            document_ids=(document(1),),
            documents=2,
            files=1,
            bytes=1,
        )


# --- planning from verified evidence -----------------------------------------------------


def chains_for(root: TrustRoot, keys: Keys, *numbers: int) -> list[tuple[Chain, Any]]:
    chosen = synthetic_policy()
    request = full_request(chosen, keys.owner)
    built = []
    for number in numbers:
        chain = Chain()
        chain.observe(Observation(renamed(original(), number), chosen, request, root))
        built.append((chain, request))
    return built


def offers_of(built: list[tuple[Chain, Any]], numbers: tuple[int, ...]) -> tuple[Any, ...]:
    return tuple(
        offer(chain, document(n), authorization=request)
        for (chain, request), n in zip(built, numbers, strict=True)
    )


def artifacts_of(built: list[tuple[Chain, Any]]) -> dict[str, bytes]:
    merged: dict[str, bytes] = {}
    for chain, _ in built:
        merged |= chain.artifacts
    return merged


def plan_request(root: TrustRoot, keys: Keys, numbers: tuple[int, ...] = (1, 2)) -> PlanRequest:
    built = chains_for(root, keys, *numbers)
    return PlanRequest(
        inventory=inventory_of(root, owner_inventory(keys, *numbers)),
        limits=limits_of(root, keys),
        offers=offers_of(built, numbers),
        artifacts=artifacts_of(built),
    )


def test_planning_from_verified_evidence_is_sealed_and_shows_every_blocker(
    root: TrustRoot, keys: Keys
) -> None:
    found = plan_release(plan_request(root, keys), root)
    assert found.status == "verified", found.reasons
    assert found.source == "engine_release_planner"
    assert authentic(found, "batch_plan")
    assert verified("batch_plan", found)
    assert found.plan is not None
    assert found.trust_root_sha256 == root.sha256
    assert found.plan_sha256 == digest_of(found.plan)
    # Today every real selection carries a per-document blocker (publication is
    # never complete), so the plan lists them all as blocked and plans nothing.
    assert {item.disposition for item in found.plan.documents} == {"blocked"}
    assert all("publication:publication_incomplete" in d.reasons for d in found.plan.documents)
    assert found.plan.batches == ()
    assert found.plan.coverage.state == "complete"


def test_the_newest_version_is_planned_and_a_blocked_head_is_never_replaced(
    root: TrustRoot, keys: Keys
) -> None:
    chosen = synthetic_policy()
    request = full_request(chosen, keys.owner)
    chain = Chain()
    chain.observe(Observation(original(), chosen, request, root))
    chain.observe(Observation(changed(), chosen, request, root))
    head = chain.entries()[-1].version_id
    offered = (offer(chain, FIRST, authorization=request),)
    inventory = inventory_of(root, owner_inventory(keys, 1))
    found = plan_release(
        PlanRequest(inventory, limits_of(root, keys), offered, chain.artifacts), root
    )
    assert found.plan is not None
    (planned,) = found.plan.documents
    assert planned.version_id == head
    assert planned.disposition == "blocked"


@pytest.mark.parametrize("which", ["inventory", "limits"])
def test_planning_needs_verified_inventory_and_limits(
    which: str, root: TrustRoot, keys: Keys
) -> None:
    request = plan_request(root, keys)
    hollow = request.inventory.model_copy(update={"provenance": None})
    if which == "limits":
        request = request._replace(limits=request.limits.model_copy(update={"provenance": None}))
    else:
        request = request._replace(inventory=hollow)
    found = plan_release(request, root)
    assert (found.status, found.plan) == ("invalid", None)
    assert f"plan_{which}_unverified" in reasons(found)


def test_planning_refuses_limits_for_another_policy(root: TrustRoot, keys: Keys) -> None:
    request = plan_request(root, keys)
    other = request.limits.model_copy(
        update={"policy": request.limits.policy.model_copy(update={"policy_version": 9})}  # type: ignore[union-attr]
    )
    found = plan_release(
        request._replace(limits=issue_provenance(other, "publication_limits")), root
    )
    assert "plan_policy_mismatch" in reasons(found)


def test_planning_refuses_evidence_from_another_root(
    tmp_path: Path, root: TrustRoot, keys: Keys
) -> None:
    other = every_root(tmp_path / "other", keys, f"{POLICY_NAMESPACE},{INVENTORY_NAMESPACE}")
    assert other.sha256 != root.sha256
    found = plan_release(plan_request(root, keys), other)
    assert (found.status, found.plan) == ("invalid", None)
    assert "plan_trust_root_conflict" in reasons(found)


def test_a_document_offered_twice_refuses_the_plan(root: TrustRoot, keys: Keys) -> None:
    request = plan_request(root, keys)
    found = plan_release(request._replace(offers=request.offers + request.offers[:1]), root)
    assert (found.status, found.plan) == ("invalid", None)
    assert "plan_offers_refused" in reasons(found)


def test_a_stored_plan_goes_stale_when_a_version_or_the_policy_changes(
    root: TrustRoot, keys: Keys
) -> None:
    request = plan_request(root, keys, (1,))
    stored = plan_release(request, root).plan
    assert stored is not None
    raw_plan = plan_text(stored).encode()
    assert verify_plan(raw_plan, request, root).status == "current"
    chosen = synthetic_policy()
    chain = Chain()
    signed = full_request(chosen, keys.owner)
    chain.observe(Observation(original(), chosen, signed, root))
    chain.observe(Observation(changed(), chosen, signed, root))
    newer = request._replace(
        offers=(offer(chain, document(1), authorization=signed),), artifacts=chain.artifacts
    )
    assert verify_plan(raw_plan, newer, root).status == "stale"
    renewed = request._replace(limits=limits_of(root, keys, limits_id="synthetic-limits-2"))
    assert verify_plan(raw_plan, renewed, root).status == "stale"
    assert verify_plan(b"{}", request, root).status == "invalid"


# --- the AR1 gates over a planned batch --------------------------------------------------


def planned_evidence(store: Store, numbers: tuple[int, ...], batch: tuple[int, ...]) -> Any:
    """Baseline evidence whose manifest is one batch of a sealed plan."""
    found = [row(store, n) for n in numbers]
    plan = build_plan(context(numbers, found), found, store.artifacts)
    batch_rows = [
        item for item in found if item.selection.document_id in {document(n) for n in batch}
    ]
    manifest = manifest_of(batch_rows)
    inventory = baseline().inventory.model_copy(
        update={
            "inventory": context(numbers).inventory,
            "inventory_sha256": "e" * 64,
        }
    )
    evidence = PlanEvidence(
        status="verified",
        source="engine_release_planner",
        reasons=(),
        plan=plan,
        plan_sha256=digest_of(plan),
        trust_root_sha256=baseline().registry.trust_root_sha256,
    )
    planned = next((b for b in plan.batches if b.document_ids == manifest_ids(manifest)), None)
    size = (
        BatchSize(documents=planned.documents, files=planned.files, bytes=planned.bytes)
        if planned
        else BatchSize(documents=len(batch_rows), files=1, bytes=1)
    )
    return baseline().model_copy(
        update={
            **consistent(manifest),
            "intent": intent(manifest, batch=size),
            "inventory": issue_provenance(inventory, "document_inventory"),
            "plan": issue_provenance(evidence, "batch_plan"),
        }
    )


def refusing(verdict: Any) -> set[str]:
    return {r.code for g in verdict.gates for r in g.reasons} & REFUSING_CODES


def consistent(manifest: Any) -> dict[str, Any]:
    """The manifest with a reproduction of exactly it and no anchors (none refusing)."""
    verification = ReleaseVerification(
        status="reproduced",
        offered_release_id=manifest.release_id,
        rebuilt_release_id=manifest.release_id,
        findings=(),
    )
    return {
        "manifest": manifest,
        "reproduction": issue_provenance(
            ReproductionEvidence(verification=verification), "manifest_reproduction"
        ),
        "anchors": (),
    }


def manifest_ids(manifest: Any) -> tuple[str, ...]:
    return tuple(item.document_id for item in manifest.content.documents)


def test_a_batch_of_a_verified_plan_accounts_for_the_rest_of_the_inventory() -> None:
    store = Store()
    evidence = planned_evidence(store, (1, 2, 3), (1, 2, 3))
    verdict = evaluate_release(evidence)
    assert not codes(verdict, "inventory") & {
        "inventory_document_missing",
        "plan_source_untrusted",
        "batch_not_in_plan",
    }
    assert "batch_size_unverified" not in codes(verdict, "constraints")


def test_documents_planned_in_another_batch_are_not_missing() -> None:
    store = Store()
    found = [row(store, n, sizes=(1,)) for n in (1, 2, 3)]
    chosen = constraints(max_documents_per_batch=2)
    plan = build_plan(context((1, 2, 3), found, chosen), found, store.artifacts)
    manifest = manifest_of(found[:2])
    evidence = planned_evidence(store, (1, 2, 3), (1, 2, 3)).model_copy(
        update={
            **consistent(manifest),
            "intent": intent(manifest, batch=BatchSize(documents=2, files=2, bytes=2)),
            "plan": issue_provenance(
                PlanEvidence(
                    status="verified",
                    source="engine_release_planner",
                    reasons=(),
                    plan=plan,
                    plan_sha256=digest_of(plan),
                    trust_root_sha256=baseline().registry.trust_root_sha256,
                ),
                "batch_plan",
            ),
        }
    )
    verdict = evaluate_release(evidence)
    assert "inventory_document_missing" not in codes(verdict, "inventory")
    assert "batch_not_in_plan" not in codes(verdict, "inventory")


def test_a_document_without_evidence_still_fails_the_inventory_gate() -> None:
    store = Store()
    found = [row(store, n) for n in (1, 2)]
    plan = build_plan(context((1, 2, 3), found), found, store.artifacts)
    manifest = manifest_of(found)
    evidence = planned_evidence(store, (1, 2), (1, 2)).model_copy(
        update={
            "inventory": issue_provenance(
                baseline().inventory.model_copy(
                    update={"inventory": context((1, 2, 3)).inventory, "inventory_sha256": "e" * 64}
                ),
                "document_inventory",
            ),
            **consistent(manifest),
            "plan": issue_provenance(
                PlanEvidence(
                    status="verified",
                    source="engine_release_planner",
                    reasons=(),
                    plan=plan,
                    plan_sha256=digest_of(plan),
                    trust_root_sha256=baseline().registry.trust_root_sha256,
                ),
                "batch_plan",
            ),
        }
    )
    verdict = evaluate_release(evidence)
    assert {document(3)} <= {
        r.document_id
        for g in verdict.gates
        if g.gate == "inventory"
        for r in g.reasons
        if r.code == "inventory_document_missing"
    }


def test_a_manifest_that_is_no_batch_of_the_plan_is_refused() -> None:
    store = Store()
    evidence = planned_evidence(store, (1, 2, 3), (1, 3))
    verdict = evaluate_release(evidence)
    assert "batch_not_in_plan" in codes(verdict, "inventory")
    assert "batch_not_in_plan" in REFUSING_CODES
    assert (verdict.status, refusing(verdict)) == ("refused", {"batch_not_in_plan"})


def test_a_document_the_plan_blocked_is_not_planned_but_not_forged() -> None:
    store = Store()
    held = row(store, 2, blockers=(Blocker(category="publication", code="publication_incomplete"),))
    found = [row(store, 1), held]
    plan = build_plan(context((1, 2), found), found, store.artifacts)
    manifest = manifest_of([held])
    evidence = planned_evidence(store, (1, 2), (1, 2)).model_copy(
        update={
            **consistent(manifest),
            "intent": intent(manifest, batch=BatchSize(documents=1, files=2, bytes=300)),
            "plan": issue_provenance(
                PlanEvidence(
                    status="verified",
                    source="engine_release_planner",
                    reasons=(),
                    plan=plan,
                    plan_sha256=digest_of(plan),
                    trust_root_sha256=baseline().registry.trust_root_sha256,
                ),
                "batch_plan",
            ),
        }
    )
    verdict = evaluate_release(evidence)
    assert "document_not_planned" in codes(verdict, "inventory")
    assert "batch_not_in_plan" not in codes(verdict, "inventory")
    assert "document_not_planned" not in REFUSING_CODES
    assert (verdict.status, refusing(verdict)) == ("blocked", set())


def test_a_stale_selection_in_the_batch_is_refused() -> None:
    store = Store()
    evidence = planned_evidence(store, (1, 2), (1, 2))
    selection = evidence.manifest.content.documents[0]
    newer = selection.model_copy(update={"version_id": "7" * 64})
    rows_now = [
        row_from(evidence.manifest, 0)._replace(selection=newer),
        row_from(evidence.manifest, 1),
    ]
    manifest = manifest_of(rows_now)
    verdict = evaluate_release(
        evidence.model_copy(
            update={**consistent(manifest), "intent": intent(manifest, batch=evidence.intent.batch)}
        )
    )
    assert "batch_selection_mismatch" in codes(verdict, "inventory")
    assert refusing(verdict) == {"batch_selection_mismatch"}


def row_from(manifest: Any, index: int) -> Any:
    return Row(manifest.content.documents[index], manifest.bindings[index])


def test_a_plan_for_another_inventory_is_refused() -> None:
    store = Store()
    evidence = planned_evidence(store, (1, 2), (1, 2))
    moved = evidence.inventory.model_copy(update={"inventory_sha256": "9" * 64})
    verdict = evaluate_release(
        evidence.model_copy(update={"inventory": issue_provenance(moved, "document_inventory")})
    )
    assert "plan_inventory_mismatch" in codes(verdict, "inventory")
    assert (verdict.status, refusing(verdict)) == ("refused", {"plan_inventory_mismatch"})


def test_a_plan_made_under_other_limits_is_refused() -> None:
    store = Store()
    evidence = planned_evidence(store, (1, 2), (1, 2))
    other = evidence.limits.model_copy(update={"limits_sha256": "9" * 64, "provenance": None})
    verdict = evaluate_release(
        evidence.model_copy(update={"limits": issue_provenance(other, "publication_limits")})
    )
    assert "plan_limits_mismatch" in codes(verdict, "inventory")
    assert "plan_limits_mismatch" in refusing(verdict)


def test_caller_batch_counts_contradicting_the_plan_are_refused() -> None:
    store = Store()
    evidence = planned_evidence(store, (1, 2), (1, 2))
    lied = evidence.intent.batch.model_copy(update={"bytes": 1})
    verdict = evaluate_release(
        evidence.model_copy(update={"intent": intent(evidence.manifest, batch=lied)})
    )
    assert "batch_size_contradicts_plan" in codes(verdict, "constraints")
    assert (verdict.status, refusing(verdict)) == ("refused", {"batch_size_contradicts_plan"})


def test_the_planned_size_not_the_callers_drives_the_caps() -> None:
    store = Store()
    evidence = planned_evidence(store, (1, 2), (1, 2))
    tight = evidence.limits.model_copy(
        update={
            "constraints": evidence.limits.constraints.model_copy(
                update={"max_bytes_per_batch": 299}
            )
        }
    )
    verdict = evaluate_release(
        evidence.model_copy(update={"limits": issue_provenance(tight, "publication_limits")})
    )
    assert "batch_bytes_exceeded" in codes(verdict, "constraints")


def test_without_a_plan_caller_counts_are_unverified() -> None:
    verdict = evaluate_release(baseline())
    assert "plan_missing" in codes(verdict, "inventory")
    assert "batch_size_unverified" in codes(verdict, "constraints")


def test_a_hand_made_plan_is_never_trusted() -> None:
    store = Store()
    evidence = planned_evidence(store, (1, 2), (1, 2))
    hollow = evidence.plan.model_copy(update={"provenance": None})
    verdict = evaluate_release(evidence.model_copy(update={"plan": hollow}))
    assert "plan_source_untrusted" in codes(verdict, "inventory")
    assert "batch_size_unverified" in codes(verdict, "constraints")


def test_a_plan_or_inventory_from_another_root_conflicts() -> None:
    store = Store()
    evidence = planned_evidence(store, (1, 2), (1, 2))
    moved = evidence.plan.model_copy(update={"trust_root_sha256": "9" * 64, "provenance": None})
    verdict = evaluate_release(
        evidence.model_copy(update={"plan": issue_provenance(moved, "batch_plan")})
    )
    assert "trust_root_conflict" in codes(verdict, "authorization")


def test_a_planned_batch_is_still_blocked_by_every_other_gate() -> None:
    store = Store()
    verdict = evaluate_release(planned_evidence(store, (1, 2), (1, 2)))
    assert verdict.release_eligible is False
    assert {"legal_assessment", "automated_publication", "execution_controls"} <= set(
        verdict.failed_gates
    )
    assert "fc7b:d10_legal_assessment_not_verifiable" in codes(verdict, "authorization")


# --- collecting evidence -----------------------------------------------------------------


class Sources:
    def __init__(self, inventory: InventoryMaterial, limit: LimitsMaterial) -> None:
        self._inventory, self._limits = inventory, limit

    def inventory(self) -> InventoryMaterial:
        return self._inventory

    def limits(self) -> LimitsMaterial:
        return self._limits


def scoped_request(root: TrustRoot, keys: Keys) -> tuple[ReleaseRequest, ReleaseSources]:
    """A release of document 1 whose plan scope offers documents 1 and 2."""
    built = chains_for(root, keys, 1, 2)
    offers = offers_of(built, (1, 2))
    artifacts = artifacts_of(built)
    manifest = build_release(offers[:1], artifacts, root)
    sources = Sources(
        InventoryMaterial(records=(owner_inventory(keys, 1, 2),)),
        LimitsMaterial(records=(signed_by(limits(), keys.owner, LIMITS_NAMESPACE),)),
    )
    request = ReleaseRequest(
        intent(manifest), release_text(manifest).encode(), offers, artifacts, root, CHECKED_AT
    )
    return request, ReleaseSources(inventory=sources, limits=sources)


def test_collected_inventory_and_plan_are_verified_and_still_blocked(
    root: TrustRoot, keys: Keys
) -> None:
    request, sources = scoped_request(root, keys)
    evidence = collect_evidence(request, sources)
    assert evidence.inventory.status == "verified", evidence.inventory.reasons
    assert evidence.plan.status == "verified", evidence.plan.reasons
    assert evidence.reproduction.verification.status == "reproduced"
    assert verified("document_inventory", evidence.inventory)
    assert verified("batch_plan", evidence.plan)
    verdict = evaluate_release(evidence)
    assert verdict.release_eligible is False
    assert "inventory_source_untrusted" not in codes(verdict, "inventory")
    again = evaluate_release(collect_evidence(request, sources))
    assert digest_of(again) == digest_of(verdict)


def test_the_resolved_root_reaches_the_inventory_and_the_planner(
    root: TrustRoot, keys: Keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, sources = scoped_request(root, keys)
    seen: list[TrustRoot] = []
    for name in ("verify_inventory", "plan_release"):
        real = getattr(release_sources, name)

        def recorded(*args: Any, real: Any = real) -> Any:
            seen.append(args[-1])
            return real(*args)

        monkeypatch.setattr(release_sources, name, recorded)
    collect_evidence(request, sources)
    assert len(seen) == 2
    assert all(item is root for item in seen)


def test_an_unavailable_inventory_source_offers_nothing(root: TrustRoot, keys: Keys) -> None:
    request, sources = scoped_request(root, keys)
    evidence = collect_evidence(request, sources._replace(inventory=release_sources.Unavailable()))
    assert evidence.inventory.status == "missing"
    assert evidence.plan.status == "invalid"
    assert "plan_inventory_unverified" in evidence.plan.reasons


# --- the modules ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", NEW_MODULES)
def test_ar2_modules_have_no_execution_capability(module: str) -> None:
    path = Path("src/lovspor/forarbeider") / f"{module}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    forbidden = ("subprocess", "socket", "urllib", "http", "requests", "httpx", "git", "os")
    assert not {name for name in imported if name.split(".")[0] in forbidden}
    writers = ("batch_release", "release_commands", "release_store", "release_checkout")
    assert not {name for name in imported if name.rsplit(".", 1)[-1] in writers}
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not calls & {"write_bytes", "write_text", "open", "mkdir", "unlink", "replace"}


def test_the_owner_key_with_every_owner_namespace_is_one_role(tmp_path: Path, keys: Keys) -> None:
    root = every_root(tmp_path / "root", keys, OWNER_EVERY)
    roles = {entry.principal: entry.role for entry in root.entries}
    assert roles["test-owner"] == "owner_policy"
