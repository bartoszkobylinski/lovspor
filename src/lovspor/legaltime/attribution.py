"""Which act an intro outside the canonical shapes binds, proven or not at all (#861 phase 4B).

``instructions`` binds an act from two fixed intro shapes. Lovtidend prints many more:
a short or full title before the citation (``I straffegjennomføringsloven 18. mai 2001 nr. 21
gjøres følgende endringer:``), a commencement clause before the verb (``Med virkning fra … gjøres
følgende endringer i lov 22. mai 1902 nr. 13 militær straffelov:``), a numbered act heading under
a list intro, or an intro printed at the end of the previous payload ledd. Such an intro binds
its act only when every link of this chain holds:

1. its sentence (the last one of the node) has one of the closed shapes below;
2. it cites exactly one act by day, month, year and number;
3. it amends that act, not an earlier amendment of it (``om endringer i``, ``romertall``);
4. the member's own header lists that act among the documents it changes.

Otherwise the block stays closed and what follows stays unattributed. A short title alone is
never an identity, and nothing is matched by similarity.

The two canonical shapes (``INTRO``, ``INTRO_SLIK``) bind a citation by day, month, year and
number as printed. A citation by date alone binds through the same header proof
(``header_proves``): only when the header lists exactly that date-only refid and no numbered
act of the same date (#872).
"""

import re

from lovspor.legaltime.document import Document, Frozen
from lovspor.legaltime.text import MONTHS, act_refid, split_sentences

_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_CITATION = re.compile(rf"(\d{{1,2}})\.\s*({_MONTH})\s+(\d{{4}})\s+nr\.\s*(\d+)\b", re.I)
_CITE = rf"\d{{1,2}}\.\s*(?:{_MONTH})\s+\d{{4}}\s+nr\.\s*\d+\b"
_ITEM = r"(?:\d{1,3}\s?[a-z]?[.)]\s*)"  # ``17.``, ``1 a.``, ``3)``
_THESE = r"(?:følgende|følgjande|desse)\s+endring\w*"
_SHAPES = (
    re.compile(  # I <title> <citation> … gjøres følgende endringer:
        rf"^{_ITEM}?I\s+[^§:]*?{_CITE}[^§:]*?\s+(?:gjøres|skal|blir|vert)\s+{_THESE}"
        r"(?:\s+gjerast)?:$",
        re.I,
    ),
    re.compile(  # <title> <citation> … endres slik:
        rf"^{_ITEM}?[^§:]*?{_CITE}[^§:]*?\s+(?:endres|endrast|vert\s+endra|blir\s+endra)\s+slik:$",
        re.I,
    ),
    re.compile(  # … gjøres følgende endringer i lov <citation> …:
        rf"^[^§:]*?\s(?:gjøres|gjerast|blir|vert)\s+{_THESE}\s+i\s+(?:lov\s+)?{_CITE}[^§:]*?"
        r"(?:\s+gjort\s+gjeldande)?:$",
        re.I,
    ),
)
_HEADING = re.compile(rf"^{_ITEM}Lov\s+{_CITE}(?:\s+(?:om|for|til)\s[^§:]*)?:?$")
_VERB = re.compile(
    r"\b(?:oppheves|opphevast|oppheva|skal|lyde|endres|endrast|gjøres|blir|vert|trer|gjelder"
    r"|tilføyes|får|erstattes)\b",
    re.I,
)  # a heading names its act and nothing more: a verb makes it an instruction
_UNIT_FIRST = re.compile(
    rf"^{_ITEM}?(?:I\s+)?(?:§|kapit|kap\.|(?:\w+\s+)?del\b|nr\.|bokstav|punkt|overskrift"
    r"|innledning|vedlegg)",
    re.I,
)
_INLINE = re.compile(
    rf"^{_ITEM}(?:I\s+)?[Ll]ov\s+{_CITE}(?P<title>[^§:]*?)\s+(?:skal\s+)?(?P<rest>(?:ny\w*\s+)?§.*)$",
    re.S,
)  # ``19. Lov 17. juni 2005 nr. 90 om mekling … § 34-4 … skal lyde:``: heading + instruction
_INDIRECT = re.compile(r"\bom\s+endring|\(\s*endring|\bromertall\b|\bendringslov", re.I)
_AFTER_VERB = re.compile(
    rf"\b(?:gjøres|gjerast|blir|vert)\s+{_THESE}\s+i\s+(?:lov\s+)?$", re.I
)  # ``… gjøres følgende endringer i lov <cit>``: the main verb's own object
_I_FRAME = re.compile(rf"^{_ITEM}?I\s")  # ``I <act> …`` already names the target up front
_ACT_NOUN = re.compile(r"\b[\wæøå-]*(?:lov|loven|lova)\b", re.I)
_TITLE_SLOT = re.compile(
    rf"^{_ITEM}?(?:I\s+)?(?:[^,()]*?(?:lov|loven|lova)|lov\s+om\s[^,()]*?)\s+$", re.I
)  # ``I straffegjennomføringsloven <cit>``, ``Lov om rettergangsmåten i straffesaker <cit>``
_NOT_A_NAME = re.compile(
    r"\b(?:etter|ved|jf|lyder|lydde|endret|endra|endring\w*|av|følger)\b", re.I
)
# ``I straffeloven, slik den lyder etter lov …`` / ``… med de endringer som følger av lov …``:
# a citation in such a clause is the source of a wording, not the act amended
_DATE_ONLY = re.compile(r"lov/\d{4}-\d{2}-\d{2}")  # Lovdata's refid of an unnumbered act
_ACT_LED = re.compile(rf"^{_ITEM}(?:I\s+)?[Ll]ov\s+(?:av\s+)?\d{{1,2}}\.\s*(?:{_MONTH})\s", re.I)
# a numbered list item led by an act citation instructs that act, never the block before it;
# an unnumbered ``Lov … (privatskolelova)`` is a payload (a new title) and closes nothing


