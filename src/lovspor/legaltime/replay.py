"""Apply announced operations to an act's provisions in legal-effective order.

The state at a legal date D is the enacting text plus every operation whose
proven effective date is ≤ D, applied in (effective date, publication time,
document order). An operation the pilot cannot apply, or one whose date is
unresolved, leaves its target provisions ``unresolved``; nothing is ever
guessed. Pending (delegated, undecided) and future operations are listed,
not applied.
"""

import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Literal

from lxml import etree

from lovspor.legaltime.baseline import (
    Commencement,
    InForce,
    UnitState,
    addresses_flagged,
    restructures,
    state_gate,
    unit_state,
)
from lovspor.legaltime.commencement import Event, Timing
from lovspor.legaltime.document import Document, Frozen, Ledd, Provision, element_text
from lovspor.legaltime.instructions import Address, Operation, _shift
from lovspor.legaltime.text import ORDINAL_ALT, ordinal, section_sort_key, split_sentences
from lovspor.parsing.xml_normalizer import safe_parser

ProvisionStatus = Literal["replayed", "unresolved"]
StateStatus = Literal["replayed", "not_yet_in_force", "unresolved_baseline"]
RESTRUCTURED = "ledd-level commencement cannot follow a restructuring of the provision"
_SECTION_REF = re.compile(r"§\s*(\d+(?:[A-Z]|\s[A-Z](?=-))?(?:-\d+)?)\s*([a-hj-zæøå](?![a-zæøå]))?")
_LEDD_REF = re.compile(
    rf"\b((?:{ORDINAL_ALT})(?:\s*,\s*(?:{ORDINAL_ALT}))*(?:\s+(?:og|til)\s+(?:{ORDINAL_ALT}))?)\s+ledd\b",
    re.I,
)
_ORDINAL_WORD = re.compile(ORDINAL_ALT, re.I)
_LIST_MARKUP = re.compile(r"<(?:ul|ol)\b")


__all__ = ["Event", "ProvisionState", "ReplayState", "replay"]


class ProvisionState(Frozen):
    key: str
    label: str
    heading: str
    ledd: tuple[Ledd, ...]
    repealed: bool = False
    status: ProvisionStatus = "replayed"
    problems: tuple[str, ...] = ()
    applied: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()
    in_force: InForce = "in_force"  # the unit's legal effectiveness at D (Decision 3b)
    effective: date | None = None  # the unit's proven commencement day, when it has one
    ledd_not_in_force: tuple[int, ...] = ()  # ledd dated apart and not yet in force at D


class ReplayState(Frozen):
    act: str
    at: date
    status: StateStatus
    provisions: tuple[ProvisionState, ...]
    applied: tuple[str, ...]
    future: tuple[str, ...]
    pending: tuple[str, ...]
    unresolved: tuple[tuple[str, str], ...]
    act_level_problems: tuple[str, ...]
    withheld: tuple[str, ...] = ()
    not_in_force: tuple[str, ...] = ()  # sections with text at D whose commencement is later
    pending_commencement: tuple[str, ...] = ()  # sections whose commencement is delegated, undated
    unresolved_commencement: tuple[str, ...] = ()  # sections whose commencement cannot be read


_IN_FORCE = UnitState(in_force="in_force", effective=None, flagged=(), problems=())


class _Provision:
    def __init__(self, source: Provision, unit: UnitState = _IN_FORCE) -> None:
        self.label, self.heading, self.ledd = source.label, source.heading, list(source.ledd)
        self.repealed = False
        self.unit = unit
        self.problems: list[str] = list(unit.problems)
        self.applied: list[str] = []
        self.pending: list[str] = []

    def freeze(self, key: str) -> ProvisionState:
        unresolved = bool(self.problems) or self.unit.in_force == "unresolved"
        return ProvisionState(
            key=key, label=self.label, heading=self.heading, ledd=tuple(self.ledd),
            repealed=self.repealed, status="unresolved" if unresolved else "replayed",
            problems=tuple(self.problems), applied=tuple(self.applied), pending=tuple(self.pending),
            in_force=self.unit.in_force, effective=self.unit.effective,
            ledd_not_in_force=tuple(i for i in self.unit.flagged if i <= len(self.ledd)),
        )  # fmt: skip


def _ledd_from_text(text: str) -> Ledd:
    return Ledd(xml=text, text=text)


def _text_of_xml(fragment: str) -> str:
    root = etree.fromstring(f"<article>{fragment}</article>", parser=safe_parser())
    return element_text(root)


