"""AR1: automatic release contracts and fail-closed safety gates (#738; §9, D6, D10, D11).

Synthetic only: the invented Prop. 901 L of the version fixtures, throwaway
keys in ``tmp_path`` and hand-made evidence records. Nothing here fetches,
signs with a real key, writes a release or publishes.
"""

import ast
import re
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_authorization_models import Governance
from lovspor.forarbeider.policy_trust import load_trust_root
from lovspor.forarbeider.release_contract_models import (
    EXECUTION_PREREQUISITES,
    GATES,
    LEGAL_QUESTIONS,
    TRUST_BOUNDARIES,
    AnchorEvidence,
    ApprovalEvidence,
    BatchSize,
    BudgetUsage,
    ControlEvidence,
    DocumentInventory,
    HeadAnchor,
    InventoryEvidence,
    InventoryScope,
    LedgerEvidence,
    LegalAssessmentEvidence,
    LimitsEvidence,
    PublisherIdentity,
    ReleaseEvidence,
    ReleaseExecution,
    ReleaseGateVerdict,
    ReleaseIntent,
    ReproductionEvidence,
    SignerRegistry,
)
from lovspor.forarbeider.release_gates import (
    evaluate_release,
    not_executed,
    parse_verdict,
    verdict_text,
)
from lovspor.forarbeider.release_limits_models import ReleaseConstraints
from lovspor.forarbeider.release_provenance import issue_provenance
from lovspor.forarbeider.release_sources import (
    ReleaseRequest,
    ReleaseSources,
    Unavailable,
    collect_evidence,
    registry_of,
)
from lovspor.forarbeider.version_lineage import sha
from lovspor.forarbeider.version_release import (
    build_release,
    release_text,
    verify_release,
)
from lovspor.forarbeider.version_release_models import (
    Blocker,
    PolicyReference,
    ReleaseManifest,
    ReleaseVerification,
)
from tests.unit.forarbeider_authorization_support import policy as synthetic_policy
from tests.unit.forarbeider_lineage_support import LATER, Chain, chain, changed, original
from tests.unit.test_forarbeider_version_release import (
    FIRST,
    SECOND,
    authorized,
    offer,
    renamed,
)

BOT = "test-bot"
BOT_KEY = "SHA256:" + "b" * 43
OWNER_KEY = "SHA256:" + "o" * 43
SOURCE_MODULES = ("release_contract_models", "release_gates", "release_sources")
ROOT_SHA = "c" * 64
LIMITS_SHA = "a" * 64
CHECKED_AT = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
ZERO = BudgetUsage(documents=0, files=0, bytes=0, commits=0)


#: Records as the engine verifier for a kind would hand them over (test-only).
sealed = issue_provenance


def reproduced(verification: ReleaseVerification) -> ReproductionEvidence:
    return sealed(ReproductionEvidence(verification=verification), "manifest_reproduction")


# --- synthetic evidence --------------------------------------------------------------


def evaluated_chain(source: Any) -> Chain:
    built = Chain()
    built.add(source, synthetic_policy())
    return built


@cache
def two_documents() -> tuple[ReleaseManifest, tuple[Chain, Chain]]:
    first, second = evaluated_chain(original()), evaluated_chain(renamed(original(), 2))
    offers = [offer(first), offer(second, SECOND)]
    return build_release(offers, first.artifacts | second.artifacts), (first, second)


def policy_reference() -> PolicyReference:
    policy = two_documents()[0].content.documents[0].policy
    assert policy is not None
    return policy


