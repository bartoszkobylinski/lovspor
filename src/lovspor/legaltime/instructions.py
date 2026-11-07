"""Amendment instructions of an announcing law, read into typed operations.

An amending law says what to do in prose: ``§ 89 skal lyde:``, ``Ny § 88 a
skal lyde:``, ``§ 4 oppheves.``. This module recognises a closed grammar of
such sentences. Anything outside it becomes an ``unsupported`` operation that
still names its target, so the replay can mark the provision unresolved
instead of guessing. Nothing here applies an operation.
"""

import re
from typing import Literal

from lovspor.legaltime.document import Document, Frozen, Ledd, Node, Provision
from lovspor.legaltime.text import (
    ORDINAL_ALT,
    ORDINALS,
    act_refid,
    ordinal,
    section_key_of,
    split_sentences,
)

OpKind = Literal[
    "replace_section", "insert_section", "replace_ledd", "insert_ledd", "replace_punktum",
    "insert_punktum", "replace_bokstav", "repeal_section", "repeal_ledd", "repeal_punktum",
    "move_ledd", "renumber", "replace_heading", "substitute_term", "unsupported",
]  # fmt: skip

_SEC = r"§\s*\d+(?:-\d+)?(?:\s*[a-hj-zæøå](?![a-zæøå]))?"  # "i" is the preposition, not a letter
_ORD = ORDINAL_ALT
_LEDD = rf"(?P<l1>{_ORD})(?:\s+(?P<lj>og|til)\s+(?P<l2>{_ORD}))?\s+ledd"
_PUNKT = rf"(?P<p1>{_ORD})(?:\s+(?P<pj>og|til)\s+(?P<p2>{_ORD}))?\s+punktum"
_NEW = r"ny(?:tt|e)?"
_LYDE = r"(?:skal\s+)?lyde(?:\s+slik)?:"
_REPEAL = r"(?:oppheves|opphevast|blir\s+oppheva|vert\s+oppheva|blir\s+opphevet)\.?"
_INTRO = re.compile(
    r"^(?:\d+\.\s*)?I\s+(?:midlertidig\s+)?(?P<act>lov\s+\d{1,2}\.\s*\w+\s+\d{4}(?:\s+nr\.\s*\d+)?)\b.*?"
    r"\s+(?:(?:gjøres|gjerast|blir|vert|skal)\s+(?P<rest>.*)|(?P<repeal>oppheves|opphevast|blir\s+oppheva|vert\s+oppheva)\.?)$",
    re.S,
)
_COMMENCEMENT_SHAPED = re.compile(
    r"\bi\s+kraft\b|\bgjeld(?:er)?\s+fr[aå]\b|\bte[kr]\s+til\s+å\s+gjelde\b|\btar\s+til\s+å\s+gjelde\b",
    re.I,
)
_INTRO_ONLY = re.compile(
    r"^(?:det\s+gjort\s+)?(?:følgende|følgjande|desse)\s+endring(?:er|ar|ane|a)?"
    r"(?:\s+gjerast|\s+gjorde)?:$"
)
_SUBSTITUTE = re.compile(
    r"^I\s+(?:følgjande|følgende)\s+(?:lovføresegner|bestemmelser|lover|føresegner)\s+"
    r"(?:vert|skal|blir)\s+(?:ordet|ordene|orda|uttrykket|uttrykka)?\s*«(?P<old>[^»]+)»"
    r"(?:\s+og\s+«(?P<old2>[^»]+)»)?\s+(?:endra|endres|endrast)\s+til\s+(?:henholdsvis\s+)?"
    r"«(?P<new>[^»]+)»(?:\s+og\s+«(?P<new2>[^»]+)»)?:$"
)
_RULES: list[tuple[OpKind, re.Pattern[str]]] = [
    ("insert_section", re.compile(rf"^{_NEW}\s+(?P<sec>{_SEC})\s+{_LYDE}$", re.I)),
    ("replace_heading", re.compile(rf"^(?P<sec>{_SEC})\s+(?:paragraf)?overskriften\s+{_LYDE}$")),
    ("replace_section", re.compile(rf"^(?P<sec>{_SEC})\s+{_LYDE}$")),
    ("insert_punktum", re.compile(rf"^(?P<sec>{_SEC})\s+(?:{_LEDD}\s+)?{_NEW}\s+{_PUNKT}\s+{_LYDE}$")),  # noqa: E501
    ("replace_punktum", re.compile(rf"^(?P<sec>{_SEC})\s+(?:{_LEDD}\s+)?{_PUNKT}\s+{_LYDE}$")),
    ("replace_bokstav", re.compile(rf"^(?P<sec>{_SEC})\s+{_LEDD}\s+bokstav\s+(?P<b>[a-zæøå])\s+{_LYDE}$")),  # noqa: E501
    ("insert_ledd", re.compile(rf"^(?P<sec>{_SEC})\s+{_NEW}\s+{_LEDD}\s+{_LYDE}$")),
    ("replace_ledd", re.compile(rf"^(?P<sec>{_SEC})\s+{_LEDD}\s+{_LYDE}$")),
    ("repeal_punktum", re.compile(rf"^(?P<sec>{_SEC})\s+(?:{_LEDD}\s+)?{_PUNKT}\s+{_REPEAL}$")),
    ("repeal_ledd", re.compile(rf"^(?P<sec>{_SEC})\s+{_LEDD}\s+{_REPEAL}$")),
    ("repeal_section", re.compile(rf"^(?P<sec>{_SEC})\s+{_REPEAL}$")),
    (
        "move_ledd",
        re.compile(
            rf"^(?P<sec>{_SEC})\s+{_LEDD}\s+blir\s+{_NEW}\s+(?P<to>{_ORD})\s+ledd\s+i\s+(?P<dest>{_SEC})\.?$"
        ),
    ),
    (
        "renumber",
        re.compile(
            rf"^(?:(?P<sec>{_SEC})\s+)?(?:Nåværende|Noverande|Gjeldende|nåværende|noverande)\s+.*\bblir\b.*$"
        ),
    ),
]  # fmt: skip