def _sentence_slice(address: Address, count: int) -> tuple[int, int]:
    """1-based inclusive sentence range an address names; ``siste punktum`` is the last one."""
    if address.last_punktum:
        return count, count
    start = address.punktum or 1
    return start, address.punktum_end or start


class _Working:
    """Mutable provision map; every ``apply_*`` returns a failure reason or None."""

    def __init__(
        self, baseline: Document, commencement: Commencement | None = None, at: date | None = None
    ) -> None:
        self.commencement, self.at = commencement, at
        self.order = [p.key for p in baseline.provisions]
        self.map = {
            p.key: _Provision(p, self.unit_state(p.key, p.chapter, (p.part or "",)))
            for p in baseline.provisions
        }
        self.done: set[str] = set()  # ids of operations that applied, for absorbed renumberings

    def unit_state(self, key: str, chapter: str | None, parts: tuple[str, ...]) -> UnitState:
        """The section's commencement at D; without a commencement every unit is in force."""
        if self.commencement is None or self.at is None:
            return _IN_FORCE
        return unit_state(self.commencement, key, (chapter, parts), self.at)

    def _target(self, key: str | None) -> _Provision | None:
        return self.map.get(key) if key else None

    def _ledd_at(self, op: Operation, unit: str = "punktum") -> tuple[_Provision, int] | str:
        target = self._target(op.address.section)
        if target is None or target.repealed:
            return "target provision missing or repealed"
        index = op.address.ledd
        if index is None:
            if len(target.ledd) != 1:
                return f"{unit} without ledd on a provision with several ledd"
            index = 1
        if not 1 <= index <= len(target.ledd):
            return f"ledd {index} does not exist"
        return target, index

    def apply_replace_section(self, op: Operation) -> str | None:
        target = self._target(op.address.section)
        if target is None or op.new_provision is None:
            return "target provision missing or no replacement text"
        if op.new_provision.key != op.address.section:
            return f"replacement text is headed {op.new_provision.key}, not {op.address.section}"
        target.heading, target.ledd, target.repealed = (
            op.new_provision.heading,
            list(op.new_provision.ledd),
            False,
        )
        return None

    def apply_insert_section(self, op: Operation) -> str | None:
        key = op.address.section
        if key is None or op.new_provision is None or key in self.map:
            return "new provision without key or text, or key already present"
        if op.new_provision.key != key:
            return f"new text is headed {op.new_provision.key}, not {key}"
        new = op.new_provision
        unit = self.unit_state(key, new.chapter, (new.part or "", op.part.upper()))
        self.map[key] = _Provision(new, unit)
        self.order.insert(_insert_position(self.order, key), key)
        return None

    def apply_repeal_section(self, op: Operation) -> str | None:
        target = self._target(op.address.section)
        if target is None:
            return "target provision missing"
        target.repealed, target.heading, target.ledd = True, "(Opphevet)", []
        return None

    def apply_replace_heading(self, op: Operation) -> str | None:
        target = self._target(op.address.section)
        heading = op.title if op.title is not None else (op.ledd[0].text if op.ledd else None)
        if target is None or heading is None:
            return "target provision missing or no heading text"
        target.heading = heading
        return None

    def apply_replace_ledd(self, op: Operation) -> str | None:
        target = self._target(op.address.section)
        start = op.address.ledd
        if target is None or start is None:
            return "target ledd range does not exist"
        end = op.address.ledd_end or start
        if not 1 <= start <= end <= len(target.ledd):
            return "target ledd range does not exist"
        if len(op.ledd) != end - start + 1:
            return f"{len(op.ledd)} ledd supplied for {end - start + 1} addressed"
        target.ledd[start - 1 : end] = list(op.ledd)
        return None

    def apply_insert_ledd(self, op: Operation) -> str | None:
        target = self._target(op.address.section)
        start = op.address.ledd
        if target is None or start is None or not 1 <= start <= len(target.ledd) + 1:
            return "insertion point does not exist"
        end = op.address.ledd_end or start
        if len(op.ledd) != end - start + 1:
            return f"{len(op.ledd)} ledd supplied for {end - start + 1} addressed"
        target.ledd[start - 1 : start - 1] = list(op.ledd)
        return None

    def apply_repeal_ledd(self, op: Operation) -> str | None:
        found = self._ledd_at(op)
        if isinstance(found, str):
            return found
        target, index = found
        end = op.address.ledd_end or index
        if not index <= end <= len(target.ledd):
            return f"ledd range {index}-{end} does not exist"
        del target.ledd[index - 1 : end]
        return None

    def apply_move_ledd(self, op: Operation) -> str | None:
        found = self._ledd_at(op)
        destination = self._target(op.destination)
        if isinstance(found, str) or destination is None:
            return "source ledd or destination provision missing"
        target, index = found
        place = op.address.destination_ledd
        if place is None or not 1 <= place <= len(destination.ledd) + 1:
            return f"destination ledd {place} does not fit {len(destination.ledd)} existing"
        moved = target.ledd.pop(index - 1)
        destination.ledd.insert(place - 1, moved)
        return None

    def _sentences(self, op: Operation) -> tuple[_Provision, int, list[str]] | str:
        found = self._ledd_at(op)
        if isinstance(found, str):
            return found
        target, index = found
        if _LIST_MARKUP.search(target.ledd[index - 1].xml):
            return "punktum operation on a ledd that carries a list"
        return target, index, split_sentences(target.ledd[index - 1].text)

    def apply_replace_punktum(self, op: Operation) -> str | None:
        found = self._sentences(op)
        if isinstance(found, str):
            return found
        target, index, sentences = found
        start, end = _sentence_slice(op.address, len(sentences))
        supplied = split_sentences(" ".join(ledd.text for ledd in op.ledd))
        if not 1 <= start <= end <= len(sentences) or len(supplied) != end - start + 1:
            return f"punktum {start}-{end}: {len(sentences)} existing, {len(supplied)} supplied"
        sentences[start - 1 : end] = supplied
        target.ledd[index - 1] = _ledd_from_text(" ".join(sentences))
        return None

    def apply_insert_punktum(self, op: Operation) -> str | None:
        found = self._sentences(op)
        if isinstance(found, str):
            return found
        target, index, sentences = found
        start, end = _sentence_slice(op.address, len(sentences) + 1)  # may follow the last
        supplied = split_sentences(" ".join(ledd.text for ledd in op.ledd))
        if not 1 <= start <= len(sentences) + 1 or len(supplied) != end - start + 1:
            return f"punktum {start} insertion: {len(sentences)} existing, {len(supplied)} supplied"
        sentences[start - 1 : start - 1] = supplied
        target.ledd[index - 1] = _ledd_from_text(" ".join(sentences))
        return None

    def apply_repeal_punktum(self, op: Operation) -> str | None:
        found = self._sentences(op)
        if isinstance(found, str):
            return found
        target, index, sentences = found
        start, end = _sentence_slice(op.address, len(sentences))
        if not 1 <= start <= end <= len(sentences):
            return f"punktum {start}-{end} does not exist among {len(sentences)}"
        del sentences[start - 1 : end]
        target.ledd[index - 1] = _ledd_from_text(" ".join(sentences))
        return None

    def apply_insert_bokstav(self, op: Operation) -> str | None:
        found = self._ledd_at(op, "item")
        if isinstance(found, str):
            return found
        if op.address.bokstav is None or len(op.ledd) != 1:
            return "item payload is not one text"
        target, index = found
        edited = _insert_item(target.ledd[index - 1].xml, op.address.bokstav, op.ledd[0].xml)
        if isinstance(edited, str):
            return edited
        target.ledd[index - 1] = edited
        return None

    def apply_repeal_bokstav(self, op: Operation) -> str | None:
        found = self._ledd_at(op, "item")
        if isinstance(found, str):
            return found
        target, index = found
        edited = _remove_item(target.ledd[index - 1].xml, op.address.bokstav or "")
        if isinstance(edited, str):
            return edited
        target.ledd[index - 1] = edited
        return None

    def apply_replace_bokstav(self, op: Operation) -> str | None:
        found = self._ledd_at(op, "item")
        if isinstance(found, str) or op.address.bokstav is None or len(op.ledd) != 1:
            return found if isinstance(found, str) else "bokstav target or replacement text missing"
        target, index = found
        letter = re.escape(op.address.bokstav)
        item = rf'<li[^>]*data-name="{letter}[.)]"[^>]*>(?:(?!<li)[\s\S])*?'
        head = rf'({item}<article class="legalP"[^>]*>)'
        pattern = re.compile(head + r"(.*?)(</article>)", re.S)
        xml, count = pattern.subn(
            lambda m: m.group(1) + op.ledd[0].xml + m.group(3), target.ledd[index - 1].xml
        )
        if count != 1:
            return f"bokstav {op.address.bokstav} matched {count} list items"
        try:
            text = _text_of_xml(xml)
        except etree.XMLSyntaxError:
            return "bokstav replacement leaves the ledd markup ill-formed"
        target.ledd[index - 1] = Ledd(xml=xml, text=text)
        return None

    def apply_substitute_term(self, op: Operation) -> str | None:
        places = _locations(op.locations)
        missing = [k for k, _ in places if k not in self.map]
        if not places or missing or op.term_old is None or op.term_new is None:
            return f"substitution locations unresolved: {missing or 'none listed'}"
        saved = {k: list(self.map[k].ledd) for k, _ in places}
        untouched = [
            k
            for k, ledd in places
            if not _substitute(self.map[k], ledd, (op.term_old, op.term_new))
        ]
        if not untouched:
            return None
        for k, ledd in saved.items():  # one location missed: the whole instruction is refused
            self.map[k].ledd = ledd
        return f"term not found in {untouched}"

    def apply_renumber(self, op: Operation) -> str | None:
        if op.absorbed_by is None:
            return "renumbering not implied by the operation before it"
        if op.absorbed_by not in self.done:
            return "the operation this renumbering follows did not apply"
        shift = _shift(op.instruction)
        if shift is None:
            return "renumbering sentence no longer parses"
        highest = max(new for _, new in shift.pairs)
        count = self._renumbered_count(op, shift.unit)
        if count < highest:
            holder = "provision" if shift.unit == "ledd" else "ledd"
            return f"renumbering names {shift.unit} {highest}, the {holder} has {count}"
        return None

    def _renumbered_count(self, op: Operation, unit: str) -> int:
        if unit == "ledd":
            target = self._target(op.address.section)
            return len(target.ledd) if target is not None else 0
        found = self._sentences(op)
        return 0 if isinstance(found, str) else len(found[2])

    def apply_unsupported(self, op: Operation) -> str | None:
        return f"unsupported instruction: {op.instruction[:80]}"


