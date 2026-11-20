"""AR1b: signed limits, budget ledger, stop/resume, one trust root, sealed provenance (#738).

Synthetic only: throwaway keys in pytest's temporary directory, the synthetic
policy and the invented Prop. 901 L of the version fixtures. Nothing here
fetches, signs with a real key, writes a ledger or publishes.
"""

import ast
import json
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider import release_contract_models, release_sources
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_authorization import SignedRecord
from lovspor.forarbeider.policy_trust import (
    CONTROL_NAMESPACE,
    LIMITS_NAMESPACE,
    POLICY_NAMESPACE,
    RELEASE_NAMESPACE,
    TrustRoot,
    load_trust_root,
)
from lovspor.forarbeider.release_contract_models import (
    ControlEvidence,
    InventoryEvidence,
    LimitsEvidence,
    verified,
)
from lovspor.forarbeider.release_control import ControlRequest, verify_control
from lovspor.forarbeider.release_gates import evaluate_release
from lovspor.forarbeider.release_ledger import (
    LedgerRequest,
    entry_bytes,
    plan_entry,
    verify_ledger,
)
from lovspor.forarbeider.release_ledger_models import AR5_LEDGER_REQUIREMENTS, LedgerEntry
from lovspor.forarbeider.release_limits import LimitsMaterial, LimitsRequest, verify_limits
from lovspor.forarbeider.release_provenance import authentic, issue_provenance
from lovspor.forarbeider.release_sources import (
    ReleaseRequest,
    ReleaseSources,
    collect_evidence,
    resolve_trust_root,
)
from lovspor.forarbeider.version_release import build_release, release_text
from tests.unit.forarbeider_authorization_support import (
    OWNER_NAMESPACES,
    raw,
    revocation,
    signer_line,
    trust_root,
)
from tests.unit.forarbeider_release_control_support import (
    BOT,
    CHECKED_AT,
    DAY,
    OWNER,
    OWNER_ALL,
    RELEASE_A,
    RELEASE_B,
    SIGNED_AT,
    Keys,
    constraints,
    control,
    digest,
    limits,
    make_keys,
    operation,
    policy,
    policy_bytes,
    root_of,
    signed_by,
    usage,
)
from tests.unit.test_forarbeider_release_gates import baseline, codes, evaluated, intent
from tests.unit.test_forarbeider_version_release import authorized, offer

NEW_MODULES = (
    "release_provenance",
    "release_signed",
    "release_limits",
    "release_limits_models",
    "release_control",
    "release_control_models",
    "release_ledger",
    "release_ledger_models",
)


@pytest.fixture(scope="module")
def keys(tmp_path_factory: pytest.TempPathFactory) -> Keys:
    return make_keys(tmp_path_factory.mktemp("keys"))


@pytest.fixture
def root(tmp_path: Path, keys: Keys) -> TrustRoot:
    return root_of(tmp_path / "root", keys)


# --- the trust root's owner namespaces ---------------------------------------------------


def test_the_owner_role_may_hold_the_limits_and_control_namespaces(root: TrustRoot) -> None:
    owner = next(entry for entry in root.entries if entry.principal == OWNER)
    assert owner.role == "owner_policy"
    assert owner.namespaces == frozenset({POLICY_NAMESPACE, LIMITS_NAMESPACE, CONTROL_NAMESPACE})


@pytest.mark.parametrize(
    "namespaces",
    [f"{CONTROL_NAMESPACE},{RELEASE_NAMESPACE}", f"{LIMITS_NAMESPACE},{RELEASE_NAMESPACE}"],
)
def test_a_key_mixing_owner_and_bot_namespaces_refuses_the_root(
    tmp_path: Path, keys: Keys, namespaces: str
) -> None:
    with pytest.raises(ForarbeiderRefusedError):
        trust_root(tmp_path, [signer_line(BOT, keys.bot, namespaces)])


# --- signed limits -----------------------------------------------------------------------


def limits_request(
    *offered: SignedRecord, revocations: tuple[SignedRecord, ...] = (), **changes: Any
) -> LimitsRequest:
    fields: dict[str, Any] = {
        "policy": policy_bytes(),
        "material": LimitsMaterial(records=offered, revocations=revocations),
        "checked_at": CHECKED_AT,
    }
    return LimitsRequest(**(fields | changes))


def owner_limits(keys: Keys, **changes: Any) -> SignedRecord:
    return signed_by(limits(**changes), keys.owner, LIMITS_NAMESPACE)


def test_owner_signed_limits_verify_and_are_sealed(root: TrustRoot, keys: Keys) -> None:
    found = verify_limits(limits_request(owner_limits(keys)), root)
    assert found.status == "verified"
    assert found.constraints == constraints()
    assert found.trust_root_sha256 == root.sha256
    assert found.limits_sha256 == sha256(raw(limits())).hexdigest()
    assert found.signer_role == "owner_policy"
    assert verified("publication_limits", found)


