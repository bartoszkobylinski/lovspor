"""AR3: the quarantine ledger's pure, deterministic engine (#738; §9.4, §9.6).

- :func:`merge` adds a run's findings to the ledger. A new finding opens a
  record; the same finding again changes nothing (so a retry gives the very
  same bytes); the same finding on other evidence or under another policy is
  appended as ``evidence_changed``, keeping ``first_seen`` and the history. A
  record a run no longer reports stays exactly as it was: absence never
  clears, and neither does a new version (it is another record) nor a policy
  change.
- :func:`settle` applies the owner exceptions the engine has just verified. A
  clearance matches a record only exactly: the same document, version and
  policy, the same evidence digests, and the finding, class or version it
  names. A record whose clearance no longer matches, or is no longer offered,
  is ``reinstated``. A stored ``cleared`` is never trusted: it is recomputed
  from verified exceptions on every run. An exclusion is permanent.
- :func:`quarantine_set` is what the planner must keep out of every batch.
- :func:`successor_findings` tells whether a ledger dropped or rewrote
  anything of the one before it.
- :func:`metrics_of` gives the §9.6 counters; quarantined documents stay in
  every denominator.

The ledger is private and canonical. Like the budget ledger, a local file
proves neither that it was not withheld nor that it was not replaced: atomic
persistence and an external anchor of its digest are AR5.
"""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from hashlib import sha256
from typing import NamedTuple

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_plan import Row
from lovspor.forarbeider.release_plan_models import ReleasePlan
from lovspor.forarbeider.release_quarantine_models import (
    ExclusionEntry,
    GapCount,
    QuarantinedDocument,
    QuarantineEvent,
    QuarantineException,
    QuarantineFinding,
    QuarantineLedger,
    QuarantineMetrics,
    QuarantineRecord,
    QuarantineSet,
    state_of,
)
from lovspor.forarbeider.release_signed import parsed
from lovspor.forarbeider.version_models import SourceState, canonical_text

EMPTY_LEDGER = QuarantineLedger()
_SPAN_SCOPES = frozenset({"span", "part"})


class VerifiedException(NamedTuple):
    """An owner exception the engine verified in this run, with the digest of its bytes."""

    record: QuarantineException
    sha256: str


# --- merging findings ------------------------------------------------------------------------


def merge(
    ledger: QuarantineLedger,
    findings: Iterable[QuarantineFinding],
    at: datetime,
    plan_id: str | None = None,
) -> QuarantineLedger:
    """``ledger`` with ``findings`` added; nothing is ever removed or reset.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid`` for conflicting findings of
            one quarantine id (:func:`finding_conflicts`).
    """
    records = {item.quarantine_id: item for item in ledger.records}
    for finding in _unique(findings):
        current = records.get(finding.quarantine_id)
        records[finding.quarantine_id] = (
            _opened(finding, at, plan_id)
            if current is None
            else _observed_again(current, finding, at, plan_id)
        )
    return _ledger(records.values(), ledger.exclusions)


def _unique(findings: Iterable[QuarantineFinding]) -> list[QuarantineFinding]:
    """One finding per quarantine id; consistent duplicates unite their evidence.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid`` when two findings of one
            quarantine id disagree on anything but their evidence (another
            policy, another category): neither is picked by input order.
    """
    merged: dict[str, QuarantineFinding] = {}
    for item in findings:
        known = merged.get(item.quarantine_id)
        if known is None:
            merged[item.quarantine_id] = item
            continue
        if _identity(known) != _identity(item):
            raise ForarbeiderRefusedError(
                RefusalReason.SCHEMA_INVALID, "conflicting quarantine findings"
            )
        evidence = tuple(sorted(set(known.evidence_sha256s) | set(item.evidence_sha256s)))
        merged[item.quarantine_id] = known.model_copy(update={"evidence_sha256s": evidence})
    return [merged[key] for key in sorted(merged)]


def _identity(finding: QuarantineFinding) -> dict[str, object]:
    """Everything a finding states except its evidence digests."""
    return finding.model_dump(exclude={"evidence_sha256s"})


def finding_conflicts(findings: Iterable[QuarantineFinding]) -> bool:
    """Whether two findings of one quarantine id disagree on anything but their evidence."""
    try:
        _unique(findings)
    except ForarbeiderRefusedError:
        return True
    return False


