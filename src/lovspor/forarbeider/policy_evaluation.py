"""FC7: a fail-closed evaluation of one version against one proposed policy (#738).

:func:`evaluate_policy` takes the raw evidence of a version and a policy
definition. It rebuilds the FC6 manifest and the FC5 assessment itself, so it
binds to the exact version id, assessment digest, raw page digests and
policy digest, and it takes no decision, label or flag from the caller.

Each entry and each attachment gets ``allow``, ``deny`` or ``unresolved``,
with every reason that applies (the strongest decides):

- **deny**: conflicting evidence; a third-party work; an embedded figure; an
  attachment discovered but not captured, captured in an unsupported format,
  or captured twice differently;
- **unresolved**: an extraction gap; unknown rights; a privacy hit or an
  unresolved privacy state; a class or attachment kind the policy does not
  cover; FC5's ``default_rule`` (not evidence of reuse rights); evidence the
  policy's rule does not accept; a document type outside the policy;
- **allow**: the policy covers the class with the evidence it rests on and
  the detectors were silent. Silence is still residual risk, and is recorded
  as such.

The document is evaluated from all of them, the FC6 source state and the
attachment discovery. ``rules_satisfied`` says whether the proposed rules
would pass everything. It is never an authorization: no verifiable mechanism
exists for the owner's signature (D6), the approval of automatic publication
(D11) or the legal assessment (D10), so ``authorization`` is ``pending`` and
``eligible`` has the single value ``False``.
"""

from collections import Counter
from collections.abc import Iterator
from typing import Annotated, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lovspor.forarbeider.corpus_models import _loaded
from lovspor.forarbeider.document_models import text_digest
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.publication_policy import PublicationPolicy, policy_digest
from lovspor.forarbeider.span_assessment import assess_document, assessment_text
from lovspor.forarbeider.span_assessment_models import (
    EntryAssessment,
    Evidence,
    PrivacyState,
    RightsClass,
    SpanAssessment,
)
from lovspor.forarbeider.version_manifest import Evidence as SourceEvidence
from lovspor.forarbeider.version_manifest import build_version_manifest
from lovspor.forarbeider.version_models import (
    AttachmentState,
    SourceState,
    VersionAttachment,
    VersionManifest,
    canonical_text,
)

EVALUATOR_VERSION = "policy-eval/1"
#: What must exist, and be verifiable, before any document could be authorized.
MISSING_FOR_AUTHORIZATION = (
    "owner_policy_signature:D6",
    "automated_publication_approval:D11",
    "legal_assessment:D10",
    "privacy_residual_risk_acceptance",
)

_Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_Text = Annotated[str, Field(pattern=r"\S")]
Decision = Literal["allow", "deny", "unresolved"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EntryDecision(_Frozen):
    """One span or gap: its decision, the rule that decided it, the evidence and every reason."""

    entry_id: _Text
    part_id: str | None
    source_sha256: _Digest | None
    decision: Decision
    rule: _Text
    evidence_entries: tuple[str, ...]
    reasons: tuple[str, ...] = Field(min_length=1)


class AttachmentDecision(_Frozen):
    """One discovered attachment, decided apart from the spans."""

    url: _Text
    kind: str
    raw_sha256: _Digest | None
    decision: Decision
    rule: _Text
    reasons: tuple[str, ...] = Field(min_length=1)


class DocumentDecision(_Frozen):
    """The whole document: what blocks it, and the authorization nothing here can give."""

    rules_satisfied: bool
    blockers: tuple[str, ...]
    authorization: Literal["pending_no_verifiable_mechanism"] = "pending_no_verifiable_mechanism"
    missing_for_authorization: tuple[str, ...] = MISSING_FOR_AUTHORIZATION
    eligible: Literal[False] = False


class PolicyDecision(_Frozen):
    """Every decision for one version under one policy, bound to the exact evidence."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_policy_decision"] = "forarbeider_policy_decision"
    evaluator_version: Literal["policy-eval/1"] = "policy-eval/1"
    release_state: Literal["private_ingestion"] = "private_ingestion"
    document_id: _Text
    version_id: _Digest
    assessment_sha256: _Digest
    source_sha256s: tuple[_Digest, ...]
    policy_id: _Text
    policy_version: int = Field(ge=1)
    policy_sha256: _Digest
    policy_status: Literal["proposed"] = "proposed"
    source_state: SourceState
    attachment_discovery: Literal["verified", "unverified"]
    entries: tuple[EntryDecision, ...]
    attachments: tuple[AttachmentDecision, ...]
    document: DocumentDecision
    published: Literal[False] = False


class _Finding(NamedTuple):
    decision: Decision
    rule: str
    reason: str


_RANK = {"deny": 2, "unresolved": 1, "allow": 0}


def evaluate_policy(evidence: SourceEvidence, policy: PublicationPolicy) -> PolicyDecision:
    """The decisions for the version ``evidence`` shows, under ``policy``; never an authorization.

    Raises:
        ForarbeiderRefusedError: the refusals of FC3, FC5 and FC6.
    """
    manifest = build_version_manifest(evidence)
    assessment = assess_document(evidence)
    applicable = manifest.content.document.document_type in policy.document_types
    entries = tuple(_entry(item, policy, applicable=applicable) for item in assessment.entries)
    attachments = tuple(_attachment(i, policy) for i in manifest.content.attachments.attachments)
    return PolicyDecision(
        document_id=assessment.document_id,
        version_id=manifest.version_id,
        assessment_sha256=text_digest(assessment_text(assessment)),
        source_sha256s=assessment.source_sha256s,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_sha256=policy_digest(policy),
        source_state=manifest.source.state,
        attachment_discovery=manifest.content.attachments.discovery,
        entries=entries,
        attachments=attachments,
        document=_document(manifest, assessment, (entries, attachments), applicable=applicable),
    )


def decision_text(decision: PolicyDecision) -> str:
    return canonical_text(decision)


def parse_decision(raw: bytes) -> PolicyDecision:
    """The decision record in ``raw``, which must be its canonical bytes.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid`` for anything else, including
            a record claiming eligibility, authorization or publication.
    """
    try:
        decision = PolicyDecision.model_validate(_loaded(raw))
    except (ValidationError, ForarbeiderRefusedError) as error:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "policy decision") from error
    if decision_text(decision).encode() != raw:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "policy decision")
    return decision


def verify_decision(
    raw: bytes, evidence: SourceEvidence, policy: PublicationPolicy
) -> PolicyDecision:
    """The record in ``raw``, when ``evidence`` and ``policy`` reproduce exactly those bytes.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid`` when they do not.
    """
    decision = parse_decision(raw)
    if decision_text(evaluate_policy(evidence, policy)).encode() != raw:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "decision not reproduced")
    return decision


# --- entries -----------------------------------------------------------------------


def _entry(item: EntryAssessment, policy: PublicationPolicy, *, applicable: bool) -> EntryDecision:
    findings = list(_findings(item, policy, applicable=applicable))
    decision, rule = _decided(findings)
    return EntryDecision(
        entry_id=item.entry_id,
        part_id=item.part_id,
        source_sha256=item.source_sha256,
        decision=decision,
        rule=rule,
        evidence_entries=item.evidence_entries,
        reasons=tuple(finding.reason for finding in findings),
    )


def _decided(findings: list[_Finding]) -> tuple[Decision, str]:
    """The strongest finding decides; its rule is the one recorded."""
    strongest = max(findings, key=lambda finding: _RANK[finding.decision])
    return strongest.decision, strongest.rule


def _findings(
    item: EntryAssessment, policy: PublicationPolicy, *, applicable: bool
) -> Iterator[_Finding]:
    if not applicable:
        yield _Finding("unresolved", "policy_scope", "policy_not_applicable")
    yield from _rights_findings(item, policy)
    yield from _privacy_findings(item)


def _rights_findings(item: EntryAssessment, policy: PublicationPolicy) -> Iterator[_Finding]:
    """The rights side: gaps, unknown and conflicting evidence, coverage, accepted evidence."""
    if item.entry == "gap":
        yield _Finding("unresolved", "extraction_gap", item.rights_reasons[0])
    elif item.evidence is Evidence.CONFLICTING:
        yield _Finding("deny", "conflicting_evidence", "conflicting_evidence")
    elif item.rights is RightsClass.UNKNOWN:
        yield _Finding("unresolved", "unknown_rights", "rights_unknown")
    elif item.rights is RightsClass.THIRD_PARTY_WORK:
        yield _Finding("deny", "third_party_work", "third_party_work_never_cleared")
    else:
        yield _coverage(item, policy)


def _coverage(item: EntryAssessment, policy: PublicationPolicy) -> _Finding:
    rule = next((r for r in policy.rights_rules if r.rights_class is item.rights), None)
    name = f"rights:{item.rights.value}"
    if rule is None:
        return _Finding("unresolved", name, "class_not_covered")
    if item.evidence is Evidence.DEFAULT_RULE:
        return _Finding("unresolved", name, "default_rule_not_evidence")
    if item.evidence not in rule.accepted_evidence:
        return _Finding("unresolved", name, f"evidence_not_accepted:{item.evidence.value}")
    return _Finding("allow", name, f"rule_matched:{item.evidence.value}")


def _privacy_findings(item: EntryAssessment) -> Iterator[_Finding]:
    if item.privacy is PrivacyState.REVIEW_REQUIRED:
        yield _Finding("unresolved", "privacy", "privacy_review_required")
    elif item.privacy is PrivacyState.UNRESOLVED:
        yield _Finding("unresolved", "privacy", "privacy_unresolved")
    else:
        yield _Finding("allow", "privacy", "privacy_residual_risk_unaccepted")


# --- attachments -------------------------------------------------------------------


_NOT_CAPTURED = {
    AttachmentState.NOT_CAPTURED: "attachment_not_captured",
    AttachmentState.UNSUPPORTED: "attachment_unsupported",
    AttachmentState.CONFLICTING: "attachment_capture_conflicting",
}


def _attachment(item: VersionAttachment, policy: PublicationPolicy) -> AttachmentDecision:
    findings = list(_attachment_findings(item, policy))
    decision, rule = _decided(findings)
    return AttachmentDecision(
        url=item.url,
        kind=item.kind,
        raw_sha256=item.raw_sha256,
        decision=decision,
        rule=rule,
        reasons=tuple(finding.reason for finding in findings),
    )


def _attachment_findings(item: VersionAttachment, policy: PublicationPolicy) -> Iterator[_Finding]:
    rule = f"attachment:{item.kind}"
    if item.state in _NOT_CAPTURED:
        yield _Finding("deny", rule, _NOT_CAPTURED[item.state])
    if item.kind == "embedded":
        yield _Finding("deny", rule, "third_party_work_never_cleared")
    elif not any(entry.kind == item.kind for entry in policy.attachment_rules):
        yield _Finding("unresolved", rule, "attachment_kind_not_covered")
    else:
        yield _Finding("allow", rule, "rule_matched:captured")


# --- document ----------------------------------------------------------------------


def _document(
    manifest: VersionManifest,
    assessment: SpanAssessment,
    decided: tuple[tuple[EntryDecision, ...], tuple[AttachmentDecision, ...]],
    *,
    applicable: bool,
) -> DocumentDecision:
    """Blockers from every entry, attachment, the source state and the discovery."""
    entries, attachments = decided
    blockers = list(_counted("entries", (item.decision for item in entries)))
    blockers += _counted("attachments", (item.decision for item in attachments))
    if manifest.source.state is not SourceState.SOURCE_COMPLETE:
        blockers.append(f"source_state:{manifest.source.state.value}")
    if manifest.content.attachments.discovery != "verified":
        blockers.append("attachment_discovery_unverified")
    if not applicable:
        blockers.append("policy_not_applicable")
    if not assessment.entries:
        blockers.append("no_entries")
    return DocumentDecision(rules_satisfied=not blockers, blockers=tuple(sorted(blockers)))


_BLOCKING: tuple[Decision, ...] = ("deny", "unresolved")


def _counted(scope: str, decisions: Iterator[Decision]) -> list[str]:
    counts = Counter(decisions)
    return [f"{scope}_{name}:{counts[name]}" for name in _BLOCKING if counts[name]]
