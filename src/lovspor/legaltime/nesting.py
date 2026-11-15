"""Amendments nested inside another act's amending provision (#893, ADR-0024 R4).

An amending law can replace a host act's "Endringer i andre lover" section before the host
commences. ``lov/2013-04-05-12`` part I: "§ 102 skal lyde:" followed by vergemålsloven § 102
whose only ledd is "Fra den tid loven trer i kraft gjøres følgende endringer i andre lover:".
The numbered items after it in the same part are that provision's list: flat siblings in the
announcement's markup, not children of it. Such an item takes effect when the host provision
does, never by the outer law's own commencement clause.

This module finds the regions after ``instructions`` has read the member, from the operations'
own tags (``#<part>:<label>:<node>``) and the part's nodes; ``hosts`` dates them. Every shape
other than the verified one is refused, never guessed.

A host can also be renamed and replaced at once (#918): ``lov/2009-06-19-74`` part I, "Nåværende
§ 401 blir § 411 som innleder tredje del og som skal lyde:" and "Nåværende § 402 blir § 412 og
skal lyde:", each provision ending in a list of amendments to other acts. Its items are a
region like any other, so the outer law's "straks" never dates them; the region is refused
(the host's unit in its own act is not followed across the renaming), so they stay
``unresolved``.
"""

import re
from collections import Counter

from lovspor.legaltime import attribution
from lovspor.legaltime.document import Document, Frozen, Node, Provision
from lovspor.legaltime.instructions import Interpretation, Operation
from lovspor.legaltime.text import split_sentences

# A host's last ledd introduces a list of amendments to other acts (broad: opens a region).
_LIST_INTRO = re.compile(
    r"endring\w*\s+i\s+(?:andre\s+lover|anna\s+lovgjeving|annan\s+lovgjeving|annen\s+lovgivning)"
    r"\s*:\s*$",
    re.I,
)
# The one wording verified against its host's commencement (S27 of the ADR-0024 evidence).
_VERIFIED = re.compile(
    r"^Fra\s+den\s+tid\s+loven\s+trer\s+i\s+kraft\s+gjøres\s+følgende\s+endringer\s+i\s+andre\s+lover:$"
)
_NUMBERED = re.compile(r"^\d{1,3}\s?[a-z]?[.)]\s")  # ``50.``, ``1 a.``, ``3)``: a list item

NESTED = "nested amendment: "
WORDING = NESTED + "host provision's wording outside the grammar"
BOUNDARY = NESTED + "region boundary not proven (an unnumbered intro inside the list)"
OWN_CLAUSE = NESTED + "region states a commencement of its own"
SECOND_HOST = NESTED + "region holds a second host provision"
LEADING = NESTED + "region does not open with a numbered item"
CONTINUES = NESTED + "region names the host act itself"
HOST_MISSING = NESTED + "host act's enacting announcement not in the archives"
SECTION_MISSING = NESTED + "host section not in the host act's enacted text"
REPLACEMENT_UNDATED = NESTED + "replacement of the host provision is not dated"
SPLIT = NESTED + "host section's commencement is split below the section or by part"
REPLACED_LATE = NESTED + "host provision replaced after it commenced"
UNPROVEN = NESTED + "host provision's commencement not proven"
RENAMED = NESTED + "host provision renamed: its unit in the host act is not followed (#918)"
_HOSTING = frozenset({"replace_section", "renumber"})  # "skal lyde:", "blir § N og skal lyde:"


class Nest(Frozen):
    """One region: the operation replacing the host section, its members, and any refusal."""

    host_op: str
    host_act: str | None
    host_section: str | None
    members: tuple[str, ...] = ()  # the operation ids that sit in the host's list
    problem: str | None = None


class Nesting(Frozen):
    """The member's regions, and its own commencement clauses without any read inside them."""

    nests: tuple[Nest, ...] = ()
    clauses: tuple[str, ...] = ()


def _place(op_id: str) -> tuple[int, int]:
    """(part index, node index) from an operation's tag ``#<part>:<label>:<node>[.<offset>]``."""
    tag = op_id.split("#", 1)[1]
    node = tag.rsplit(":", 1)[1]  # a part label may itself hold a colon
    return int(tag.split(":", 1)[0]), int(node.split(".", 1)[0].split("-", 1)[0])


def _opens(op: Operation) -> bool:
    provision = op.new_provision
    return op.kind in _HOSTING and _host_list(provision)