def _opened(finding: QuarantineFinding, at: datetime, plan_id: str | None) -> QuarantineRecord:
    event = QuarantineEvent(
        sequence=1,
        at=at,
        event="observed",
        evidence_sha256s=finding.evidence_sha256s,
        policy=finding.policy,
        plan_id=plan_id,
    )
    fields = finding.model_dump() | {
        "quarantine_id": finding.quarantine_id,
        "first_seen": at,
        "state": "active",
        "history": [event.model_dump()],
    }
    return QuarantineRecord.model_validate(fields)


def _observed_again(
    record: QuarantineRecord, finding: QuarantineFinding, at: datetime, plan_id: str | None
) -> QuarantineRecord:
    if (finding.evidence_sha256s, finding.policy) == (record.evidence_sha256s, record.policy):
        return record
    event = QuarantineEvent(
        sequence=len(record.history) + 1,
        at=at,
        event="evidence_changed",
        evidence_sha256s=finding.evidence_sha256s,
        policy=finding.policy,
        plan_id=plan_id,
    )
    return _appended(record, event)


def _appended(record: QuarantineRecord, event: QuarantineEvent) -> QuarantineRecord:
    history = (*record.history, event)
    fields = record.model_dump() | {
        "history": [item.model_dump() for item in history],
        "evidence_sha256s": event.evidence_sha256s,
        "policy": event.policy.model_dump() if event.policy else None,
        "state": state_of(history),
    }
    return QuarantineRecord.model_validate(fields)


def _ledger(
    records: Iterable[QuarantineRecord], exclusions: Iterable[ExclusionEntry]
) -> QuarantineLedger:
    return QuarantineLedger(
        records=tuple(sorted(records, key=lambda item: item.quarantine_id)),
        exclusions=tuple(sorted(exclusions, key=lambda item: item.document_id)),
    )


# --- owner exceptions ------------------------------------------------------------------------


def settle(
    ledger: QuarantineLedger, verified: Sequence[VerifiedException], at: datetime
) -> QuarantineLedger:
    """Every record's state under exactly the exceptions verified now; exclusions kept forever."""
    clearances = [item for item in verified if item.record.action == "clearance"]
    records = [_settled(record, clearances, at) for record in ledger.records]
    exclusions = {item.document_id: item for item in ledger.exclusions}
    for item in sorted(verified, key=lambda found: found.sha256):
        if item.record.action == "exclusion" and item.record.document_id not in exclusions:
            exclusions[item.record.document_id] = ExclusionEntry(
                document_id=item.record.document_id,
                reason_code=item.record.reason_code,
                exception_sha256=item.sha256,
                excluded_at=at,
            )
    return _ledger(records, exclusions.values())


def _settled(
    record: QuarantineRecord, clearances: Sequence[VerifiedException], at: datetime
) -> QuarantineRecord:
    matching = sorted(item.sha256 for item in clearances if clears(item.record, record))
    if matching and record.state == "active":
        return _appended(record, _event(record, at, "cleared", matching[0]))
    if not matching and record.state == "cleared":
        return _appended(record, _event(record, at, "reinstated", None))
    return record


def _event(
    record: QuarantineRecord, at: datetime, kind: str, exception: str | None
) -> QuarantineEvent:
    return QuarantineEvent.model_validate(
        {
            "sequence": len(record.history) + 1,
            "at": at,
            "event": kind,
            "evidence_sha256s": record.evidence_sha256s,
            "policy": record.policy,
            "exception_sha256": exception,
        }
    )


def clears(exception: QuarantineException, record: QuarantineRecord) -> bool:
    """Whether ``exception`` is a clearance for exactly this record, as it stands now."""
    if exception.action != "clearance" or record.policy is None:
        return False
    scope, policy = exception.scope, record.policy
    same_policy = (scope.policy_id, scope.policy_version, scope.policy_sha256) == (
        policy.policy_id,
        policy.policy_version,
        policy.policy_sha256,
    )
    exact = (
        same_policy
        and (exception.document_id, exception.version_id) == (record.document_id, record.version_id)
        and exception.evidence_sha256s == record.evidence_sha256s
    )
    if exception.target == "finding":
        return exact and exception.quarantine_id == record.quarantine_id
    if exception.target == "class":
        return exact and exception.code == record.code
    return exact


# --- what the planner sees ----------------------------------------------------------------


