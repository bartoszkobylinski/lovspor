"""AR3: quarantine, failure attribution and safe re-planning (#738; §9.4, §9.6, §9.9).

Synthetic only: invented documents, synthetic bytes, hand-built FC7 decisions
and throwaway keys in pytest's temporary directory. Nothing here fetches,
signs with a real key, writes a ledger outside ``tmp_path`` or publishes.
"""

import ast
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider import release_plan, release_quarantine_run
from lovspor.forarbeider.policy_authorization import SignedRecord
from lovspor.forarbeider.policy_trust import (
    BOT_NAMESPACES,
    OWNER_NAMESPACES,
    POLICY_NAMESPACE,
    QUARANTINE_NAMESPACE,
    TrustRoot,
)
from lovspor.forarbeider.release_attribution import (
    StageFailure,
    attribute_plan,
)
from lovspor.forarbeider.release_contract_models import (
    REFUSING_CODES,
    trusted,
    verified,
)
from lovspor.forarbeider.release_gates import evaluate_release
from lovspor.forarbeider.release_plan import PlanRequest, Prepared, Row, plan_release
from lovspor.forarbeider.release_plan_models import ReleasePlan
from lovspor.forarbeider.release_provenance import issue_provenance
from lovspor.forarbeider.release_quarantine import (
    EMPTY_LEDGER,
    ledger_sha256,
    ledger_text,
    merge,
    metrics_of,
    parse_ledger,
    quarantine_set,
    settle,
    successor_findings,
)
from lovspor.forarbeider.release_quarantine_models import (
    QuarantineEvidence,
    QuarantineFinding,
    QuarantineLedger,
    QuarantineScope,
)
from lovspor.forarbeider.release_quarantine_run import (
    ExceptionMaterial,
    QuarantineOutcome,
    QuarantineRequest,
    run_quarantine,
    verify_exceptions,
)
from lovspor.forarbeider.version_models import SourceState
from lovspor.forarbeider.version_release_models import digest_of
from tests.unit.forarbeider_authorization_support import raw, revocation, scope
from tests.unit.forarbeider_quarantine_support import (
    LATER,
    attachment,
    blocked_row,
    decided_row,
    entry,
    exception,
    owner_signed,
    planned,
    quarantine_root,
)
from tests.unit.forarbeider_release_control_support import (
    BOT,
    CHECKED_AT,
    Keys,
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
    row,
)
from tests.unit.test_forarbeider_release_gates import baseline
from tests.unit.test_forarbeider_release_plan import plan_request, planned_evidence

NEW_MODULES = (
    "release_attribution",
    "release_quarantine",
    "release_quarantine_models",
    "release_quarantine_run",
)
SOURCE_INCOMPLETE = ("source", "source_incomplete")


@pytest.fixture(scope="module")
def keys(tmp_path_factory: pytest.TempPathFactory) -> Keys:
    return make_keys(tmp_path_factory.mktemp("keys"))


@pytest.fixture
def root(tmp_path: Path, keys: Keys) -> TrustRoot:
    return quarantine_root(tmp_path / "root", keys)


def findings_of(plan: ReleasePlan, rows: Sequence[Row], store: Store, **kw: Any) -> Any:
    return attribute_plan(plan, rows, store.artifacts, **kw)


def ledger_after(store: Store, rows: Sequence[Row], numbers: Sequence[int]) -> QuarantineLedger:
    base = planned(store, rows, numbers)
    found = findings_of(base, rows, store)
    assert found.halts == ()
    return merge(EMPTY_LEDGER, found.local, CHECKED_AT, base.plan_id)


def disposition(plan: ReleasePlan, number: int) -> str:
    return next(d.disposition for d in plan.documents if d.document_id == document(number))


# --- failure attribution -------------------------------------------------------------------


def test_a_document_local_failure_is_attributed_to_that_document_only() -> None:
    store = Store()
    rows = [row(store, 1), blocked_row(store, 2, SOURCE_INCOMPLETE), row(store, 3)]
    found = findings_of(planned(store, rows, (1, 2, 3)), rows, store)
    assert found.halts == ()
    (finding,) = found.local
    assert (finding.document_id, finding.code, finding.category) == (
        document(2),
        "source:source_incomplete",
        "completeness",
    )
    assert finding.scope == QuarantineScope(kind="document")
    assert finding.version_id == rows[1].selection.version_id
    assert finding.evidence_sha256s == (rows[1].binding.manifest_sha256,)


@pytest.mark.parametrize(
    ("blocker", "category"),
    [
        (("trust", "head_missing"), "trust"),
        (("policy", "policy_not_evaluated"), "policy"),
        (("policy", "fc7:policy_not_applicable"), "policy"),
        (("authorization", "fc7b_missing_evidence"), "authorization"),
        (("authorization", "fc7b:trust_root_not_engine_pinned"), "authorization"),
        (("integrity", "fc7b_authorization_not_reproduced"), "authorization"),
    ],
)
def test_a_failure_no_document_owns_is_global(blocker: tuple[str, str], category: str) -> None:
    store = Store()
    rows = [row(store, 1), blocked_row(store, 2, blocker)]
    found = findings_of(planned(store, rows, (1, 2)), rows, store)
    assert found.local == ()
    (halt,) = found.halts
    assert (halt.code, halt.category) == (f"{blocker[0]}:{blocker[1]}", category)


