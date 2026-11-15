"""Numbered items a replacement provision announces, printed after it as flat siblings (#907).

Lovtidend prints a replaced section whose last ledd announces a list of amendments to other
acts (``… gjøres følgende endringer i andre lover:``), then the list's numbered items as
``defaultP`` siblings of the provision, not as its children: ``lov/2009-06-19-74`` part I
§ 411 and § 412, ``lov/2013-04-05-12`` § 102 (``nesting``). The items are the provision's
own text: none of them amends the act the outer block amends.

While such a list is open, the reader:

* starts every numbered item with no act and the block unbound: an item binds only an act it
  proves itself (ADR-0020 D7), never the outer act and never the previous item's;
* after an open item (``… gjøres følgende endring[er]:``, ``… endres slik:``), reads the
  unnumbered instructions that follow as that item's, as the D7 chain does at the top level;
* after any other item, or before the first, binds no act to an unnumbered instruction.

Where the list ends is not stated by the markup, and the grammar does not state it either: in
``lov/2009-06-19-74`` item 3 ("I lov … nr. 39 … skal § 9 annet punktum lyde:") is followed by
"Nåværende § 402 blir § 412 og skal lyde:", straffeloven's own renaming, while item 18 ("I lov …
nr. 6 … skal § 4 annet ledd lyde:") is followed by "§ 4 annet ledd annet punktum oppheves.", the
item's own second instruction. The same shape sits on either side of the boundary, so neither
the item's act nor the outer act is proven for such an instruction: it stays an operation of
no act, and the outer act is never restored inside the part.
"""

import re
from typing import Literal

from lovspor.legaltime.document import Node, Provision

# ``Fra den tid … gjøres følgende endringer i andre lover:``: the provision announces a list
LIST_INTRO = re.compile(
    r"endring\w*\s+i\s+(?:andre\s+lover|anna\s+lovgjeving|annan\s+lovgjeving|annen\s+lovgivning)"
    r"\s*:\s*$",
    re.I,
)
NUMBERED = re.compile(r"^\d{1,3}\s?[a-z]?[.)]\s")  # ``50.``, ``1 a.``, ``3)``: a list item
_OPEN = re.compile(  # the item announces a block of its own instructions, and states none
    r"(?:\b(?:følgende|følgjande|desse)\s+endring\w*(?:\s+gjøres|\s+gjerast)?"
    r"|\b(?:endres|endrast|vert\s+endra|blir\s+endra)\s+slik):$",
    re.I,
)

State = tuple[str | None, bool, str | None]  # the reader's (act, unbound, bound host)
Item = Literal["none", "open", "unproven"]


def host_list(provision: Provision | None) -> bool:
    """The provision's last ledd announces a list of amendments to other acts."""
    return (
        provision is not None
        and bool(provision.ledd)
        and LIST_INTRO.search(provision.ledd[-1].text.strip()) is not None
    )


class Listing:
    """Whether a consumed payload opened a list in this part, and how its last item began."""

    def __init__(self) -> None:
        self.active = False
        self.item: Item = "none"

    def open(self, payload: tuple[Node, ...]) -> None:
        """An instruction's payload ending in a list-announcing provision opens the list."""
        if payload and host_list(payload[-1].provision):
            self.active, self.item = True, "none"

    def step(self, node: Node, state: State) -> State:
        """The reader's state for ``node``, before the node binds anything itself."""
        if not self.active or node.kind != "paragraph":
            return state
        if node.host_section != state[2]:
            self.active = False  # another article: the reader closes the block itself
            return state
        opens = _OPEN.search(" ".join(node.text.split())) is not None
        if NUMBERED.match(node.text):
            self.item = "open" if opens else "unproven"
            return (None, True, state[2])
        if self.item == "open":
            return state
        self.item = "open" if opens else self.item  # an unnumbered intro binds its own block
        return (None, True, state[2])