def test_no_limits_record_is_missing(root: TrustRoot) -> None:
    found = verify_limits(limits_request(), root)
    assert (found.status, found.reasons) == ("missing", ("signed_limits_missing",))
    assert not verified("publication_limits", found)


def test_unsigned_limits_are_missing(root: TrustRoot) -> None:
    found = verify_limits(limits_request(SignedRecord(raw(limits()), None)), root)
    assert found.status == "missing"
    assert "signature_missing" in found.reasons


def test_a_modified_limits_record_is_refused(root: TrustRoot, keys: Keys) -> None:
    good = owner_limits(keys)
    raised = raw(limits(constraints=constraints(max_documents_per_day=10_000)))
    found = verify_limits(limits_request(SignedRecord(raised, good.signature)), root)
    assert found.status == "invalid"
    assert "signature_invalid" in found.reasons
    assert found.constraints is None


def test_limits_signed_in_another_namespace_are_refused(root: TrustRoot, keys: Keys) -> None:
    offered = signed_by(limits(), keys.owner, POLICY_NAMESPACE)
    found = verify_limits(limits_request(offered), root)
    assert "signature_namespace_mismatch" in found.reasons


def test_the_bot_cannot_sign_or_raise_its_own_limits(root: TrustRoot, keys: Keys) -> None:
    raised = limits(signer_principal=BOT, constraints=constraints(max_documents_per_day=10_000))
    found = verify_limits(limits_request(signed_by(raised, keys.bot, LIMITS_NAMESPACE)), root)
    assert found.status == "invalid"
    assert "signer_role_not_permitted" in found.reasons


def test_an_owner_key_without_the_limits_namespace_is_refused(tmp_path: Path, keys: Keys) -> None:
    narrow = root_of(tmp_path / "narrow", keys, owner_namespaces=OWNER_NAMESPACES)
    found = verify_limits(limits_request(owner_limits(keys)), narrow)
    assert "signer_role_not_permitted" in found.reasons


def test_an_unlisted_key_is_refused(root: TrustRoot, keys: Keys) -> None:
    found = verify_limits(limits_request(signed_by(limits(), keys.other, LIMITS_NAMESPACE)), root)
    assert "signer_not_in_trust_root" in found.reasons


def test_a_revoked_key_is_refused(tmp_path: Path, keys: Keys) -> None:
    revoked = root_of(tmp_path / "revoked", keys, revoked=(keys.owner,))
    found = verify_limits(limits_request(owner_limits(keys)), revoked)
    assert "signer_key_revoked" in found.reasons


def test_a_key_expired_by_the_check_time_is_refused(tmp_path: Path, keys: Keys) -> None:
    window = ("20260101000000Z", "20261114110000Z")
    lines = [signer_line(OWNER, keys.owner, OWNER_ALL, window)]
    expiring = trust_root(tmp_path / "expiring", lines)
    found = verify_limits(limits_request(owner_limits(keys)), expiring)
    assert found.status == "invalid"
    assert "signer_key_expired" in found.reasons


def test_a_record_naming_another_signer_is_refused(root: TrustRoot, keys: Keys) -> None:
    found = verify_limits(limits_request(owner_limits(keys, signer_principal="someone")), root)
    assert "signer_principal_mismatch" in found.reasons


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"valid_until": SIGNED_AT + timedelta(minutes=30)}, "limits_expired"),
        ({"valid_from": CHECKED_AT + timedelta(hours=1)}, "limits_not_yet_valid"),
        ({"signed_at": CHECKED_AT + timedelta(minutes=1)}, "signed_after_check_time"),
    ],
)
def test_limits_outside_their_window_are_refused(
    root: TrustRoot, keys: Keys, changes: dict[str, Any], reason: str
) -> None:
    found = verify_limits(limits_request(owner_limits(keys, **changes)), root)
    assert found.status == "invalid"
    assert reason in found.reasons


def test_revoked_limits_are_refused(root: TrustRoot, keys: Keys) -> None:
    withdrawn = signed_by(revocation("synthetic-limits-1"), keys.owner, POLICY_NAMESPACE)
    found = verify_limits(limits_request(owner_limits(keys), revocations=(withdrawn,)), root)
    assert found.status == "invalid"
    assert "limits_revoked" in found.reasons


def test_a_revocation_by_the_bot_is_not_honoured_and_fails_closed(
    root: TrustRoot, keys: Keys
) -> None:
    forged = signed_by(revocation("synthetic-limits-1"), keys.bot, POLICY_NAMESPACE)
    found = verify_limits(limits_request(owner_limits(keys), revocations=(forged,)), root)
    assert found.status == "invalid"
    assert "revocation_not_verified" in found.reasons


def test_limits_for_another_policy_are_refused(root: TrustRoot, keys: Keys) -> None:
    other = policy().model_copy(update={"policy_version": 2})
    offered = signed_by(limits(other), keys.owner, LIMITS_NAMESPACE)
    found = verify_limits(limits_request(offered), root)
    assert found.status == "invalid"
    assert {"policy_version_mismatch", "policy_digest_mismatch"} <= set(found.reasons)


