"""FC8 records: a document's version lineage and its verification (#738; §2.2).

Private ingestion only. A ``LineageEntry`` records one *observation* of one
document: the version id the raw bytes give (FC6), the digests of every
artifact built from them (the raw responses, the FC6 manifest, the FC5
assessment and, when a policy was evaluated, the policy, the FC7 decision and
the FC7b verdict) and the time the bytes were read. Entries form a hash chain:
each names the SHA-256 of the canonical bytes of the entry before it.

The lineage lives outside the version hash, as §2.2 requires: no version
manifest names an entry or another version. A content version and an
observation are different things. The same bytes read again at a later time
are a new entry with ``content_change: unchanged`` and the same version id;
only different bytes (or a different extractor) make a ``new_version``.
``content_change`` is not a claim the caller can make: the model derives it
from the two version ids and refuses any other value.

A digest chain is tamper-*evident*, not tamper-*proof*. It shows that the
entries offered are consistent with one another; it does not show that none
was dropped from the end, or that the store holding them is append-only.
:data:`STORAGE_GUARANTEE` says so in every verification; only a head digest
anchored somewhere else (``expected_head``) detects truncation.

Nothing here is eligible for release or published: ``release_eligible`` and
``published`` have the single value ``False``.
"""

from typing import Final, Literal, Self

from pydantic import Field, model_validator

from lovspor.forarbeider.policy_authorization_models import Instant
from lovspor.forarbeider.version_models import Digest, Frozen, NonBlank

LINEAGE_DOMAIN: Final = "lovspor.forarbeider.version-lineage.v1"
#: What a verified chain does and does not prove; stated in every verification.
STORAGE_GUARANTEE: Final = "digest_chain_consistency_only_append_only_storage_not_proven"
#: What the verifier proves of an FC7b verdict: digest and policy binding, not a rerun.
AUTHORIZATION_PROOF: Final = "digest_and_policy_binding_only"

ContentChange = Literal["first_version", "new_version", "unchanged"]
FindingEffect = Literal["invalid", "gap"]
LineageStatus = Literal["verified", "incomplete", "invalid"]


class ProcessingVersions(Frozen):
    """The versions of every step that turned raw bytes into the recorded artifacts.

    A diff compares them to tell a source change from a change of extractor,
    classifier, assessor or evaluator.
    """

    extractor_version: NonBlank
    classifier_version: NonBlank
    assessment_version: NonBlank
    evaluator_version: NonBlank | None
    authorization_verifier_version: NonBlank | None


class LineageEntry(Frozen):
    """One observation of one document, chained to the observation before it."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_lineage_entry"] = "forarbeider_lineage_entry"
    domain: Literal["lovspor.forarbeider.version-lineage.v1"] = LINEAGE_DOMAIN
    lineage_version: Literal["lineage/1"] = "lineage/1"
    release_state: Literal["private_ingestion"] = "private_ingestion"
    document_id: NonBlank
    sequence: int = Field(ge=0)
    previous_entry_sha256: Digest | None
    version_id: Digest
    previous_version_id: Digest | None
    content_change: ContentChange
    observed_at: Instant
    source_sha256s: tuple[Digest, ...] = Field(min_length=1)
    manifest_sha256: Digest
    assessment_sha256: Digest
    policy_sha256: Digest | None
    policy_decision_sha256: Digest | None
    authorization_sha256: Digest | None
    processing: ProcessingVersions
    release_eligible: Literal[False] = False
    published: Literal[False] = False

    @model_validator(mode="after")
    def _chain_fields_agree(self) -> Self:
        first = self.sequence == 0
        if first != (self.previous_entry_sha256 is None) or first != (
            self.previous_version_id is None
        ):
            raise ValueError("only the first entry, and every first entry, has no predecessor")
        if self.content_change != _change(self.version_id, self.previous_version_id):
            raise ValueError("content_change does not follow from the two version ids")
        if list(self.source_sha256s) != sorted(set(self.source_sha256s)):
            raise ValueError("source digests are sorted and unique")
        return self

    @model_validator(mode="after")
    def _evaluation_needs_its_inputs(self) -> Self:
        if (self.policy_sha256 is None) != (self.policy_decision_sha256 is None):
            raise ValueError("a policy and its decision are recorded together")
        if self.policy_decision_sha256 is None and self.authorization_sha256 is not None:
            raise ValueError("an authorization verdict needs an evaluated policy")
        if (self.processing.evaluator_version is None) != (self.policy_decision_sha256 is None):
            raise ValueError("the evaluator version is recorded with its decision")
        verifier = self.processing.authorization_verifier_version
        if (verifier is None) != (self.authorization_sha256 is None):
            raise ValueError("the authorization verifier version is recorded with its verdict")
        return self


def _change(version_id: str, previous: str | None) -> ContentChange:
    if previous is None:
        return "first_version"
    return "unchanged" if previous == version_id else "new_version"


class LineageFinding(Frozen):
    """One thing wrong with, or missing from, the lineage: ``invalid`` or an explicit ``gap``."""

    code: NonBlank
    effect: FindingEffect
    entry_sha256: Digest | None = None
    sequence: int | None = None
    detail: str | None = None


class ChainLink(Frozen):
    """One verified position of the chain, from the first entry on."""

    sequence: int = Field(ge=0)
    entry_sha256: Digest
    version_id: Digest
    content_change: ContentChange


class LineageVerification(Frozen):
    """What the offered entries and artifacts prove; status computed from the findings."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_lineage_verification"] = "forarbeider_lineage_verification"
    verifier_version: Literal["lineage-verify/1"] = "lineage-verify/1"
    document_id: str | None
    status: LineageStatus
    chain: tuple[ChainLink, ...]
    head_entry_sha256: Digest | None
    head_version_id: Digest | None
    expected_head_sha256: Digest | None
    content_versions: int = Field(ge=0)
    observations: int = Field(ge=0)
    findings: tuple[LineageFinding, ...]
    storage_guarantee: Literal["digest_chain_consistency_only_append_only_storage_not_proven"] = (
        STORAGE_GUARANTEE
    )
    authorization_proof: Literal["digest_and_policy_binding_only"] = AUTHORIZATION_PROOF
    text_included: Literal[False] = False
    release_eligible: Literal[False] = False
    published: Literal[False] = False

    @model_validator(mode="after")
    def _status_follows_findings(self) -> Self:
        if self.status != status_of(self.findings):
            raise ValueError("status does not follow from the findings")
        if self.status == "verified" and not self.chain:
            raise ValueError("an empty chain is never verified")
        head = self.chain[-1] if self.chain else None
        if (self.head_entry_sha256, self.head_version_id) != (
            (head.entry_sha256, head.version_id) if head else (None, None)
        ):
            raise ValueError("the head is the last link of the chain")
        return self


def status_of(findings: tuple[LineageFinding, ...]) -> LineageStatus:
    """Invalid when any finding is; incomplete when any gap is open; else verified."""
    effects = {finding.effect for finding in findings}
    if "invalid" in effects:
        return "invalid"
    return "incomplete" if effects else "verified"
