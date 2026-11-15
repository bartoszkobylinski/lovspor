"""FC8: compare two observations of one document by stable id and digest (#738).

:func:`load_snapshot` reads one lineage entry and its artifacts, and refuses
unless every artifact verifies (:func:`~lovspor.forarbeider.version_lineage.entry_findings`):
a diff is never computed over evidence that does not hold.

:func:`diff_snapshots` reports, without any source text:

- the raw sources added and removed;
- chapter inventory (the source's own navigation), part, entry (span and gap)
  and attachment inventory changes, each by key and digest;
- FC5 rights and privacy changes, and FC3 candidate changes, entry by entry;
- FC7 decision changes and document blockers added or removed.

``causes`` says why: ``source_content_changed`` when the raw bytes differ;
``extractor_version_changed``, ``classifier_version_changed``,
``assessment_version_changed``, ``evaluator_version_changed`` or
``authorization_verifier_version_changed`` when a processing step changed;
``policy_changed`` or ``authorization_changed``; ``no_change`` for a replay.
A difference none of them explains is ``unexplained_difference``, never
silently attributed.

``observation`` keeps metadata apart from meaning: a later retrieval time
changes the manifest digest and, because FC5 and FC7 cover the extraction
digest, the assessment and decision digests too, with ``semantic_change``
``False`` and ``causes`` ``no_change``.

Nothing is fetched, written, signed or published here.
"""

import json
from collections.abc import Mapping
from typing import NamedTuple

from pydantic import BaseModel

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.models import JSON_LAYOUT
from lovspor.forarbeider.policy_evaluation import PolicyDecision, parse_decision
from lovspor.forarbeider.span_assessment import parse_assessment
from lovspor.forarbeider.span_assessment_models import SpanAssessment
from lovspor.forarbeider.version_diff_models import (
    AssessmentChange,
    BlockerChange,
    DecisionChange,
    InventoryChange,
    ObservationChange,
    SetChange,
    VersionDiff,
)
from lovspor.forarbeider.version_lineage import entry_findings, parse_entry, sha
from lovspor.forarbeider.version_lineage_models import LineageEntry
from lovspor.forarbeider.version_manifest import parse_version_manifest
from lovspor.forarbeider.version_models import VersionManifest, canonical_text

_PROCESSING = (
    "extractor_version",
    "classifier_version",
    "assessment_version",
    "evaluator_version",
    "authorization_verifier_version",
)
_ASSESSED = ("rights", "evidence", "privacy", "candidate_rights", "candidate_privacy")


class Snapshot(NamedTuple):
    """One verified observation: its entry and parsed artifacts."""

    key: str
    entry: LineageEntry
    manifest: VersionManifest
    assessment: SpanAssessment
    decision: PolicyDecision | None


def load_snapshot(raw: bytes, artifacts: Mapping[str, bytes]) -> Snapshot:
    """The observation in ``raw``, when every artifact it names verifies.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid`` for an entry that does not
            parse; ``lineage_conflict`` when any artifact is missing, altered,
            unbound or not rebuilt from its sources.
    """
    entry = parse_entry(raw)
    findings = entry_findings(sha(raw), entry, artifacts)
    if findings:
        raise _conflict(",".join(sorted({finding.code for finding in findings})))
    decision = entry.policy_decision_sha256
    return Snapshot(
        key=sha(raw),
        entry=entry,
        manifest=parse_version_manifest(artifacts[entry.manifest_sha256]),
        assessment=parse_assessment(artifacts[entry.assessment_sha256]),
        decision=None if decision is None else parse_decision(artifacts[decision]),
    )


def diff_text(diff: VersionDiff) -> str:
    return canonical_text(diff)


def diff_snapshots(older: Snapshot, newer: Snapshot) -> VersionDiff:
    """What changed from ``older`` to ``newer``, and why; no text, nothing eligible.

    Raises:
        ForarbeiderRefusedError: ``lineage_conflict`` for two documents.
    """
    if older.entry.document_id != newer.entry.document_id:
        raise _conflict("two documents")
    body: dict[str, object] = {
        **_inventories(older.manifest, newer.manifest),
        "assessments": _assessments(older.assessment, newer.assessment),
        "decisions": _decisions(older.decision, newer.decision),
    }
    blockers = _blockers(older.decision, newer.decision)
    changed = any(body.values()) or bool(blockers.added or blockers.removed)
    causes = _causes(older.entry, newer.entry, changed=changed)
    observation = _observation(older, newer, semantic=changed)
    return VersionDiff.model_validate(
        {**_header(older, newer), **body, "blockers": blockers, "causes": causes}
        | {"observation": observation}
    )


def _observation(older: Snapshot, newer: Snapshot, *, semantic: bool) -> ObservationChange:
    before, after = older.entry, newer.entry
    return ObservationChange(
        observed_at_changed=before.observed_at != after.observed_at,
        provenance_changed=older.manifest.provenance != newer.manifest.provenance,
        manifest_sha256_changed=before.manifest_sha256 != after.manifest_sha256,
        assessment_sha256_changed=before.assessment_sha256 != after.assessment_sha256,
        policy_decision_sha256_changed=(
            before.policy_decision_sha256 != after.policy_decision_sha256
        ),
        authorization_sha256_changed=before.authorization_sha256 != after.authorization_sha256,
        semantic_change=semantic,
    )