def quarantine_set(ledger: QuarantineLedger) -> QuarantineSet:
    """Every document with an active quarantine or an owner exclusion, and why."""
    reasons: dict[str, set[str]] = {}
    for record in ledger.records:
        if record.state == "active":
            reasons.setdefault(record.document_id, set()).add(
                f"quarantine:{record.category}:{record.code}"
            )
    for item in ledger.exclusions:
        reasons.setdefault(item.document_id, set()).add(f"owner_exclusion:{item.reason_code}")
    return QuarantineSet(
        ledger_sha256=ledger_sha256(ledger),
        documents=tuple(
            QuarantinedDocument(document_id=document, reasons=tuple(sorted(found)))
            for document, found in sorted(reasons.items())
        ),
    )


# --- the ledger's bytes ----------------------------------------------------------------------


def ledger_text(ledger: QuarantineLedger) -> str:
    return canonical_text(ledger)


def ledger_sha256(ledger: QuarantineLedger) -> str:
    return sha256(ledger_text(ledger).encode()).hexdigest()


def parse_ledger(raw: bytes) -> QuarantineLedger:
    """The ledger in ``raw``, which must be its canonical bytes.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid`` for anything else, including a
            record whose state does not follow from its history.
    """
    record, _ = parsed(QuarantineLedger, raw)
    if not isinstance(record, QuarantineLedger):
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "quarantine ledger")
    return record


def latest_event(ledger: QuarantineLedger) -> datetime | None:
    times = [item.history[-1].at for item in ledger.records]
    times += [item.excluded_at for item in ledger.exclusions]
    return max(times, default=None)


def successor_findings(previous: QuarantineLedger, current: QuarantineLedger) -> tuple[str, ...]:
    """How ``current`` fails to extend ``previous``; empty when it only appends."""
    found: set[str] = set()
    now = {item.quarantine_id: item for item in current.records}
    for record in previous.records:
        later = now.get(record.quarantine_id)
        if later is None:
            found.add("quarantine_record_dropped")
        elif later.history[: len(record.history)] != record.history:
            found.add("quarantine_history_rewritten")
    if not set(previous.exclusions) <= set(current.exclusions):
        found.add("quarantine_exclusion_dropped")
    return tuple(sorted(found))


# --- metrics ---------------------------------------------------------------------------------


def metrics_of(
    plan: ReleasePlan, rows: Sequence[Row], ledger: QuarantineLedger
) -> QuarantineMetrics:
    """The §9.6 counters; a quarantined document still counts as ingested and complete."""
    expected = {d.document_id for d in plan.documents if d.disposition != "outside_inventory"}
    ingested = {d.document_id for d in plan.documents if d.document_id in expected and d.version_id}
    complete = _source_complete({r.selection.document_id: r for r in rows}, ingested)
    restricted = {item.document_id for item in quarantine_set(ledger).documents}
    active = [r for r in ledger.records if r.state == "active"]
    return QuarantineMetrics(
        documents_expected=len(expected),
        documents_ingested=len(ingested),
        documents_source_complete=len(complete),
        documents_quarantined=len(restricted & {d.document_id for d in plan.documents}),
        documents_excluded_by_owner=len(ledger.exclusions),
        documents_planned=plan.coverage.planned,
        spans_rights_unresolved=_count(active, "rights", _SPAN_SCOPES),
        spans_privacy_unresolved=_count(active, "privacy", _SPAN_SCOPES),
        attachments_unpublishable=_count(active, "attachment", None),
        release_gaps=_gaps(active, ledger.exclusions),
    )


def _source_complete(rows: Mapping[str, Row], ingested: set[str]) -> set[str]:
    return {
        document
        for document in ingested
        if document in rows and rows[document].selection.source_state is SourceState.SOURCE_COMPLETE
    }


def _count(
    records: Sequence[QuarantineRecord], category: str, scopes: frozenset[str] | None
) -> int:
    return sum(
        1
        for item in records
        if item.category == category and (scopes is None or item.scope.kind in scopes)
    )


def _gaps(
    records: Sequence[QuarantineRecord], exclusions: Sequence[ExclusionEntry]
) -> tuple[GapCount, ...]:
    counted: Counter[str] = Counter(item.category for item in records)
    if exclusions:
        counted["owner_exclusion"] = len(exclusions)
    return tuple(GapCount(category=name, count=counted[name]) for name in sorted(counted))