def test_two_valid_limits_records_conflict(root: TrustRoot, keys: Keys) -> None:
    second = owner_limits(keys, limits_id="synthetic-limits-2")
    found = verify_limits(limits_request(owner_limits(keys), second), root)
    assert (found.status, found.constraints) == ("invalid", None)
    assert "limits_conflict" in found.reasons


@pytest.mark.parametrize(
    "change",
    [
        lambda body: body["constraints"].update(max_documents_per_batch=101),
        lambda body: body["constraints"].update(max_files_per_batch=101),
        lambda body: body["constraints"].update(max_bytes_per_batch=25_000_001),
        lambda body: body.update(schema_version=2),
        lambda body: body.update(domain="lovspor.forarbeider.policy-approval.v1"),
        lambda body: body.update(verified=True),
    ],
)
def test_invalid_or_incompatible_limits_are_malformed(
    root: TrustRoot, keys: Keys, change: Any
) -> None:
    body = json.loads(raw(limits()))
    change(body)
    data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    found = verify_limits(limits_request(signed_by(data, keys.owner, LIMITS_NAMESPACE)), root)
    assert found.reasons == ("record_malformed",)


def test_noncanonical_limits_bytes_are_refused(root: TrustRoot, keys: Keys) -> None:
    padded = raw(limits()) + b"\n"
    found = verify_limits(limits_request(signed_by(padded, keys.owner, LIMITS_NAMESPACE)), root)
    assert "record_noncanonical" in found.reasons


def test_limits_without_the_policy_bytes_are_missing(root: TrustRoot, keys: Keys) -> None:
    found = verify_limits(limits_request(owner_limits(keys), policy=None), root)
    assert (found.status, found.reasons) == ("missing", ("limits_policy_unavailable",))


def test_limits_a_caller_labels_verified_are_not_trusted() -> None:
    claimed = LimitsEvidence(
        status="verified",
        source="owner_signed_policy_limits",
        constraints=constraints(max_documents_per_day=10_000),
        limits_sha256="f" * 64,
        reasons=(),
    )
    assert not verified("publication_limits", claimed)
    assert "limits_source_untrusted" in codes(evaluated(limits=claimed), "constraints")


# --- the budget ledger -------------------------------------------------------------------


def sealed_limits(root: TrustRoot, keys: Keys, **changes: Any) -> LimitsEvidence:
    return verify_limits(limits_request(owner_limits(keys, **changes)), root)


def chained(limited: LimitsEvidence, *operations: Any) -> tuple[bytes, ...]:
    entries: tuple[bytes, ...] = ()
    for item in operations:
        plan = plan_entry(entries, item, limited)
        assert plan.outcome == "append", plan.reasons
        assert plan.entry is not None
        entries = (*entries, entry_bytes(plan.entry))
    return entries


def ledger(entries: tuple[bytes, ...], limited: LimitsEvidence, release: str = RELEASE_A) -> Any:
    return verify_ledger(LedgerRequest(entries, limited, DAY, release))


def test_a_reservation_is_counted_once_and_is_never_anchored(root: TrustRoot, keys: Keys) -> None:
    limited = sealed_limits(root, keys)
    entries = chained(limited, operation("reserve"))
    found = ledger(entries, limited)
    assert found.status == "verified"
    assert (found.reserved, found.committed) == (usage(), usage(0, 0, 0, 0))
    assert found.release_state == "reserved"
    assert found.anchoring == "unanchored"
    assert found.persistence_requirements == AR5_LEDGER_REQUIREMENTS
    assert verified("budget_ledger", found)


def test_an_empty_ledger_is_missing_not_a_zero_budget(root: TrustRoot, keys: Keys) -> None:
    found = ledger((), sealed_limits(root, keys))
    assert found.status == "missing"


def test_a_retried_reservation_is_idempotent(root: TrustRoot, keys: Keys) -> None:
    limited = sealed_limits(root, keys)
    entries = chained(limited, operation("reserve"))
    plan = plan_entry(entries, operation("reserve", recorded_at=CHECKED_AT + timedelta(1)), limited)
    assert (plan.outcome, plan.entry) == ("already_applied", None)


def test_a_conflicting_reservation_is_refused(root: TrustRoot, keys: Keys) -> None:
    limited = sealed_limits(root, keys)
    entries = chained(limited, operation("reserve"))
    for changed in (
        operation("reserve", amounts=usage(documents=3)),
        operation("reserve", batch_id="batch-002"),
    ):
        plan = plan_entry(entries, changed, limited)
        assert plan.outcome == "refused"
        assert "ledger_conflicting_reservation" in plan.reasons


def test_a_reservation_over_the_daily_budget_is_refused(root: TrustRoot, keys: Keys) -> None:
    limited = sealed_limits(root, keys)
    entries = chained(limited, operation("reserve", amounts=usage(documents=15)))
    plan = plan_entry(entries, operation("reserve", RELEASE_B, amounts=usage(6)), limited)
    assert plan.outcome == "refused"
    assert "budget_documents_exceeded" in plan.reasons