@pytest.mark.parametrize(
    "lineage", ["fork", "history_rewritten", "head_mismatch", "parent_missing", "root_missing"]
)
def test_a_rewritten_or_broken_lineage_is_a_repository_failure(lineage: str) -> None:
    store = Store()
    rows = [
        blocked_row(store, 1, ("integrity", "lineage_invalid"), ("integrity", f"lineage:{lineage}"))
    ]
    found = findings_of(planned(store, rows, (1,)), rows, store)
    assert found.local == ()
    assert {(h.code, h.category) for h in found.halts} == {
        (f"integrity:lineage:{lineage}", "repository")
    }


def test_another_policy_is_global_never_local() -> None:
    store = Store()
    other = row(store, 1).selection.policy
    assert other is not None
    foreign = other.model_copy(update={"policy_version": other.policy_version + 1})
    rows = [row(store, 1, policy=foreign)]
    found = findings_of(planned(store, rows, (1,)), rows, store)
    assert found.local == ()
    assert [(h.code, h.category) for h in found.halts] == [("policy_mismatch", "policy")]


@pytest.mark.parametrize(
    "blocker",
    [
        ("integrity", "lineage:never_seen_before"),
        ("source", "source_somewhere_else"),
        ("governance", "d99_new_decision"),
    ],
)
def test_an_unknown_code_is_a_global_failure_of_unknown_category(blocker: tuple[str, str]) -> None:
    store = Store()
    rows = [blocked_row(store, 1, blocker)]
    found = findings_of(planned(store, rows, (1,)), rows, store)
    assert found.local == ()
    assert {h.category for h in found.halts} <= {"unknown", "repository"}
    assert found.halts


def test_a_summary_code_without_its_details_is_ambiguous_and_global() -> None:
    store = Store()
    rows = [blocked_row(store, 1, ("policy", "fc7_unresolved"))]
    found = findings_of(planned(store, rows, (1,)), rows, store)
    assert found.local == ()
    assert [(h.code, h.category) for h in found.halts] == [
        ("unattributed:policy:fc7_unresolved", "unknown")
    ]


def test_pending_owner_decisions_are_neither_quarantined_nor_halting() -> None:
    store = Store()
    pending = [
        ("authorization", f"fc7b:{c}")
        for c in ("d6_signatures_not_accepted", "release_integration_not_implemented")
    ]
    rows = [blocked_row(store, 1, *pending)]
    found = findings_of(planned(store, rows, (1,)), rows, store)
    assert found == ((), ())


def test_spans_parts_and_attachments_come_from_the_bound_decision() -> None:
    store = Store()
    decided = decided_row(
        store,
        1,
        entries=[
            entry("s-1", "unresolved", "privacy"),
            entry("s-2", "deny", "rights:official_text"),
            entry("g-1", "unresolved", "extraction_gap", part_id="p-3"),
            entry("s-4", "allow", "rights:official_text"),
        ],
        attachments=[attachment("one")],
    )
    found = findings_of(planned(store, [decided], (1,)), [decided], store)
    assert found.halts == ()
    seen = {(f.scope.kind, f.scope.ref, f.category, f.code) for f in found.local}
    attachment_ref = next(f.scope.ref for f in found.local if f.scope.kind == "attachment")
    assert seen == {
        ("span", "s-1", "privacy", "span_unresolved:privacy"),
        ("span", "s-2", "rights", "span_deny:rights:official_text"),
        ("part", "p-3", "extraction", "span_unresolved:extraction_gap"),
        ("attachment", attachment_ref, "attachment", "attachment_deny:attachment:embedded"),
    }
    assert all(len(ref) == 64 for ref in [attachment_ref])  # a digest, never the URL
    for finding in found.local:
        assert "regjeringen.no" not in finding.model_dump_json()


def test_an_allowed_span_count_is_never_a_failure_to_quarantine() -> None:
    store = Store()
    decided = decided_row(
        store, 1, entries=[entry("s-1", "allow", "privacy")], counts={"entries_allow": 1}
    )
    found = findings_of(planned(store, [decided], (1,)), [decided], store)
    assert found.local == ()
    assert [h.category for h in found.halts] == ["unknown"]


def test_a_decision_that_contradicts_its_counts_is_global() -> None:
    store = Store()
    decided = decided_row(
        store, 1, entries=[entry("s-1", "unresolved", "privacy")], counts={"entries_unresolved": 2}
    )
    found = findings_of(planned(store, [decided], (1,)), [decided], store)
    assert found.local == ()
    assert [h.category for h in found.halts] == ["unknown"]


def test_an_unreadable_decision_is_global() -> None:
    store = Store()
    decided = decided_row(store, 1, entries=[entry("s-1", "unresolved", "privacy")])
    key = decided.binding.policy_decision_sha256
    assert key is not None
    store.artifacts[key] = b"{}"
    found = findings_of(planned(store, [decided], (1,)), [decided], store)
    assert all(h.category == "unknown" for h in found.halts)
    assert not [f for f in found.local if f.scope.kind == "span"]