INTRO = re.compile(
    r"^(?:\d+\.\s*)?I\s+(?:midlertidig\s+)?(?:lov\s+av\s+(?=\d)|(?=lov))(?P<act>lov\s+)?"
    r"(?P<date>\d{1,2}\.\s*\w+\s+\d{4}(?:\s+nr\.\s*\d+)?)\b.*?"
    r"\s+(?:(?:gjøres|gjerast|gjer\s+ein|blir|vert|skal)\s+(?P<rest>.*)"
    r"|(?P<inline>(?:oppheves|opphevast)\s+§.*)|(?P<repeal>oppheves|opphevast|blir\s+oppheva|vert\s+oppheva)\.?)$",
    re.S,
)
INTRO_SLIK = re.compile(
    r"^(?:\d+\.\s*)?(?:I\s+)?[Ll]ov\s+(?:av\s+)?(?P<date>\d{1,2}\.\s*\w+\s+\d{4}(?:\s+nr\.\s*\d+)?)\b"
    r"[^:]*?\s+(?:endres|endrast|vert\s+endra|blir\s+endra)\s+slik:$"
)  # ``Lov 20. mai 2005 nr. 28 om straff endres slik:`` opens an amendment block too


class Binding(Frozen):
    """What a node does to the open block: close it, and bind what follows to ``act``."""

    opens: bool = False
    act: str | None = None
    head: str = ""  # the node's text before the intro sentence, still read unattributed
    intro: str = ""  # the intro sentence itself


def citations(text: str) -> list[str]:
    """``lov/YYYY-MM-DD-N`` for every act cited by day, month, year and number."""
    return [
        f"lov/{int(y):04d}-{MONTHS[m.lower()]:02d}-{int(d):02d}-{int(n)}"
        for d, m, y, n in _CITATION.findall(text)
    ]


def header_proves(refid: str, changes: tuple[str, ...]) -> bool:
    """The member's own ``changesToDocuments`` lists ``refid``, and nothing else it could mean.

    A date-only refid (an unnumbered act) is proven only when the header lists exactly it and
    no numbered act of the same date: a date alone may be a citation with its number left out.
    """
    if _DATE_ONLY.fullmatch(refid) is None:
        return refid in changes
    date = refid.split("-")  # ``lov/YYYY``, ``MM``, ``DD``: any act of that date shares them
    return {c for c in changes if c.split("-")[:3] == date} == {refid}