@cache
def baseline() -> ReleaseEvidence:
    """The strongest evidence the contracts can express today, every source still untrusted."""
    manifest, chains = two_documents()
    offers = [offer(chains[0]), offer(chains[1], SECOND)]
    artifacts = chains[0].artifacts | chains[1].artifacts
    reproduction = verify_release(release_text(manifest).encode(), offers, artifacts)
    ids = (FIRST, SECOND)
    return ReleaseEvidence(
        intent=intent(manifest),
        manifest=manifest,
        reproduction=reproduced(reproduction),
        inventory=inventory(ids),
        anchors=tuple(
            anchor(b.document_id, b.lineage_head_sha256, b.lineage_entries)
            for b in manifest.bindings
        ),
        registry=sealed(
            SignerRegistry(
                trust_root_sha256=ROOT_SHA, origin="engine_pinned", owner_keys=1, bot_keys=1
            ),
            "signer_registry",
        ),
        publisher=publisher(),
        approvals=(approval(),),
        legal=LegalAssessmentEvidence(
            status="verified",
            source="independent_legal_assessment",
            assessment_sha256="d" * 64,
            reference_stated="synthetic assessment reference",
            reasons=(),
        ),
        limits=limits_evidence(),
        ledger=ledger_evidence(),
        control=sealed(
            ControlEvidence(
                status="verified",
                source="owner_signed_control",
                reasons=(),
                policy_id=policy_reference().policy_id,
                state="not_stopped",
                active_stops=(),
                records=1,
                head_sha256="1" * 64,
                trust_root_sha256=ROOT_SHA,
            ),
            "release_control",
        ),
    )


def limits_evidence(**changes: Any) -> LimitsEvidence:
    fields: dict[str, Any] = {
        "status": "verified",
        "source": "owner_signed_policy_limits",
        "constraints": constraints(),
        "policy": policy_reference(),
        "limits_id": "limits-001",
        "limits_sha256": LIMITS_SHA,
        "trust_root_sha256": ROOT_SHA,
        "signer_fingerprint": OWNER_KEY,
        "signer_role": "owner_policy",
        "reasons": (),
    }
    return sealed(LimitsEvidence(**(fields | changes)), "publication_limits")


def ledger_evidence(**changes: Any) -> LedgerEvidence:
    fields: dict[str, Any] = {
        "status": "verified",
        "source": "runner_budget_ledger",
        "reasons": (),
        "day": CHECKED_AT.date(),
        "limits_sha256": LIMITS_SHA,
        "committed": ZERO,
        "reserved": ZERO,
        "entries": 1,
        "head_sha256": "2" * 64,
    }
    return sealed(LedgerEvidence(**(fields | changes)), "budget_ledger")


def intent(manifest: ReleaseManifest, **changes: Any) -> ReleaseIntent:
    fields: dict[str, Any] = {
        "intent_id": "intent-001",
        "release_id": manifest.release_id,
        "bindings_sha256": manifest.bindings_sha256,
        "document_ids": tuple(d.document_id for d in manifest.content.documents),
        "publisher_principal": BOT,
        "batch": BatchSize(documents=len(manifest.content.documents), files=4, bytes=20_000),
    }
    return ReleaseIntent(**(fields | changes))


def inventory(ids: tuple[str, ...], **changes: Any) -> InventoryEvidence:
    record = DocumentInventory(
        inventory_id="inventory-001",
        scope=InventoryScope(document_types=("prop-l",)),
        document_ids=ids,
    )
    fields: dict[str, Any] = {
        "status": "verified",
        "source": "owner_signed_inventory",
        "inventory": record,
        "inventory_sha256": "e" * 64,
        "reasons": (),
    }
    return InventoryEvidence(**(fields | changes))


def anchor(document_id: str, head: str | None, entries: int, **changes: Any) -> AnchorEvidence:
    record = (
        None
        if head is None
        else HeadAnchor(document_id=document_id, head_sha256=head, lineage_entries=entries)
    )
    fields: dict[str, Any] = {
        "document_id": document_id,
        "status": "verified",
        "source": "published_release_attestation",
        "anchor": record,
        "reasons": (),
    }
    return AnchorEvidence(**(fields | changes))


def publisher(**changes: Any) -> PublisherIdentity:
    fields: dict[str, Any] = {
        "principal": BOT,
        "key_fingerprint": BOT_KEY,
        "role": "bot_execution",
        "namespaces": ("lovspor-forarbeider-publication", "lovspor-forarbeider-release"),
    }
    return sealed(PublisherIdentity(**(fields | changes)), "publisher_identity")