def test_an_altered_artifact_is_a_local_integrity_failure() -> None:
    store = Store()
    rows = [row(store, 1), row(store, 2)]
    key = rows[1].selection.source_sha256s[0]
    store.artifacts[key] = store.artifacts[key] + b"x"
    found = findings_of(planned(store, rows, (1, 2)), rows, store)
    assert found.halts == ()
    assert [(f.document_id, f.code, f.category) for f in found.local] == [
        (document(2), "artifact_altered", "integrity")
    ]


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        ("render_failed", "local"),
        ("privacy_detector_anomaly", "local"),
        ("repository_guard_refused", "global"),
        ("source_proof_mismatch", "global"),
        ("publication_authorization_failed", "global"),
        ("brand_new_stage_code", "global"),
    ],
)
def test_a_reported_stage_failure_is_attributed_by_exact_code(code: str, kind: str) -> None:
    store = Store()
    rows = [row(store, 1), row(store, 2)]
    plan = planned(store, rows, (1, 2))
    version = rows[1].selection.version_id
    assert version is not None
    report = StageFailure(
        document_id=document(2), version_id=version, code=code, evidence_sha256s=("c" * 64,)
    )
    found = findings_of(plan, rows, store, reported=[report])
    assert bool(found.local) == (kind == "local")
    assert bool(found.halts) == (kind == "global")


def test_a_stage_failure_for_another_version_is_global() -> None:
    store = Store()
    rows = [row(store, 1)]
    report = StageFailure(
        document_id=document(1),
        version_id="f" * 64,
        code="render_failed",
        evidence_sha256s=("c" * 64,),
    )
    found = findings_of(planned(store, rows, (1,)), rows, store, reported=[report])
    assert found.local == ()
    assert [h.code for h in found.halts] == ["stage_failure_unbound"]


# --- the ledger: merging, deduplication, persistence -----------------------------------------


def test_a_new_finding_opens_an_active_record_first_seen_now() -> None:
    store = Store()
    rows = [row(store, 1), blocked_row(store, 2, SOURCE_INCOMPLETE)]
    ledger = ledger_after(store, rows, (1, 2))
    (record,) = ledger.records
    assert (record.state, record.first_seen, len(record.history)) == ("active", CHECKED_AT, 1)
    assert record.history[0].plan_id is not None
    assert ledger.private is True
    assert ledger.published is False


def test_duplicate_findings_and_repeated_runs_change_nothing() -> None:
    store = Store()
    rows = [blocked_row(store, 2, SOURCE_INCOMPLETE)]
    ledger = ledger_after(store, rows, (2,))
    found = findings_of(planned(store, rows, (2,)), rows, store).local
    again = merge(ledger, [*found, *found], LATER)
    assert ledger_text(again) == ledger_text(ledger)
    assert merge(again, found, LATER + LATER.resolution) == again


def test_a_failure_no_longer_reported_stays_quarantined() -> None:
    store = Store()
    rows = [blocked_row(store, 2, SOURCE_INCOMPLETE)]
    ledger = ledger_after(store, rows, (2,))
    retried = merge(ledger, [], LATER)
    assert retried == ledger
    assert quarantine_set(retried).reasons_of(document(2))


def test_changed_evidence_is_appended_and_first_seen_kept() -> None:
    store = Store()
    rows = [blocked_row(store, 2, SOURCE_INCOMPLETE)]
    ledger = ledger_after(store, rows, (2,))
    (finding,) = findings_of(planned(store, rows, (2,)), rows, store).local
    moved = finding.model_copy(update={"evidence_sha256s": ("b" * 64,)})
    (record,) = merge(ledger, [moved], LATER).records
    assert record.first_seen == CHECKED_AT
    assert [e.event for e in record.history] == ["observed", "evidence_changed"]
    assert record.evidence_sha256s == ("b" * 64,)
    assert record.history[0].evidence_sha256s == finding.evidence_sha256s


def test_a_new_version_opens_a_new_record_and_never_clears_the_old_one() -> None:
    store = Store()
    old = blocked_row(store, 2, SOURCE_INCOMPLETE)
    ledger = ledger_after(store, [old], (2,))
    fixed = row(store, 2, version_id="9" * 64)  # the next version is clean
    plan = planned(store, [fixed], (2,), quarantine_set(merge(ledger, [], LATER)))
    assert disposition(plan, 2) == "quarantined"
    (current,) = plan.documents
    assert current.version_id == "9" * 64  # the current version is shown, never an older one
    assert current.batch_id is None


def test_a_policy_change_never_clears_a_quarantine() -> None:
    store = Store()
    rows = [blocked_row(store, 2, SOURCE_INCOMPLETE)]
    ledger = ledger_after(store, rows, (2,))
    (finding,) = findings_of(planned(store, rows, (2,)), rows, store).local
    assert finding.policy is not None
    newer = finding.policy.model_copy(update={"policy_version": 2})
    (record,) = merge(ledger, [finding.model_copy(update={"policy": newer})], LATER).records
    assert record.state == "active"
    assert record.history[-1].event == "evidence_changed"


def test_the_ledger_round_trips_through_its_canonical_bytes(tmp_path: Path) -> None:
    store = Store()
    rows = [blocked_row(store, 2, SOURCE_INCOMPLETE)]
    ledger = ledger_after(store, rows, (2,))
    path = tmp_path / "quarantine-ledger.json"
    path.write_text(ledger_text(ledger))
    assert parse_ledger(path.read_bytes()) == ledger
    assert ledger_sha256(parse_ledger(path.read_bytes())) == ledger_sha256(ledger)