def _locations(text: str) -> list[tuple[str, frozenset[int]]]:
    """``§ 14 andre ledd tredje punktum og sjette ledd, § 15`` → [(§14, {2, 6}), (§15, {})]."""
    places: list[tuple[str, frozenset[int]]] = []
    chunks = list(_SECTION_REF.finditer(text))
    for index, match in enumerate(chunks):
        tail = text[match.end() : chunks[index + 1].start() if index + 1 < len(chunks) else None]
        ledd = frozenset(n for phrase in _LEDD_REF.findall(tail) for n in _ledd_numbers(phrase))
        places.append((f"§{match.group(1).replace(' ', '')}{match.group(2) or ''}", ledd))
    return places


def _ledd_numbers(phrase: str) -> list[int]:
    """``tredje og fjerde`` → [3, 4]; ``andre til femte`` → [2, 3, 4, 5]."""
    numbers = [n for word in _ORDINAL_WORD.findall(phrase) if (n := ordinal(word)) is not None]
    bounds = 2  # "andre til femte" names the two ends of a range
    if " til " in phrase and len(numbers) == bounds:
        return list(range(numbers[0], numbers[1] + 1))
    return numbers


def _word_pattern(word: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\wæøåÆØÅ]){re.escape(word)}(?![\wæøåÆØÅ])")