@pytest.mark.parametrize("resource", ["documents", "files", "bytes", "commits"])
def test_each_daily_budget_accepts_its_boundary_and_refuses_one_more(
    root: TrustRoot, keys: Keys, resource: str
) -> None:
    limited = sealed_limits(root, keys, constraints=constraints(**{f"max_{resource}_per_day": 4}))
    amounts = usage(0, 0, 0, 0).model_copy(update={resource: 2})
    entries = chained(limited, operation("reserve", amounts=amounts))
    boundary = operation("reserve", RELEASE_B, amounts=amounts)
    plan = plan_entry(entries, boundary, limited)
    assert plan.outcome == "append"
    assert plan.entry is not None
    replayed = ledger((*entries, entry_bytes(plan.entry)), limited)
    assert replayed.status == "verified"
    assert getattr(replayed.reserved, resource) == 4

    over = boundary.model_copy(update={"amounts": amounts.model_copy(update={resource: 3})})
    refused = plan_entry(entries, over, limited)
    assert (refused.outcome, refused.entry) == ("refused", None)
    assert refused.reasons == (f"budget_{resource}_exceeded",)


@pytest.mark.parametrize("resource", ["documents", "files", "bytes"])
def test_each_batch_cap_accepts_its_boundary_and_refuses_one_more(
    root: TrustRoot, keys: Keys, resource: str
) -> None:
    limited = sealed_limits(root, keys, constraints=constraints(**{f"max_{resource}_per_batch": 4}))
    amounts = usage(0, 0, 0, 0).model_copy(update={resource: 4})
    assert plan_entry((), operation("reserve", amounts=amounts), limited).outcome == "append"
    over = amounts.model_copy(update={resource: 5})
    refused = plan_entry((), operation("reserve", amounts=over), limited)
    assert (refused.outcome, refused.entry) == ("refused", None)
    assert refused.reasons == (f"batch_{resource}_exceeded",)


def test_partial_settlement_releases_unused_budget_and_retries_do_not_spend_it_twice(
    root: TrustRoot, keys: Keys
) -> None:
    limited = sealed_limits(root, keys)
    settled = usage(5)
    entries = chained(
        limited, operation("reserve", amounts=usage(15)), operation("commit", amounts=settled)
    )
    found = ledger(entries, limited)
    assert (found.committed, found.reserved, found.release_state) == (
        settled,
        usage(0, 0, 0, 0),
        "settled",
    )
    retry = plan_entry(entries, operation("commit", amounts=settled), limited)
    assert (retry.outcome, retry.entry, retry.reasons) == ("already_applied", None, ())
    next_plan = plan_entry(entries, operation("reserve", RELEASE_B, amounts=usage(15)), limited)
    assert next_plan.outcome == "append"
    assert next_plan.entry is not None
    found = ledger((*entries, entry_bytes(next_plan.entry)), limited, RELEASE_B)
    assert (found.committed, found.reserved) == (settled, usage(15))


def test_retries_cannot_bypass_the_budget(root: TrustRoot, keys: Keys) -> None:
    limited = sealed_limits(root, keys)
    entries = chained(
        limited, operation("reserve", amounts=usage(15)), operation("commit", amounts=usage(15))
    )
    for retry in (operation("reserve", amounts=usage(15)), operation("commit", amounts=usage(15))):
        plan = plan_entry(entries, retry, limited)
        assert plan.outcome in {"already_applied", "refused"}
        assert plan.entry is None
    assert ledger(entries, limited).committed == usage(15)


def test_a_committed_release_cannot_be_reserved_again(root: TrustRoot, keys: Keys) -> None:
    limited = sealed_limits(root, keys)
    entries = chained(limited, operation("reserve"), operation("commit"))
    plan = plan_entry(entries, operation("reserve", batch_id="batch-002"), limited)
    assert plan.outcome == "refused"
    assert "ledger_reservation_after_commit" in plan.reasons


def test_a_cancelled_release_cannot_be_reserved_again(root: TrustRoot, keys: Keys) -> None:
    limited = sealed_limits(root, keys)
    entries = chained(limited, operation("reserve"), operation("cancel"))
    plan = plan_entry(entries, operation("reserve"), limited)
    assert "ledger_reservation_after_cancel" in plan.reasons
    assert ledger(entries, limited).reserved == usage(0, 0, 0, 0)


@pytest.mark.parametrize(
    ("operations", "reason"),
    [
        (("commit",), "ledger_commit_without_reservation"),
        (("cancel",), "ledger_cancel_without_reservation"),
    ],
)
def test_settling_without_a_reservation_is_refused(
    root: TrustRoot, keys: Keys, operations: tuple[str, ...], reason: str
) -> None:
    plan = plan_entry((), operation(operations[0]), sealed_limits(root, keys))
    assert (plan.outcome, reason in plan.reasons) == ("refused", True)