def approval(**changes: Any) -> ApprovalEvidence:
    fields: dict[str, Any] = {
        "policy": policy_reference(),
        "policy_approval": "verified",
        "automated_publication": "verified",
        "approver_fingerprints": (OWNER_KEY,),
        "approver_roles": ("owner_policy",),
        "max_documents_per_batch": 100,
        "legal_reference_stated": "synthetic assessment reference",
        "governance": Governance(),
    }
    return sealed(ApprovalEvidence(**(fields | changes)), "policy_approval")


def constraints(**changes: Any) -> ReleaseConstraints:
    fields: dict[str, Any] = {
        "max_documents_per_batch": 100,
        "max_files_per_batch": 100,
        "max_bytes_per_batch": 25_000_000,
        "max_documents_per_day": 500,
        "max_files_per_day": 2_000,
        "max_bytes_per_day": 200_000_000,
        "max_commits_per_day": 20,
    }
    return ReleaseConstraints(**(fields | changes))


def evaluated(**changes: Any) -> ReleaseGateVerdict:
    return evaluate_release(baseline().model_copy(update=changes))


def codes(verdict: ReleaseGateVerdict, gate: str) -> set[str]:
    result = next(item for item in verdict.gates if item.gate == gate)
    return {reason.code for reason in result.reasons}


def documents_of(verdict: ReleaseGateVerdict, gate: str, code: str) -> set[str | None]:
    result = next(item for item in verdict.gates if item.gate == gate)
    return {reason.document_id for reason in result.reasons if reason.code == code}


def status_of(verdict: ReleaseGateVerdict, gate: str) -> str:
    return next(item.status for item in verdict.gates if item.gate == gate)


# --- the verdict as a whole ----------------------------------------------------------


def test_every_gate_is_reported_in_a_fixed_order_and_nothing_is_eligible() -> None:
    verdict = evaluate_release(baseline())
    assert [item.gate for item in verdict.gates] == list(GATES)
    assert verdict.status == "blocked"
    assert verdict.failed_gates
    assert (verdict.authorized, verdict.release_eligible, verdict.published) == (
        False,
        False,
        False,
    )


def test_even_the_strongest_evidence_today_stays_blocked() -> None:
    verdict = evaluate_release(baseline())
    assert codes(verdict, "legal_assessment") >= {"d10_not_independently_accepted"}
    assert "d11_not_accepted" in codes(verdict, "automated_publication")
    assert codes(verdict, "execution_controls") == set(EXECUTION_PREREQUISITES)
    assert "anchor_source_untrusted" in codes(verdict, "lineage_anchor")
    assert "inventory_source_untrusted" in codes(verdict, "inventory")


def test_the_verdict_binds_intent_evidence_and_release() -> None:
    evidence = baseline()
    verdict = evaluate_release(evidence)
    assert verdict.release_id == evidence.manifest.release_id
    assert verdict.intent_sha256 != verdict.evidence_sha256
    other = evaluated(intent=intent(evidence.manifest, intent_id="intent-002"))
    assert other.intent_sha256 != verdict.intent_sha256
    assert other.evidence_sha256 != verdict.evidence_sha256


def test_evaluation_is_deterministic_and_the_verdict_round_trips() -> None:
    first, second = evaluate_release(baseline()), evaluate_release(baseline())
    assert verdict_text(first) == verdict_text(second)
    assert parse_verdict(verdict_text(first).encode()) == first


def test_a_verdict_claiming_eligibility_or_approval_does_not_parse() -> None:
    text = verdict_text(evaluate_release(baseline()))
    for field in ("release_eligible", "authorized", "published"):
        forged = text.replace(f'"{field}": false', f'"{field}": true')
        with pytest.raises(ForarbeiderRefusedError):
            parse_verdict(forged.encode())


def test_a_verdict_hiding_a_failed_gate_does_not_parse() -> None:
    verdict = evaluate_release(baseline())
    with pytest.raises(ValidationError):
        ReleaseGateVerdict.model_validate(
            verdict.model_dump() | {"failed_gates": verdict.failed_gates[1:]}
        )
    passed = [g.model_dump() | {"status": "passed", "reasons": ()} for g in verdict.gates]
    with pytest.raises(ValidationError):
        ReleaseGateVerdict.model_validate(verdict.model_dump() | {"gates": passed})


