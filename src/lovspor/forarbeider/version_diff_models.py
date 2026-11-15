"""FC8 diff record: what changed between two observations, by stable id and digest (#738).

A ``VersionDiff`` holds ids, digests, enum values and counts, never source
text (``text_included`` has the single value ``False``). ``causes`` tells a
change of the source bytes apart from a change of extractor, classifier,
assessor, evaluator or policy; ``no_change`` is a replay.
"""

from typing import Literal

from lovspor.forarbeider.version_models import Digest, Frozen, NonBlank

Change = Literal["added", "removed", "changed"]


class SetChange(Frozen):
    """Digests present in only one of the two observations."""

    added: tuple[Digest, ...]
    removed: tuple[Digest, ...]


class BlockerChange(Frozen):
    """FC7 document blockers present in only one of the two decisions."""

    added: tuple[NonBlank, ...]
    removed: tuple[NonBlank, ...]


class InventoryChange(Frozen):
    """One item of an inventory (chapter, part, entry, attachment), by key and digest."""

    key: NonBlank
    change: Change
    older_sha256: Digest | None
    newer_sha256: Digest | None


class AssessmentChange(Frozen):
    """One field of one entry's FC5 assessment or FC3 candidate that changed."""

    entry_id: NonBlank
    field: Literal["rights", "evidence", "privacy", "candidate_rights", "candidate_privacy"]
    older: NonBlank
    newer: NonBlank


class DecisionChange(Frozen):
    """One entry's or attachment's FC7 decision that changed."""

    scope: Literal["entry", "attachment"]
    key: NonBlank
    older: NonBlank
    newer: NonBlank


class ObservationChange(Frozen):
    """What changed about the observation itself, apart from its meaning.

    ``semantic_change`` is ``True`` exactly when an inventory, assessment,
    decision or blocker changed. The other fields name digests that changed;
    with no semantic change they come from the observation alone (a later
    retrieval time, another URL for the same bytes): the FC5 assessment and
    FC7 decision cover the extraction digest, which covers retrieval times.
    """

    observed_at_changed: bool
    provenance_changed: bool
    manifest_sha256_changed: bool
    assessment_sha256_changed: bool
    policy_decision_sha256_changed: bool
    authorization_sha256_changed: bool
    semantic_change: bool


class VersionDiff(Frozen):
    """Two observations of one document compared; no text, nothing eligible."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_version_diff"] = "forarbeider_version_diff"
    diff_version: Literal["version-diff/1"] = "version-diff/1"
    document_id: NonBlank
    older_entry_sha256: Digest
    newer_entry_sha256: Digest
    older_version_id: Digest
    newer_version_id: Digest
    same_content_version: bool
    causes: tuple[NonBlank, ...]
    sources: SetChange
    chapters: tuple[InventoryChange, ...]
    parts: tuple[InventoryChange, ...]
    entries: tuple[InventoryChange, ...]
    attachments: tuple[InventoryChange, ...]
    assessments: tuple[AssessmentChange, ...]
    older_policy_sha256: Digest | None
    newer_policy_sha256: Digest | None
    decisions: tuple[DecisionChange, ...]
    blockers: BlockerChange
    observation: ObservationChange
    text_included: Literal[False] = False
    release_eligible: Literal[False] = False
    published: Literal[False] = False