def test_a_settlement_larger_than_its_reservation_is_refused(root: TrustRoot, keys: Keys) -> None:
    limited = sealed_limits(root, keys)
    entries = chained(limited, operation("reserve"))
    plan = plan_entry(entries, operation("commit", amounts=usage(documents=3)), limited)
    assert "ledger_settlement_exceeds_reservation" in plan.reasons


def test_reconciliation_must_match_the_settled_totals(root: TrustRoot, keys: Keys) -> None:
    limited = sealed_limits(root, keys)
    entries = chained(limited, operation("reserve"), operation("commit"))
    good = plan_entry(entries, operation("reconcile", None), limited)
    assert good.outcome == "append"
    bad = plan_entry(entries, operation("reconcile", None, amounts=usage(1)), limited)
    assert "ledger_reconciliation_mismatch" in bad.reasons


def forged(entries: tuple[bytes, ...], **changes: Any) -> bytes:
    """The last entry re-chained after itself with ``changes``: a hand-made append."""
    last = LedgerEntry.model_validate_json(entries[-1])
    update = {
        "sequence": last.sequence + 1,
        "previous_sha256": sha256(entries[-1]).hexdigest(),
        **changes,
    }
    return entry_bytes(last.model_copy(update=update))


def test_a_duplicated_reservation_in_the_chain_is_invalid(root: TrustRoot, keys: Keys) -> None:
    limited = sealed_limits(root, keys)
    entries = chained(limited, operation("reserve"))
    found = ledger((*entries, forged(entries)), limited)
    assert found.status == "invalid"
    assert "ledger_duplicate_reservation" in found.reasons


def test_a_double_settlement_in_the_chain_is_invalid(root: TrustRoot, keys: Keys) -> None:
    limited = sealed_limits(root, keys)
    entries = chained(limited, operation("reserve"), operation("commit"))
    found = ledger((*entries, forged(entries)), limited)
    assert "ledger_duplicate_settlement" in found.reasons


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"previous_sha256": "0" * 64}, "ledger_chain_broken"),
        ({"sequence": 5}, "ledger_sequence_gap"),
        ({"ledger_id": "another-ledger"}, "ledger_id_mismatch"),
        ({"recorded_at": CHECKED_AT - timedelta(hours=1)}, "ledger_time_reordered"),
    ],
)
def test_a_broken_chain_is_invalid(
    root: TrustRoot, keys: Keys, changes: dict[str, Any], reason: str
) -> None:
    limited = sealed_limits(root, keys)
    entries = chained(limited, operation("reserve"))
    appended = forged(entries, operation="cancel", **changes)
    found = ledger((*entries, appended), limited)
    assert found.status == "invalid"
    assert reason in found.reasons


def test_a_chain_over_budget_is_invalid(root: TrustRoot, keys: Keys) -> None:
    limited = sealed_limits(root, keys)
    entries = chained(limited, operation("reserve", amounts=usage(15)))
    over = forged(entries, release_id=RELEASE_B, amounts=usage(15))
    assert "budget_documents_exceeded" in ledger((*entries, over), limited).reasons


def test_reservations_under_other_limits_are_invalid(root: TrustRoot, keys: Keys) -> None:
    limited = sealed_limits(root, keys)
    entries = chained(limited, operation("reserve"))
    other = sealed_limits(root, keys, constraints=constraints(max_documents_per_day=19))
    assert "ledger_limits_mismatch" in ledger(entries, other).reasons


def test_a_noncanonical_entry_is_invalid(root: TrustRoot, keys: Keys) -> None:
    limited = sealed_limits(root, keys)
    entries = chained(limited, operation("reserve"))
    found = ledger((entries[0] + b" ",), limited)
    assert found.reasons == ("ledger_entry_malformed",)


def test_a_ledger_entry_cannot_settle_another_day(root: TrustRoot, keys: Keys) -> None:
    limited = sealed_limits(root, keys)
    entries = chained(limited, operation("reserve"))
    later = operation("commit", day=DAY + timedelta(1), recorded_at=CHECKED_AT + timedelta(1))
    plan = plan_entry(entries, later, limited)
    assert "ledger_day_mismatch" in plan.reasons


def test_the_ledger_never_lets_a_release_pass_the_constraints_gate(
    root: TrustRoot, keys: Keys
) -> None:
    limited = sealed_limits(root, keys)
    found = ledger(chained(limited, operation("reserve"), operation("commit")), limited)
    verdict = evaluated(limits=limited, ledger=found)
    assert {"budget_ledger_not_anchored", "release_already_settled"} <= codes(
        verdict, "constraints"
    )
    assert verdict.status == "refused"


# --- stop and resume ---------------------------------------------------------------------


class History:
    """A control chain signed in order by the owner unless told otherwise."""

    def __init__(self, keys: Keys) -> None:
        self.keys = keys
        self.records: list[SignedRecord] = []

    def add(self, action: str, key: Any = None, **changes: Any) -> SignedRecord:
        previous = self.records[-1] if self.records else None
        record = control(action, len(self.records) + 1, previous, **changes)
        item = signed_by(record, key or self.keys.owner, CONTROL_NAMESPACE)
        self.records.append(item)
        return item