def test_a_non_canonical_verdict_is_refused() -> None:
    text = verdict_text(evaluate_release(baseline()))
    with pytest.raises(ForarbeiderRefusedError):
        parse_verdict(text.replace("\n", "\n ").encode())


# --- the release contract ------------------------------------------------------------


@pytest.mark.parametrize("flag", ["eligible", "approved", "published", "release_eligible"])
def test_an_intent_cannot_carry_an_eligibility_or_approval_flag(flag: str) -> None:
    manifest, _ = two_documents()
    with pytest.raises(ValidationError):
        intent(manifest, **{flag: True})


def test_an_intent_lists_its_documents_once_in_order() -> None:
    manifest, _ = two_documents()
    with pytest.raises(ValidationError):
        intent(manifest, document_ids=(SECOND, FIRST))
    with pytest.raises(ValidationError):
        intent(manifest, document_ids=(FIRST, FIRST))


def test_a_legal_assessment_cannot_be_recorded_as_accepted() -> None:
    accepted = baseline().legal.model_dump() | {"independently_accepted": True}
    with pytest.raises(ValidationError):
        LegalAssessmentEvidence.model_validate(accepted)
    assert {"rights_section_14", "rights_section_24", "rights_section_29", "privacy"} <= set(
        LEGAL_QUESTIONS
    )


def test_d11_governance_cannot_be_recorded_as_accepted() -> None:
    with pytest.raises(ValidationError):
        approval(governance={"d11_automated_publication": "accepted"})


def test_batch_limits_cannot_exceed_the_hard_bounds() -> None:
    with pytest.raises(ValidationError):
        constraints(max_files_per_batch=101)
    with pytest.raises(ValidationError):
        constraints(max_bytes_per_batch=25_000_001)


def test_verified_evidence_needs_its_record_and_its_source() -> None:
    with pytest.raises(ValidationError):
        inventory((FIRST,), inventory=None)
    with pytest.raises(ValidationError):
        anchor(FIRST, "f" * 64, 1, source=None)


def test_every_trust_boundary_names_independent_sources_never_the_request() -> None:
    kinds = {boundary.evidence for boundary in TRUST_BOUNDARIES}
    assert kinds >= {
        "lineage_head_anchor",
        "document_inventory",
        "signer_registry",
        "policy_approval",
        "legal_assessment",
        "publication_limits",
    }
    for boundary in TRUST_BOUNDARIES:
        assert "api_request" not in boundary.accepted_sources
        assert boundary.accepted_sources


def test_the_execution_record_has_no_executed_state() -> None:
    verdict = evaluate_release(baseline())
    record = not_executed(verdict)
    assert record.status == "not_executed"
    assert record.published is False
    with pytest.raises(ValidationError):
        ReleaseExecution.model_validate(record.model_dump() | {"status": "executed"})


# --- inventory -----------------------------------------------------------------------


def test_missing_inventory_blocks() -> None:
    missing = InventoryEvidence(
        status="missing", source=None, inventory=None, inventory_sha256=None, reasons=("x",)
    )
    verdict = evaluated(inventory=missing)
    assert "inventory_missing" in codes(verdict, "inventory")
    assert verdict.status == "blocked"


def test_an_inventory_from_the_request_is_never_trusted() -> None:
    verdict = evaluated(inventory=inventory((FIRST, SECOND), source="api_request"))
    assert "inventory_source_untrusted" in codes(verdict, "inventory")


def test_an_inventory_document_absent_from_the_manifest_fails() -> None:
    third = "regjeringen-9000003"
    verdict = evaluated(inventory=inventory((FIRST, SECOND, third)))
    assert documents_of(verdict, "inventory", "inventory_document_missing") == {third}


def test_a_manifest_document_outside_the_inventory_is_refused() -> None:
    verdict = evaluated(inventory=inventory((FIRST,)))
    assert documents_of(verdict, "inventory", "document_outside_inventory") == {SECOND}
    assert verdict.status == "refused"


