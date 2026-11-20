"""AR3: fail-closed attribution of failures to one document or to the whole run (#738; §9.4).

:func:`attribute_plan` reads an AR2 plan (built without any quarantine), the
FC8b rows it was built from, the private artifact store and the failures later
engine stages report, and splits every failure into:

- a :class:`QuarantineFinding` when the failure is **proven** to belong to one
  document: extraction, rendering, artifact integrity, rights or privacy spans,
  attachments, source or publication completeness;
- a :class:`GlobalFailure` otherwise: the trust root or a signature, the
  policy (another policy, none evaluated, not applicable), FC7b authorization,
  a lineage history that was rewritten or forked (the private store itself),
  a source-wide proof mismatch, the repository, publication authorization.

Attribution is an explicit table of exact codes, never a pattern guess. A
code not in the table, a summary code without its details, an FC7 decision
that cannot be read or does not match its counts: each is a global failure
with category ``unknown``. A global failure is never turned into a local one
so that a run can continue; one global failure halts the run.

The release-wide blockers every document carries today (the unanchored head,
D10, D11, and FC7b's always-blocking D6/D10/D11/integration codes) are not
failures of anything: they are pending owner decisions, judged by the AR1
gates. Planning dispositions that are not failures (a missing document, one
outside the inventory, one too large for any batch) stay visible in the plan
and are not quarantined either.

Span, part and attachment findings come from the FC7 decision bound to the
selection: spans and parts by their FC5 ids, attachments by a digest. All
findings of one observation rest on the same evidence: the digests of its
bound FC6, FC5, FC7 and FC7b records. No text, URL or name is read into a
finding.
"""

from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Final, NamedTuple

from pydantic import Field

from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_authorization import ALWAYS_BLOCKING
from lovspor.forarbeider.policy_evaluation import (
    AttachmentDecision,
    EntryDecision,
    PolicyDecision,
    parse_decision,
)
from lovspor.forarbeider.release_plan import Row
from lovspor.forarbeider.release_plan_models import PlannedDocument, ReleasePlan
from lovspor.forarbeider.release_quarantine_models import (
    GlobalCategory,
    GlobalFailure,
    LocalCategory,
    QuarantineFinding,
    QuarantineScope,
)
from lovspor.forarbeider.version_models import Digest, Frozen, NonBlank
from lovspor.forarbeider.version_release_models import digest_of