def state(root: TrustRoot, records: Any, policy_id: str | None = None, **changes: Any) -> Any:
    fields: dict[str, Any] = {
        "history": tuple(records),
        "policy_id": policy_id,
        "checked_at": CHECKED_AT,
    }
    return verify_control(ControlRequest(**(fields | changes)), root)


def test_no_control_history_means_stopped(root: TrustRoot) -> None:
    found = state(root, ())
    assert (found.status, found.state) == ("missing", "stopped")
    assert "control_history_missing" in found.reasons


def test_an_owner_resume_of_the_initial_stop_clears_it(root: TrustRoot, keys: Keys) -> None:
    history = History(keys)
    history.add("resume")
    found = state(root, history.records)
    assert (found.status, found.state, found.active_stops) == ("verified", "not_stopped", ())
    assert found.anchoring == "unanchored"
    assert verified("release_control", found)


def test_a_stop_overrides_an_earlier_resume(root: TrustRoot, keys: Keys) -> None:
    history = History(keys)
    history.add("resume")
    stop = history.add("stop")
    found = state(root, history.records)
    assert (found.state, found.active_stops) == ("stopped", (digest(stop),))


def test_a_resume_naming_the_stop_clears_it(root: TrustRoot, keys: Keys) -> None:
    history = History(keys)
    history.add("resume")
    stop = history.add("stop")
    history.add("resume", resumes_control_sha256=digest(stop))
    assert state(root, history.records).state == "not_stopped"


def test_a_resume_cannot_silently_clear_a_stop_it_does_not_name(
    root: TrustRoot, keys: Keys
) -> None:
    history = History(keys)
    history.add("resume")
    history.add("stop")
    history.add("resume")
    found = state(root, history.records)
    assert (found.status, found.state) == ("verified", "stopped")


def test_a_resume_naming_an_unknown_stop_is_invalid(root: TrustRoot, keys: Keys) -> None:
    history = History(keys)
    history.add("resume")
    history.add("stop")
    history.add("resume", resumes_control_sha256="9" * 64)
    found = state(root, history.records)
    assert (found.status, found.state) == ("invalid", "stopped")
    assert "resume_target_unknown" in found.reasons


def test_the_bot_cannot_resume_itself(root: TrustRoot, keys: Keys) -> None:
    history = History(keys)
    history.add("resume")
    stop = history.add("stop")
    history.add("resume", keys.bot, signer_principal=BOT, resumes_control_sha256=digest(stop))
    found = state(root, history.records)
    assert (found.status, found.state) == ("invalid", "stopped")
    assert "signer_role_not_permitted" in found.reasons


def test_a_forged_resume_is_invalid(root: TrustRoot, keys: Keys) -> None:
    history = History(keys)
    history.add("resume")
    stop = history.add("stop")
    good = history.add("resume", resumes_control_sha256=digest(stop))
    body = json.loads(good.record)
    body["reason"] = "forged"
    history.records[-1] = SignedRecord(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode(), good.signature
    )
    found = state(root, history.records)
    assert (found.status, found.state) == ("invalid", "stopped")


def test_a_resume_by_an_unlisted_key_is_invalid(root: TrustRoot, keys: Keys) -> None:
    history = History(keys)
    history.add("resume", keys.other)
    assert state(root, history.records).state == "stopped"


@pytest.mark.parametrize("order", [(1, 0, 2), (0, 2, 1), (0, 2)])
def test_reordered_or_incomplete_history_is_stopped(
    root: TrustRoot, keys: Keys, order: tuple[int, ...]
) -> None:
    history = History(keys)
    history.add("resume")
    stop = history.add("stop")
    history.add("resume", resumes_control_sha256=digest(stop))
    found = state(root, [history.records[i] for i in order])
    assert (found.status, found.state) == ("invalid", "stopped")


def test_conflicting_history_is_stopped(root: TrustRoot, keys: Keys) -> None:
    history = History(keys)
    first = history.add("resume")
    fork = signed_by(
        control("resume", 1, None, control_id="synthetic-control-fork"),
        keys.owner,
        CONTROL_NAMESPACE,
    )
    found = state(root, [first, fork])
    assert (found.status, found.state) == ("invalid", "stopped")


def test_a_stale_resume_from_another_chain_is_refused(root: TrustRoot, keys: Keys) -> None:
    old = History(keys)
    old.add("resume")
    old_stop = old.add("stop")
    old_resume = old.add("resume", resumes_control_sha256=digest(old_stop))
    new = History(keys)
    new.add("resume", control_id="synthetic-new-1")
    new.add("stop", control_id="synthetic-new-2")
    found = state(root, [*new.records, old_resume])
    assert (found.status, found.state) == ("invalid", "stopped")