class Address(Frozen):
    section: str | None = None
    ledd: int | None = None
    ledd_end: int | None = None
    punktum: int | None = None
    punktum_end: int | None = None
    bokstav: str | None = None
    destination_ledd: int | None = None


class Operation(Frozen):
    id: str
    kind: OpKind
    act: str | None
    address: Address
    instruction: str
    part: str
    destination: str | None = None
    new_provision: Provision | None = None
    ledd: tuple[Ledd, ...] = ()
    title: str | None = None
    term_old: str | None = None
    term_new: str | None = None
    locations: str = ""
    change_part: tuple[str, ...] = ()
    host_section: str | None = None
    absorbed_by: str | None = None  # renumber only: the operation whose effect this sentence states


class Interpretation(Frozen):
    document: str
    operations: tuple[Operation, ...]
    clauses: tuple[str, ...]


def _ord(groups: dict[str, str | None], key: str) -> int | None:
    word = groups.get(key)
    return ordinal(word) if word else None


def _span(
    groups: dict[str, str | None], first: str, joiner: str, last: str
) -> tuple[int | None, int | None]:
    start, end = _ord(groups, first), _ord(groups, last)
    if end is not None and groups.get(joiner) == "og" and start is not None and end != start + 1:
        return None, None  # "første og tredje" is two targets, not a range: no address
    return start, end


def _address(groups: dict[str, str | None]) -> Address:
    ledd, ledd_end = _span(groups, "l1", "lj", "l2")
    punktum, punktum_end = _span(groups, "p1", "pj", "p2")
    section = groups.get("sec")
    return Address(
        section=section_key_of(section) if section is not None else None,
        ledd=ledd, ledd_end=ledd_end, punktum=punktum, punktum_end=punktum_end,
        bokstav=groups.get("b"), destination_ledd=_ord(groups, "to"),
    )  # fmt: skip


def classify(sentence: str) -> tuple[OpKind, dict[str, str | None]]:
    """The operation a sentence names, or ``unsupported`` with no address."""
    for kind, pattern in _RULES:
        match = pattern.match(sentence)
        if match is not None:
            return kind, match.groupdict()
    return "unsupported", {}


