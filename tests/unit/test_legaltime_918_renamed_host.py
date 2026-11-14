"""#918: the list of a host provision that is renamed and replaced at once is a nested region.

``lov/2009-06-19-74`` part I (pinned ``lti/2009/nl-20090619-074.xml``) renames and replaces
straffeloven's §§ 401-402 in one sentence each: "Nåværende § 401 blir § 411 som innleder tredje
del og som skal lyde:" and "Nåværende § 402 blir § 412 og skal lyde:". Each replacement ends in a
list of amendments to other acts ("… gjøres følgende endringer i andre lover:", "… gjøres
følgende endringer i annen lovgivning:"), and the numbered items follow as flat siblings. The
member's own "Loven trer i kraft straks." dated those items until #918; ``nesting`` read only
``replace_section`` hosts. ADR-0024 R4: a nested operation is never dated from the nesting
instrument's own clause, and is ``unresolved`` unless the host provision's date is proven. The
renamed host's unit in its own act is not followed across the renaming (no provision lineage is
integrated), so such a region is refused and its items stay ``unresolved``.

The markup is synthetic, built with the #893 helpers; the sentence shapes are the archive's.
"""

from datetime import date
from pathlib import Path

import pytest

from lovspor.legaltime.archive import Evidence, collect
from lovspor.legaltime.commencement import Timing
from lovspor.legaltime.document import Document
from lovspor.legaltime.instructions import interpret
from lovspor.legaltime.nesting import RENAMED, WORDING, nesting
from lovspor.legaltime.run import events_for, unattributed_for
from tests.unit.test_legaltime_893_nested_timing import (
    FULL,
    HOST,
    HOST_ACT,
    HOST_LIST,
    OTHER,
    OUTER,
    REGION,
    TARGET,
    TARGET_ACT,
    _act,
    _bytes_of,
    _doc,
    _evidence,
    _future,
    _header,
    _outer,
    _p,
    _part,
)
from tests.unit.test_legaltime_archive import make_archive

STRAKS = date(2010, 1, 5)  # OUTER's own "Loven trer i kraft straks."
OTHER_ACT = _act(OTHER, "lti/2002/nl-20020303-003.xml", ("§4", "Fjerde."), in_force="2002-04-01")
RENAME = "Nåværende § 8 blir § 9 og skal lyde:"  # the shape of "Nåværende § 402 blir § 412 …"


def _renaming(
    region: tuple[str, ...],
    *,
    host: tuple[str, ...] | None = (HOST_LIST,),
    rename: str = RENAME,
    name: str = "lti/2010/nl-20100105-005.xml",
    refid: str = OUTER,
) -> Document:
    """OUTER, whose part I renames HOST § 8 to § 9 and replaces it; region; part II; part III."""
    payload = (_future("§9", "Endringer i andre lover", *host),) if host is not None else ()
    main = (
        _part(
            "I",
            _p("I lov 2. januar 2000 nr. 1 om verge gjøres følgende endringer:"),
            _p(rename),
            *payload,
            *region,
        )
        + _part(
            "II",
            _p("I lov 2. februar 2001 nr. 2 om mål gjøres følgende endringer:"),
            _p("§ 5 oppheves."),
        )
        + _part("III", _p("Loven trer i kraft straks."))
    )
    header = _header(
        refid, "2010-01-05", "2010-01-05 14:00", "Lov om endringer i vergeloven",
        (HOST, TARGET, OTHER),
    )  # fmt: skip
    return _doc(name, header, main)


def _all(evidence: Evidence) -> dict[tuple[str | None, str | None], Timing]:
    every = [*events_for(evidence), *unattributed_for(evidence)]
    return {(e.operation.address.section, e.operation.act): e.timing for e in every}


def _target(document: Document, **kwargs: object) -> dict[tuple[str | None, str | None], Timing]:
    return _all(_evidence(document, **kwargs))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- the defect


def test_an_item_of_a_renamed_hosts_list_never_takes_the_outer_laws_straks() -> None:
    """Replay-8 dated the item 2010-01-05, OUTER's own "straks"; the host's date is not proven."""
    timing = _target(_renaming(REGION))[("§3", TARGET)]
    assert timing.effective != STRAKS
    assert (timing.status, timing.effective, timing.reason) == ("unresolved", None, RENAMED)