def test_an_expired_resume_stops_again(root: TrustRoot, keys: Keys) -> None:
    history = History(keys)
    history.add("resume", valid_until=SIGNED_AT + timedelta(minutes=30))
    found = state(root, history.records)
    assert (found.status, found.state) == ("verified", "stopped")
    assert "resume_expired" in found.reasons


def test_a_resume_signed_after_the_check_time_is_invalid(root: TrustRoot, keys: Keys) -> None:
    history = History(keys)
    history.add("resume")
    found = state(root, history.records, checked_at=SIGNED_AT)
    assert (found.status, found.state) == ("invalid", "stopped")


def test_a_resume_by_a_key_revoked_later_is_invalid(tmp_path: Path, keys: Keys) -> None:
    history = History(keys)
    history.add("resume")
    revoked = root_of(tmp_path / "revoked", keys, revoked=(keys.owner,))
    assert state(revoked, history.records).state == "stopped"


def test_a_resume_valid_for_more_than_a_week_does_not_parse() -> None:
    with pytest.raises(ValidationError):
        control("resume", 1, None, valid_until=SIGNED_AT + timedelta(days=8))


def test_a_policy_scoped_stop_stops_only_its_policy(root: TrustRoot, keys: Keys) -> None:
    history = History(keys)
    history.add("resume")
    history.add("stop", scope={"policy_id": "other-policy"})
    assert state(root, history.records, "synthetic-policy").state == "not_stopped"
    assert state(root, history.records, "other-policy").state == "stopped"


def test_a_resume_of_another_scope_is_invalid(root: TrustRoot, keys: Keys) -> None:
    history = History(keys)
    history.add("resume")
    stop = history.add("stop")
    history.add(
        "resume", scope={"policy_id": "synthetic-policy"}, resumes_control_sha256=digest(stop)
    )
    found = state(root, history.records, "synthetic-policy")
    assert (found.status, found.state) == ("invalid", "stopped")
    assert "resume_scope_mismatch" in found.reasons


def test_a_caller_cannot_claim_the_pipeline_is_not_stopped() -> None:
    with pytest.raises(ValidationError):
        ControlEvidence(
            status="missing", source=None, reasons=("x",), state="not_stopped", active_stops=()
        )
    claimed = ControlEvidence(
        status="verified",
        source="owner_signed_control",
        reasons=(),
        state="not_stopped",
        active_stops=(),
        head_sha256="1" * 64,
    )
    gate = codes(evaluated(control=claimed), "stop_control")
    assert {"control_source_untrusted", "control_history_unanchored"} <= gate


def test_stop_overrides_every_approval(root: TrustRoot, keys: Keys) -> None:
    history = History(keys)
    history.add("resume")
    history.add("stop")
    verdict = evaluated(control=state(root, history.records))
    assert "release_stopped" in codes(verdict, "stop_control")
    assert "stop_control" in verdict.failed_gates
    assert verdict.release_eligible is False


def test_even_a_verified_resume_leaves_the_stop_gate_failed(root: TrustRoot, keys: Keys) -> None:
    history = History(keys)
    history.add("resume")
    found = state(root, history.records, "synthetic-policy")
    assert codes(evaluated(control=found), "stop_control") == {"control_history_unanchored"}
    unscoped = state(root, history.records)
    assert "control_scope_mismatch" in codes(evaluated(control=unscoped), "stop_control")


# --- sealed provenance -------------------------------------------------------------------


def test_a_kind_without_a_verifier_cannot_be_sealed() -> None:
    with pytest.raises(ValueError, match="no verifier"):
        issue_provenance(baseline().legal, "legal_assessment")


def test_a_copied_seal_does_not_vouch_for_other_evidence(root: TrustRoot, keys: Keys) -> None:
    genuine = sealed_limits(root, keys)
    fabricated = genuine.model_copy(
        update={"constraints": constraints(max_documents_per_day=10_000)}
    )
    assert authentic(genuine, "publication_limits")
    assert not authentic(fabricated, "publication_limits")
    assert not verified("publication_limits", fabricated)


def test_a_seal_does_not_survive_serialization(root: TrustRoot, keys: Keys) -> None:
    genuine = sealed_limits(root, keys)
    revived = LimitsEvidence.model_validate(genuine.model_dump())
    assert revived == genuine.model_copy(update={"provenance": revived.provenance})
    assert not verified("publication_limits", revived)


def test_a_seal_for_one_kind_does_not_vouch_for_another(root: TrustRoot, keys: Keys) -> None:
    assert not authentic(sealed_limits(root, keys), "budget_ledger")


def test_widening_the_verified_sources_does_not_trust_fabricated_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    every = frozenset(
        b for t in release_contract_models.TRUST_BOUNDARIES for b in t.accepted_sources
    )
    monkeypatch.setattr(release_contract_models, "VERIFIED_SOURCES", every)
    claimed: InventoryEvidence = baseline().inventory
    assert release_contract_models.trusted("document_inventory", "verified", claimed.source)
    assert not verified("document_inventory", claimed)
    assert "inventory_source_untrusted" in codes(evaluate_release(baseline()), "inventory")


