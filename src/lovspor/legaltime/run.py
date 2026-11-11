"""The pilot end to end: evidence → events → states per date → report.

The report is the pilot's only product. It carries identities, dates,
verdicts, counts and the instruction addresses ("§ 89 skal lyde:"); the
sentences a date was read from are carried as digests. No provision text
leaves the archive, so a report can be committed as evidence.
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from lovspor.legaltime.archive import Evidence, pins
from lovspor.legaltime.baseline import Commencement, baseline_commencement
from lovspor.legaltime.commencement import Decisions, resolve_timings
from lovspor.legaltime.document import Document
from lovspor.legaltime.instructions import Operation, interpret
from lovspor.legaltime.replay import Event, ReplayState, replay
from lovspor.legaltime.verify import verify

REPLAY_VERSION = "legaltime-replay-5"  # phase 4B: proven attribution of other intros (#861)


def _stamp(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def chapter_map(evidence: Evidence) -> dict[str, str]:
    """Section key → chapter, from the act's enacting structure and every chapter-stamped insertion.

    Text existence only (ADR-0020 Decision 3a): a key is placed in the chapter the
    announcement that carries it names, whether or not that text is in force at any date.
    """
    placed = {p.key: p.chapter for p in evidence.baseline.provisions if p.chapter is not None}
    for amender in evidence.amenders:
        for op in interpret(amender).operations:
            new = op.new_provision
            if op.act == evidence.act and new is not None and new.chapter is not None:
                placed.setdefault(new.key, new.chapter)
    return placed


def _events_of(amender: Document, act: str | None, decisions: Decisions, start: int) -> list[Event]:
    """The member's operations aimed at ``act``; ``None`` selects those naming no act."""
    result = interpret(amender)
    timings = resolve_timings(amender, result.clauses, result.operations, decisions)
    events = []
    for index, op in enumerate(result.operations):
        if op.act != act:
            continue
        events.append(
            Event(
                sequence=start + index,
                law=amender.header.refid,
                member=amender.member,
                operation=op,
                timing=timings[op.id],
                published_at=amender.header.published_at,
            )
        )
    return events


def decisions_for(evidence: Evidence) -> Decisions:
    return Decisions(documents=evidence.decisions, chapter_of=chapter_map(evidence))


def _collect(evidence: Evidence, act: str | None) -> list[Event]:
    events: list[Event] = []
    decisions = decisions_for(evidence)
    for amender in evidence.amenders:
        events.extend(_events_of(amender, act, decisions, len(events)))
    return events


def events_for(evidence: Evidence) -> list[Event]:
    """Every operation aimed at the act, across all amenders, in document order."""
    return _collect(evidence, evidence.act)


def unattributed_for(evidence: Evidence) -> list[Event]:
    """Every operation an amender states without a recognisable act (ADR-0020 D7, #859).

    Each may be aimed at the act; the replay never applies one, and keeps every state it
    may be in effect at unproven.
    """
    return _collect(evidence, None)


def commencement_for(evidence: Evidence, events: list[Event]) -> Commencement:
    """The act's commencement per unit, from its header, its decisions and other instruments."""
    return baseline_commencement(evidence.baseline, decisions_for(evidence), events)


def state_at(evidence: Evidence, events: list[Event], at: date) -> ReplayState:
    every = [*events, *unattributed_for(evidence)]
    return replay(evidence.baseline, commencement_for(evidence, events), every, at)


def _operation_record(op: Operation) -> dict[str, Any]:
    payload = {
        "provision": op.new_provision.key if op.new_provision else None,
        "ledd": len(op.ledd),
    }
    return {
        "id": op.id,
        "kind": op.kind,
        "part": op.part,
        "instruction": op.instruction,
        "address": op.address.model_dump(exclude_defaults=True),
        "destination": op.destination,
        "change_part": list(op.change_part),
        "host_section": op.host_section,
        "absorbed_by": op.absorbed_by,
        "payload": payload,
    }


def _event_record(event: Event) -> dict[str, Any]:
    return {
        "law": event.law,
        "member": event.member,
        "published_at": _stamp(event.published_at),
        "operation": _operation_record(event.operation),
        "timing": event.timing.model_dump(mode="json"),
    }


