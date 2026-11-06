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
from lovspor.legaltime.text import ORDINAL_ALT, act_refid, ordinal, section_key, split_sentences

OpKind = Literal[
    "replace_section", "insert_section", "replace_ledd", "insert_ledd", "replace_punktum",
    "insert_punktum", "replace_bokstav", "repeal_section", "repeal_ledd", "repeal_punktum",
    "move_ledd", "renumber", "replace_heading", "substitute_term", "unsupported",
]  # fmt: skip

_SEC = r"§\s*\d+(?:-\d+)?(?:\s*[a-zæøå](?![a-zæøå]))?"
_ORD = ORDINAL_ALT
_LEDD = rf"(?P<l1>{_ORD})(?:\s+(?P<lj>og|til)\s+(?P<l2>{_ORD}))?\s+ledd"
_PUNKT = rf"(?P<p1>{_ORD})(?:\s+(?P<pj>og|til)\s+(?P<p2>{_ORD}))?\s+punktum"
_NEW = r"ny(?:tt|e)?"
_LYDE = r"(?:skal\s+)?lyde(?:\s+slik)?:"
_REPEAL = r"(?:oppheves|opphevast|blir\s+oppheva|vert\s+oppheva|blir\s+opphevet)\.?"
_INTRO = re.compile(
    r"^(?:\d+\.\s*)?I\s+(?:midlertidig\s+)?(?P<act>lov\s+\d{1,2}\.\s*\w+\s+\d{4}(?:\s+nr\.\s*\d+)?)\b.*?"
    r"\s+(?:gjøres|gjerast|blir|vert|skal)\s+(?P<rest>.*)$",
    re.S,
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
    locations: str | None = None
    change_part: tuple[str, ...] = ()


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
        return start, None  # "første og tredje" is two targets, not a range: refuse
    return start, end


def _address(groups: dict[str, str | None]) -> Address:
    ledd, ledd_end = _span(groups, "l1", "lj", "l2")
    punktum, punktum_end = _span(groups, "p1", "pj", "p2")
    return Address(
        section=section_key(groups.get("sec") or "") if groups.get("sec") else None,
        ledd=ledd, ledd_end=ledd_end, punktum=punktum, punktum_end=punktum_end,
        bokstav=groups.get("b"),
    )  # fmt: skip


def classify(sentence: str) -> tuple[OpKind, dict[str, str | None]]:
    """The operation a sentence names, or ``unsupported`` with no address."""
    for kind, pattern in _RULES:
        match = pattern.match(sentence)
        if match is not None:
            return kind, match.groupdict()
    return "unsupported", {}


def _pairs(groups: dict[str, str | None]) -> tuple[tuple[str, str], ...]:
    """``«a» og «b» … henholdsvis «c» og «d»`` is two substitutions, in order."""
    old, new = groups.get("old") or "", groups.get("new") or ""
    old2, new2 = groups.get("old2"), groups.get("new2")
    if (old2 is None) != (new2 is None):
        return ()
    return ((old, new),) if old2 is None or new2 is None else ((old, new), (old2, new2))


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
        for part in self.document.parts:
            self._read_part(part.label, list(part.nodes))
        return Interpretation(
            document=self.document.header.refid,
            operations=tuple(self.operations),
            clauses=tuple(self.clauses),
        )

    def _read_part(self, label: str, nodes: list[Node]) -> None:
        self.substitution, self.pending_item_act = None, None
        seen_instruction, index = False, 0
        while index < len(nodes):
            node = nodes[index]
            if _instruction_like(node):
                end = _next_instruction(nodes, index + 1)
                consumed = self._paragraph(label, index, node, tuple(nodes[index + 1 : end]))
                seen_instruction, index = True, end if consumed else index + 1
            else:
                self._loose(node, seen_instruction, label, index)
                index += 1

    def _loose(self, node: Node, seen_instruction: bool, label: str, index: int) -> None:
        if self.substitution is not None and node.kind == "ledd":
            self._substitution_item(label, index, node)
        elif not seen_instruction and node.kind == "ledd":
            self.clauses.append(node.text)

    def _paragraph(self, label: str, index: int, node: Node, payload: tuple[Node, ...]) -> bool:
        """True when ``payload`` was consumed by an instruction."""
        text = node.text
        substitute = _SUBSTITUTE.match(text)
        if substitute is not None:
            self.substitution = _pairs(substitute.groupdict())
            return False
        if self.substitution is not None and self._substitution_item(label, index, node):
            return False
        intro = _INTRO.match(text)
        if intro is not None:
            self.substitution, self.act = None, act_refid(intro.group("act"))
            text = intro.group("rest")
            if _INTRO_ONLY.match(text):
                return False
        self._instruction(f"{label}:{index}", text, node.change_part, payload)
        return True

    def _substitution_item(self, label: str, index: int, node: Node) -> bool:
        act = act_refid(node.text)
        has_locations = "§" in node.text
        if act is not None and not has_locations:
            self.pending_item_act = act
            return True
        if act is None and has_locations and self.pending_item_act is not None:
            act, self.pending_item_act = self.pending_item_act, None
        if act is None or not has_locations or self.substitution is None:
            return False
        for offset, (old, new) in enumerate(self.substitution):
            self.operations.append(
                self._substitute(f"{label}:{index}.{offset}", act, node.text, (old, new))
            )
        return True

    def _substitute(self, tag: str, act: str, text: str, terms: tuple[str, str]) -> Operation:
        part = tag.split(":", maxsplit=1)[0]
        return Operation(
            id=f"{self.document.header.refid}#{tag}", kind="substitute_term", act=act,
            address=Address(), instruction=text, part=part, term_old=terms[0],
            term_new=terms[1], locations=text[text.index("§") :],
        )  # fmt: skip

    def _instruction(
        self, tag: str, text: str, change_part: tuple[str, ...], payload: tuple[Node, ...]
    ) -> None:
        label = tag.split(":", maxsplit=1)[0]
        sentences = split_sentences(text)
        for offset, sentence in enumerate(sentences):
            kind, groups = classify(sentence)
            last = offset == len(sentences) - 1
            if kind == "unsupported" and self.act is None:
                continue  # prose outside any act's amendment block: not an operation
            self.operations.append(
                _operation(
                    f"{self.document.header.refid}#{tag}.{offset}",
                    (kind, groups, sentence),
                    (self.act, label, change_part),
                    payload if last else (),
                )
            )


def _operation(
    op_id: str,
    parsed: tuple[OpKind, dict[str, str | None], str],
    context: tuple[str | None, str, tuple[str, ...]],
    payload: tuple[Node, ...],
) -> Operation:
    kind, groups, sentence = parsed
    act, label, change_part = context
    provisions = [n.provision for n in payload if n.provision is not None]
    return Operation(
        id=op_id, kind=kind, act=act, address=_address(groups), instruction=sentence, part=label,
        destination=section_key(groups.get("dest") or "") if groups.get("dest") else None,
        new_provision=provisions[0] if provisions else None,
        ledd=tuple(n.ledd for n in payload if n.ledd is not None),
        title=next((n.text for n in payload if n.kind == "title"), None),
        change_part=change_part,
    )  # fmt: skip


def interpret(document: Document) -> Interpretation:
    """Every operation an amending law states, in document order, plus its bare clauses."""
    return _Reader(document).run()