#: Exact codes that belong to one document, and their category.
LOCAL_CODES: Final[dict[str, LocalCategory]] = {
    "artifact_missing": "integrity",
    "artifact_altered": "integrity",
    "integrity:lineage:artifact_altered": "integrity",
    "integrity:lineage:artifact_invalid": "integrity",
    "integrity:lineage:artifact_mismatch": "integrity",
    "integrity:lineage:artifact_missing": "integrity",
    "integrity:lineage:artifact_not_rebuilt": "rendering",
    "integrity:lineage:evidence_not_reconstructed": "extraction",
    "source:source_incomplete": "completeness",
    "source:source_unknown": "completeness",
    "publication:publication_incomplete": "completeness",
    "publication:publication_unknown": "completeness",
    "policy:fc7:no_entries": "extraction",
    "policy:fc7:attachment_discovery_unverified": "attachment",
    "policy:fc7:source_state:source_incomplete": "completeness",
    "policy:fc7:source_state:unknown": "completeness",
}
#: Exact codes that no single document owns, and their category.
GLOBAL_CODES: Final[dict[str, GlobalCategory]] = {
    "trust:head_missing": "trust",
    "policy:policy_not_evaluated": "policy",
    "policy:fc7:policy_not_applicable": "policy",
    "policy_mismatch": "policy",
    "integrity:fc7b_authorization_not_reproduced": "authorization",
    "authorization:fc7b_authorization_missing": "authorization",
    "authorization:fc7b_authorization_not_reverified": "authorization",
    "authorization:fc7b_invalid": "authorization",
    "authorization:fc7b_missing_evidence": "authorization",
}
#: Codes that only summarize detail codes; alone they prove nothing.
SUMMARY_CODES: Final = {
    "integrity:lineage_invalid": "integrity:lineage:",
    "integrity:lineage_incomplete": "integrity:lineage:",
    "policy:fc7_rules_not_satisfied": "policy:fc7:",
    "policy:fc7_deny": "policy:fc7:",
    "policy:fc7_unresolved": "policy:fc7:",
}
#: Pending owner decisions every document carries; the AR1 gates judge them.
PENDING_CODES: Final = frozenset(f"authorization:fc7b:{code}" for code in ALWAYS_BLOCKING)
#: Plan dispositions that are not failures; they stay visible in the plan.
NOT_FAILURES: Final = frozenset(
    {
        "document_exceeds_batch_cap",
        "document_outside_inventory",
        "inventory_document_without_evidence",
    }
)
#: Failures later engine stages (AR4 rendering, publication) may report, by exact code.
STAGE_LOCAL: Final[dict[str, LocalCategory]] = {
    "render_failed": "rendering",
    "render_output_mismatch": "rendering",
    "extraction_fidelity_below_floor": "extraction",
    "privacy_detector_anomaly": "privacy",
    "attachment_render_failed": "attachment",
    "artifact_integrity_mismatch": "integrity",
}
STAGE_GLOBAL: Final[dict[str, GlobalCategory]] = {
    "trust_root_unavailable": "trust",
    "policy_revoked": "policy",
    "source_proof_mismatch": "source_wide",
    "repository_guard_refused": "repository",
    "repository_integrity_failed": "repository",
    "publication_authorization_failed": "authorization",
}
_SPAN_PREFIXES: Final = ("policy:fc7:entries_", "policy:fc7:attachments_")
_BLOCKING: Final = frozenset({"deny", "unresolved"})


class StageFailure(Frozen):
    """A failure a later engine stage reports for one document version, by exact code.

    A report can only add a restriction: it quarantines or halts, never clears.
    """

    document_id: NonBlank
    version_id: NonBlank
    code: NonBlank
    scope: QuarantineScope = QuarantineScope(kind="document")
    evidence_sha256s: tuple[Digest, ...] = Field(min_length=1)


class Attributed(NamedTuple):
    """Every failure of a run, split by whether one document is proven to own it."""

    local: tuple[QuarantineFinding, ...]
    halts: tuple[GlobalFailure, ...]


class _Doc(NamedTuple):
    planned: PlannedDocument
    row: Row | None
    artifacts: Mapping[str, bytes]


def attribute_plan(
    plan: ReleasePlan,
    rows: Sequence[Row],
    artifacts: Mapping[str, bytes],
    reported: Sequence[StageFailure] = (),
) -> Attributed:
    """Each failure in ``plan`` and ``reported``, attributed; ambiguity is always global."""
    by_id = {item.selection.document_id: item for item in rows}
    local: set[QuarantineFinding] = set()
    halts: set[GlobalFailure] = set()
    for planned in plan.documents:
        found = _attribute(_Doc(planned, by_id.get(planned.document_id), artifacts))
        local.update(found.local)
        halts.update(found.halts)
    for report in reported:
        found = _stage(report, {d.document_id: d for d in plan.documents}, by_id)
        local.update(found.local)
        halts.update(found.halts)
    return Attributed(_ordered_findings(local), _ordered_halts(halts))


def _ordered_findings(found: set[QuarantineFinding]) -> tuple[QuarantineFinding, ...]:
    return tuple(sorted(found, key=lambda item: (item.document_id, item.quarantine_id)))


def _ordered_halts(found: set[GlobalFailure]) -> tuple[GlobalFailure, ...]:
    return tuple(sorted(found, key=lambda item: (item.document_id or "", item.code)))