def test_every_item_of_the_list_is_unresolved_with_the_same_provenance() -> None:
    outer = _renaming(REGION)
    for act in (TARGET_ACT, OTHER_ACT):
        timings = _all(_evidence(outer, act=act))
        key = ("§3", TARGET) if act is TARGET_ACT else ("§4", OTHER)
        assert (timings[key].status, timings[key].reason) == ("unresolved", RENAMED)
        assert timings[key].known_from == STRAKS  # #858: known from the announcement
        assert OUTER in [p.refid for p in timings[key].proofs]  # the renaming's own proof


def test_the_region_is_the_renamed_hosts_list_and_names_its_refusal() -> None:
    outer = _renaming(REGION)
    reading = interpret(outer)
    (nest,) = nesting(outer, reading).nests
    host = next(op for op in reading.operations if op.id == nest.host_op)
    assert (host.kind, host.act, host.new_provision and host.new_provision.key) == (
        "renumber", HOST, "§9",
    )  # fmt: skip
    assert (nest.host_act, nest.problem) == (HOST, RENAMED)
    assert len(nest.members) == 2


def test_a_renamed_host_outside_the_verified_wording_is_refused_for_its_wording_first() -> None:
    """§ 412's own wording: "Med virkning fra den tid hele straffeloven … i annen lovgivning:"."""
    wording = "Med virkning fra den tid hele loven settes i kraft, gjøres følgende endringer i " \
        "annen lovgivning:"  # fmt: skip
    timing = _target(_renaming(REGION, host=(wording,)))[("§3", TARGET)]
    assert (timing.status, timing.effective, timing.reason) == ("unresolved", None, WORDING)


def test_even_a_dated_host_commencement_does_not_date_a_renamed_hosts_item() -> None:
    """FULL dates HOST 2010-07-01; the renamed § 9 is not HOST's enacted § 8 by any proof here."""
    timing = _target(_renaming(REGION), host_decisions=(FULL,))[("§3", TARGET)]
    assert (timing.status, timing.effective, timing.reason) == ("unresolved", None, RENAMED)
    assert "forskrift/2010-03-01-1" not in {p.refid for p in timing.proofs}


def test_without_any_host_commencement_the_item_is_unresolved_not_straks() -> None:
    for hosts, decisions in (((HOST_ACT,), ()), ((), ())):
        timing = _target(_renaming(REGION), hosts=hosts, host_decisions=decisions)[("§3", TARGET)]
        assert (timing.status, timing.effective) == ("unresolved", None)


def test_an_unbound_operation_in_the_list_stays_unbound_and_undated() -> None:
    """#909: an item naming no act keeps act None; its timing is the region's, never straks."""
    unbound = _p("3. I lov 4. april 2003 om ukjent skal § 2 lyde:")
    region = (*REGION, unbound, _future("§2", "A", "B."))
    unknown = [
        e for e in unattributed_for(_evidence(_renaming(region)))
        if e.operation.address.section == "§2"
    ]  # fmt: skip
    assert len(unknown) == 1
    assert unknown[0].operation.act is None
    assert (unknown[0].timing.status, unknown[0].timing.effective) == ("unresolved", None)


# ----------------------------------------------------------------- what stays as it was


def test_the_renaming_itself_keeps_the_outer_laws_date() -> None:
    timings = _all(_evidence(_renaming(REGION), act=HOST_ACT))
    renaming = [t for (section, act), t in timings.items() if act == HOST]
    assert [(t.status, t.effective) for t in renaming] == [("dated", STRAKS)]


def test_an_ordinary_straks_amendment_outside_the_list_keeps_its_date() -> None:
    timing = _target(_renaming(REGION))[("§5", TARGET)]
    assert (timing.status, timing.effective) == ("dated", STRAKS)


def test_a_rename_without_a_payload_opens_no_region() -> None:
    outer = _renaming(REGION, host=None, rename="Nåværende § 8 blir § 9.")
    assert nesting(outer, interpret(outer)).nests == ()