def _inline_payload(text: str, payload: tuple[Node, ...]) -> tuple[str, tuple[Node, ...]]:
    """``… bokstav d lyde: Stortinget, …`` carries its replacement text in the same paragraph."""
    match = _INLINE_PAYLOAD.match(text)
    if match is None or payload:
        return text, payload
    tail = match.group("tail")
    node = Node(kind="ledd", css_class="inline", text=tail, ledd=Ledd(xml=tail, text=tail))
    return match.group("head"), (node,)


def _addressed_act(change_part: tuple[str, ...]) -> str | None:
    """The act a structured change address names, when every address names the same one."""
    acts = {part.split("/§")[0].split("/del")[0].split("/kap")[0] for part in change_part}
    return acts.pop() if len(acts) == 1 and next(iter(acts)).startswith("lov/") else None


_COMMENCEMENT_HEADING = re.compile(r"ikraft|iverkset|i\s+kraft", re.I)


def _commencement_provisions(document: Document) -> list[str]:
    """An enacting act states its commencement in a provision headed Ikrafttredelse or alike."""
    return [
        ledd.text
        for provision in document.provisions
        if _COMMENCEMENT_HEADING.search(provision.heading)
        for ledd in provision.ledd
    ]


def _pairs(match: re.Match[str]) -> tuple[tuple[str, str], ...]:
    """``«a» og «b» … henholdsvis «c» og «d»`` is two substitutions, in order."""
    old, new = match.group("old"), match.group("new")
    old2, new2 = match.group("old2"), match.group("new2")
    if old2 is None and new2 is None:
        return ((old, new),)
    if old2 is None or new2 is None:
        return ()  # one side lists a pair, the other does not: not a substitution we read
    return ((old, new), (old2, new2))


def _instruction_like(node: Node) -> bool:
    """Older members carry instructions as ``legalP``; recognise them by grammar."""
    if node.kind == "paragraph":
        return True
    if node.kind != "ledd":
        return False
    text = node.text
    return bool(_INTRO.match(text) or _SUBSTITUTE.match(text)) or classify(text)[0] != "unsupported"


def _next_instruction(nodes: list[Node], start: int) -> int:
    for index in range(start, len(nodes)):
        if _instruction_like(nodes[index]):
            return index
    return len(nodes)