def _attribute(doc: _Doc) -> Attributed:
    reasons = set(doc.planned.reasons)
    local: list[QuarantineFinding] = []
    halts: list[GlobalFailure] = []
    for code in sorted(reasons):
        if code in PENDING_CODES or code in NOT_FAILURES:
            continue
        if code.startswith(_SPAN_PREFIXES):
            spans = _spans(doc, code)
            local += spans.local
            halts += spans.halts
        elif code in SUMMARY_CODES:
            halts += _summary(doc.planned.document_id, code, reasons)
        else:
            found = _exact(doc, code)
            local += found.local
            halts += found.halts
    return Attributed(tuple(local), tuple(halts))


def _summary(document_id: str, code: str, reasons: set[str]) -> list[GlobalFailure]:
    family = SUMMARY_CODES[code]
    if any(item.startswith(family) for item in reasons):
        return []
    return [_unknown(f"unattributed:{code}", document_id)]


def _exact(doc: _Doc, code: str) -> Attributed:
    document_id = doc.planned.document_id
    if code in LOCAL_CODES:
        return Attributed((_finding(doc, QuarantineScope(kind="document"), code),), ())
    if code in GLOBAL_CODES:
        failure = GlobalFailure(code=code, category=GLOBAL_CODES[code], document_id=document_id)
        return Attributed((), (failure,))
    if code.startswith("authorization:fc7b:"):
        failure = GlobalFailure(code=code, category="authorization", document_id=document_id)
        return Attributed((), (failure,))
    if code.startswith("integrity:lineage:"):
        failure = GlobalFailure(code=code, category="repository", document_id=document_id)
        return Attributed((), (failure,))
    return Attributed((), (_unknown(f"unattributed:{code}", document_id),))


def _unknown(code: str, document_id: str | None) -> GlobalFailure:
    return GlobalFailure(code=code[:128], category="unknown", document_id=document_id)


def _finding(doc: _Doc, scope: QuarantineScope, code: str) -> QuarantineFinding:
    category = LOCAL_CODES.get(code) or _span_category(code)
    return QuarantineFinding(
        document_id=doc.planned.document_id,
        version_id=doc.planned.version_id,
        scope=scope,
        code=code,
        category=category,
        evidence_sha256s=evidence_of(doc.row, doc.planned),
        policy=doc.row.selection.policy if doc.row else None,
    )


def evidence_of(row: Row | None, planned: PlannedDocument) -> tuple[str, ...]:
    """The digests an observation rests on: its bound records, else its artifacts or binding."""
    if row is None:
        return planned.artifact_sha256s or (digest_of(planned),)
    binding = row.binding
    bound = {
        binding.manifest_sha256,
        binding.assessment_sha256,
        binding.policy_decision_sha256,
        binding.authorization_sha256,
    } - {None}
    found = sorted(item for item in bound if item is not None)
    return tuple(found) or planned.artifact_sha256s or (digest_of(binding),)


# --- spans, parts and attachments from the bound FC7 decision ------------------------------


def _spans(doc: _Doc, code: str) -> Attributed:
    document_id = doc.planned.document_id
    decision = _decision(doc)
    if decision is None:
        return Attributed((), (_unknown(f"fc7_decision_unreadable:{code}", document_id),))
    kind, value, count = _parsed(code)
    items = _decided_items(decision, kind, value)
    if value not in _BLOCKING or count is None or len(items) != count:
        return Attributed((), (_unknown(f"fc7_decision_inconsistent:{code}", document_id),))
    local: list[QuarantineFinding] = []
    halts: list[GlobalFailure] = []
    for item in items:
        found = _item(doc, item, value)
        local += found.local
        halts += found.halts
    return Attributed(tuple(local), tuple(halts))


def _decision(doc: _Doc) -> PolicyDecision | None:
    if doc.row is None or doc.row.binding.policy_decision_sha256 is None:
        return None
    key = doc.row.binding.policy_decision_sha256
    data = doc.artifacts.get(key)
    if data is None or sha256(data).hexdigest() != key:
        return None
    try:
        decision = parse_decision(data)
    except ForarbeiderRefusedError:
        return None
    selection = doc.row.selection
    if (decision.document_id, decision.version_id) != (selection.document_id, selection.version_id):
        return None
    return decision