def test_a_hand_edited_ledger_is_refused() -> None:
    store = Store()
    ledger = ledger_after(store, [blocked_row(store, 2, SOURCE_INCOMPLETE)], (2,))
    text = ledger_text(ledger)
    with pytest.raises(Exception, match="schema_invalid|quarantine ledger"):
        parse_ledger(text.replace('"active"', '"cleared"').encode())
    with pytest.raises(Exception, match="schema_invalid|quarantine ledger"):
        parse_ledger((text + " ").encode())


def test_a_ledger_that_drops_or_rewrites_a_record_is_not_a_successor() -> None:
    store = Store()
    ledger = ledger_after(store, [blocked_row(store, 2, SOURCE_INCOMPLETE)], (2,))
    assert successor_findings(ledger, merge(ledger, [], LATER)) == ()
    assert successor_findings(ledger, EMPTY_LEDGER) == ("quarantine_record_dropped",)
    (record,) = ledger.records
    rewritten = record.model_copy(
        update={"history": (record.history[0].model_copy(update={"at": LATER}),)}
    )
    edited = ledger.model_copy(update={"records": (rewritten,)})
    assert successor_findings(ledger, edited) == ("quarantine_history_rewritten",)


def test_records_hold_no_text_url_or_name() -> None:
    store = Store()
    decided = decided_row(
        store,
        1,
        entries=[entry("s-1", "unresolved", "privacy")],
        attachments=[attachment("secret-name")],
    )
    ledger = ledger_after(store, [decided], (1,))
    text = ledger_text(ledger)
    assert "secret-name" not in text
    assert "https://" not in text


# --- re-planning ---------------------------------------------------------------------------


def test_one_failing_document_does_not_block_the_others() -> None:
    store = Store()
    rows = [row(store, n) for n in (1, 2, 3)]
    base = planned(store, rows, (1, 2, 3))
    (batch,) = base.batches
    version = rows[1].selection.version_id
    assert version is not None
    report = StageFailure(
        document_id=document(2),
        version_id=version,
        code="render_failed",
        evidence_sha256s=("c" * 64,),
    )
    found = findings_of(base, rows, store, reported=[report])
    ledger = merge(EMPTY_LEDGER, found.local, CHECKED_AT, base.plan_id)
    again = planned(store, rows, (1, 2, 3), quarantine_set(ledger))
    (replanned,) = again.batches
    assert replanned.document_ids == (document(1), document(3))
    assert replanned.batch_id != batch.batch_id
    assert again.plan_id != base.plan_id
    quarantined = next(d for d in again.documents if d.document_id == document(2))
    original = next(d for d in base.documents if d.document_id == document(2))
    assert quarantined.disposition == "quarantined"
    assert (quarantined.selection_sha256, quarantined.binding_sha256) == (
        original.selection_sha256,
        original.binding_sha256,
    )
    assert quarantined.artifact_sha256s == original.artifact_sha256s
    assert "quarantine:rendering:stage:render_failed" in quarantined.reasons


def test_batches_are_recalculated_within_the_signed_caps_in_order() -> None:
    store = Store()
    rows = [row(store, n) for n in range(1, 6)]
    report = StageFailure(
        document_id=document(2),
        version_id=rows[1].selection.version_id or "",
        code="render_failed",
        evidence_sha256s=("c" * 64,),
    )
    base = planned(store, rows, range(1, 6), max_documents_per_batch=2)
    assert [b.document_ids for b in base.batches] == [
        (document(1), document(2)),
        (document(3), document(4)),
        (document(5),),
    ]
    ledger = merge(
        EMPTY_LEDGER, findings_of(base, rows, store, reported=[report]).local, CHECKED_AT
    )
    again = planned(store, rows, range(1, 6), quarantine_set(ledger), max_documents_per_batch=2)
    assert [b.document_ids for b in again.batches] == [
        (document(1), document(3)),
        (document(4), document(5)),
    ]
    assert all(b.documents <= 2 for b in again.batches)


def test_every_inventory_document_stays_accounted_for() -> None:
    store = Store()
    rows = [row(store, 1), blocked_row(store, 2, SOURCE_INCOMPLETE), row(store, 3)]
    ledger = ledger_after(store, rows, (1, 2, 3, 4))
    plan = planned(store, rows, (1, 2, 3, 4), quarantine_set(ledger))
    coverage = plan.coverage
    assert (coverage.expected, coverage.planned, coverage.quarantined, coverage.missing) == (
        4,
        2,
        1,
        1,
    )
    assert len(plan.documents) == 4


def test_the_re_plan_is_deterministic() -> None:
    store = Store()
    rows = [row(store, 1), blocked_row(store, 2, SOURCE_INCOMPLETE), row(store, 3)]
    first = ledger_after(store, rows, (1, 2, 3))
    second = ledger_after(store, list(reversed(rows)), (1, 2, 3))
    assert ledger_text(first) == ledger_text(second)
    a = planned(store, rows, (1, 2, 3), quarantine_set(first))
    b = planned(store, list(reversed(rows)), (1, 2, 3), quarantine_set(second))
    assert a == b


