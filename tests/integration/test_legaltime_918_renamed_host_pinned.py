"""#918 on the pinned archives: the lists of straffeloven § 411 and § 412 are never "straks".

``lti/2009/nl-20090619-074.xml`` (``lov/2009-06-19-74``, member sha256 ``7ec6a1b4…04d4``) in
the pinned 2001-2025 archive. Part I amends straffeloven; part II is "Loven trer i kraft
straks. Ikraftsetting av straffeloven 20. mai 2005 nr. 28 reguleres av § 411 i lovvedtaket del
I.". Two sentences of part I rename and replace a section at once (kind ``renumber``):

* node 239, "Nåværende § 401 blir § 411 som innleder tredje del og som skal lyde:" (act
  straffeloven). § 411's last ledd is conditional on chapter 16 ("Fra den tid kapittel 16 tar
  til å gjelde, gjøres følgende endringer i andre lover:"), and has two ledd;
* node 245, "Nåværende § 402 blir § 412 og skal lyde:" (no act since #909). § 412's only ledd
  is "Med virkning fra den tid hele straffeloven 20. mai 2005 nr. 28 settes i kraft, gjøres
  følgende endringer i annen lovgivning:".

Neither wording is the one verified against its host's commencement (#893, ADR-0024 R4), and the
host's unit is not followed across the renaming, so the region is refused and every operation
after node 240 in part I is ``unresolved``. Replay-8 dated all of them 2009-06-19 by part II's
"straks": 4 arbeidsmiljøloven and 10 utlendingsloven operations, and 37 naming no act.

Runs only where the pinned archives sit in the gitignored cache; a skip is not a pass.
"""

from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from lovspor.legaltime.archive import collect, file_sha256, read_members
from lovspor.legaltime.commencement import Timing
from lovspor.legaltime.document import Document
from lovspor.legaltime.instructions import interpret
from lovspor.legaltime.nesting import WORDING, nesting
from lovspor.legaltime.run import events_for, unattributed_for

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/cache/legaltime"
ARCHIVES = [CACHE / "lovtidend-avd1-2001-2025.tar.bz2", CACHE / "lovtidend-avd1-2026.tar.bz2"]
PIN = "d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0"
MEMBER = "lti/2009/nl-20090619-074.xml"
MEMBER_SHA = "7ec6a1b4ae319b86ae6735eb2e969022e827afad552c9f9e8a9e4e12db1304d4"
STRAFFELOVEN = "lov/2005-05-20-28"
ARBEIDSMILJOLOVEN = "lov/2005-06-17-62"
UTLENDINGSLOVEN = "lov/2008-05-15-35"
OP = "lov/2009-06-19-74#1:I:"
STRAKS = date(2009, 6, 19)
AFFECTED = {  # operation → (kind, section) in replay-8's attributed events, dated 2009-06-19
    ARBEIDSMILJOLOVEN: {
        "1361.0": ("repeal_punktum", "§19-2"),
        "1362.0": ("insert_punktum", "§19-4"),
        "1364.0": ("replace_section", "§19-5"),
        "1366.0": ("repeal_section", "§19-7"),
    },
    UTLENDINGSLOVEN: {
        "1417.0": ("replace_bokstav", "§66"),
        "1418.0": ("unsupported", None),
        "1419.0": ("replace_bokstav", "§68"),
        "1420.0": ("replace_punktum", "§77"),
        "1422.0": ("replace_punktum", "§97"),
        "1424.0": ("unsupported", None),
        "1425.0": ("unsupported", None),
        "1426.0": ("insert_ledd", "§108"),
        "1428.0": ("repeal_ledd", "§108"),
        "1428.1": ("renumber", None),
    },
}

pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in ARCHIVES), reason="no local Lovdata archives"
)


def _pinned() -> None:
    if file_sha256(ARCHIVES[0]) != PIN:
        pytest.skip("local archives are not the pinned ones")


@pytest.fixture(scope="module")
def member() -> Document:
    _pinned()
    return read_members(ARCHIVES[0], [MEMBER])[MEMBER]


@pytest.fixture(scope="module", params=[ARBEIDSMILJOLOVEN, UTLENDINGSLOVEN])
def act_events(request: pytest.FixtureRequest) -> tuple[str, dict[str, Timing], dict[str, Timing]]:
    _pinned()
    evidence = collect(request.param, ARCHIVES)
    own = {e.operation.id: e.timing for e in events_for(evidence)}
    unbound = {e.operation.id: e.timing for e in unattributed_for(evidence)}
    return request.param, own, unbound