def _parsed(code: str) -> tuple[str, str, int | None]:
    """``policy:fc7:entries_deny:3`` as ``("entries", "deny", 3)``."""
    body = code.removeprefix("policy:fc7:")
    head, _, number = body.partition(":")
    kind, _, value = head.partition("_")
    return kind, value, int(number) if number.isdigit() else None


def _decided_items(
    decision: PolicyDecision, kind: str, value: str
) -> list[EntryDecision | AttachmentDecision]:
    pool: Sequence[EntryDecision | AttachmentDecision] = (
        decision.entries if kind == "entries" else decision.attachments
    )
    return [item for item in pool if item.decision == value]


def _item(doc: _Doc, item: EntryDecision | AttachmentDecision, value: str) -> Attributed:
    document_id = doc.planned.document_id
    if isinstance(item, AttachmentDecision):
        ref = item.raw_sha256 or sha256(item.url.encode()).hexdigest()
        scope = QuarantineScope(kind="attachment", ref=ref)
        return Attributed((_finding(doc, scope, f"attachment_{value}:{item.rule}"),), ())
    category = _rule_category(item.rule)
    if category is None:
        return Attributed((), (_rule_failure(item.rule, document_id),))
    scope = _entry_scope(item, category)
    return Attributed((_finding(doc, scope, f"span_{value}:{item.rule}"),), ())


def _rule_failure(rule: str, document_id: str) -> GlobalFailure:
    if rule == "policy_scope":
        return GlobalFailure(code="fc7:policy_scope", category="policy", document_id=document_id)
    return _unknown(f"unattributed_rule:{rule}", document_id)


def _entry_scope(item: EntryDecision, category: LocalCategory) -> QuarantineScope:
    if category == "extraction" and item.part_id:
        return QuarantineScope(kind="part", ref=item.part_id)
    return QuarantineScope(kind="span", ref=item.entry_id)


_RIGHTS_RULES: Final = frozenset({"conflicting_evidence", "unknown_rights", "third_party_work"})


def _rule_category(rule: str) -> LocalCategory | None:
    if rule == "privacy":
        return "privacy"
    if rule == "extraction_gap":
        return "extraction"
    if rule in _RIGHTS_RULES or rule.startswith("rights:"):
        return "rights"
    return None


def _span_category(code: str) -> LocalCategory:
    if code.startswith("attachment_"):
        return "attachment"
    category = _rule_category(code.partition(":")[2])
    if category is None:
        raise ValueError(f"no local category for {code}")
    return category


# --- failures reported by later stages --------------------------------------------------


def _stage(
    report: StageFailure, planned: Mapping[str, PlannedDocument], rows: Mapping[str, Row]
) -> Attributed:
    document = planned.get(report.document_id)
    if document is None or document.version_id != report.version_id:
        return Attributed((), (_unknown("stage_failure_unbound", report.document_id),))
    if report.code in STAGE_GLOBAL:
        failure = GlobalFailure(
            code=f"stage:{report.code}",
            category=STAGE_GLOBAL[report.code],
            document_id=report.document_id,
        )
        return Attributed((), (failure,))
    category = STAGE_LOCAL.get(report.code)
    if category is None:
        return Attributed((), (_unknown(f"unattributed:stage:{report.code}", report.document_id),))
    return Attributed((_stage_finding(report, document, rows, category),), ())


def _stage_finding(
    report: StageFailure,
    document: PlannedDocument,
    rows: Mapping[str, Row],
    category: LocalCategory,
) -> QuarantineFinding:
    row = rows.get(report.document_id)
    return QuarantineFinding.model_validate(
        {
            "document_id": report.document_id,
            "version_id": document.version_id,
            "scope": report.scope,
            "code": f"stage:{report.code}",
            "category": category,
            "evidence_sha256s": tuple(sorted(set(report.evidence_sha256s))),
            "policy": row.selection.policy if row else None,
        }
    )