def test_metrics_keep_quarantined_documents_in_every_denominator() -> None:
    store = Store()
    complete = SourceState.SOURCE_COMPLETE
    rows = [
        row(store, 1, source_state=complete),
        decided_row(
            store,
            2,
            entries=[
                entry("s-1", "unresolved", "privacy"),
                entry("s-2", "deny", "rights:official_text"),
            ],
            attachments=[attachment("a")],
        ),
        row(store, 3, source_state=complete),
    ]
    rows[1] = Row(rows[1].selection.model_copy(update={"source_state": complete}), rows[1].binding)
    ledger = ledger_after(store, rows, (1, 2, 3, 4))
    plan = planned(store, rows, (1, 2, 3, 4), quarantine_set(ledger))
    found = metrics_of(plan, rows, ledger)
    assert (found.documents_expected, found.documents_ingested) == (4, 3)
    assert (found.documents_source_complete, found.documents_quarantined) == (3, 1)
    assert (found.spans_rights_unresolved, found.spans_privacy_unresolved) == (1, 1)
    assert found.attachments_unpublishable == 1
    assert found.documents_planned == 2
    assert {g.category: g.count for g in found.release_gaps} == {
        "attachment": 1,
        "privacy": 1,
        "rights": 1,
    }
    assert "https" not in found.model_dump_json()


# --- owner exceptions ----------------------------------------------------------------------


def verdict_of(
    root: TrustRoot, *records: SignedRecord, revocations: tuple[SignedRecord, ...] = ()
) -> Any:
    material = ExceptionMaterial(records=records, revocations=revocations)
    return verify_exceptions(material, policy_bytes(), CHECKED_AT, root)


def quarantined_ledger() -> tuple[Store, list[Row], QuarantineLedger]:
    store = Store()
    rows = [row(store, 1), blocked_row(store, 2, SOURCE_INCOMPLETE)]
    return store, rows, ledger_after(store, rows, (1, 2))


def test_the_quarantine_namespace_is_owner_only() -> None:
    assert QUARANTINE_NAMESPACE in OWNER_NAMESPACES
    assert QUARANTINE_NAMESPACE not in BOT_NAMESPACES


def test_an_owner_clearance_lifts_only_its_quarantine_never_the_gates(
    root: TrustRoot, keys: Keys
) -> None:
    store, rows, ledger = quarantined_ledger()
    (record,) = ledger.records
    verdict = verdict_of(root, owner_signed(keys, exception(record)))
    assert verdict.halts == ()
    assert len(verdict.verified) == 1
    settled = settle(ledger, verdict.verified, LATER)
    (cleared,) = settled.records
    assert cleared.state == "cleared"
    assert cleared.history[-1].exception_sha256 == verdict.verified[0].sha256
    assert quarantine_set(settled).documents == ()
    plan = planned(store, rows, (1, 2), quarantine_set(settled))
    # The source is still incomplete: the document stays blocked, never batched.
    assert disposition(plan, 2) == "blocked"
    assert "source:source_incomplete" in plan.documents[1].reasons
    assert all(document(2) not in b.document_ids for b in plan.batches)


@pytest.mark.parametrize("signer", ["bot", "other", "tampered"])
def test_a_clearance_the_owner_did_not_sign_halts_and_clears_nothing(
    signer: str, root: TrustRoot, keys: Keys
) -> None:
    _, _, ledger = quarantined_ledger()
    (record,) = ledger.records
    if signer == "bot":
        offered = signed_by(exception(record, signer_principal=BOT), keys.bot, QUARANTINE_NAMESPACE)
    elif signer == "other":
        offered = signed_by(exception(record), keys.other, QUARANTINE_NAMESPACE)
    else:
        good = owner_signed(keys, exception(record))
        offered = SignedRecord(raw(exception(record, reason_code="owner_forged")), good.signature)
    verdict = verdict_of(root, offered)
    assert verdict.verified == ()
    assert [(h.code, h.category) for h in verdict.halts] == [
        ("quarantine_exception_untrusted", "trust")
    ]


def test_an_owner_key_without_the_quarantine_namespace_cannot_clear(
    tmp_path: Path, keys: Keys
) -> None:
    _, _, ledger = quarantined_ledger()
    (record,) = ledger.records
    narrow = quarantine_root(tmp_path / "narrow", keys, OWNER_EVERY)
    assert verdict_of(narrow, owner_signed(keys, exception(record))).halts


def test_a_clearance_signed_in_another_namespace_halts(root: TrustRoot, keys: Keys) -> None:
    _, _, ledger = quarantined_ledger()
    (record,) = ledger.records
    offered = signed_by(exception(record), keys.owner, POLICY_NAMESPACE)
    assert verdict_of(root, offered).halts


def test_a_clearance_by_a_revoked_key_halts(tmp_path: Path, keys: Keys) -> None:
    _, _, ledger = quarantined_ledger()
    (record,) = ledger.records
    revoked = quarantine_root(tmp_path / "revoked", keys, revoked=(keys.owner,))
    verdict = verdict_of(revoked, owner_signed(keys, exception(record)))
    assert verdict.verified == ()
    assert verdict.halts


def test_a_revoked_clearance_is_rejected_and_the_quarantine_reinstated(
    root: TrustRoot, keys: Keys
) -> None:
    _, _, ledger = quarantined_ledger()
    (record,) = ledger.records
    clearance = owner_signed(keys, exception(record))
    cleared = settle(ledger, verdict_of(root, clearance).verified, LATER)
    withdrawn = signed_by(revocation("synthetic-clearance-1"), keys.owner, POLICY_NAMESPACE)
    verdict = verdict_of(root, clearance, revocations=(withdrawn,))
    assert verdict.verified == ()
    assert verdict.rejected == ("exception_revoked",)
    (again,) = settle(cleared, verdict.verified, LATER + LATER.resolution).records
    assert again.state == "active"
    assert [e.event for e in again.history] == ["observed", "cleared", "reinstated"]


