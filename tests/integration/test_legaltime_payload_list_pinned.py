"""#907 on the pinned member: § 411's list items never rebind straffeloven's § 402 → § 412.

``lti/2009/nl-20090619-074.xml`` (``lov/2009-06-19-74``) in the pinned 2001-2025 archive.
Part I opens "Lov 20. mai 2005 nr. 28 om straff endres slik:". Node 239 renames § 401 to
§ 411, whose last ledd ends "… gjøres følgende endringer i andre lover:"; nodes 241-243 are that
list's items as flat ``defaultP`` siblings (244 is item 3's payload); node 245 renames § 402 to
§ 412. Main ``76d93b69`` binds 245 to ``lov/1975-06-13-39``, the act item 3 cites.

§ 412's own list (nodes 247-1430, to the end of part I) holds item 18 at node 460, "I lov 17.
juni 1932 nr. 6 … skal § 4 annet ledd lyde:", payload 461, then node 462 "§ 4 annet ledd annet
punktum oppheves.": the item's own second instruction, in the same shape as 245. The source
does not state where § 411's list ends, so 245 is bound to no act rather than to a guessed one,
and 462 likewise (main binds it to item 18's act by the D7 chain).

Runs only where the pinned archive sits in the gitignored cache; a skip is not a pass.
"""

import hashlib
from pathlib import Path

import pytest

from lovspor.legaltime.archive import file_sha256, read_members
from lovspor.legaltime.document import Document
from lovspor.legaltime.instructions import interpret
from lovspor.legaltime.nesting import nesting

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "data/cache/legaltime/lovtidend-avd1-2001-2025.tar.bz2"
PIN = "d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0"
MEMBER = "lti/2009/nl-20090619-074.xml"
MEMBER_SHA = "7ec6a1b4ae319b86ae6735eb2e969022e827afad552c9f9e8a9e4e12db1304d4"
STRAFFELOVEN = "lov/2005-05-20-28"
OP = "lov/2009-06-19-74#1:I:"
BOUNDARY = {  # node index in part I → its text, as the source prints it
    239: "Nåværende § 401 blir § 411 som innleder tredje del og som skal lyde:",
    241: "1. Militær straffelov 22. mai 1902 nr. 13 § 107 oppheves.",
    242: "2. Lov 13. desember 1946 nr. 14 om straff for utlendske krigsbrotsmenn oppheves.",
    243: "3. I lov 13. juni 1975 nr. 39 om utlevering av lovbrytere mv. skal § 9 annet punktum"
    " lyde:",
    245: "Nåværende § 402 blir § 412 og skal lyde:",
    460: "18. I lov 17. juni 1932 nr. 6 om kvalitetskontroll med landbruksvarer m.v. skal § 4 annet"
    " ledd lyde:",
    462: "§ 4 annet ledd annet punktum oppheves.",
}

pytestmark = pytest.mark.skipif(not ARCHIVE.exists(), reason="no local Lovdata archive")


@pytest.fixture(scope="module")
def member() -> Document:
    if file_sha256(ARCHIVE) != PIN:
        pytest.skip("local archive is not the pinned one")
    return read_members(ARCHIVE, [MEMBER])[MEMBER]


def test_the_member_and_its_boundaries_are_the_pinned_ones(member: Document) -> None:
    assert member.sha256 == MEMBER_SHA
    nodes = member.parts[1].nodes
    assert (member.parts[1].label, nodes[0].text) == (
        "I",
        "Lov 20. mai 2005 nr. 28 om straff endres slik:",
    )
    assert {i: nodes[i].text for i in BOUNDARY} == BOUNDARY
    host = nodes[240].provision
    assert host is not None and host.label == "§ 411"
    assert host.ledd[-1].text.endswith("gjøres følgende endringer i andre lover:")
    assert (nodes[244].kind, nodes[246].provision and nodes[246].provision.label) == (
        "ledd",
        "§ 412",
    )
    digest = hashlib.sha256(nodes[245].text.encode()).hexdigest()  # ADR-0020 D9 sentence digest
    assert digest == "57a9640b483586ee7503faed2e6875d9073ecac65d62387f3100d797bb3947c9"


def test_the_rename_after_the_list_binds_no_guessed_act(member: Document) -> None:
    acts = {op.id: (op.kind, op.act) for op in interpret(member).operations}
    assert acts[OP + "239.0"] == ("renumber", STRAFFELOVEN)
    assert acts[OP + "245.0"] == ("renumber", None)  # main: lov/1975-06-13-39


def test_the_same_shape_inside_the_list_is_not_guessed_either(member: Document) -> None:
    acts = {op.id: (op.kind, op.act) for op in interpret(member).operations}
    assert acts[OP + "460.0"] == ("replace_ledd", "lov/1932-06-17-6")
    assert acts[OP + "462.0"] == ("repeal_punktum", None)  # main: lov/1932-06-17-6


def test_the_list_items_bind_only_what_they_prove(member: Document) -> None:
    acts = {op.id: op.act for op in interpret(member).operations}
    assert acts[OP + "241.0"] is None  # militær straffelov, unproven; main: straffeloven
    assert acts[OP + "242.0"] is None
    assert acts[OP + "243.0"] == "lov/1975-06-13-39"


def test_every_operation_is_kept_with_its_kind(member: Document) -> None:
    """Only acts move: the member's (id, kind) list is main ``76d93b69``'s, digest for digest."""
    operations = interpret(member).operations
    lines = "".join(f"{op.id} {op.kind}\n" for op in operations)
    assert len(operations) == 835
    digest = hashlib.sha256(lines.encode()).hexdigest()
    assert digest == "348c8bf4ec9ee4e6a5cc14b0c73ed91a9d489246b738f600244bc775628c3308"


def test_no_nesting_region_is_found_for_renaming_hosts(member: Document) -> None:
    """``nesting`` reads ``replace_section`` hosts only; § 411 and § 412 are renamed (as main)."""
    assert nesting(member, interpret(member)).nests == ()
