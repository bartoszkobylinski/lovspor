"""FC8b records: a release manifest over exact document versions (#738; §2.2, §9.2).

Private ingestion only. A ``ReleaseManifest`` says, for every document a
caller offered, which version was selected, which artifacts it rests on and
what blocks it. It has three sections, each with its own scope:

- ``content`` (hashed into ``release_id``): what would be released and why
  not. Document ids, version ids, raw source digests, policy ids and digests,
  processing versions, both completeness states, the status and every
  blocker. Nothing in it depends on when a page was read, so the same content
  observed again gives the same ``release_id``;
- ``bindings`` (hashed into ``bindings_sha256``): the exact bytes each
  selection rests on. The verified FC8a lineage head, the FC6 manifest, the
  FC5 assessment, the FC7 decision and the FC7b verdict, each by SHA-256.
  These cover retrieval times (FC6 records them, and FC5, FC7 and FC7b cover
  FC6), so a later read of the same bytes changes them and not the release id;
- ``observations``: operational metadata (retrieval time, observation
  counts), outside both digests.

A document is ``excluded`` when its evidence does not hold (an integrity
blocker), ``blocked`` when it holds but a gate fails, and ``included`` only
with no blocker at all. ``included`` is refused while the lineage head is
``unanchored``: no external head anchor exists yet, so a caller's head is
never trusted, and nothing here can be included. ``release_eligible``,
``published`` and ``text_included`` have the single value ``False``.
"""

import hashlib
from typing import Final, Literal, Self

from pydantic import Field, model_validator

from lovspor.forarbeider.policy_authorization_models import Instant
from lovspor.forarbeider.version_lineage_models import LineageStatus, ProcessingVersions
from lovspor.forarbeider.version_models import (
    Digest,
    Frozen,
    NonBlank,
    PublicationState,
    SourceState,
    canonical_text,
)

BUILDER_VERSION: Final = "version-release/1"
#: Why no release can happen yet, whatever the documents: stated in every manifest.
RELEASE_BLOCKERS: Final = (
    "d10_legal_assessment_unresolved",
    "d11_automated_publication_not_accepted",
    "head_anchoring_not_implemented",
    "publication_integration_not_implemented",
    "release_signing_not_implemented",
)

Category = Literal[
    "integrity", "trust", "source", "publication", "policy", "authorization", "governance"
]
DocumentStatus = Literal["included", "excluded", "blocked"]


class Blocker(Frozen):
    """One reason a document is not included, and the gate it belongs to."""

    code: NonBlank
    category: Category


class PolicyReference(Frozen):
    """The policy the FC7 decision was evaluated against, by id, version and digest."""

    policy_id: NonBlank
    policy_version: int = Field(ge=1)
    policy_sha256: Digest


class DocumentSelection(Frozen):
    """One offered document: the version selected, or none, and every blocker."""

    document_id: NonBlank
    status: DocumentStatus
    version_id: Digest | None
    source_sha256s: tuple[Digest, ...]
    policy: PolicyReference | None
    processing: ProcessingVersions | None
    source_state: SourceState | None
    publication_state: PublicationState | None
    blockers: tuple[Blocker, ...]

    @model_validator(mode="after")
    def _status_follows_blockers(self) -> Self:
        ordered = sorted(set(self.blockers), key=lambda item: (item.category, item.code))
        if list(self.blockers) != ordered:
            raise ValueError("blockers are sorted by category and code, once each")
        if self.status != status_of(self.blockers):
            raise ValueError("status does not follow from the blockers")
        if self.version_id is None and self.status != "excluded":
            raise ValueError("only an excluded document has no selected version")
        return self


class DocumentBinding(Frozen):
    """The exact artifact bytes a selection rests on, each by SHA-256."""

    document_id: NonBlank
    lineage_status: LineageStatus
    lineage_entries: int = Field(ge=0)
    claimed_head_sha256: Digest | None
    lineage_head_sha256: Digest | None
    manifest_sha256: Digest | None
    assessment_sha256: Digest | None
    policy_decision_sha256: Digest | None
    authorization_sha256: Digest | None