def identified(refid: str | None, changes: tuple[str, ...]) -> str | None:
    """The act a canonical citation or a substitution item names (#872).

    A citation by day, month, year and number names its act as printed, as it always has. A
    date alone names one only when ``header_proves`` its refid; otherwise it names no act.
    """
    if refid is None or _DATE_ONLY.fullmatch(refid) is None:
        return refid
    return refid if header_proves(refid, changes) else None


def item_act(text: str, changes: tuple[str, ...]) -> str | None:
    """The act a substitution-list item cites: ``identified``, or ``""`` when it cites one unproven.

    ``""`` keeps the item's locations as operations of no act, so they are counted, not dropped.
    """
    cited = act_refid(text)
    return cited and (identified(cited, changes) or "")


def canonical_act(date: str, changes: tuple[str, ...]) -> str | None:
    """The act a canonical intro's citation names: ``identified``, from its date group."""
    return identified(act_refid(f"lov {date}"), changes)


def _named(lead: str) -> bool:
    """The text before the citation leaves it where the amended act is named.

    Either right after the main verb's ``gjøres følgende endringer i [lov]``, or after a title
    ending in an act noun (or ``Lov om <title>``) that holds no other act noun and no comma,
    bracket or referring word: ``I straffeloven med hjemmel i lov …`` names two acts.
    """
    if _AFTER_VERB.search(lead):
        return _I_FRAME.match(lead) is None  # ``I straffeloven, gjøres … i lov <cit>``: two targets
    titled = _TITLE_SLOT.match(lead) is not None and len(_ACT_NOUN.findall(lead)) == 1
    return titled and _NOT_A_NAME.search(lead) is None


def proven(sentence: str, changes: tuple[str, ...]) -> str | None:
    """The act a sentence proves it names: the evidence chain of the module docstring."""
    return _proven(sentence, changes)


def _proven(sentence: str, changes: tuple[str, ...]) -> str | None:
    cited = citations(sentence)
    if len(cited) != 1 or _INDIRECT.search(sentence) or not header_proves(cited[0], changes):
        return None
    first = next(_CITATION.finditer(sentence))  # the one citation ``cited`` holds
    return cited[0] if _named(sentence[: first.start()]) else None


def _shaped(sentence: str) -> bool:
    if _UNIT_FIRST.match(sentence):
        return False
    return any(shape.match(sentence) for shape in _SHAPES)


def _heading(text: str) -> bool:
    return _HEADING.match(text) is not None and _VERB.search(text) is None


def is_intro(text: str) -> bool:
    """One of the two canonical intro shapes; ``instructions`` binds them by ``canonical_act``."""
    return INTRO.match(text) is not None or INTRO_SLIK.match(text) is not None


def opens_block(text: str) -> bool:
    """An intro of a closed shape ends the node's text, so no payload runs past it.

    Numbered headings and items are left out: a payload provision may list acts.
    """
    return not is_intro(text) and _shaped(split_sentences(text)[-1])


def bind(text: str, changes: tuple[str, ...], in_list: bool) -> Binding:
    """The binding a node's text states; ``in_list``: inside a list intro naming no act."""
    if is_intro(text):
        return Binding()  # ``instructions._open_block`` reads it
    sentences = split_sentences(text)  # never empty: ``""`` is one empty sentence
    last = sentences[-1]
    if _shaped(last):
        head = " ".join(sentences[:-1])
        return Binding(opens=True, act=_proven(last, changes), head=head, intro=last)
    if _heading(text):
        return Binding(opens=True, act=_proven(text, changes) if in_list else None)
    return Binding(opens=_ACT_LED.match(text) is not None)


def inline(text: str, changes: tuple[str, ...]) -> tuple[str, str] | None:
    """(act, instruction) of a numbered item naming its act and instructing it in one sentence."""
    match = _INLINE.match(text)
    if match is None or _VERB.search(match.group("title")):
        return None
    act = _proven(text[: match.start("rest")], changes)
    return (act, match.group("rest")) if act is not None else None


_COMMENCEMENT_HEADING = re.compile(r"ikraft|iverkset|i\s+kraft", re.I)


def commencement_provisions(document: Document) -> list[str]:
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