def _header(older: Snapshot, newer: Snapshot) -> dict[str, object]:
    return {
        "document_id": newer.entry.document_id,
        "older_entry_sha256": older.key,
        "newer_entry_sha256": newer.key,
        "older_version_id": older.entry.version_id,
        "newer_version_id": newer.entry.version_id,
        "same_content_version": older.entry.version_id == newer.entry.version_id,
        "sources": _sources(older.entry, newer.entry),
        "older_policy_sha256": older.entry.policy_sha256,
        "newer_policy_sha256": newer.entry.policy_sha256,
    }


def _conflict(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.LINEAGE_CONFLICT, detail)


def _causes(older: LineageEntry, newer: LineageEntry, *, changed: bool) -> tuple[str, ...]:
    causes = ["source_content_changed"] if older.source_sha256s != newer.source_sha256s else []
    for field in _PROCESSING:
        if getattr(older.processing, field) != getattr(newer.processing, field):
            causes.append(f"{field}_changed")
    if older.policy_sha256 != newer.policy_sha256:
        causes.append("policy_changed")
    elif older.authorization_sha256 != newer.authorization_sha256:
        causes.append("authorization_changed")
    if causes:
        return tuple(causes)
    return ("unexplained_difference",) if changed else ("no_change",)


def _sources(older: LineageEntry, newer: LineageEntry) -> SetChange:
    before, after = set(older.source_sha256s), set(newer.source_sha256s)
    return SetChange(added=tuple(sorted(after - before)), removed=tuple(sorted(before - after)))


# --- inventories -------------------------------------------------------------------


def _inventories(
    older: VersionManifest, newer: VersionManifest
) -> dict[str, tuple[InventoryChange, ...]]:
    return {
        "chapters": _changes(_chapters(older), _chapters(newer)),
        "parts": _changes(_parts(older), _parts(newer)),
        "entries": _changes(_entries(older), _entries(newer)),
        "attachments": _changes(_attachments(older), _attachments(newer)),
    }


def _digest(value: BaseModel | dict[str, object], exclude: set[str] | None = None) -> str:
    data = value.model_dump(mode="json", exclude=exclude) if isinstance(value, BaseModel) else value
    return sha((json.dumps(data, **JSON_LAYOUT) + "\n").encode())


def _chapters(manifest: VersionManifest) -> dict[str, str]:
    return {f"chapter-{c.chapter_number}": _digest(c) for c in manifest.content.chapters_listed}


def _parts(manifest: VersionManifest) -> dict[str, str]:
    return {part.id: _digest(part, {"entries"}) for part in manifest.content.parts}


def _entries(manifest: VersionManifest) -> dict[str, str]:
    content = manifest.content
    every = [*(e for part in content.parts for e in part.entries), *content.document_gaps]
    return {entry.id: _digest(entry, {"rights", "privacy"}) for entry in every}


def _attachments(manifest: VersionManifest) -> dict[str, str]:
    return {item.url: _digest(item) for item in manifest.content.attachments.attachments}


def _changes(older: Mapping[str, str], newer: Mapping[str, str]) -> tuple[InventoryChange, ...]:
    changes = []
    for key in sorted(older.keys() | newer.keys()):
        before, after = older.get(key), newer.get(key)
        if before == after:
            continue
        change = "added" if before is None else "removed" if after is None else "changed"
        changes.append(
            InventoryChange.model_validate(
                {"key": key, "change": change, "older_sha256": before, "newer_sha256": after}
            )
        )
    return tuple(changes)


# --- assessments and decisions -----------------------------------------------------


def _assessments(older: SpanAssessment, newer: SpanAssessment) -> tuple[AssessmentChange, ...]:
    before = {item.entry_id: item for item in older.entries}
    changes = []
    for item in sorted(newer.entries, key=lambda entry: entry.entry_id):
        previous = before.get(item.entry_id)
        if previous is None:
            continue
        for field in _ASSESSED:
            old, new = str(getattr(previous, field)), str(getattr(item, field))
            if old != new:
                changes.append(
                    AssessmentChange.model_validate(
                        {"entry_id": item.entry_id, "field": field, "older": old, "newer": new}
                    )
                )
    return tuple(changes)


def _decisions(
    older: PolicyDecision | None, newer: PolicyDecision | None
) -> tuple[DecisionChange, ...]:
    if older is None or newer is None:
        return ()
    entries = _decided(
        "entry",
        {item.entry_id: item.decision for item in older.entries},
        {item.entry_id: item.decision for item in newer.entries},
    )
    attachments = _decided(
        "attachment",
        {item.url: item.decision for item in older.attachments},
        {item.url: item.decision for item in newer.attachments},
    )
    return (*entries, *attachments)


def _decided(
    scope: str, older: Mapping[str, str], newer: Mapping[str, str]
) -> tuple[DecisionChange, ...]:
    return tuple(
        DecisionChange.model_validate(
            {"scope": scope, "key": key, "older": older[key], "newer": newer[key]}
        )
        for key in sorted(older.keys() & newer.keys())
        if older[key] != newer[key]
    )


def _blockers(older: PolicyDecision | None, newer: PolicyDecision | None) -> BlockerChange:
    before = set() if older is None else set(older.document.blockers)
    after = set() if newer is None else set(newer.document.blockers)
    return BlockerChange(added=tuple(sorted(after - before)), removed=tuple(sorted(before - after)))
