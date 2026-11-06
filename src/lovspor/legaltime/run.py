"""The pilot end to end: evidence → events → states per date → report.

The report is the pilot's only product. It carries identities, dates,
verdicts and counts, never provision text, so it can be committed as evidence
without moving any Lovdata bytes into Git.
"""

from datetime import date, datetime
from pathlib import Path
from typing import Any

from lovspor.legaltime.archive import Evidence, pins
from lovspor.legaltime.commencement import baseline_timing, resolve_timings
from lovspor.legaltime.document import Document
from lovspor.legaltime.instructions import Operation, interpret
from lovspor.legaltime.replay import Event, ReplayState, replay
from lovspor.legaltime.verify import verify

REPLAY_VERSION = "legaltime-replay-1"


def _stamp(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _events_of(
    amender: Document, act: str, decisions: tuple[Document, ...], start: int
) -> list[Event]:
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


def events_for(evidence: Evidence) -> list[Event]:
    """Every operation aimed at the act, across all amenders, in document order."""
    events: list[Event] = []
    for amender in evidence.amenders:
        events.extend(_events_of(amender, evidence.act, evidence.decisions, len(events)))
    return events


def state_at(evidence: Evidence, events: list[Event], at: date) -> ReplayState:
    timing = baseline_timing(evidence.baseline, evidence.decisions)
    return replay(evidence.baseline, timing, events, at)


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
        "address": op.address.model_dump(exclude_none=True),
        "destination": op.destination,
        "change_part": list(op.change_part),
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
    return [
        {
            "key": p.key,
            "status": p.status,
            "repealed": p.repealed,
            "ledd": len(p.ledd),
            "applied": list(p.applied),
            "problems": list(p.problems),
        }
        for p in state.provisions
    ]


def _state_record(state: ReplayState) -> dict[str, Any]:
    return {
        "at": state.at.isoformat(),
        "status": state.status,
        "applied": list(state.applied),
        "future": list(state.future),
        "pending": list(state.pending),
        "unresolved": [list(pair) for pair in state.unresolved],
        "act_level_problems": list(state.act_level_problems),
        "provisions": _provision_record(state),
    }


def _member_record(document: Document, role: str) -> dict[str, Any]:
    return {
        "role": role,
        "member": document.member,
        "sha256": document.sha256,
        "refid": document.header.refid,
        "title": document.header.title,
        "date_in_force": document.header.date_in_force,
        "published_at": _stamp(document.header.published_at),
    }


def _members(evidence: Evidence) -> list[dict[str, Any]]:
    return [
        _member_record(evidence.baseline, "enacting"),
        *(_member_record(d, "amending") for d in evidence.amenders),
        *(_member_record(d, "decision") for d in evidence.decisions),
    ]


def _archive_records(evidence: Evidence) -> list[dict[str, Any]]:
    return [
        {"archive": i.archive, "sha256": i.sha256, "members": len(i.members), "horizon": i.horizon}
        for i in evidence.archives
    ]


def build_report(
    evidence: Evidence, dates: list[date], oracle: Document | None, archives: list[Path]
) -> dict[str, Any]:
    """Deterministic report: same archives, same act, same dates → identical JSON."""
    events = events_for(evidence)
    states = [state_at(evidence, events, at) for at in sorted(dates)]
    report: dict[str, Any] = {
        "replay_version": REPLAY_VERSION,
        "act": evidence.act,
        "inputs": pins(archives),
        "archives": _archive_records(evidence),
        "members": _members(evidence),
        "baseline_timing": baseline_timing(evidence.baseline, evidence.decisions).model_dump(
            mode="json"
        ),
        "events": [_event_record(e) for e in events],
        "states": [_state_record(s) for s in states],
    }
    if oracle is not None:
        report["oracle"] = {"member": oracle.member, "sha256": oracle.sha256}
        report["verification"] = [verify(s, oracle).model_dump(mode="json") for s in states]
    return report