def test_a_rename_whose_payload_announces_no_list_opens_no_region() -> None:
    outer = _renaming(REGION, host=("Ny tekst uten liste.",))
    assert nesting(outer, interpret(outer)).nests == ()


def test_a_replace_section_host_is_still_dated_by_its_proven_commencement() -> None:
    """#893's verified shape is unchanged: § 9 replaced, not renamed, dated 2010-07-01."""
    timing = _target(_outer(REGION))[("§3", TARGET)]
    assert (timing.status, timing.effective) == ("dated", date(2010, 7, 1))


def test_a_dated_replace_section_host_and_a_renamed_host_never_share_a_date() -> None:
    renamed = _renaming(
        (_p("1. I lov 2. februar 2001 nr. 2 om mål skal § 5 lyde:"), _future("§5", "F", "Endret.")),
        name="lti/2011/nl-20110101-007.xml", refid="lov/2011-01-01-7",
    )  # fmt: skip
    evidence = Evidence(
        act=TARGET, archives=(), baseline=TARGET_ACT, amenders=(_outer(REGION), renamed),
        decisions=(), hosts=(HOST_ACT,), host_decisions=(FULL,),
    )  # fmt: skip
    by_id = {e.operation.id: e.timing for e in events_for(evidence)}
    assert by_id[f"{OUTER}#0:I:4.0"].effective == date(2010, 7, 1)
    item = by_id["lov/2011-01-01-7#0:I:3.0"]
    assert (item.status, item.effective, item.reason) == ("unresolved", None, RENAMED)
    assert by_id["lov/2011-01-01-7#1:II:1.0"].status == "dated"  # its ordinary "straks" amendment


def test_no_operation_is_dropped_or_changes_its_act_or_kind() -> None:
    outer = _renaming(REGION)
    operations = interpret(outer).operations
    evidence = _evidence(outer)
    listed = [*events_for(evidence), *unattributed_for(evidence)]
    targeted = {op.id for op in operations if op.act in {TARGET, None}}
    assert {e.operation.id for e in listed} == targeted
    assert {(e.operation.id, e.operation.kind, e.operation.act) for e in listed} == {
        (op.id, op.kind, op.act) for op in operations if op.id in targeted
    }


@pytest.mark.parametrize("order", [0, 1])
def test_timings_do_not_depend_on_the_amenders_order_or_on_a_rerun(order: int) -> None:
    first, second = _outer(REGION), _renaming(
        (_p("1. I lov 2. februar 2001 nr. 2 om mål skal § 5 lyde:"), _future("§5", "F", "Endret.")),
        name="lti/2011/nl-20110101-007.xml", refid="lov/2011-01-01-7",
    )  # fmt: skip
    amenders = (first, second) if order == 0 else (second, first)
    evidence = Evidence(
        act=TARGET, archives=(), baseline=TARGET_ACT, amenders=amenders, decisions=(),
        hosts=(HOST_ACT,), host_decisions=(FULL,),
    )  # fmt: skip
    runs = [{e.operation.id: e.timing for e in events_for(evidence)} for _ in range(2)]
    assert runs[0] == runs[1]
    assert runs[0][f"{OUTER}#0:I:4.0"].effective == date(2010, 7, 1)
    assert runs[0]["lov/2011-01-01-7#0:I:3.0"].reason == RENAMED


def test_collect_reads_no_host_evidence_for_a_refused_region(tmp_path: Path) -> None:
    """A refused region never consults its host, so the host is not read; the item is unchanged."""
    outer = _renaming(REGION)
    members = {d.member: d for d in (TARGET_ACT, outer, HOST_ACT, FULL)}
    archive = make_archive(
        tmp_path / "lovtidend-avd1-2001-2025.tar.bz2",
        layout={},
        extra={name: _bytes_of(doc) for name, doc in members.items()},
    )
    evidence = collect(TARGET, [archive], cache_dir=tmp_path)
    assert (evidence.hosts, evidence.host_decisions) == ((), ())
    timing = _all(evidence)[("§3", TARGET)]
    assert (timing.status, timing.reason) == ("unresolved", RENAMED)