class _Reader:
    """Walks one document's parts; state is the act under amendment."""

    def __init__(self, document: Document) -> None:
        self.document = document
        self.act: str | None = None
        self.operations: list[Operation] = []
        self.clauses: list[str] = []
        self.substitution: tuple[tuple[str, str], ...] | None = None
        self.pending_item_act: str | None = None

    def run(self) -> Interpretation:
        for index, part in enumerate(self.document.parts):
            self._read_part((part.label, f"{index}:{part.label}"), list(part.nodes))
        clauses = self.clauses or _commencement_provisions(self.document)
        return Interpretation(
            document=self.document.header.refid,
            operations=tuple(self.operations),
            clauses=tuple(clauses),
        )

    def _read_part(self, part: tuple[str, str], nodes: list[Node]) -> None:
        """``part`` is (printed label, unique tag prefix)."""
        self.substitution, self.pending_item_act, self.act = None, None, None
        seen_instruction, skip_until = False, 0
        for index, node in enumerate(nodes):  # a for loop: no index arithmetic can stall it
            if index < skip_until:
                continue  # consumed as the payload of the instruction before it
            if _instruction_like(node):
                end = _next_instruction(nodes, index + 1)
                consumed = self._paragraph(part, index, node, tuple(nodes[index + 1 : end]))
                seen_instruction = True
                if consumed:
                    skip_until = end
            else:
                self._loose(node, seen_instruction, part, index)

    def _loose(self, node: Node, seen: bool, part: tuple[str, str], index: int) -> None:
        if self.substitution is not None and node.kind == "ledd":
            self._substitution_item(part, index, node)
        elif not seen and node.kind == "ledd" and not self.document.provisions:
            self.clauses.append(node.text)

    def _paragraph(
        self, part: tuple[str, str], index: int, node: Node, payload: tuple[Node, ...]
    ) -> bool:
        """True when ``payload`` was consumed by an instruction."""
        text = node.text
        substitute = _SUBSTITUTE.match(text)
        if substitute is not None:
            self.substitution = _pairs(substitute)
            return False
        if self.substitution is not None and self._substitution_item(part, index, node):
            return False
        intro = _INTRO.match(text)
        if intro is not None:
            self.substitution, self.act = None, act_refid(intro.group("act"))
            text = intro.group("rest") or f"{intro.group('act')} {intro.group('repeal')}"
            if _INTRO_ONLY.match(text):
                return False
        where = (part[0], f"{part[1]}:{index}", node.host_section)
        self._instruction(where, text, node.change_part, payload)
        return True

    def _substitution_item(self, part: tuple[str, str], index: int, node: Node) -> bool:
        act = act_refid(node.text)
        has_locations = "§" in node.text
        if act is not None and not has_locations:
            self.pending_item_act = act
            return True
        if act is None and has_locations and self.pending_item_act is not None:
            act, self.pending_item_act = self.pending_item_act, None
        if act is None or not has_locations:
            return False
        for offset, terms in enumerate(self.substitution or ()):
            where = (part[0], f"{part[1]}:{index}.{offset}", node.host_section)
            self.operations.append(self._substitute(where, act, node.text, terms))
        return True

    def _substitute(
        self, where: tuple[str, str, str | None], act: str, text: str, terms: tuple[str, str]
    ) -> Operation:
        label, tag, host = where
        return Operation(
            id=f"{self.document.header.refid}#{tag}", kind="substitute_term", act=act,
            address=Address(), instruction=text, part=label, term_old=terms[0],
            term_new=terms[1], locations=text[text.index("§") :], host_section=host,
        )  # fmt: skip

    def _instruction(
        self,
        where: tuple[str, str, str | None],
        text: str,
        change_part: tuple[str, ...],
        payload: tuple[Node, ...],
    ) -> None:
        label, tag, host = where
        act = _addressed_act(change_part) or self.act
        text, payload = _inline_payload(text, payload)
        sentences = split_sentences(text)
        for offset, sentence in enumerate(sentences):
            kind, groups = classify(sentence)
            last = offset == len(sentences) - 1
            if kind == "unsupported" and _COMMENCEMENT_SHAPED.search(sentence):
                self.clauses.append(sentence)  # a commencement sentence among instructions
                continue
            if kind == "unsupported" and act is None:
                continue  # prose outside any act's amendment block: not an operation
            self.operations.append(
                _operation(
                    f"{self.document.header.refid}#{tag}.{offset}",
                    (kind, groups, sentence),
                    (act, label, change_part, host),
                    payload if last else (),
                )
            )


def _operation(
    op_id: str,
    parsed: tuple[OpKind, dict[str, str | None], str],
    context: tuple[str | None, str, tuple[str, ...], str | None],
    payload: tuple[Node, ...],
) -> Operation:
    kind, groups, sentence = parsed
    act, label, change_part, host = context
    provisions = [n.provision for n in payload if n.provision is not None]
    return Operation(
        id=op_id, kind=kind, act=act, address=_address(groups), instruction=sentence, part=label,
        destination=section_key_of(dest) if (dest := groups.get("dest")) is not None else None,
        new_provision=provisions[0] if provisions else None,
        ledd=tuple(n.ledd for n in payload if n.ledd is not None),
        title=next((n.text for n in payload if n.kind == "title"), None),
        change_part=change_part, host_section=host,
    )  # fmt: skip


_SPAN = rf"(?P<a>{_ORD})(?:\s+(?P<j>og|til)\s+(?:nytt\s+|ny\s+)?(?P<b>{_ORD}))?"
_RENUMBER_SHIFT = re.compile(
    rf"^(?:(?P<sec>{_SEC})\s+)?(?:Nåværende|Noverande|Gjeldende|nåværende|noverande)\s+"
    rf"{_SPAN}\s+(?P<unit>ledd|punktum)\s+blir\s+(?:nytt\s+|ny\s+)?"
    rf"{_SPAN.replace('?P<a>', '?P<c>').replace('?P<j>', '?P<k>').replace('?P<b>', '?P<d>')}"
    r"\s+(?P<unit2>ledd|punktum)\.?$"
)
_INLINE_PAYLOAD = re.compile(r"^(?P<head>.*?\blyde:)\s*(?P<tail>\S.*)$", re.S)