def _host_list(provision: Provision | None) -> bool:
    """The replacement text is a host provision: its last ledd introduces a list of amendments."""
    return (
        provision is not None
        and bool(provision.ledd)
        and bool(_LIST_INTRO.search(provision.ledd[-1].text.strip()))
    )


def _wording(provision: Provision | None) -> str | None:
    """None for the verified shape (one ledd, the verified sentence); else the refusal."""
    texts = [" ".join(ledd.text.split()) for ledd in provision.ledd] if provision else []
    return None if len(texts) == 1 and _VERIFIED.match(texts[0]) else WORDING


def nesting(document: Document, reading: Interpretation) -> Nesting:
    """Every host region of the member; a region runs from its host to the end of its part."""
    hosts = [op for op in reading.operations if _opens(op)]
    taken: set[str] = set()
    nests = []
    for host in hosts:
        if host.id in taken:
            continue  # a host inside an earlier host's list is that region's refusal
        nest = _region(document, reading, host)
        taken.update(nest.members)
        nests.append(nest)
    inside = {n.host_op: set(_sentences(_region_nodes(document, n))) for n in nests}
    outside = Counter(_sentences(_outside_nodes(document, nests)))
    nests = [_clause_refusal(n, inside[n.host_op], reading.clauses) for n in nests]
    return Nesting(nests=tuple(nests), clauses=_outer_clauses(reading.clauses, inside, outside))


def _outer_clauses(
    clauses: tuple[str, ...], inside: dict[str, set[str]], outside: Counter[str]
) -> tuple[str, ...]:
    """Each copy of a clause read in a list is the host's; only copies read outside stay ours."""
    kept: list[str] = []
    for clause in clauses:
        key = _norm(clause)
        in_list = any(key in found for found in inside.values())
        if not in_list or kept.count(clause) < outside[key]:
            kept.append(clause)
    return tuple(kept)


def _region(document: Document, reading: Interpretation, host: Operation) -> Nest:
    part, start = _place(host.id)
    members = [op for op in reading.operations if _after(op, part, start)]
    nodes = [n for n in document.parts[part].nodes[start + 1 :] if n.kind == "paragraph"]
    checks = (
        (_wording(host.new_provision) is not None, WORDING),
        (host.kind == "renumber", RENAMED),
        (any(_opens(op) for op in members), SECOND_HOST),
        (not nodes or not _NUMBERED.match(nodes[0].text), LEADING),
        (any(_unnumbered_intro(node.text) for node in nodes), BOUNDARY),
        (any(op.act == host.act for op in members), CONTINUES),
    )
    problem = next((reason for failed, reason in checks if failed), None)
    return Nest(
        host_op=host.id, host_act=host.act, host_section=host.address.section,
        members=tuple(op.id for op in members), problem=problem,
    )  # fmt: skip


def _after(op: Operation, part: int, start: int) -> bool:
    where = _place(op.id)
    return where[0] == part and where[1] > start


def _unnumbered_intro(text: str) -> bool:
    intro = attribution.is_intro(text) or attribution.opens_block(text)
    return intro and not _NUMBERED.match(text)


def _region_nodes(document: Document, nest: Nest) -> list[Node]:
    part, start = _place(nest.host_op)
    return list(document.parts[part].nodes[start + 1 :])


def _outside_nodes(document: Document, nests: list[Nest]) -> list[Node]:
    regions = {_place(n.host_op) for n in nests}
    return [
        node
        for index, part in enumerate(document.parts)
        for position, node in enumerate(part.nodes)
        if not any(p == index and position > start for p, start in regions)
    ]


def _sentences(nodes: list[Node]) -> list[str]:
    """Instruction paragraphs' sentences, whole and split, once per node; payload never counts."""
    found: list[str] = []
    for node in nodes:
        if node.kind == "paragraph":
            found.extend({" ".join(node.text.split()), *split_sentences(node.text)})
    return found


def _norm(clause: str) -> str:
    return " ".join(clause.split())


def _clause_refusal(nest: Nest, inside: set[str], clauses: tuple[str, ...]) -> Nest:
    """A commencement read inside the list refuses it; no precedence is invented (D6)."""
    if nest.problem is None and any(_norm(c) in inside for c in clauses):
        return nest.model_copy(update={"problem": OWN_CLAUSE})
    return nest
