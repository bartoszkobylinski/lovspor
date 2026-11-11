"""Amendment instructions of an announcing law, read into typed operations.

An amending law says what to do in prose: ``§ 89 skal lyde:``, ``Ny § 88 a
skal lyde:``, ``§ 4 oppheves.``. This module recognises a closed grammar of
such sentences. Anything outside it becomes an ``unsupported`` operation that
still names its target, so the replay can mark the provision unresolved
instead of guessing. Nothing here applies an operation.
"""

import re
from typing import Literal

from lovspor.legaltime import attribution
from lovspor.legaltime.attribution import Binding, is_intro
from lovspor.legaltime.document import Document, Frozen, Ledd, Node, Provision
from lovspor.legaltime.text import (
    ORDINAL_ALT,
    ORDINALS,
    act_refid,
    chapter_key,
    ordinal,
    section_key,
    section_key_of,
    split_sentences,
)

OpKind = Literal[
    "replace_section", "insert_section", "replace_ledd", "insert_ledd", "replace_punktum",
    "insert_punktum", "replace_bokstav", "insert_bokstav", "repeal_bokstav", "repeal_section",
    "repeal_ledd", "repeal_punktum", "move_ledd", "renumber", "replace_heading", "substitute_term",
    "unsupported",
]  # fmt: skip