# --- lineage anchors and freshness ---------------------------------------------------


def test_a_missing_head_anchor_blocks_its_document() -> None:
    evidence = baseline()
    verdict = evaluated(anchors=evidence.anchors[:1])
    assert documents_of(verdict, "lineage_anchor", "anchor_missing") == {SECOND}
    assert documents_of(verdict, "freshness", "freshness_unverifiable") == {SECOND}


def test_a_head_anchor_from_the_request_is_never_trusted() -> None:
    evidence = baseline()
    forged = tuple(a.model_copy(update={"source": "api_request"}) for a in evidence.anchors)
    verdict = evaluated(anchors=forged)
    assert documents_of(verdict, "lineage_anchor", "anchor_source_untrusted") == {FIRST, SECOND}


def test_a_forged_anchor_naming_another_head_is_refused() -> None:
    evidence = baseline()
    binding = evidence.manifest.bindings[0]
    forged = anchor(FIRST, "f" * 64, binding.lineage_entries)
    verdict = evaluated(anchors=(forged, evidence.anchors[1]))
    assert documents_of(verdict, "lineage_anchor", "anchor_head_mismatch") == {FIRST}
    assert verdict.status == "refused"


def test_two_anchors_for_one_document_are_contradictory() -> None:
    evidence = baseline()
    other = anchor(FIRST, "f" * 64, 9)
    verdict = evaluated(anchors=(*evidence.anchors, other))
    assert documents_of(verdict, "lineage_anchor", "anchor_conflict") == {FIRST}
    assert verdict.status == "refused"


def test_an_anchor_for_a_document_outside_the_release_is_refused() -> None:
    evidence = baseline()
    stray = anchor("regjeringen-9000009", "f" * 64, 1)
    verdict = evaluated(anchors=(*evidence.anchors, stray))
    assert "anchor_for_unknown_document" in codes(verdict, "lineage_anchor")


def test_an_anchor_ahead_of_the_manifest_makes_the_version_stale() -> None:
    evidence = baseline()
    binding = evidence.manifest.bindings[0]
    ahead = anchor(FIRST, "f" * 64, binding.lineage_entries + 1)
    verdict = evaluated(anchors=(ahead, evidence.anchors[1]))
    assert documents_of(verdict, "freshness", "version_stale") == {FIRST}


def test_a_manifest_ahead_of_its_anchor_is_unanchored() -> None:
    built = chain(original(), changed(LATER))
    offered = offer(built)
    manifest = build_release([offered], built.artifacts)
    reproduction = verify_release(release_text(manifest).encode(), [offered], built.artifacts)
    verdict = evaluated(
        intent=intent(manifest),
        manifest=manifest,
        reproduction=reproduced(reproduction),
        inventory=inventory((FIRST,)),
        anchors=(anchor(FIRST, sha(built.raws[0]), 1),),
    )
    assert documents_of(verdict, "lineage_anchor", "head_ahead_of_anchor") == {FIRST}
    assert verdict.status == "refused"


def test_an_incomplete_lineage_fails_lineage_and_evidence() -> None:
    built = chain(original(), changed(LATER))
    gapped = offer(built, entries=tuple(built.raws[1:]), claimed_head=None)
    manifest = build_release([gapped], built.artifacts)
    raw = release_text(manifest).encode()
    reproduction = verify_release(raw, [gapped], built.artifacts)
    verdict = evaluated(
        intent=intent(manifest),
        manifest=manifest,
        reproduction=reproduced(reproduction),
        inventory=inventory((FIRST,)),
        anchors=(anchor(FIRST, sha(built.raws[-1]), 2),),
    )
    assert "lineage_incomplete" in codes(verdict, "lineage_anchor")
    assert "document_excluded" in codes(verdict, "source_evidence")