def _replace_words(value: str, old: str, new: str) -> str:
    """Whole-word replacement of ``old`` and its capitalised form, outside markup tags."""
    pairs = ((old, new), (old[:1].upper() + old[1:], new[:1].upper() + new[1:]))
    pieces = re.split(r"(<[^>]*>)", value)
    for index, piece in enumerate(pieces):
        if piece.startswith("<"):
            continue
        replaced = piece
        for before, after in pairs:
            replaced = _word_pattern(before).sub(after, replaced)
        pieces[index] = replaced
    return "".join(pieces)


def _substitute(provision: _Provision, ledd: frozenset[int], terms: tuple[str, str]) -> bool:
    """Replace within the listed ledd (every ledd when none is listed); True if changed."""
    old, new = terms
    changed = False
    for index, current in enumerate(provision.ledd):
        if ledd and index + 1 not in ledd:
            continue
        text, xml = _replace_words(current.text, old, new), _replace_words(current.xml, old, new)
        if text != current.text:
            provision.ledd[index], changed = Ledd(xml=xml, text=text), True
    return changed


_LETTERS = "abcdefghijklmnopqrstuvwxyzæøå"
# Item edits keep the space between two inline elements; the hashing default would fuse the words.
_FRAGMENT_PARSER = safe_parser(remove_blank_text=False)