_SEC = (  # "i" is the preposition, not a letter; ``§ 9 A-1`` carries a spaced chapter letter
    r"§\s*\d+(?:[A-Z]|\s[A-Z](?=-))?(?:-\d+)?(?:\s*[a-hj-zæøå](?![a-zæøå]))?"
)
_SECNUM = r"\d+(?:-\d+)?(?:\s*[a-hj-zæøå](?![a-zæøå]))?"
_SECS = rf"§§?\s*(?P<s1>{_SECNUM})\s+(?P<sj>og|til)\s+(?P<s2>{_SECNUM})"
_ORD = ORDINAL_ALT
_LEDD = (
    rf"(?:(?P<l1>{_ORD})(?:\s+(?P<lj>og|til)\s+(?P<l2>{_ORD}))?\s+ledd"
    r"|\((?P<n1>\d+)\)(?:\s+(?P<nj>og|til)\s+\((?P<n2>\d+)\))?)"
)
_PUNKT = rf"(?:(?P<p1>{_ORD})(?:\s+(?P<pj>og|til)\s+(?P<p2>{_ORD}))?|(?P<plast>siste))\s+punktum"
_NEW = r"ny(?:tt|e)?"
_ITEM = r"(?:bokstav\s+(?P<b>[a-zæøå])|nr\.\s+(?P<bn>\d+))"  # one list item, by letter or number
_STRUCTURE = re.compile(
    rf"^(?:{_NEW}\s+kapit(?:te)?l(?:er)?\s+(?:\d+(?:\s?[A-Za-z])?|\S+)(?:\s+til\s+\S+)?"
    r"(?:\s+plasseres\s+i\s+(?P<in>\w+)\s+del)?"
    rf"|(?P<part>\w+)\s+del),?\s+(?:og\s+)?(?:skal\s+)?lyde(?:\s+slik)?:$",
    re.I,
)  # a new chapter, new chapters placed in a part, or a whole part rewritten
_PART_WORDS = {"annen": 2}  # the one part ordinal the ordinal table does not spell this way
_LYDE = r"(?:skal\s+)?lyde(?:\s+slik)?:"
_REPEAL = (
    r"(?:oppheves|opphevast|blir\s+oppheva|vert\s+oppheva|blir\s+opphevet|strykes"
    r"|blir\s+sletta|vert\s+sletta|utgår)\.?"
)
_UNBOUND_INTRO = re.compile(
    r"(?:\b(?:gjøres|gjerast|gjer\s+ein|blir\s+gjort|vert\s+gjort)\s+(?:følgende|følgjande|desse|slike)"
    r"\s+endring\w*|\b(?:endres|endrast|vert\s+endra|blir\s+endra)\s+slik)\b[^:]*:$",
    re.I,
)  # opens an amendment block whose act the intro grammar cannot name (#856)
_UNIT_LED = re.compile(
    rf"^(?:\d+\.\s*)?(?:I\s+)?(?:§|kapit|kap\.|(?:\w+\s+)?del\b|overskrift|innledning|vedlegg"
    rf"|nr\.|bokstav|punkt|(?:{ORDINAL_ALT})\s+(?:ledd|punktum))",
    re.I,
)  # ``§ 4-2 endres slik:``: a unit of the act already named, not another act
_ACT_WORD = re.compile(
    r"\b[\wæøå-]+(?:loven|lova|lov)\b|\blov\b|\bforskrift\w*|\blovgiv\w*|\blovgjev\w*", re.I
)
_AMENDS = re.compile(r"\bendring\w*|\bendr(?:es|ast|a|et|ede)\b", re.I)
_COMMENCEMENT_SHAPED = re.compile(
    r"\bi\s+kraft\b|\bgjeld(?:er)?\s+fr[aå]\b|\bte[kr]\s+til\s+å\s+gjelde\b|\btar\s+til\s+å\s+gjelde\b",
    re.I,
)
_INTRO_ONLY = re.compile(
    r"^(?:det\s+gjort\s+|ein\s+)?(?:følgende|følgjande|desse)\s+endring(?:er|ar|ane|a)?"
    r"(?:\s+gjerast|\s+gjorde)?:$"
)
_MULTI_REPEAL = re.compile(rf"^{_SECS}\s+{_REPEAL}$")
_INLINE_REPEAL = re.compile(r"^(?:oppheves|opphevast)\s+(?P<target>§.*?)\.?$")
_SUBSTITUTE = re.compile(
    r"^I\s+(?:følgjande|følgende)\s+(?:lovføresegner|bestemmelser|lover|føresegner)\s+"
    r"(?:vert|skal|blir)\s+(?:ordet|ordene|orda|uttrykket|uttrykka)?\s*«(?P<old>[^»]+)»"
    r"(?:\s+og\s+«(?P<old2>[^»]+)»)?\s+(?:endra|endres|endrast)\s+til\s+(?:henholdsvis\s+)?"
    r"«(?P<new>[^»]+)»(?:\s+og\s+«(?P<new2>[^»]+)»)?:$"
)
_SUBSTITUTE_IN = re.compile(
    r"^(?:Ordet|Ordene|Orda|Uttrykket|Uttrykka)\s+«(?P<old>[^»]+)»\s+(?:skal\s+)?(?:endres|endrast|vert\s+endra|blir\s+endra)\s+til\s+"
    r"«(?P<new>[^»]+)»\s+i\s+(?P<locations>§.+?)\.?$"
)  # one sentence naming the term, its replacement and the sections and ledd it applies in
_RULES: list[tuple[OpKind, re.Pattern[str]]] = [
    ("insert_section", re.compile(rf"^{_NEW}\s+(?P<sec>{_SEC})\s+{_LYDE}$", re.I)),
    ("replace_heading", re.compile(rf"^(?P<sec>{_SEC})\s+(?:paragraf)?overskrift(?:en|a)\s+{_LYDE}$")),  # noqa: E501
    ("replace_heading", re.compile(rf"^Overskrift(?:en|a)\s+(?:til|i)\s+(?P<sec>{_SEC})\s+{_LYDE}$")),  # noqa: E501
    ("insert_section", re.compile(rf"^I\s+kapittel\s+\S+\s+skal\s+{_NEW}\s+(?P<sec>{_SEC})\s+{_LYDE}$", re.I)),  # noqa: E501
    ("repeal_ledd", re.compile(rf"^I\s+(?P<sec>{_SEC})\s+(?:blir|vert)\s+{_LEDD}\s+sletta\.?$")),
    ("replace_section", re.compile(rf"^(?P<sec>{_SEC})\s+{_LYDE}$")),
    ("insert_punktum", re.compile(rf"^(?P<sec>{_SEC})\s+(?:{_LEDD}\s+)?{_NEW}\s+{_PUNKT}\s+{_LYDE}$")),  # noqa: E501
    ("replace_punktum", re.compile(rf"^(?P<sec>{_SEC})\s+(?:{_LEDD}\s+)?{_PUNKT}\s+{_LYDE}$")),
    ("insert_bokstav", re.compile(rf"^(?P<sec>{_SEC})\s+(?:{_LEDD}\s+)?{_NEW}\s+{_ITEM}\s+{_LYDE}$")),  # noqa: E501
    ("insert_bokstav", re.compile(rf"^I\s+(?P<sec>{_SEC})\s+skal\s+{_NEW}\s+{_ITEM}\s+lyde:$")),
    ("replace_bokstav", re.compile(rf"^(?P<sec>{_SEC})\s+(?:{_LEDD}\s+)?{_ITEM}\s+{_LYDE}$")),
    ("repeal_bokstav", re.compile(rf"^(?P<sec>{_SEC})\s+(?:{_LEDD}\s+)?{_ITEM}\s+{_REPEAL}$")),
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
            rf"^(?:(?P<sec>{_SEC})\s+)?(?:Nåværende|Noverande|Gjeldende|Tidlegare|Tidligere|nåværende|noverande|tidlegare|tidligere)\s+.*(?:\b(?:ledd|punktum)|§\s*\d+\w*(?:\s+[a-zæøå])?)\s+blir\b.*$"
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
    last_punktum: bool = False  # ``siste punktum``: the ledd's last sentence, whatever its number


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
    if word is None and key in _NUMBERED_KEYS:
        number = groups.get(_NUMBERED_KEYS[key])
        return int(number) if number else None
    return ordinal(word) if word else None


_NUMBERED_KEYS = {"l1": "n1", "l2": "n2"}  # ``(1)`` numbers a ledd where an ordinal would


def _span(
    groups: dict[str, str | None], first: str, joiner: str, last: str
) -> tuple[int | None, int | None]:
    start, end = _ord(groups, first), _ord(groups, last)
    joined = groups.get(joiner) or (groups.get("nj") if first == "l1" else None)
    if end is not None and joined == "og" and start is not None and end != start + 1:
        return None, None  # "første og tredje" is two targets, not a range: no address
    return start, end


def _address(groups: dict[str, str | None]) -> Address:
    ledd, ledd_end = _span(groups, "l1", "lj", "l2")
    punktum, punktum_end = _span(groups, "p1", "pj", "p2")
    section = groups.get("sec")
    return Address(
        section=section_key_of(section) if section is not None else None,
        ledd=ledd, ledd_end=ledd_end, punktum=punktum, punktum_end=punktum_end,
        bokstav=groups.get("b") or groups.get("bn"), destination_ledd=_ord(groups, "to"),
        last_punktum=bool(groups.get("plast")),
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


def _intro_rest(intro: re.Match[str]) -> str:
    """What an intro sentence says after naming the act: its instructions, or its own repeal."""
    if intro.group("rest"):
        return intro.group("rest")
    inline = _INLINE_REPEAL.match(intro.group("inline") or "")
    if inline is not None:
        return f"{inline.group('target')} oppheves."  # ``oppheves § 12`` is ``§ 12 oppheves.``
    return f"lov {intro.group('date')} {intro.group('repeal')}"


def _expand(sentence: str) -> list[str]:
    """``§§ 14 og 15 oppheves.`` is one repeal per section; a lettered range is left as it is."""
    match = _MULTI_REPEAL.match(sentence)
    if match is None:
        return [sentence]
    first, last, joiner = match.group("s1"), match.group("s2"), match.group("sj")
    if joiner == "og":
        return [f"§ {first} oppheves.", f"§ {last} oppheves."]
    if not (first.isdigit() and last.isdigit()) or int(last) < int(first):
        return [sentence]
    return [f"§ {n} oppheves." for n in range(int(first), int(last) + 1)]


def _pieces(text: str) -> list[tuple[str, str, str]]:
    """(id suffix, sentence to classify, announced sentence) per instruction sentence."""
    pieces: list[tuple[str, str, str]] = []
    for offset, sentence in enumerate(split_sentences(text)):
        expanded = _expand(sentence)
        for k, piece in enumerate(expanded):
            pieces.append((f"{offset}" if len(expanded) == 1 else f"{offset}-{k}", piece, sentence))
    return pieces


def _addressed_act(change_part: tuple[str, ...]) -> str | None:
    """The act a structured change address names, when every address names the same one."""
    acts = {part.split("/§")[0].split("/del")[0].split("/kap")[0] for part in change_part}
    return acts.pop() if len(acts) == 1 and next(iter(acts)).startswith("lov/") else None


_COMMENCEMENT_HEADING = re.compile(r"ikraft|iverkset|i\s+kraft", re.I)


def _commencement_provisions(document: Document) -> list[str]:
    """An enacting act states its commencement in a provision headed Ikrafttredelse or alike.

    From the first amendment intro on, a provision's ledd are an instruction and its
    payload about another act (straffelovens ikraftsettingslov § 1), never this act's clause.
    """
    texts: list[str] = []
    for provision in document.provisions:
        if not _COMMENCEMENT_HEADING.search(provision.heading):
            continue
        for ledd in provision.ledd:
            if is_intro(ledd.text):
                break
            texts.append(ledd.text)
    return texts


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
    return bool(is_intro(text) or _SUBSTITUTE.match(text)) or classify(text)[0] != "unsupported"


def _unbound_intro(text: str) -> bool:
    """An intro the grammar cannot bind, unless it only opens a unit of the same act.

    Any word order counts: a sentence ending in a colon that amends and names an act
    (``I lov … skal følgende endringer gjøres:``) opens a block as surely as the shapes
    ``_UNBOUND_INTRO`` spells out.
    """
    if is_intro(text) or _SUBSTITUTE.match(text):
        return False
    names_act = act_refid(text) is not None or _ACT_WORD.search(text) is not None
    instructs = _OPENS_PAYLOAD.search(text) is not None  # ``… slik § 52 a lyder etter endringer
    # ved lov …, skal … lyde:`` cites an earlier amendment, it does not open a block
    amends = text.endswith(":") and _AMENDS.search(text) is not None and names_act
    amends = amends and not instructs
    if _UNBOUND_INTRO.search(text) is None and not amends:
        return False
    return _UNIT_LED.match(text) is None or names_act


def _next_instruction(nodes: list[Node], start: int) -> int:
    """Where an instruction's payload ends: the next instruction, intro or article (#856)."""
    host = nodes[start - 1].host_section
    for index in range(start, len(nodes)):
        node = nodes[index]
        closes = _unbound_intro(node.text) or attribution.opens_block(node.text)
        if _instruction_like(node) or closes or node.host_section != host:
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
        self.bound_host: str | None = None  # the article whose intro bound ``act``
        self.unbound = False  # inside a block whose intro names an act the grammar cannot bind
        self.in_list = False  # inside a list intro naming no act: numbered act headings bind

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
        self.in_list, seen_instruction, skip_until = False, False, 0
        for index, node in enumerate(nodes):  # a for loop: no index arithmetic can stall it
            if index < skip_until:
                continue  # consumed as the payload of the instruction before it
            binding = self._boundary(node)
            read = self._bound(node, binding) if binding.act else node
            if read is None:
                seen_instruction = True
            elif _instruction_like(read):
                end = _next_instruction(nodes, index + 1)
                consumed = self._paragraph(part, index, read, tuple(nodes[index + 1 : end]))
                seen_instruction = True
                if consumed:
                    skip_until = end
            else:
                self._loose(read, seen_instruction, part, index)
            if binding.act:  # the intro closes its own node: its act binds what follows
                self.act, self.unbound = binding.act, False

    def _boundary(self, node: Node) -> Binding:
        """Close the open block where its act is no longer proven (#856).

        An act is bound only by an intro naming it, inside the article that intro sits
        in. A new article, or an intro whose act the grammar cannot name, ends the block:
        what follows is unattributed until another intro names its act, never inherited.
        """
        unbound, moved = _unbound_intro(node.text), node.host_section != self.bound_host
        binding = self._binding(node.text, unbound)
        if moved or unbound or binding.opens:
            self.substitution, self.pending_item_act, self.act = None, None, None
            self.bound_host, self.unbound = node.host_section, unbound or binding.opens
        listing = unbound and not attribution.citations(node.text)  # ``… i andre lover:``
        self.in_list = (self.in_list and not moved) or listing
        return binding

    def _bound(self, node: Node, binding: Binding) -> Node | None:
        """A proven intro is no operation; what precedes it in a paragraph is read as before."""
        if not _instruction_like(node):
            return node  # a ledd intro reads as loose text, exactly as Phase 4A read it
        if _clause_like(binding.intro):
            self.clauses.append(binding.intro)
        return node.model_copy(update={"text": binding.head}) if binding.head else None

    def _binding(self, text: str, unbound: bool) -> Binding:
        if _SUBSTITUTE.match(text) or (self.substitution and not unbound):
            return Binding()  # a substitution intro, or an item of its list
        return attribution.bind(text, self.document.header.changes_to_documents, self.in_list)

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
        quoted = self._open_block(text)
        if quoted is None:
            return False
        where = (part[0], f"{part[1]}:{index}", node.host_section)
        self._instruction(where, quoted, node.change_part, payload)
        return True

    def _open_block(self, text: str) -> str | tuple[str, str] | None:
        """Name the act an intro opens; the sentence(s) to classify, or None for an intro alone."""
        slik = attribution.INTRO_SLIK.match(text)
        intro = slik or attribution.INTRO.match(text)
        if intro is None:
            return self._inline(text)
        changes = self.document.header.changes_to_documents
        self.substitution, self.act = None, attribution.canonical_act(intro.group("date"), changes)
        self.unbound = self.unbound or self.act is None  # #872: unproven, so never inherited
        if slik is not None or _INTRO_ONLY.match(rest := _intro_rest(intro)):
            return None  # an intro alone: ``INTRO_SLIK`` always, ``INTRO`` without an instruction
        return (rest, text) if intro.group("inline") else rest

    def _inline(self, text: str) -> str:
        """Phase 4B: ``N. Lov <citation> … § X skal lyde:`` binds its proven act from here on."""
        item = attribution.inline(text, self.document.header.changes_to_documents)
        if item is None:
            return text
        self.act, self.unbound = item[0], False
        return item[1]

    def _substitution_item(self, part: tuple[str, str], index: int, node: Node) -> bool:
        act = attribution.item_act(node.text, self.document.header.changes_to_documents)
        has_locations = "§" in node.text
        if act is not None and not has_locations:
            self.pending_item_act = act
            return True
        if act == "":  # #872: an unproven item ends the act pending before it, too
            self.pending_item_act = None
        elif act is None and has_locations and self.pending_item_act is not None:
            act, self.pending_item_act = self.pending_item_act, None
        if act is None or not has_locations:
            return False
        for offset, terms in enumerate(self.substitution or ()):
            where = (part[0], f"{part[1]}:{index}.{offset}", node.host_section)
            self.operations.append(self._substitute(where, act or None, node.text, terms))
        return True

    def _substitute(
        self, where: tuple[str, str, str | None], act: str | None, text: str, terms: tuple[str, str]
    ) -> Operation:
        label, tag, host = where
        op_id = tag if "#" in tag else f"{self.document.header.refid}#{tag}"
        return Operation(
            id=op_id, kind="substitute_term", act=act,
            address=Address(), instruction=text, part=label, term_old=terms[0],
            term_new=terms[1], locations=text[text.index("§") :], host_section=host,
        )  # fmt: skip

    def _instruction(
        self,
        where: tuple[str, str, str | None],
        text: str | tuple[str, str],
        change_part: tuple[str, ...],
        payload: tuple[Node, ...],
    ) -> None:
        label, tag, host = where
        addressed, changes = _addressed_act(change_part), self.document.header.changes_to_documents
        act = self.act if addressed is None else attribution.identified(addressed, changes)  # #872
        context = (act, label, change_part, host)
        text, quote = (text, None) if isinstance(text, str) else text
        text, payload = _inline_payload(text, payload)
        sentences = _pieces(text)
        for offset, (tag_offset, sentence, announced) in enumerate(sentences):
            last = offset == len(sentences) - 1
            op_id = f"{self.document.header.refid}#{tag}.{tag_offset}"
            self._sentence(op_id, (sentence, quote or announced), context, payload if last else ())

    def _sentence(
        self,
        op_id: str,
        sentence: tuple[str, str],
        context: tuple[str | None, str, tuple[str, ...], str | None],
        payload: tuple[Node, ...],
    ) -> None:
        read, quote = sentence  # the sentence as classified, and as the member announced it
        kind, groups = classify(read)
        substitute_in = _SUBSTITUTE_IN.match(read)
        if substitute_in is not None and context[0] is not None:
            where = (context[1], op_id, context[3])
            terms = (substitute_in.group("old"), substitute_in.group("new"))
            text = f"§{substitute_in.group('locations')[1:]}"  # locations start at the first §
            self.operations.append(self._substitute(where, context[0], text, terms))
        elif payload and _STRUCTURE.match(read) and any(n.provision for n in payload):
            self.operations.extend(_structure(op_id, quote, context, payload))
        elif kind == "unsupported" and _clause_like(read):
            self.clauses.append(read)  # a commencement sentence among instructions
        elif kind == "unsupported" and context[0] is None and not self.unbound:
            return  # prose outside any act's amendment block: not an operation
        else:
            self.operations.append(_operation(op_id, (kind, groups, quote), context, payload))


_OPENS_PAYLOAD = re.compile(r"lyde(?:\s+slik)?:$")


def _clause_like(sentence: str) -> bool:
    """A commencement-shaped sentence that opens no payload.

    ``De delene av § 102 … som ikke allerede er satt i kraft, blir § 402, og skal lyde slik:``
    instructs, whatever it says about being in force; it is an unsupported operation.
    """
    return bool(_COMMENCEMENT_SHAPED.search(sentence)) and _OPENS_PAYLOAD.search(sentence) is None


def _structure_part(sentence: str) -> str | None:
    """``Annen del skal lyde:`` / ``… plasseres i annen del`` → ``del2``; None when unnamed."""
    match = _STRUCTURE.match(sentence)
    word = (match.group("part") or match.group("in")) if match else None
    if word is None:
        return None
    number = ordinal(word) or _PART_WORDS.get(word.lower())
    return f"del{number}" if number is not None else None


def _placed(node: Node, provision: Provision, chapter: str | None, part: str | None) -> Node:
    placed = provision.model_copy(update={"chapter": chapter, "part": part})
    return node.model_copy(update={"provision": placed})


def _structure(
    base: str,
    sentence: str,
    context: tuple[str | None, str, tuple[str, ...], str | None],
    payload: tuple[Node, ...],
) -> list[Operation]:
    """``Nytt kapittel 6A skal lyde:`` / ``Annen del skal lyde:`` insert every provision carried.

    Each inserted provision is stamped with the chapter whose title precedes it (or the
    chapter the sentence names) and the part the sentence names, read, never inferred.
    """
    carried = [n for n in payload if n.provision is not None]
    labelled = all(section_key(n.provision.label) is not None for n in carried if n.provision)
    if not labelled or any(n.kind == "ledd" for n in payload):
        return [_operation(base, ("unsupported", {}, sentence), context, ())]
    chapter, part = chapter_key(sentence), _structure_part(sentence)
    operations: list[Operation] = []
    for node in payload:
        if node.kind == "title":
            chapter = chapter_key(node.text) or chapter
        elif node.provision is not None:
            placed = _placed(node, node.provision, chapter, part)
            parsed: tuple[OpKind, dict[str, str | None], str] = (
                "insert_section", {"sec": node.provision.label}, sentence,
            )  # fmt: skip
            operations.append(_operation(f"{base}-{len(operations)}", parsed, context, (placed,)))
    return operations


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
    rf"^(?:(?P<sec>{_SEC})\s+)?(?:Nåværende|Noverande|Gjeldende|Tidlegare|Tidligere|nåværende|noverande|tidlegare|tidligere)\s+"
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
    """``§ N X ledd skal lyde`` + ``Nåværende X [til Y] ledd blir X+1 [til Y+1] ledd`` inserts.

    Every renumbered ledd moves up by one from the ledd the previous sentence addressed, so
    that sentence states a new ledd, not a replacement of the one it numbers.
    """
    same_section = shift.section is None or shift.section == previous.address.section
    first = shift.pairs[0][0]
    fits = previous.kind == "replace_ledd" and previous.address.ledd == first
    shifted = all(new == old + 1 for old, new in shift.pairs) and all(
        shift.pairs[k][0] == first + k for k in range(len(shift.pairs))
    )
    if fits and same_section and shift.unit == "ledd" and shifted:
        return previous.model_copy(update={"kind": "insert_ledd"})
    return None


def _implied(previous: Operation, unit: str) -> tuple[int, int] | None:
    """(first number affected, delta) the previous operation imposes on ``unit`` numbers."""
    address = previous.address
    start = address.ledd if unit == "ledd" else address.punktum
    if start is None or previous.kind not in (f"insert_{unit}", f"repeal_{unit}"):
        return None
    end = (address.ledd_end if unit == "ledd" else address.punktum_end) or start
    count = end - start + 1
    if previous.kind == f"insert_{unit}":
        return (start, count)
    return (end + 1, -count)  # the repealed numbers themselves are gone, not shifted


def _absorbed(previous: Operation, shift: Shift) -> bool:
    """True when every old→new pair is exactly what the previous operation did to the numbers."""
    if shift.section is not None and shift.section != previous.address.section:
        return False
    implied = _implied(previous, shift.unit)
    if implied is None:
        return False
    first, delta = implied
    return all(old >= first and new == old + delta for old, new in shift.pairs)


def _absorb(current: Operation, previous: Operation, shift: Shift) -> Operation:
    """Once absorbed, address the renumbering to its predecessor's section (and ledd)."""
    if not _absorbed(previous, shift):
        return current  # its own address already names the section the sentence gave, if any
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