def _provision_record(state: ReplayState) -> list[dict[str, Any]]:
    """Per provision; the legal-effectiveness fields appear only where they say something."""
    records = []
    for p in state.provisions:
        record: dict[str, Any] = {
            "key": p.key,
            "status": p.status,
            "repealed": p.repealed,
            "ledd": len(p.ledd),
            "applied": list(p.applied),
            "problems": list(p.problems),
        }
        if p.in_force != "in_force":
            record["in_force"] = p.in_force
        if p.ledd_not_in_force:
            record["ledd_not_in_force"] = list(p.ledd_not_in_force)
        records.append(record)
    return records


def _state_record(state: ReplayState) -> dict[str, Any]:
    return {
        "at": state.at.isoformat(),
        "status": state.status,
        "applied": list(state.applied),
        "future": list(state.future),
        "pending": list(state.pending),
        "unresolved": [list(pair) for pair in state.unresolved],
        "act_level_problems": list(state.act_level_problems),
        "withheld": list(state.withheld),
        "not_in_force": list(state.not_in_force),
        "pending_commencement": list(state.pending_commencement),
        "unresolved_commencement": list(state.unresolved_commencement),
        "provisions": _provision_record(state),
    }


def _unattributed(document: Document) -> int:
    """Operations the amending law states without a recognisable act: never silently lost."""
    return sum(1 for op in interpret(document).operations if op.act is None)


def _member_record(document: Document, role: str) -> dict[str, Any]:
    return {
        "role": role,
        "member": document.member,
        "sha256": document.sha256,
        "refid": document.header.refid,
        "title": document.header.title,
        "date_in_force": document.header.date_in_force,
        "published_at": _stamp(document.header.published_at),
        "unattributed_operations": _unattributed(document) if role == "amending" else 0,
    }


def _members(evidence: Evidence) -> list[dict[str, Any]]:
    return [
        _member_record(evidence.baseline, "enacting"),
        *(_member_record(d, "amending") for d in evidence.amenders),
        *(_member_record(d, "decision") for d in evidence.decisions),
    ]


def _archive_records(evidence: Evidence) -> list[dict[str, Any]]:
    return [
        {
            "archive": i.archive,
            "sha256": i.sha256,
            "members": len(i.members),
            "horizon": i.horizon,
            "skipped_members": list(i.skipped),
        }
        for i in sorted(evidence.archives, key=lambda i: i.archive)
    ]


def build_report(
    evidence: Evidence, dates: list[date], oracle: Document | None, archives: list[Path]
) -> dict[str, Any]:
    """Deterministic report: same archives, same act, same dates → identical JSON."""
    events, unattributed = events_for(evidence), unattributed_for(evidence)
    commencement = commencement_for(evidence, events)
    every = [*events, *unattributed]
    states = [replay(evidence.baseline, commencement, every, at) for at in sorted(dates)]
    report: dict[str, Any] = {
        "replay_version": REPLAY_VERSION,
        "act": evidence.act,
        "inputs": pins(sorted(archives, key=lambda p: p.name)),
        "archives": _archive_records(evidence),
        "members": _members(evidence),
        "baseline_timing": commencement.default.model_dump(mode="json"),
        "commencement": _commencement_record(commencement),
        "events": [_event_record(e) for e in events],
        "unattributed_events": [_event_record(e) for e in unattributed],
        "states": [_state_record(s) for s in states],
    }
    if oracle is not None:
        report["oracle"] = {"member": oracle.member, "sha256": oracle.sha256}
        report["verification"] = _verifications(states, oracle)
    return report


def _commencement_record(commencement: Commencement) -> dict[str, Any]:
    """The default and every unit dated apart from it, with each unit's timing and proofs."""
    return {
        "default": commencement.default.model_dump(mode="json"),
        "units": [
            {
                "unit": entry.unit.describe(),
                "part": entry.part,
                "timing": entry.timing.model_dump(mode="json"),
            }
            for entry in commencement.units
        ],
    }


def _verifications(states: list[ReplayState], oracle: Document) -> list[dict[str, Any]]:
    """One JSON-native verdict record per replayed state; non-replayed states have none."""
    return [
        json.loads(verify(state, oracle).model_dump_json())
        for state in states
        if state.status == "replayed"
    ]