def test_an_unverifiable_revocation_halts(root: TrustRoot, keys: Keys) -> None:
    _, _, ledger = quarantined_ledger()
    (record,) = ledger.records
    unsigned = SignedRecord(raw(revocation("synthetic-clearance-1")), None)
    verdict = verdict_of(root, owner_signed(keys, exception(record)), revocations=(unsigned,))
    assert verdict.verified == ()
    assert [h.code for h in verdict.halts] == ["quarantine_revocation_not_verified"]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"valid_until": CHECKED_AT}, "exception_expired"),
        (
            {"valid_from": LATER, "valid_until": LATER + LATER.resolution * 10**9},
            "exception_not_yet_valid",
        ),
    ],
)
def test_an_expired_or_premature_clearance_is_rejected(
    change: dict[str, Any], reason: str, root: TrustRoot, keys: Keys
) -> None:
    _, _, ledger = quarantined_ledger()
    (record,) = ledger.records
    verdict = verdict_of(root, owner_signed(keys, exception(record, **change)))
    assert (verdict.verified, verdict.halts) == ((), ())
    assert reason in verdict.rejected


def test_a_clearance_for_another_policy_is_rejected(root: TrustRoot, keys: Keys) -> None:
    _, _, ledger = quarantined_ledger()
    (record,) = ledger.records
    other = scope(policy(), policy_version=7)
    verdict = verdict_of(root, owner_signed(keys, exception(record, scope=other)))
    assert verdict.verified == ()
    assert "policy_version_mismatch" in verdict.rejected


def test_a_clearance_on_changed_evidence_needs_a_new_assessment(
    root: TrustRoot, keys: Keys
) -> None:
    store, rows, ledger = quarantined_ledger()
    (record,) = ledger.records
    verdict = verdict_of(root, owner_signed(keys, exception(record)))
    (finding,) = findings_of(planned(store, rows, (1, 2)), rows, store).local
    moved = merge(ledger, [finding.model_copy(update={"evidence_sha256s": ("b" * 64,)})], LATER)
    (still,) = settle(moved, verdict.verified, LATER).records
    assert still.state == "active"


def test_a_class_clearance_clears_only_its_class_of_that_version(
    root: TrustRoot, keys: Keys
) -> None:
    store = Store()
    decided = decided_row(
        store,
        1,
        entries=[
            entry("s-1", "unresolved", "privacy"),
            entry("s-2", "unresolved", "privacy"),
            entry("s-3", "deny", "rights:official_text"),
        ],
    )
    ledger = ledger_after(store, [decided], (1,))
    sample = next(r for r in ledger.records if r.code == "span_unresolved:privacy")
    clearance = exception(sample, target="class", quarantine_id=None, code=sample.code)
    settled = settle(ledger, verdict_of(root, owner_signed(keys, clearance)).verified, LATER)
    states = {(r.scope.ref, r.state) for r in settled.records}
    assert states == {("s-1", "cleared"), ("s-2", "cleared"), ("s-3", "active")}
    assert quarantine_set(settled).reasons_of(document(1)) == (
        "quarantine:rights:span_deny:rights:official_text",
    )


def test_a_version_clearance_never_reaches_another_version(root: TrustRoot, keys: Keys) -> None:
    _, _, ledger = quarantined_ledger()
    (record,) = ledger.records
    other = record.model_copy(update={"version_id": "7" * 64})
    clearance = exception(other, target="version", quarantine_id=None)
    assert settle(ledger, verdict_of(root, owner_signed(keys, clearance)).verified, LATER) == ledger


def test_an_exclusion_is_permanent_even_when_no_longer_offered(root: TrustRoot, keys: Keys) -> None:
    store = Store()
    rows = [row(store, 1), row(store, 2)]
    excluded = exception(
        next(
            iter(ledger_after(Store(), [blocked_row(Store(), 1, SOURCE_INCOMPLETE)], (1,)).records)
        ),
        exception_id="synthetic-exclusion-1",
        action="exclusion",
        target="document",
        quarantine_id=None,
        version_id=None,
        reason_code="owner_withdrawn_document",
    )
    verdict = verdict_of(root, owner_signed(keys, excluded))
    ledger = settle(EMPTY_LEDGER, verdict.verified, CHECKED_AT)
    later = settle(ledger, (), LATER)  # the exclusion is no longer offered
    assert later.exclusions == ledger.exclusions
    plan = planned(store, rows, (1, 2), quarantine_set(later))
    assert disposition(plan, 1) == "quarantined"
    assert "owner_exclusion:owner_withdrawn_document" in plan.documents[0].reasons
    assert successor_findings(ledger, EMPTY_LEDGER) == ("quarantine_exclusion_dropped",)


def test_a_stored_cleared_state_is_never_trusted_without_its_clearance(
    root: TrustRoot, keys: Keys
) -> None:
    _, _, ledger = quarantined_ledger()
    (record,) = ledger.records
    cleared = settle(
        ledger, verdict_of(root, owner_signed(keys, exception(record))).verified, LATER
    )
    (again,) = settle(cleared, (), LATER).records  # clearance not offered on the next run
    assert again.state == "active"