def test_a_returning_older_version_is_a_rollback() -> None:
    evidence = baseline()
    documents = evidence.manifest.content.documents
    cycle = Blocker(code="lineage:version_cycle", category="integrity")
    blockers = sorted((*documents[0].blockers, cycle), key=lambda b: (b.category, b.code))
    flagged = documents[0].model_copy(
        update={"status": "excluded", "version_id": None, "blockers": tuple(blockers)}
    )
    content = evidence.manifest.content.model_copy(update={"documents": (flagged, documents[1])})
    verdict = evaluated(manifest=evidence.manifest.model_copy(update={"content": content}))
    assert documents_of(verdict, "freshness", "version_rolled_back") == {FIRST}
    assert verdict.status == "refused"


# --- the FC8b manifest ---------------------------------------------------------------


def test_a_changed_manifest_is_not_reproduced() -> None:
    evidence = baseline()
    manifest, chains = two_documents()
    altered = dict(chains[0].artifacts | chains[1].artifacts)
    key = next(k for k in chains[0].artifacts if k != sha(chains[0].raws[-1]))
    altered[key] = altered[key] + b" "
    offers = [offer(chains[0]), offer(chains[1], SECOND)]
    reproduction = verify_release(release_text(manifest).encode(), offers, altered)
    verdict = evaluated(reproduction=reproduced(reproduction))
    assert "reproduction_invalid" in codes(verdict, "manifest_reproduced")
    assert verdict.status == "refused"
    assert evidence.reproduction.verification.status == "reproduced"


def test_an_intent_naming_another_release_is_refused() -> None:
    manifest, _ = two_documents()
    verdict = evaluated(intent=intent(manifest, release_id="0" * 64, bindings_sha256="1" * 64))
    assert {"intent_release_mismatch", "intent_bindings_mismatch"} <= codes(
        verdict, "manifest_reproduced"
    )
    assert verdict.status == "refused"


def test_an_intent_naming_other_documents_is_refused() -> None:
    manifest, _ = two_documents()
    verdict = evaluated(intent=intent(manifest, document_ids=(FIRST,)))
    assert "intent_documents_mismatch" in codes(verdict, "manifest_reproduced")


def test_a_reproduction_of_another_manifest_does_not_count() -> None:
    evidence = baseline()
    other = reproduced(
        evidence.reproduction.verification.model_copy(
            update={"offered_release_id": "0" * 64, "rebuilt_release_id": "0" * 64}
        )
    )
    verdict = evaluated(reproduction=other)
    assert "reproduction_of_other_manifest" in codes(verdict, "manifest_reproduced")


def test_fc8b_gates_are_carried_into_their_own_gates() -> None:
    verdict = evaluate_release(baseline())
    assert "fc7_rules_not_satisfied" in codes(verdict, "policy_rules")
    assert "fc7b_authorization_missing" in codes(verdict, "authorization")
    assert {"rights_or_privacy_unresolved", "publication_incomplete"} <= codes(
        verdict, "privacy_rights"
    )
    assert documents_of(verdict, "privacy_rights", "publication_incomplete") == {FIRST, SECOND}


def test_an_unevaluated_document_has_unassessed_rights() -> None:
    built = chain(original())
    offered = offer(built)
    manifest = build_release([offered], built.artifacts)
    reproduction = verify_release(release_text(manifest).encode(), [offered], built.artifacts)
    verdict = evaluated(
        intent=intent(manifest),
        manifest=manifest,
        reproduction=reproduced(reproduction),
        inventory=inventory((FIRST,)),
        anchors=(anchor(FIRST, sha(built.raws[-1]), 1),),
        approvals=(),
    )
    assert "policy_not_evaluated" in codes(verdict, "policy_rules")
    assert "rights_or_privacy_not_assessed" in codes(verdict, "privacy_rights")


# --- signers, publisher and approvals ------------------------------------------------


def test_an_unpinned_signer_registry_blocks_authorization_and_publisher() -> None:
    evidence = baseline()
    unpinned = evidence.registry.model_copy(update={"origin": "unpinned"})
    verdict = evaluated(registry=unpinned)
    assert "trust_root_not_engine_pinned" in codes(verdict, "authorization")
    assert "publisher_registry_unpinned" in codes(verdict, "publisher")