class Shift(Frozen):
    """``Nåværende X [og|til Y] ledd blir A [og|til B] ledd``, read as old→new pairs."""

    section: str | None
    unit: str
    pairs: tuple[tuple[int, int], ...]


def _ordinals(first: str, joiner: str | None, last: str | None) -> list[int]:
    a = ORDINALS[first.lower()]
    if last is None:
        return [a]
    b = ORDINALS[last.lower()]
    return list(range(a, b + 1)) if joiner == "til" else [a, b]


def _shift(sentence: str) -> Shift | None:
    match = _RENUMBER_SHIFT.match(sentence)
    if match is None or match.group("unit") != match.group("unit2"):
        return None
    olds = _ordinals(match.group("a"), match.group("j"), match.group("b"))
    news = _ordinals(match.group("c"), match.group("k"), match.group("d"))
    if not olds or len(olds) != len(news):
        return None
    section = section_key_of(match.group("sec")) if match.group("sec") else None
    return Shift(
        section=section, unit=match.group("unit"), pairs=tuple(zip(olds, news, strict=True))
    )


def _as_insertion(previous: Operation, shift: Shift) -> Operation | None:
    """``§ N X ledd skal lyde`` + ``Nåværende X ledd blir X+1 ledd`` is an insertion."""
    same_section = shift.section is None or shift.section == previous.address.section
    (old, new), *rest = shift.pairs
    fits = previous.kind == "replace_ledd" and previous.address.ledd == old and new == old + 1
    if fits and same_section and shift.unit == "ledd" and not rest:
        return previous.model_copy(update={"kind": "insert_ledd"})
    return None


def _implied(previous: Operation, unit: str) -> tuple[int, int, int] | None:
    """(first affected, last removed, delta) the previous operation imposes on ``unit`` numbers."""
    address = previous.address
    start = address.ledd if unit == "ledd" else address.punktum
    if start is None or previous.kind not in (f"insert_{unit}", f"repeal_{unit}"):
        return None
    end = (address.ledd_end if unit == "ledd" else address.punktum_end) or start
    count = end - start + 1
    if previous.kind == f"insert_{unit}":
        return (start, start - 1, count)
    return (start, end, -count)


def _absorbed(previous: Operation, shift: Shift) -> bool:
    """True when every old→new pair is exactly what the previous operation did to the numbers."""
    if shift.section is not None and shift.section != previous.address.section:
        return False
    implied = _implied(previous, shift.unit)
    if implied is None:
        return False
    first, last_removed, delta = implied
    return all(
        old > last_removed and old >= first and new == old + delta for old, new in shift.pairs
    )


def _absorb(current: Operation, previous: Operation, shift: Shift) -> Operation:
    """Address the renumbering to the section it names or, once absorbed, to its predecessor's."""
    if not _absorbed(previous, shift):
        return current.model_copy(update={"address": Address(section=shift.section)})
    ledd = previous.address.ledd if shift.unit == "punktum" else None
    address = Address(section=previous.address.section, ledd=ledd)
    return current.model_copy(update={"address": address, "absorbed_by": previous.id})


def _with_renumber_insertions(operations: list[Operation]) -> tuple[Operation, ...]:
    result = list(operations)
    for index in range(1, len(result)):
        current, previous = result[index], result[index - 1]
        shift = _shift(current.instruction)  # only a renumbering sentence matches
        if shift is None or current.act != previous.act:
            continue
        converted = _as_insertion(previous, shift)
        if converted is not None:
            result[index - 1] = previous = converted
        result[index] = _absorb(current, previous, shift)
    return tuple(result)


def interpret(document: Document) -> Interpretation:
    """Every operation an amending law states, in document order, plus its bare clauses."""
    reading = _Reader(document).run()
    return reading.model_copy(
        update={"operations": _with_renumber_insertions(list(reading.operations))}
    )