def test_hand_made_registry_publisher_approval_and_reproduction_are_unverified() -> None:
    fabricated = baseline().model_copy(
        update={
            "registry": baseline().registry.model_copy(update={"provenance": None}),
            "publisher": baseline().publisher.model_copy(update={"provenance": None}),
            "approvals": tuple(
                a.model_copy(update={"provenance": None}) for a in baseline().approvals
            ),
            "reproduction": baseline().reproduction.model_copy(update={"provenance": None}),
        }
    )
    verdict = evaluate_release(fabricated)
    assert "trust_root_unverified" in codes(verdict, "authorization")
    assert "publisher_unverified" in codes(verdict, "publisher")
    assert "approval_unverified" in codes(verdict, "automated_publication")
    assert "reproduction_unverified" in codes(verdict, "manifest_reproduced")


def test_only_engine_verifiers_import_the_seal() -> None:
    allowed = {"release_provenance", "release_limits", "release_control", "release_ledger"}
    allowed |= {"release_sources", "release_inventory", "release_plan", "release_quarantine_run"}
    allowed |= {"publication_authorize"}
    importers = set()
    for path in Path("src/lovspor").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "issue_provenance" for alias in node.names
            ):
                importers.add(path.stem)
    assert importers <= allowed


@pytest.mark.parametrize("module", NEW_MODULES)
def test_ar1b_modules_have_no_execution_capability(module: str) -> None:
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


# --- one trust root ----------------------------------------------------------------------


def authorized_request(tmp_path: Path, root: TrustRoot | None = None) -> ReleaseRequest:
    built, request, made = authorized(tmp_path)
    offers = [offer(built, authorization=request)]
    manifest = build_release(offers, built.artifacts, made)
    raw_manifest = release_text(manifest).encode()
    return ReleaseRequest(
        intent(manifest), raw_manifest, tuple(offers), built.artifacts, root or made, CHECKED_AT
    )


def test_one_resolved_root_reaches_every_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = authorized_request(tmp_path)
    seen: list[TrustRoot | None] = []

    def spy(name: str) -> Any:
        real = getattr(release_sources, name)

        def recorded(*args: Any) -> Any:
            seen.append(next(a for a in args if isinstance(a, TrustRoot)))
            return real(*args)

        return recorded

    for name in (
        "verify_release",
        "verify_authorization",
        "verify_limits",
        "verify_control",
        "registry_of",
        "publisher_of",
    ):
        monkeypatch.setattr(release_sources, name, spy(name))
    evidence = collect_evidence(request, ReleaseSources())
    assert len(seen) >= 6
    assert all(item is request.root for item in seen)
    assert evidence.registry.trust_root_sha256 == request.root.sha256  # type: ignore[union-attr]


def test_without_a_root_the_engine_root_is_resolved_once_explicitly() -> None:
    resolved = resolve_trust_root(None)
    assert resolved.sha256 == load_trust_root().sha256
    assert resolved.origin == "engine_pinned"
    assert resolve_trust_root(resolved) is resolved


def test_a_root_conflicting_with_the_stored_authorization_is_refused(
    tmp_path: Path, keys: Keys
) -> None:
    other = root_of(tmp_path / "other", keys)
    request = authorized_request(tmp_path, other)
    with pytest.raises(ForarbeiderRefusedError, match="trust root conflict"):
        collect_evidence(request, ReleaseSources())


def test_the_engine_root_is_not_silently_substituted_for_a_stored_one(tmp_path: Path) -> None:
    request = authorized_request(tmp_path)._replace(root=None)
    with pytest.raises(ForarbeiderRefusedError, match="trust root conflict"):
        collect_evidence(request, ReleaseSources())


def test_evidence_from_another_root_is_refused_by_the_gates(root: TrustRoot, keys: Keys) -> None:
    history = History(keys)
    history.add("resume")
    verdict = evaluated(limits=sealed_limits(root, keys), control=state(root, history.records))
    assert "trust_root_conflict" in codes(verdict, "authorization")
    assert verdict.status == "refused"


def test_collected_evidence_is_sealed_and_still_blocked(tmp_path: Path) -> None:
    request = authorized_request(tmp_path)
    evidence = collect_evidence(request, ReleaseSources())
    assert authentic(evidence.registry, "signer_registry")
    assert authentic(evidence.publisher, "publisher_identity")
    assert authentic(evidence.reproduction, "manifest_reproduction")
    assert all(authentic(item, "policy_approval") for item in evidence.approvals)
    assert evidence.reproduction.verification.status == "reproduced"
    assert (evidence.limits.status, evidence.control.state) == ("missing", "stopped")
    verdict = evaluate_release(evidence)
    assert verdict.release_eligible is False
    assert "release_stopped" in codes(verdict, "stop_control")
    assert "trust_root_unverified" not in codes(verdict, "authorization")