def test_the_shipped_signer_registry_lists_no_key() -> None:
    registry = registry_of(load_trust_root())
    assert (registry.origin, registry.owner_keys, registry.bot_keys) == ("engine_pinned", 0, 0)
    verdict = evaluated(registry=registry)
    assert "trust_root_has_no_owner_key" in codes(verdict, "authorization")


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"role": None}, "publisher_not_in_registry"),
        ({"role": "owner_policy"}, "publisher_holds_owner_key"),
        (
            {"namespaces": ("lovspor-forarbeider-policy", "lovspor-forarbeider-release")},
            "publisher_scope_exceeded",
        ),
        ({"key_fingerprint": None}, "publisher_key_missing"),
        ({"principal": "someone-else"}, "publisher_mismatch"),
    ],
)
def test_an_invalid_publisher_role_or_scope_fails(changes: dict[str, Any], code: str) -> None:
    verdict = evaluated(publisher=publisher(**changes))
    assert code in codes(verdict, "publisher")


def test_the_bot_cannot_approve_its_own_publication() -> None:
    by_bot = approval(approver_fingerprints=(BOT_KEY,), approver_roles=("bot_execution",))
    verdict = evaluated(approvals=(by_bot,))
    assert {"publisher_self_approval", "approval_signed_by_bot"} <= codes(verdict, "publisher")
    assert verdict.status == "refused"


def test_a_missing_d11_approval_blocks() -> None:
    verdict = evaluated(approvals=())
    assert "d11_not_accepted" in codes(verdict, "automated_publication")


def test_an_unverified_d11_approval_fails() -> None:
    verdict = evaluated(approvals=(approval(automated_publication="missing"),))
    assert "d11_approval_missing" in codes(verdict, "automated_publication")


def test_two_approvals_for_one_policy_are_contradictory() -> None:
    verdict = evaluated(approvals=(approval(), approval(max_documents_per_batch=1)))
    assert "approval_conflict" in codes(verdict, "automated_publication")
    assert verdict.status == "refused"


def test_a_batch_above_the_approved_document_limit_fails() -> None:
    verdict = evaluated(approvals=(approval(max_documents_per_batch=1),))
    assert "batch_documents_exceeded" in codes(verdict, "automated_publication")


# --- D10 -----------------------------------------------------------------------------


def test_missing_legal_assessment_blocks() -> None:
    missing = LegalAssessmentEvidence(
        status="missing", source=None, assessment_sha256=None, reference_stated=None, reasons=("x",)
    )
    verdict = evaluated(legal=missing, approvals=(approval(legal_reference_stated=None),))
    assert {
        "d10_not_independently_accepted",
        "legal_assessment_missing",
        "legal_reference_missing",
    } <= codes(verdict, "legal_assessment")


def test_a_stated_legal_reference_never_counts_as_acceptance() -> None:
    verdict = evaluate_release(baseline())
    assert "d10_not_independently_accepted" in codes(verdict, "legal_assessment")
    assert status_of(verdict, "legal_assessment") == "failed"


# --- limits and budgets --------------------------------------------------------------


def test_missing_limits_and_ledger_block() -> None:
    limits = LimitsEvidence(status="missing", source=None, constraints=None, reasons=("x",))
    ledger = LedgerEvidence(status="missing", source=None, reasons=("x",))
    verdict = evaluated(limits=limits, ledger=ledger)
    assert {"limits_missing", "budget_ledger_missing"} <= codes(verdict, "constraints")


def test_an_exceeded_batch_limit_fails() -> None:
    manifest, _ = two_documents()
    big = BatchSize(documents=2, files=101, bytes=25_000_001)
    verdict = evaluated(intent=intent(manifest, batch=big))
    assert {"batch_files_exceeded", "batch_bytes_exceeded"} <= codes(verdict, "constraints")


def test_a_batch_above_the_signed_limits_fails() -> None:
    limits = limits_evidence(
        constraints=constraints(max_files_per_batch=3, max_bytes_per_batch=100)
    )
    verdict = evaluated(limits=limits)
    assert {"batch_files_exceeded", "batch_bytes_exceeded"} <= codes(verdict, "constraints")