def _previous_label(label: str) -> str | None:
    """The item label before ``label``: ``c`` → ``b``, ``3`` → ``2``; None for the first."""
    if label.isdigit():
        return str(int(label) - 1) if int(label) > 1 else None
    position = _LETTERS.find(label)
    return _LETTERS[position - 1] if position > 0 else None


def _items(root: etree._Element, label: str) -> list[etree._Element]:
    return [li for li in root.iter("li") if (li.get("data-name") or "").rstrip(".)") == label]


def _insert_item(xml: str, label: str, payload: str) -> Ledd | str:
    """A new ``<li>`` after its predecessor in the ledd's list, in Lovdata's item markup."""
    root = _ledd_root(xml)
    if root is None:
        return "ledd markup does not parse"
    if _items(root, label):
        return f"item {label} is already in the ledd"
    before = _previous_label(label)
    anchors = _items(root, before) if before else []
    lists = root.findall(".//ol") + root.findall(".//ul")
    if before is not None and len(anchors) != 1:
        return f"item {before}, the one before {label}, is {'not in the ledd' if not anchors else f'in {len(anchors)} places'}"  # noqa: E501
    if before is None and len(lists) != 1:
        return f"the ledd carries {len(lists)} lists"
    new = _item_element(label, payload)
    if isinstance(new, str):
        return new
    if anchors:
        anchors[0].addnext(new)
    else:
        lists[0].insert(0, new)
    return _ledd_from_root(root)


def _item_element(label: str, payload: str) -> etree._Element | str:
    markup = (
        f'<li data-name="{label}."><article class="listArticle">'
        f'<article class="legalP">{payload}</article></article></li>'
    )
    try:
        return etree.fromstring(markup, parser=_FRAGMENT_PARSER)
    except etree.XMLSyntaxError:
        return "item payload markup does not parse"


def _ledd_root(xml: str) -> etree._Element | None:
    try:
        return etree.fromstring(f"<article>{xml}</article>", parser=_FRAGMENT_PARSER)
    except etree.XMLSyntaxError:
        return None


def _remove_item(xml: str, label: str) -> Ledd | str:
    root = _ledd_root(xml)
    if root is None:
        return "ledd markup does not parse"
    found = _items(root, label)
    if len(found) != 1:
        return f"bokstav {label} matched {len(found)} list items"
    parent = found[0].getparent()
    if parent is None:
        return f"item {label} has no list around it"
    parent.remove(found[0])
    return _ledd_from_root(root)


def _ledd_from_root(root: etree._Element) -> Ledd:
    inner = (root.text or "") + "".join(etree.tostring(child, encoding=str) for child in root)
    return Ledd(xml=inner, text=element_text(root))


def _insert_position(order: list[str], key: str) -> int:
    """After the last existing key that sorts before ``key``; at the end when none does."""
    position = 0
    for index, existing in enumerate(order):
        if section_sort_key(existing) < section_sort_key(key):
            position = index + 1
    return position


def _targets(op: Operation, working: _Working) -> list[_Provision]:
    keys = (
        [op.address.section, op.destination]
        if op.kind != "substitute_term"
        else [k for k, _ in _locations(op.locations)]
    )
    return [working.map[k] for k in keys if k and k in working.map]


def _order_key(event: Event) -> tuple[date, datetime, int]:
    return (event.timing.effective or date.max, event.published_at or datetime.min, event.sequence)


def _classify(event: Event, at: date) -> Literal["apply", "future", "pending", "unresolved"]:
    if event.timing.status == "dated" and event.timing.effective is not None:
        return "apply" if event.timing.effective <= at else "future"
    return "pending" if event.timing.status == "pending" else "unresolved"


def _apply(working: _Working, event: Event, act_problems: list[str]) -> str | None:
    op = event.operation
    handler: Callable[[Operation], str | None] = getattr(working, f"apply_{op.kind}")
    problem = handler(op)
    targets = _targets(op, working)
    if problem is None:
        working.done.add(op.id)
        for target in targets:
            target.applied.append(op.id)
        return None
    for target in targets:
        target.problems.append(f"{op.id}: {problem}")
    if not targets:
        act_problems.append(f"{op.id}: {problem}")
    return problem


def _baseline_status(baseline_timing: Timing, at: date) -> StateStatus:
    if baseline_timing.status != "dated" or baseline_timing.effective is None:
        return "unresolved_baseline"
    return "replayed" if baseline_timing.effective <= at else "not_yet_in_force"


