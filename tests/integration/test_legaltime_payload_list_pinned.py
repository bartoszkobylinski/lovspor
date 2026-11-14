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
import re
import tarfile
from pathlib import Path

import pytest

from lovspor.legaltime.archive import file_sha256, read_members
from lovspor.legaltime.document import Document
from lovspor.legaltime.instructions import interpret
from lovspor.legaltime.nesting import WORDING, nesting

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


def test_the_renaming_hosts_open_one_refused_region(member: Document) -> None:
    """#918: § 411's renaming opens a region (to the end of part I, § 412's inside); it is refused,
    so no item is dated, and no item's act changes (#907's bindings above are the same)."""
    (nest,) = nesting(member, interpret(member)).nests
    assert (nest.host_op, nest.host_act, nest.problem) == (OP + "239.0", STRAFFELOVEN, WORDING)
    assert {OP + "241.0", OP + "245.0", OP + "462.0"} <= set(nest.members)


DIGESTS = {  # ADR-0020 D9 convention: whitespace collapsed; payload provisions by their ledd
    239: "bbbac519dcb305364cd56dcfebf96675ca010466f7d33ed5f0ec81c000fdc926",
    240: "cf83713f91dfb2c5c808f68c10d903be2e1c590ce6403620ad1e642fba7cc8e6",
    241: "7869a79a332963b986537722c5ff8d030bd112fbd89a50af11ab0a372d9a3363",
    242: "5d0fe5ffbdc1792dbdcc2ef134b25ef32edc45da4946b7eb6cff12eb8a147cea",
    243: "5241f5a81269a5b0ec6552805132230333aec9e170173100691d310269a792f6",
    244: "ce04b1e5cf017d66e8e198100bceb45ae56d1e9dfe22adb2c47605de950b238f",
    245: "57a9640b483586ee7503faed2e6875d9073ecac65d62387f3100d797bb3947c9",
    246: "04e77b90033724068cb4a1ca5855216f64cd6c65cf58c0a416e191d0b16fa823",
    460: "5e51ec6dc9c3636e1f7218e7e1807f67ddd1caf15494b20ef1a32ae5b0b2bcbd",
    461: "cde1f59a74e8b87f2465d4427283eeb97e8bc69542f76371a9b4f012c5681fa6",
    462: "3ecc081e3643bf1455394f12715b059e6c4afa28ac4efbf670f636dc8d070c96",
}


def _digest(member: Document, index: int) -> str:
    node = member.parts[1].nodes[index]
    if node.provision is not None:
        text = "\n".join(" ".join(ledd.text.split()) for ledd in node.provision.ledd)
    else:
        text = " ".join(node.text.split())
    return hashlib.sha256(text.encode()).hexdigest()


def test_every_boundary_node_is_pinned_by_digest(member: Document) -> None:
    assert {i: _digest(member, i) for i in DIGESTS} == DIGESTS


def test_no_signal_in_the_member_separates_245_from_an_items_own_instruction(
    member: Document,
) -> None:
    """Why 245 is refused, not bound to straffeloven: each candidate signal has a counterexample.

    * markup: 245 (outer), 462 and 1332 (items' own) are the same ``defaultP`` paragraph;
    * the preceding item's shape: item 3 before 245 and item 18 before 462 are both
      "I lov … skal § N … lyde:" followed by one ``legalP`` payload;
    * a provision payload: 666, 836, 1053 and 1386 are items' own instructions whose payload is a
      ``futureLegalArticle``, as 245's is.
    """
    nodes = member.parts[1].nodes
    assert {nodes[i].css_class for i in (245, 462, 1332)} == {"defaultP"}
    assert [(nodes[i].kind, nodes[i + 1].kind) for i in (243, 460)] == [("paragraph", "ledd")] * 2
    assert all(nodes[i].text.endswith("lyde:") for i in (243, 460))
    assert all(nodes[i + 1].kind == "provision" for i in (245, 666, 836, 1053, 1386))
    acts = {op.id: op.act for op in interpret(member).operations}
    assert acts[OP + "666.0"] is not None  # an open item's own: bound by the D7 chain


def _member_bytes() -> bytes:
    with tarfile.open(ARCHIVE, "r:bz2") as archive:  # one member read in memory, nothing extracted
        handle = archive.extractfile(MEMBER)
        assert handle is not None
        return handle.read()


def test_neither_the_markup_ids_nor_the_members_summary_separate_the_levels(
    member: Document,
) -> None:
    """Two more signals, two more counterexamples, read from the member's own bytes.

    * ``futureLegalArticle`` ids count one sequence over the whole member: § 411 and § 412 are
      ``foreslåttparagraf-177`` and ``-178``, and the very next one, ``-179``, is § 3-6 of item
      77's act (skadeserstatningsloven), not straffeloven;
    * part 0's summary ("Endringer i følgende lover: …") names straffeloven and the items' acts
      alike, and says nothing of which instruction belongs to which.
    """
    body = _member_bytes()
    assert hashlib.sha256(body).hexdigest() == MEMBER_SHA
    ids = re.findall(
        r'class="futureLegalArticle" data-name="([^"]+)" id="kapittel-1-foreslåttparagraf-(\d+)"',
        body.decode(),
    )
    assert [pair for pair in ids if pair[1] in {"177", "178", "179"}] == [
        ("§411", "177"),
        ("§412", "178"),
        ("§3-6", "179"),
    ]
    summary = member.parts[0].nodes[1].text
    assert all(
        cited in summary
        for cited in ("20. mai 2005 nr. 28", "13. juni 1975 nr. 39", "17. juni 1932 nr. 6")
    )
