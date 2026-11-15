"""The effective date of an amendment nested in a host provision (#893, ADR-0024 R4).

Five dates stay apart: the outer law's own commencement (it dates the replacement of the host
section, never the items), the host provision's commencement (the host act's unit, read by the
same per-unit machinery that dates any act), the nested item's effective date (= that
commencement, when proven), the target act's commencement (never consulted here; ADR-0024 R1
defers application to it in the replay), and ``known_from`` (the later publication of the two
instruments the date rests on).

The item is ``dated`` only when every link is proven: the region's shape is the verified one,
the replacement of the host section is dated no later than the host commences, the host act's
enacting announcement and the host section are in the archives, and the host section's own
commencement is dated, whole, by the host's own instruments. Anything else is ``unresolved``
with its reason; a target's commencement or the consolidation never supplies a date.
"""

from datetime import date

from lovspor.legaltime.baseline import baseline_commencement
from lovspor.legaltime.commencement import Decisions, DecisionsLike, Timing, _decisions
from lovspor.legaltime.document import Document
from lovspor.legaltime.nesting import (
    HOST_MISSING,
    REPLACED_LATE,
    REPLACEMENT_UNDATED,
    SECTION_MISSING,
    SPLIT,
    UNPROVEN,
    Nest,
    Nesting,
)


def nested_timings(
    regions: Nesting, timings: dict[str, Timing], decisions: DecisionsLike
) -> dict[str, Timing]:
    """``timings`` with every nested operation dated by its host, never by the outer law."""
    hosts = _decisions(decisions)
    result = dict(timings)
    for nest in regions.nests:
        timing = _nest_timing(nest, timings[nest.host_op], hosts)
        result.update(dict.fromkeys(nest.members, timing))
    return result


def _refused(reason: str, replacement: Timing, *more: Timing) -> Timing:
    """Unproven, but known from the outer law's publication (#858 counts it from that day)."""
    proofs = tuple(proof for source in (replacement, *more) for proof in source.proofs)
    known = replacement.known_from
    return Timing(status="unresolved", reason=reason, known_from=known, proofs=proofs)


def _nest_timing(nest: Nest, replacement: Timing, hosts: Decisions) -> Timing:
    if nest.problem is not None:
        return _refused(nest.problem, replacement)
    if replacement.dated_on is None:
        return _refused(REPLACEMENT_UNDATED, replacement)
    host = next((a for a in hosts.host_acts if a.header.refid == nest.host_act), None)
    if host is None:
        return _refused(HOST_MISSING, replacement)
    if nest.host_section not in {p.key for p in host.provisions}:
        return _refused(SECTION_MISSING, replacement)
    return _from_host(host, str(nest.host_section), replacement, hosts)


def _from_host(host: Document, section: str, replacement: Timing, hosts: Decisions) -> Timing:
    chapters = {p.key: p.chapter for p in host.provisions if p.chapter is not None}
    context = Decisions(documents=hosts.host_decisions, chapter_of=chapters)
    commencement = baseline_commencement(host, context, [])
    split = commencement.ledd_timings(section) or commencement.finer_than_ledd(section)
    if split or any(entry.part is not None for entry in commencement.units):
        return _refused(SPLIT, replacement)
    own = commencement.section_timing(section, chapters.get(section), ())
    effective, replaced = own.dated_on, replacement.dated_on
    if effective is None or replaced is None:
        return _refused(f"{UNPROVEN} ({own.reason or own.status})", replacement, own)
    if replaced > effective:
        return _refused(REPLACED_LATE, replacement, own)
    return _combined(own, replacement, effective)


def _combined(own: Timing, replacement: Timing, effective: date) -> Timing:
    known = max((d for d in (own.known_from, replacement.known_from) if d), default=None)
    return Timing(
        status="dated", effective=effective, known_from=known,
        retroactive=_retroactive(effective, known), proofs=(*replacement.proofs, *own.proofs),
    )  # fmt: skip


def _retroactive(effective: date, known: date | None) -> bool:
    return known is not None and effective < known