def _substitutes_flagged(op: Operation, working: _Working) -> bool:
    """A term substitution reaches a ledd not in force: a listed one, or any when none is listed."""
    for key, ledd in _locations(op.locations):
        target = working.map.get(key)
        if target is None:
            continue
        present = {i for i in target.unit.flagged if i <= len(target.ledd)}
        if present and (not ledd or ledd & present):
            return True
    return False


def _unit_gate(op: Operation, working: _Working) -> str | None:
    """``withheld`` / an unresolved reason / None (apply) from the targets' commencement at D."""
    if op.kind == "insert_section" and op.new_provision is not None and op.address.section:
        new = op.new_provision
        parts = (new.part or "", op.part.upper())
        return state_gate(working.unit_state(op.address.section, new.chapter, parts))
    for target in _targets(op, working):
        gate = state_gate(target.unit)
        if gate is not None:
            return gate
        if addresses_flagged(op, target.unit.flagged, len(target.ledd)):
            return "withheld"
        if restructures(op, target.unit.flagged, len(target.ledd)):
            return RESTRUCTURED
    if op.kind == "substitute_term" and _substitutes_flagged(op, working):
        return "withheld"
    return None


def _keys(provisions: tuple[ProvisionState, ...], in_force: InForce) -> tuple[str, ...]:
    return tuple(p.key for p in provisions if p.in_force == in_force)


class _Run:
    """One replay pass: the bucket every event lands in."""

    def __init__(self, working: _Working) -> None:
        self.working = working
        self.applied: list[str] = []
        self.future: list[str] = []
        self.pending: list[str] = []
        self.unresolved: list[tuple[str, str]] = []
        self.act_problems: list[str] = []
        self.withheld: list[str] = []

    def take(self, event: Event, verdict: str) -> None:
        op = event.operation
        if verdict == "apply":
            self._apply(event)
        elif verdict == "unresolved":
            self.unresolved.append((op.id, event.timing.reason))
            for target in _targets(op, self.working):
                target.problems.append(f"{op.id}: commencement {event.timing.reason}")
        elif verdict == "future":
            self.future.append(op.id)
        else:
            self.pending.append(op.id)
            for target in _targets(op, self.working):
                target.pending.append(op.id)

    def _apply(self, event: Event) -> None:
        """Apply a dated operation unless its target unit is not in force at D (Decision 4)."""
        op = event.operation
        gate = _unit_gate(op, self.working)
        if gate == "withheld":
            self.withheld.append(op.id)
            return
        if gate is not None:
            self.unresolved.append((op.id, gate))
            for target in _targets(op, self.working):
                target.problems.append(f"{op.id}: {gate}")
            return
        problem = _apply(self.working, event, self.act_problems)
        if problem:
            self.unresolved.append((op.id, problem))
        else:
            self.applied.append(op.id)

    def state(
        self, act: str, at: date, status: StateStatus, provisions: tuple[ProvisionState, ...]
    ) -> ReplayState:
        return ReplayState(
            act=act, at=at, status=status, provisions=provisions,
            applied=tuple(self.applied), future=tuple(self.future), pending=tuple(self.pending),
            unresolved=tuple(self.unresolved), act_level_problems=tuple(self.act_problems),
            withheld=tuple(self.withheld), not_in_force=_keys(provisions, "not_yet_in_force"),
            pending_commencement=_keys(provisions, "pending"),
            unresolved_commencement=_keys(provisions, "unresolved"),
        )  # fmt: skip


def replay(
    baseline: Document, commencement: Timing | Commencement, events: list[Event], at: date
) -> ReplayState:
    """The act's provisions at legal date ``at``; refuses nothing, marks everything.

    ``commencement`` is the act's per-unit commencement; a bare ``Timing`` is the act-wide
    special case (one commencement for every unit).
    """
    if isinstance(commencement, Timing):
        commencement = Commencement(default=commencement)
    status = _baseline_status(commencement.default, at)
    working = _Working(baseline, commencement, at)
    run = _Run(working)
    for event in sorted(events, key=_order_key):
        verdict = _classify(event, at)
        if verdict == "apply" and status != "replayed":
            run.withheld.append(event.operation.id)  # dated, but the act itself is not in force
        else:
            run.take(event, verdict)
    provisions = (
        tuple(working.map[k].freeze(k) for k in working.order) if status == "replayed" else ()
    )
    return run.state(baseline.header.refid, at, status, provisions)