def test_no_caller_can_set_a_clearance_flag() -> None:
    assert "cleared" not in QuarantineFinding.model_fields
    assert "verified" not in QuarantineFinding.model_fields
    with pytest.raises(ValidationError):
        QuarantineFinding.model_validate(
            {
                "document_id": document(1),
                "version_id": None,
                "scope": {"kind": "document"},
                "code": "source:source_incomplete",
                "category": "completeness",
                "evidence_sha256s": ["a" * 64],
                "policy": None,
                "state": "cleared",
            }
        )


# --- the run, end to end -------------------------------------------------------------------


@pytest.fixture
def synthetic(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Feed the run synthetic FC8b rows instead of signed lineages (prepare is mocked)."""
    store = Store()
    rows = [row(store, n) for n in (1, 2, 3)]

    def prepare(request: PlanRequest, root: TrustRoot) -> Prepared:
        del request, root
        return Prepared(context((1, 2, 3), rows), tuple(rows))

    monkeypatch.setattr(release_plan, "prepare_plan", prepare)
    monkeypatch.setattr(release_quarantine_run, "prepare_plan", prepare)
    return store, rows


def request_of(store: Store, **changes: Any) -> QuarantineRequest:
    base = baseline()
    plan = PlanRequest(base.inventory, base.limits, (), store.artifacts)
    fields: dict[str, Any] = {
        "plan": plan,
        "policy": policy_bytes(),
        "ledger": None,
        "checked_at": CHECKED_AT,
    }
    return QuarantineRequest(**(fields | changes))


def kept(outcome: QuarantineOutcome) -> QuarantineLedger:
    assert outcome.ledger is not None
    return outcome.ledger


def render_failure(rows: Sequence[Row], number: int) -> StageFailure:
    return StageFailure(
        document_id=document(number),
        version_id=rows[number - 1].selection.version_id or "",
        code="render_failed",
        evidence_sha256s=("c" * 64,),
    )


def test_a_run_quarantines_re_plans_and_seals(synthetic: Any, root: TrustRoot) -> None:
    store, rows = synthetic
    outcome = run_quarantine(request_of(store, reported=(render_failure(rows, 2),)), root)
    assert outcome.status == "replanned", outcome.halts
    assert verified("quarantine_assessment", outcome.quarantine)
    assert verified("batch_plan", outcome.plan)
    plan = outcome.plan.plan
    assert plan is not None
    assert [b.document_ids for b in plan.batches] == [(document(1), document(3))]
    assert plan.basis.quarantine_sha256 == outcome.quarantine.quarantine_sha256
    assert outcome.metrics is not None
    assert outcome.metrics.documents_quarantined == 1


def test_a_retry_keeps_the_quarantine_and_the_same_bytes(synthetic: Any, root: TrustRoot) -> None:
    store, rows = synthetic
    first = run_quarantine(request_of(store, reported=(render_failure(rows, 2),)), root)
    stored = ledger_text(kept(first)).encode()
    retry = run_quarantine(request_of(store, ledger=stored, checked_at=LATER), root)
    assert retry.status == "replanned"
    assert ledger_text(kept(retry)).encode() == stored
    assert retry.plan.plan is not None
    assert retry.plan.plan == first.plan.plan


def test_a_global_failure_halts_and_leaves_the_ledger_untouched(
    synthetic: Any, root: TrustRoot
) -> None:
    store, rows = synthetic
    first = run_quarantine(request_of(store, reported=(render_failure(rows, 2),)), root)
    stored = ledger_text(kept(first)).encode()
    guard = render_failure(rows, 3).model_copy(update={"code": "repository_guard_refused"})
    halted = run_quarantine(
        request_of(
            store, ledger=stored, checked_at=LATER, reported=(render_failure(rows, 1), guard)
        ),
        root,
    )
    assert halted.status == "halted"
    assert [(h.code, h.category) for h in halted.halts] == [
        ("stage:repository_guard_refused", "repository")
    ]
    assert ledger_text(kept(halted)).encode() == stored  # doc 1's local failure is not merged
    assert (halted.plan.status, halted.plan.plan) == ("invalid", None)
    assert not verified("quarantine_assessment", halted.quarantine)


def test_a_malformed_ledger_halts(synthetic: Any, root: TrustRoot) -> None:
    store, _ = synthetic
    outcome = run_quarantine(request_of(store, ledger=b"{}"), root)
    assert [h.code for h in outcome.halts] == ["quarantine_ledger_malformed"]
    assert outcome.ledger is None  # never an empty ledger to write over the unreadable one


def test_a_clock_that_runs_backwards_halts(synthetic: Any, root: TrustRoot) -> None:
    store, rows = synthetic
    first = run_quarantine(
        request_of(store, reported=(render_failure(rows, 2),), checked_at=LATER), root
    )
    stored = ledger_text(kept(first)).encode()
    outcome = run_quarantine(request_of(store, ledger=stored), root)
    assert [h.code for h in outcome.halts] == ["quarantine_clock_regressed"]


def test_a_bot_clearance_in_a_run_halts_and_keeps_the_quarantine(
    synthetic: Any, root: TrustRoot, keys: Keys
) -> None:
    store, rows = synthetic
    first = run_quarantine(request_of(store, reported=(render_failure(rows, 2),)), root)
    (record,) = kept(first).records
    forged = signed_by(exception(record, signer_principal=BOT), keys.bot, QUARANTINE_NAMESPACE)
    outcome = run_quarantine(
        request_of(
            store,
            ledger=ledger_text(kept(first)).encode(),
            checked_at=LATER,
            exceptions=ExceptionMaterial(records=(forged,)),
        ),
        root,
    )
    assert outcome.status == "halted"
    assert outcome.ledger == first.ledger


def test_an_owner_clearance_in_a_run_returns_the_document_to_its_batch(
    synthetic: Any, root: TrustRoot, keys: Keys
) -> None:
    store, rows = synthetic
    first = run_quarantine(request_of(store, reported=(render_failure(rows, 2),)), root)
    (record,) = kept(first).records
    clearance = owner_signed(keys, exception(record))
    outcome = run_quarantine(
        request_of(
            store,
            ledger=ledger_text(kept(first)).encode(),
            checked_at=LATER,
            exceptions=ExceptionMaterial(records=(clearance,)),
        ),
        root,
    )
    assert outcome.status == "replanned"
    assert outcome.plan.plan is not None
    assert [b.document_ids for b in outcome.plan.plan.batches] == [
        (document(1), document(2), document(3))
    ]
    # The plan is still no authorization: every AR1 release-wide blocker stands.
    assert outcome.plan.plan.release_eligible is False
    assert outcome.plan.plan.basis.pending_release_blockers


# --- integration with the AR2 planner and the AR1 gates ------------------------------------


def sealed_quarantine(
    store: Store, rows: Sequence[Row], numbers: Sequence[int], root: TrustRoot, **changes: Any
) -> QuarantineEvidence:
    base = planned(store, rows, numbers)
    ctx = context(numbers, rows)
    ledger = merge(EMPTY_LEDGER, findings_of(base, rows, store).local, CHECKED_AT)
    found = quarantine_set(ledger)
    fields: dict[str, Any] = {
        "status": "verified",
        "source": "engine_quarantine",
        "reasons": (),
        "quarantine": found,
        "quarantine_sha256": digest_of(found),
        "base_plan_id": base.plan_id,
        "inventory_sha256": ctx.inventory_sha256,
        "limits_sha256": ctx.limits_sha256,
        "trust_root_sha256": root.sha256,
    }
    return issue_provenance(QuarantineEvidence(**(fields | changes)), "quarantine_assessment")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"base_plan_id": "1" * 64}, "plan_quarantine_stale"),
        ({"trust_root_sha256": "2" * 64}, "plan_quarantine_mismatch"),
        ({"limits_sha256": "3" * 64}, "plan_quarantine_mismatch"),
    ],
)
def test_the_planner_refuses_a_quarantine_made_for_other_inputs(
    change: dict[str, Any], reason: str, synthetic: Any, root: TrustRoot
) -> None:
    store, rows = synthetic
    evidence = sealed_quarantine(store, rows, (1, 2, 3), root, **change)
    found = plan_release(request_of(store).plan._replace(quarantine=evidence), root)
    assert (found.status, found.reasons) == ("invalid", (reason,))


def test_the_planner_refuses_an_unsealed_quarantine(synthetic: Any, root: TrustRoot) -> None:
    store, rows = synthetic
    hollow = sealed_quarantine(store, rows, (1, 2, 3), root).model_copy(update={"provenance": None})
    found = plan_release(request_of(store).plan._replace(quarantine=hollow), root)
    assert found.reasons == ("plan_quarantine_unverified",)


def test_the_quarantine_evidence_has_its_own_trust_boundary() -> None:
    assert trusted("quarantine_assessment", "verified", "engine_quarantine")
    assert not trusted("quarantine_assessment", "verified", "api_request")
    assert not trusted("batch_plan", "verified", "engine_quarantine")


def test_a_plan_without_a_quarantine_assessment_blocks_the_inventory_gate() -> None:
    store = Store()
    verdict = evaluate_release(planned_evidence(store, (1, 2), (1, 2)))
    inventory = next(g for g in verdict.gates if g.gate == "inventory")
    assert inventory.status == "failed"
    assert "plan_quarantine_unassessed" in {r.code for r in inventory.reasons}
    assert "plan_quarantine_unassessed" not in REFUSING_CODES


def test_a_quarantine_run_writes_no_stop_record() -> None:
    source = Path(release_quarantine_run.__file__).read_text()
    tree = ast.parse(source)
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not {m for m in imported if m.endswith(("release_control", "release_ledger"))}


def test_new_modules_never_fetch_nor_write() -> None:
    package = Path(release_quarantine_run.__file__).parent
    for name in NEW_MODULES:
        text = (package / f"{name}.py").read_text()
        for banned in (
            "httpx",
            "requests",
            "urllib",
            "open(",
            "write_text",
            "write_bytes",
            "subprocess",
        ):
            assert banned not in text, (name, banned)


def test_real_evidence_under_an_unpinned_trust_root_halts_the_run(
    root: TrustRoot, keys: Keys
) -> None:
    request = QuarantineRequest(
        plan=plan_request(root, keys), policy=policy_bytes(), ledger=None, checked_at=CHECKED_AT
    )
    outcome = run_quarantine(request, root)
    assert outcome.status == "halted"
    assert ("authorization", "authorization:fc7b:trust_root_not_engine_pinned") in {
        (h.category, h.code) for h in outcome.halts
    }
    assert outcome.ledger == EMPTY_LEDGER
    assert outcome.plan.plan is None
    assert outcome.metrics is not None
    assert outcome.metrics.documents_ingested == 2
