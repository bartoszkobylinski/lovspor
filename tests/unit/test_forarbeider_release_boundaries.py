"""Boundary regressions for AR1's documented trust and budget contracts."""

from typing import get_args

import pytest

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_contract_models import (
    TRUST_BOUNDARIES,
    BudgetUsage,
    EvidenceStatus,
    LimitsEvidence,
    SourceKind,
    trusted,
)
from lovspor.forarbeider.release_gates import parse_verdict, verdict_text
from tests.unit.test_forarbeider_release_gates import (
    baseline,
    codes,
    constraints,
    evaluated,
    intent,
    ledger_evidence,
    limits_evidence,
)


@pytest.mark.parametrize("status", get_args(EvidenceStatus))
@pytest.mark.parametrize("source", [None, *get_args(SourceKind)])
def test_only_implemented_independent_verifiers_are_trusted(
    status: EvidenceStatus, source: SourceKind | None
) -> None:
    # AR1b and AR2 document exactly these implemented verifiers; even another kind's
    # implemented source must not cross a trust boundary.
    implemented = {
        ("signer_registry", "engine_trust_root"),
        ("publisher_identity", "engine_trust_root"),
        ("policy_approval", "owner_signed_record"),
        ("manifest_reproduction", "engine_release_verifier"),
        ("publication_limits", "owner_signed_policy_limits"),
        ("release_control", "owner_signed_control"),
        ("budget_ledger", "runner_budget_ledger"),
        ("document_inventory", "owner_signed_inventory"),
        ("batch_plan", "engine_release_planner"),
    }
    for boundary in TRUST_BOUNDARIES:
        assert trusted(boundary.evidence, status, source) == (
            status == "verified" and (boundary.evidence, source) in implemented
        )


@pytest.mark.parametrize("resource", ["documents", "files", "bytes", "commits"])
@pytest.mark.parametrize("excess", [0, 1])
def test_daily_budget_accepts_equality_and_rejects_one_over(resource: str, excess: int) -> None:
    evidence = baseline()
    batch = evidence.intent.batch
    planned = {
        "documents": batch.documents,
        "files": batch.files,
        "bytes": batch.bytes,
        "commits": 1,
    }
    # Nonzero consumed usage proves the limit covers consumed + planned,
    # including the one commit a release would add.
    used = BudgetUsage(documents=3, files=3, bytes=3, commits=3)
    limits = constraints(**{f"max_{name}_per_day": amount + 3 for name, amount in planned.items()})
    used = used.model_copy(update={resource: 3 + excess})
    verdict = evaluated(
        limits=limits_evidence(constraints=limits),
        ledger=ledger_evidence(committed=used),
    )
    # The baseline has no plan, so its stated batch size is unverified (AR2).
    expected = {"budget_ledger_not_anchored", "batch_size_unverified"}
    if excess:
        expected.add(f"budget_{resource}_exceeded")
    assert codes(verdict, "constraints") == expected


@pytest.mark.parametrize("missing_limits", [False, True])
@pytest.mark.parametrize("resource,limit", [("files", 100), ("bytes", 25_000_000)])
@pytest.mark.parametrize("excess", [0, 1])
def test_hard_batch_caps_apply_even_without_signed_limits(
    missing_limits: bool, resource: str, limit: int, excess: int
) -> None:
    evidence = baseline()
    batch = evidence.intent.batch.model_copy(update={resource: limit + excess})
    limits = (
        LimitsEvidence(status="missing", source=None, constraints=None, reasons=())
        if missing_limits
        else evidence.limits
    )
    verdict = evaluated(intent=intent(evidence.manifest, batch=batch), limits=limits)
    assert (f"batch_{resource}_exceeded" in codes(verdict, "constraints")) == bool(excess)
    if missing_limits:
        assert "limits_missing" in codes(verdict, "constraints")


@pytest.mark.parametrize("raw", [b"", b"{", b"[]", b"null", b"\xff"])
def test_malformed_verdict_is_a_typed_schema_refusal(raw: bytes) -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        parse_verdict(raw)
    assert caught.value.reason == RefusalReason.SCHEMA_INVALID


@pytest.mark.parametrize("change", ["missing_gate", "reordered_gates", "wrong_status"])
def test_canonical_verdict_cannot_hide_or_reclassify_gate_results(change: str) -> None:
    verdict = evaluated()
    if change == "missing_gate":
        forged = verdict.model_copy(update={"gates": verdict.gates[1:]})
    elif change == "reordered_gates":
        forged = verdict.model_copy(update={"gates": tuple(reversed(verdict.gates))})
    else:
        assert verdict.status == "blocked"
        forged = verdict.model_copy(update={"status": "refused"})
    with pytest.raises(ForarbeiderRefusedError) as caught:
        parse_verdict(verdict_text(forged).encode())
    assert caught.value.reason == RefusalReason.SCHEMA_INVALID