class DocumentObservation(Frozen):
    """Operational metadata of the selected observation; outside every digest."""

    document_id: NonBlank
    observed_at: Instant | None
    observations: int = Field(ge=0)
    content_versions: int = Field(ge=0)


class ReleaseCounts(Frozen):
    documents: int = Field(ge=0)
    included: int = Field(ge=0)
    excluded: int = Field(ge=0)
    blocked: int = Field(ge=0)


class ReleaseContent(Frozen):
    """What would be released and why not; ``release_id`` is its digest."""

    builder_version: Literal["version-release/1"] = BUILDER_VERSION
    head_trust: Literal["unanchored"] = "unanchored"
    release_blockers: tuple[NonBlank, ...] = RELEASE_BLOCKERS
    documents: tuple[DocumentSelection, ...]


class ReleaseManifest(Frozen):
    """Every offered document, its selected version, its bindings and its blockers."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_release_manifest"] = "forarbeider_release_manifest"
    release_state: Literal["private_ingestion"] = "private_ingestion"
    release_id: Digest
    content: ReleaseContent
    counts: ReleaseCounts
    bindings_sha256: Digest
    bindings: tuple[DocumentBinding, ...]
    observations: tuple[DocumentObservation, ...]
    text_included: Literal[False] = False
    release_eligible: Literal[False] = False
    published: Literal[False] = False

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        ids = [item.document_id for item in self.content.documents]
        if ids != sorted(set(ids)):
            raise ValueError("documents are sorted by id, once each")
        if [b.document_id for b in self.bindings] != ids or [
            o.document_id for o in self.observations
        ] != ids:
            raise ValueError("bindings and observations follow the documents")
        if self.release_id != digest_of(self.content):
            raise ValueError("release_id is the digest of the content")
        if self.bindings_sha256 != digest_of(*self.bindings):
            raise ValueError("bindings_sha256 is the digest of the bindings")
        if self.counts != counts_of(self.content.documents):
            raise ValueError("counts follow from the documents")
        if self.counts.included and self.content.head_trust == "unanchored":
            raise ValueError("nothing is included while the lineage head is unanchored")
        return self


class ReleaseFinding(Frozen):
    """One way an offered manifest differs from the one its inputs rebuild."""

    code: NonBlank
    document_id: str | None = None


class ReleaseVerification(Frozen):
    """Whether an offered manifest is exactly what its inputs rebuild; never eligibility."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_release_verification"] = "forarbeider_release_verification"
    verifier_version: Literal["release-verify/1"] = "release-verify/1"
    status: Literal["reproduced", "invalid"]
    offered_release_id: Digest | None
    rebuilt_release_id: Digest | None
    head_trust: Literal["unanchored"] = "unanchored"
    findings: tuple[ReleaseFinding, ...]
    release_eligible: Literal[False] = False
    published: Literal[False] = False

    @model_validator(mode="after")
    def _status_follows_findings(self) -> Self:
        if (self.status == "reproduced") != (not self.findings):
            raise ValueError("reproduced exactly when nothing differs")
        return self


def status_of(blockers: tuple[Blocker, ...]) -> DocumentStatus:
    """Excluded on any integrity blocker; blocked on any other; included with none."""
    if any(item.category == "integrity" for item in blockers):
        return "excluded"
    return "blocked" if blockers else "included"


def counts_of(documents: tuple[DocumentSelection, ...]) -> ReleaseCounts:
    statuses = [item.status for item in documents]
    return ReleaseCounts(
        documents=len(statuses),
        included=statuses.count("included"),
        excluded=statuses.count("excluded"),
        blocked=statuses.count("blocked"),
    )


def digest_of(*records: Frozen) -> str:
    """SHA-256 of the records' canonical texts, concatenated in order."""
    text = "".join(canonical_text(record) for record in records)
    return hashlib.sha256(text.encode()).hexdigest()