def test_an_exceeded_daily_budget_fails() -> None:
    used = BudgetUsage(documents=499, files=1_999, bytes=199_990_000, commits=20)
    ledger = ledger_evidence(committed=used)
    verdict = evaluated(ledger=ledger)
    assert {
        "budget_documents_exceeded",
        "budget_files_exceeded",
        "budget_bytes_exceeded",
        "budget_commits_exceeded",
    } <= codes(verdict, "constraints")


def test_a_batch_size_contradicting_the_intent_is_refused() -> None:
    manifest, _ = two_documents()
    verdict = evaluated(intent=intent(manifest, batch=BatchSize(documents=5, files=4, bytes=1)))
    assert "batch_size_contradicts_intent" in codes(verdict, "constraints")
    assert verdict.status == "refused"


# --- collecting evidence through the trust boundaries --------------------------------


def test_unavailable_sources_report_missing_never_verified() -> None:
    sources = Unavailable()
    assert sources.anchor(FIRST).status == "missing"
    assert sources.inventory().records == ()
    assert sources.inventory().catalogue == ()
    assert sources.assessment().status == "missing"
    assert sources.limits().records == ()
    assert sources.history() == ()
    assert sources.entries() == ()


def test_collected_evidence_is_blocked_and_replays_deterministically(tmp_path: Path) -> None:
    built, request, root = authorized(tmp_path)
    offers = [offer(built, authorization=request)]
    manifest = build_release(offers, built.artifacts, root)
    raw = release_text(manifest).encode()
    release_request = ReleaseRequest(
        intent(manifest), raw, tuple(offers), built.artifacts, root, CHECKED_AT
    )
    evidence = collect_evidence(release_request, ReleaseSources())
    assert evidence.reproduction.verification.status == "reproduced"
    assert evidence.approvals and evidence.approvals[0].automated_publication == "verified"
    assert evidence.registry.origin == "unpinned"
    verdict = evaluate_release(evidence)
    assert verdict.status == "blocked"
    assert "anchor_missing" in codes(verdict, "lineage_anchor")
    assert "inventory_missing" in codes(verdict, "inventory")
    assert "trust_root_not_engine_pinned" in codes(verdict, "authorization")
    again = evaluate_release(collect_evidence(release_request, ReleaseSources()))
    assert verdict_text(again) == verdict_text(verdict)


def test_collecting_refuses_a_manifest_that_does_not_parse(tmp_path: Path) -> None:
    built = chain(original())
    manifest = build_release([offer(built)], built.artifacts)
    raw = release_text(manifest).encode() + b" "
    request = ReleaseRequest(
        intent(manifest), raw, (offer(built),), built.artifacts, None, CHECKED_AT
    )
    with pytest.raises(ForarbeiderRefusedError):
        collect_evidence(request, ReleaseSources())


def test_the_publisher_is_resolved_from_the_registry_not_the_intent(tmp_path: Path) -> None:
    built, request, root = authorized(tmp_path)
    offers = [offer(built, authorization=request)]
    manifest = build_release(offers, built.artifacts, root)
    raw = release_text(manifest).encode()
    claimed = intent(manifest, publisher_principal="test-owner")
    evidence = collect_evidence(
        ReleaseRequest(claimed, raw, tuple(offers), built.artifacts, root, CHECKED_AT),
        ReleaseSources(),
    )
    assert evidence.publisher.role == "owner_policy"
    verdict = evaluate_release(evidence)
    assert "publisher_holds_owner_key" in codes(verdict, "publisher")


# --- no execution capability ---------------------------------------------------------


@pytest.mark.parametrize("module", SOURCE_MODULES)
def test_ar1_modules_have_no_execution_capability(module: str) -> None:
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
    release_writers = ("batch_release", "release_commands", "release_store", "release_checkout")
    assert not {name for name in imported if name.rsplit(".", 1)[-1] in release_writers}
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.ClassDef)}
    words = {w.lower() for n in names for w in re.findall(r"[A-Z]?[a-z]+", n)}
    assert not words & {"publish", "push", "commit", "sign", "apply", "write", "upload", "send"}