def test_the_two_hosts_are_renamed_replacements_ending_in_a_list(member: Document) -> None:
    assert member.sha256 == MEMBER_SHA
    ops = {op.id: op for op in interpret(member).operations}
    host_411, host_412 = ops[OP + "239.0"], ops[OP + "245.0"]
    assert (host_411.kind, host_411.act) == ("renumber", STRAFFELOVEN)
    assert (host_412.kind, host_412.act) == ("renumber", None)  # #909: no act, kept so
    assert host_411.new_provision is not None and host_412.new_provision is not None
    assert host_411.new_provision.key == "§411" and len(host_411.new_provision.ledd) == 2
    assert host_411.new_provision.ledd[-1].text.rstrip().endswith("endringer i andre lover:")
    assert [" ".join(ledd.text.split()) for ledd in host_412.new_provision.ledd] == [
        "Med virkning fra den tid hele straffeloven 20. mai 2005 nr. 28 settes i kraft, gjøres"
        " følgende endringer i annen lovgivning:"
    ]


def test_one_refused_region_runs_from_411_to_the_end_of_part_i(member: Document) -> None:
    reading = interpret(member)
    regions = nesting(member, reading)
    (nest,) = regions.nests
    assert (nest.host_op, nest.host_act, nest.host_section, nest.problem) == (
        OP + "239.0", STRAFFELOVEN, None, WORDING,
    )  # fmt: skip
    part_i = [op.id for op in reading.operations if op.id.startswith(OP)]
    after = part_i[part_i.index(OP + "239.0") + 1 :]
    assert nest.members == tuple(after)
    assert len(nest.members) == 623 and nest.members[0] == OP + "241.0"
    assert OP + "245.0" in nest.members  # § 412's own renaming sits inside § 411's list
    acts = Counter(op.act for op in reading.operations if op.id in set(nest.members))
    assert STRAFFELOVEN not in acts  # no item continues the host act (#909's attribution kept)
    assert (acts[ARBEIDSMILJOLOVEN], acts[UTLENDINGSLOVEN], acts[None]) == (4, 10, 37)
    assert regions.clauses == reading.clauses  # part II's clause stays the outer law's


def test_every_affected_operation_is_unresolved_never_straks(
    act_events: tuple[str, dict[str, Timing], dict[str, Timing]],
) -> None:
    act, own, _ = act_events
    for tag in AFFECTED[act]:
        timing = own[OP + tag]
        assert timing.effective != STRAKS
        assert (timing.status, timing.effective, timing.reason) == ("unresolved", None, WORDING)
        assert timing.known_from == STRAKS  # #858: known from the announcement, unchanged
        assert [p.refid for p in timing.proofs] == ["lov/2009-06-19-74"]


def test_the_affected_set_is_exactly_the_members_operations_in_the_list(
    act_events: tuple[str, dict[str, Timing], dict[str, Timing]], member: Document
) -> None:
    act, own, _ = act_events
    from_member = {i.removeprefix(OP) for i in own if i.startswith("lov/2009-06-19-74#")}
    assert from_member == set(AFFECTED[act])
    ops = {op.id: op for op in interpret(member).operations}
    assert {t: (ops[OP + t].kind, ops[OP + t].address.section) for t in from_member} == AFFECTED[
        act
    ]


def test_the_operations_naming_no_act_are_undated_and_stay_unbound(
    act_events: tuple[str, dict[str, Timing], dict[str, Timing]], member: Document
) -> None:
    _, _, unbound = act_events
    ops = {op.id: op for op in interpret(member).operations}
    listed = {i: t for i, t in unbound.items() if i.startswith(OP)}
    assert len(listed) == 37
    assert all(ops[i].act is None for i in listed)
    assert {(t.status, t.effective, t.reason) for t in listed.values()} == {
        ("unresolved", None, WORDING)
    }
    assert unbound[OP + "245.0"].effective is None  # never straffeloven's, never "straks"


def test_straffelovens_own_operations_in_the_member_keep_the_outer_laws_date() -> None:
    """No host date reaches a top-level operation: the renamings and every straffeloven
    instruction of the member stay as replay-8 dated them, 2009-06-19 ("straks")."""
    _pinned()
    events = [
        e
        for e in events_for(collect(STRAFFELOVEN, ARCHIVES))
        if e.operation.id.startswith("lov/2009-06-19-74#")
    ]
    assert {e.operation.id for e in events} >= {OP + "239.0"}
    assert {(e.timing.status, e.timing.effective) for e in events} == {("dated", STRAKS)}
    assert not any(e.operation.id in _region_members() for e in events)


def _region_members() -> set[str]:
    member = read_members(ARCHIVES[0], [MEMBER])[MEMBER]
    (nest,) = nesting(member, interpret(member)).nests
    return set(nest.members)
