"""Compare a replayed state with the consolidated text Lovdata publishes.

The consolidated member is an oracle for the *current* text only. Agreement
there says the replay reached the same end state; it does not certify any
intermediate state, which only the dated evidence trail can speak for. Every
section lands in exactly one class; no law text leaves this module, only
section keys, classes and counts.
"""

from collections import Counter
from typing import Literal

from lovspor.legaltime.document import Document, Frozen, Provision
from lovspor.legaltime.replay import ProvisionState, ReplayState

Verdict = Literal[
    "exact_raw",
    "exact_normalized",
    "text_differs",
    "unresolved",
    "only_in_oracle",
    "only_in_replay",
]


class SectionVerdict(Frozen):
    key: str
    verdict: Verdict
    detail: str = ""
    applied: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()
    oracle_notes: int = 0


class Verification(Frozen):
    act: str
    at: str
    oracle_member: str
    oracle_sha256: str
    counts: dict[str, int]
    sections: tuple[SectionVerdict, ...]
    notes_without_replay_event: tuple[str, ...]
    replay_events_without_note: tuple[str, ...]


def _texts(provision: Provision | ProvisionState) -> tuple[str, tuple[str, ...]]:
    return provision.heading, tuple(ledd.text for ledd in provision.ledd)


def _raw(provision: Provision | ProvisionState) -> tuple[str, tuple[str, ...]]:
    return provision.heading, tuple(ledd.xml for ledd in provision.ledd)


def _difference(replayed: ProvisionState, oracle: Provision) -> str:
    if replayed.heading != oracle.heading:
        return "heading differs"
    if len(replayed.ledd) != len(oracle.ledd):
        return f"ledd count {len(replayed.ledd)} vs {len(oracle.ledd)}"
    differing = [
        str(i + 1)
        for i, (a, b) in enumerate(zip(replayed.ledd, oracle.ledd, strict=True))
        if a.text != b.text
    ]
    return f"ledd {', '.join(differing)} differ"


def _verdict(replayed: ProvisionState, oracle: Provision) -> SectionVerdict:
    if replayed.status == "unresolved":
        verdict: Verdict = "unresolved"
    elif _raw(replayed) == _raw(oracle):
        verdict = "exact_raw"
    elif _texts(replayed) == _texts(oracle):
        verdict = "exact_normalized"
    else:
        verdict = "text_differs"
    detail = _difference(replayed, oracle) if verdict in {"text_differs", "unresolved"} else ""
    return SectionVerdict(
        key=replayed.key, verdict=verdict, detail=detail, applied=replayed.applied,
        problems=replayed.problems, oracle_notes=len(oracle.notes),
    )  # fmt: skip


def _note_checks(
    replayed: dict[str, ProvisionState], oracle: dict[str, Provision]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Each oracle note should meet a replay event on that section, and vice versa."""
    noted = {key for key, provision in oracle.items() if provision.notes}
    touched = {key for key, state in replayed.items() if state.applied or state.problems}
    return tuple(sorted(noted - touched)), tuple(sorted(touched - noted))


def verify(state: ReplayState, oracle_document: Document) -> Verification:
    """One verdict per section of the replayed state and of the oracle."""
    replayed = {p.key: p for p in state.provisions}
    oracle = {p.key: p for p in oracle_document.provisions}
    sections: list[SectionVerdict] = []
    for key in list(replayed) + [k for k in oracle if k not in replayed]:
        if key in replayed and key in oracle:
            sections.append(_verdict(replayed[key], oracle[key]))
        elif key in replayed:
            sections.append(SectionVerdict(key=key, verdict="only_in_replay"))
        else:
            sections.append(SectionVerdict(key=key, verdict="only_in_oracle"))
    missing_events, missing_notes = _note_checks(replayed, oracle)
    return Verification(
        act=state.act, at=state.at.isoformat(), oracle_member=oracle_document.member,
        oracle_sha256=oracle_document.sha256,
        counts=dict(sorted(Counter(s.verdict for s in sections).items())),
        sections=tuple(sections), notes_without_replay_event=missing_events,
        replay_events_without_note=missing_notes,
    )  # fmt: skip
