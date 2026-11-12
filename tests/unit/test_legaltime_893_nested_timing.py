"""#893: an amendment nested inside another act's amending provision is dated by its host.

The verified shape, quoted from pinned members (``lovtidend-avd1-2001-2025``, sha256
``d55f8a38…``): ``lov/2013-04-05-12`` part I replaces vergemålsloven § 102 ("§ 102 skal lyde:")
with a provision whose only ledd is "Fra den tid loven trer i kraft gjøres følgende endringer i
andre lover:", and the numbered items that follow it in the same part ("50. I lov 20. mai 2005
nr. 28 om straff skal § 261 første ledd lyde:") are that provision's list. The member's own
"Loven trer i kraft straks." dates the replacement of § 102, never the items: they take effect
"fra den tid loven [vergemålsloven] trer i kraft". ``lov/2020-12-18-149`` has the same shape for
arveloven § 181. ADR-0024 R4: a nested operation is never dated from the nesting instrument's own
commencement clause; it is dated only when the host provision's commencement is proven, and is
otherwise ``unresolved``. ADR-0020 D6: no precedence rule between instruments is invented.

The markup is synthetic; the sentences that carry the rule are the archive's.
"""

from datetime import date, datetime
from pathlib import Path

import pytest

from lovspor.legaltime.archive import Evidence, collect
from lovspor.legaltime.commencement import Timing
from lovspor.legaltime.document import Document, parse_document
from lovspor.legaltime.instructions import interpret
from lovspor.legaltime.nesting import _place, _wording, nesting
from lovspor.legaltime.replay import replay
from lovspor.legaltime.run import commencement_for, events_for, unattributed_for
from tests.unit.test_legaltime_archive import make_archive

HOST = "lov/2000-01-02-1"  # the act whose "Endringer i andre lover" section is replaced
TARGET = "lov/2001-02-02-2"  # an act a nested item amends
OTHER = "lov/2002-03-03-3"  # a second act a nested item amends
OUTER = "lov/2010-01-05-5"  # the amending law that replaces the host section
HOST_LIST = "Fra den tid loven trer i kraft gjøres følgende endringer i andre lover:"  # S27
NESTED = "nested amendment: "


def _header(refid: str, in_force: str, published: str, title: str, changes: tuple[str, ...]) -> str:
    items = "".join(f"<li>{c}</li>" for c in changes)
    return (
        f'<header><dl><dd class="refid">{refid}</dd><dd class="dateInForce">{in_force}</dd>'
        f'<dd class="dateOfPublication">{published}</dd><dd class="title">{title}</dd>'
        f'<dd class="changesToDocuments"><ul>{items}</ul></dd></dl></header>'
    )


def _xml(header: str, main: str) -> bytes:
    return f"<html><body>{header}<main>{main}</main></body></html>".encode()


_BYTES: dict[tuple[str, str], bytes] = {}  # (member, sha256) → the bytes it was parsed from


def _doc(name: str, header: str, main: str) -> Document:
    body = _xml(header, main)
    document = parse_document(body, name)
    _BYTES[(name, document.sha256)] = body
    return document


def _bytes_of(document: Document) -> bytes:
    return _BYTES[(document.member, document.sha256)]


def _p(text: str) -> str:
    return f'<article class="defaultP">{text}</article>'


def _ledd(text: str) -> str:
    return f'<article class="legalP">{text}</article>'


def _future(key: str, title: str, *ledd: str) -> str:
    label = key.replace("§", "§ ")
    return (
        f'<article class="futureLegalArticle" data-name="{key}">'
        f'<span class="futureLegalArticleHeader">{label}. '
        f'<span class="legalArticleTitle">{title}</span></span>'
        f"{''.join(_ledd(t) for t in ledd)}</article>"
    )


def _part(label: str, *children: str) -> str:
    return f'<section class="section"><h2>{label}</h2>{"".join(children)}</section>'


def _act(
    refid: str, member: str, *sections: tuple[str, str], in_force: str = "Kongen bestemmer."
) -> Document:
    body = "".join(
        f'<article class="legalArticle" data-name="{key}"><span class="legalArticleHeader">'
        f"{key.replace('§', '§ ')}.</span>{_ledd(text)}</article>"
        for key, text in sections
    )
    return _doc(member, _header(refid, in_force, "2000-01-01 12:00", "Lov om verge", ()), body)


HOST_ACT = _act(
    HOST, "lti/2000/nl-20000102-001.xml", ("§1", "Loven gjelder vert."), ("§9", "Gammel.")
)
TARGET_ACT = _act(
    TARGET, "lti/2001/nl-20010202-002.xml", ("§3", "Gammel tekst."), ("§5", "Femte."),
    in_force="2001-03-01",
)  # fmt: skip


def _decision(
    name: str, refid: str, in_force: str, published: str, title: str, body: str
) -> Document:
    return _doc(name, _header(refid, in_force, published, title, (HOST,)), _p(body))


FULL = _decision(
    "lti/2010/sf-20100301-0001.xml", "forskrift/2010-03-01-1", "2010-07-01", "2010-03-01 12:00",
    "Ikraftsetting av lov 2. januar 2000 nr. 1 om verge", "Loven trer i kraft 1. juli 2010.",
)  # fmt: skip


def _outer(
    region: tuple[str, ...],
    *,
    host_ledd: tuple[str, ...] = (HOST_LIST,),
    in_force: str = "2010-01-05",
    tail: tuple[str, ...] = (),
    closing: tuple[str, ...] = (_p("Loven trer i kraft straks."),),
) -> Document:
    """OUTER: part I replaces HOST § 9 by a host list; region items; part II direct; part III."""
    main = (
        _part(
            "I",
            _p("I lov 2. januar 2000 nr. 1 om verge gjøres følgende endringer:"),
            _p("§ 9 skal lyde:"),
            _future("§9", "Endringer i andre lover", *host_ledd),
            *region,
        )
        + _part(
            "II",
            _p("I lov 2. februar 2001 nr. 2 om mål gjøres følgende endringer:"),
            _p("§ 5 oppheves."),
            *tail,
        )
        + (_part("III", *closing) if closing else "")
    )
    header = _header(
        OUTER, in_force, "2010-01-05 14:00", "Lov om endringer i vergeloven", (HOST, TARGET, OTHER)
    )
    return _doc("lti/2010/nl-20100105-005.xml", header, main)


ITEM_1 = (
    _p("1. I lov 2. februar 2001 nr. 2 om mål gjøres følgende endringer:"),
    _p("§ 3 skal lyde:"),
    _future("§3", "Ny", "Ny tekst."),
)
ITEM_2 = (
    _p("2. I lov 3. mars 2002 nr. 3 om annet skal § 4 lyde:"),
    _future("§4", "Fjerde", "Endret."),
)
REGION = (*ITEM_1, *ITEM_2)


def _evidence(
    amender: Document,
    *,
    act: Document = TARGET_ACT,
    hosts: tuple[Document, ...] = (HOST_ACT,),
    host_decisions: tuple[Document, ...] = (FULL,),
) -> Evidence:
    return Evidence(
        act=act.header.refid, archives=(), baseline=act, amenders=(amender,), decisions=(),
        hosts=hosts, host_decisions=host_decisions,
    )  # fmt: skip


def _timings(evidence: Evidence) -> dict[tuple[str | None, str | None], Timing]:
    every = [*events_for(evidence), *unattributed_for(evidence)]
    return {(e.operation.address.section, e.operation.act): e.timing for e in every}


def _nested(evidence: Evidence) -> Timing:
    return _timings(evidence)[("§3", TARGET)]


# --------------------------------------------------------------------------- the defect


def test_nested_item_never_takes_the_outer_laws_own_date() -> None:
    """Replay-6 dated the item from OUTER's "straks" (2010-01-05). That date must not survive."""
    timing = _nested(_evidence(_outer(REGION)))
    assert timing.effective != date(2010, 1, 5)


def test_nested_item_is_dated_by_the_host_provisions_proven_commencement() -> None:
    timing = _nested(_evidence(_outer(REGION)))
    assert (timing.status, timing.effective) == ("dated", date(2010, 7, 1))
    refids = [p.refid for p in timing.proofs]
    assert OUTER in refids  # the replacement of the host section is part of the proof
    assert "forskrift/2010-03-01-1" in refids  # and the decision that dates the host
    # Known only once both instruments are published: the later publication wins.
    assert timing.known_from == date(2010, 3, 1)
    assert timing.retroactive is False


def test_the_direct_amendment_after_the_region_keeps_the_outer_laws_date() -> None:
    """Part II is OUTER's own instruction to TARGET: its own clause dates it, as before."""
    timing = _timings(_evidence(_outer(REGION)))[("§5", TARGET)]
    assert (timing.status, timing.effective) == ("dated", date(2010, 1, 5))


def test_the_host_replacement_itself_keeps_the_outer_laws_date() -> None:
    evidence = _evidence(_outer(REGION), act=HOST_ACT, hosts=(HOST_ACT,))
    timing = _timings(evidence)[("§9", HOST)]
    assert (timing.status, timing.effective) == ("dated", date(2010, 1, 5))


def test_the_region_holds_exactly_the_host_provisions_list() -> None:
    outer = _outer(REGION)
    reading = interpret(outer)
    by_section = {(op.address.section, op.act): op.id for op in reading.operations}
    (nest,) = nesting(outer, reading).nests
    assert nest.host_op == by_section[("§9", HOST)]
    assert (nest.host_act, nest.host_section, nest.problem) == (HOST, "§9", None)
    assert nest.members == (by_section[("§3", TARGET)], by_section[("§4", OTHER)])


# ------------------------------------------------------------------- fail-closed edges


def test_without_a_host_commencement_decision_the_item_is_unresolved() -> None:
    """HOST says "Kongen bestemmer." and no decision dates it: nothing is proven (R4)."""
    timing = _nested(_evidence(_outer(REGION), host_decisions=()))
    assert timing.status == "unresolved"
    assert timing.reason.startswith(NESTED)
    assert timing.effective is None


def test_an_unresolved_item_is_known_from_the_outer_laws_publication() -> None:
    """ADR-0020 D9 / #858: an unproven nested item counts from the day it was announced."""
    timing = _nested(_evidence(_outer(REGION), host_decisions=()))
    assert timing.known_from == date(2010, 1, 5)


def test_an_unknown_target_in_an_unproven_region_is_no_problem_before_it_was_announced() -> None:
    region = (
        *REGION,
        _p("3. I lov 4. april 2003 om ukjent skal § 2 lyde:"),
        _future("§2", "A", "B."),
    )
    evidence = _evidence(_outer(region), host_decisions=())
    events = [*events_for(evidence), *unattributed_for(evidence)]
    commencement = commencement_for(evidence, events_for(evidence))
    before = replay(evidence.baseline, commencement, events, date(2009, 12, 31))
    after = replay(evidence.baseline, commencement, events, date(2010, 1, 5))
    assert not any("unattributed" in p for p in before.act_level_problems)
    assert any("unattributed" in p for p in after.act_level_problems)


def test_without_the_host_acts_enacting_member_the_item_is_unresolved() -> None:
    timing = _nested(_evidence(_outer(REGION), hosts=()))
    assert (timing.status, timing.effective) == ("unresolved", None)
    assert timing.reason.startswith(NESTED)


def test_a_host_section_absent_from_the_host_acts_enacted_text_is_unresolved() -> None:
    bare = _act(HOST, "lti/2000/nl-20000102-001.xml", ("§1", "Loven gjelder vert."))
    timing = _nested(_evidence(_outer(REGION), hosts=(bare,)))
    assert (timing.status, timing.effective) == ("unresolved", None)


PARTIAL_HOST_SECTION = _decision(
    "lti/2010/sf-20100301-0002.xml", "forskrift/2010-03-01-2", "2010-07-01", "2010-03-01 12:00",
    "Delvis ikraftsetting av lov 2. januar 2000 nr. 1 om verge",
    "Loven § 9 trer i kraft 1. juli 2010.",
)  # fmt: skip
PARTIAL_OTHER_SECTION = _decision(
    "lti/2010/sf-20100301-0003.xml", "forskrift/2010-03-01-3", "2010-07-01", "2010-03-01 12:00",
    "Delvis ikraftsetting av lov 2. januar 2000 nr. 1 om verge",
    "Loven § 1 trer i kraft 1. juli 2010.",
)  # fmt: skip
CONDITIONAL = _decision(
    "lti/2010/sf-20100301-0004.xml", "forskrift/2010-03-01-4",
    "Når personvernforordningen trer i kraft", "2010-03-01 12:00",
    "Ikraftsetting av lov 2. januar 2000 nr. 1 om verge",
    "Loven trer i kraft når personvernforordningen trer i kraft.",
)  # fmt: skip


def test_a_partial_decision_dating_the_host_section_itself_proves_it() -> None:
    timing = _nested(_evidence(_outer(REGION), host_decisions=(PARTIAL_HOST_SECTION,)))
    assert (timing.status, timing.effective) == ("dated", date(2010, 7, 1))


def test_a_partial_decision_dating_another_host_section_proves_nothing() -> None:
    timing = _nested(_evidence(_outer(REGION), host_decisions=(PARTIAL_OTHER_SECTION,)))
    assert (timing.status, timing.effective) == ("unresolved", None)


def test_a_conditional_host_decision_proves_nothing() -> None:
    timing = _nested(_evidence(_outer(REGION), host_decisions=(CONDITIONAL,)))
    assert (timing.status, timing.effective) == ("unresolved", None)


def test_a_host_replaced_after_it_commenced_is_unresolved() -> None:
    """The host list speaks of a commencement already past: its date is not this item's date."""
    late = _outer(REGION, in_force="2011-01-01")
    timing = _nested(_evidence(late))
    assert (timing.status, timing.effective) == ("unresolved", None)


def test_an_undated_host_replacement_leaves_the_item_unresolved() -> None:
    timing = _nested(_evidence(_outer(REGION, in_force="Kongen bestemmer.")))
    assert (timing.status, timing.effective) == ("unresolved", None)
    assert timing.reason.startswith(NESTED)


@pytest.mark.parametrize(
    "ledd",
    [
        ("Med virkning fra den tid loven trer i kraft gjøres følgende endringer i andre lover:",),
        ("Fra 1. juli 2010 gjøres følgende endringer i andre lover:",),
        ("Loven gjelder vert.", HOST_LIST),
    ],
)
def test_a_host_list_outside_the_verified_wording_is_unresolved(ledd: tuple[str, ...]) -> None:
    timing = _nested(_evidence(_outer(REGION, host_ledd=ledd)))
    assert (timing.status, timing.effective) == ("unresolved", None)
    assert timing.reason.startswith(NESTED)


def test_an_unnumbered_intro_inside_the_region_leaves_the_boundary_unproven() -> None:
    """After a numbered list, an unnumbered intro may be the outer law's own: never guessed."""
    region = (
        *ITEM_1,
        _p("I lov 3. mars 2002 nr. 3 om annet skal § 4 lyde:"),
        _future("§4", "F", "E."),
    )
    outer = _outer(region)
    (nest,) = nesting(outer, interpret(outer)).nests
    assert nest.problem is not None and nest.problem.startswith(NESTED)
    timing = _nested(_evidence(_outer(region)))
    assert (timing.status, timing.effective) == ("unresolved", None)


def test_a_commencement_sentence_inside_the_region_is_not_the_outer_laws_clause() -> None:
    """No precedence is invented (ADR-0020 D6): the region is unresolved, OUTER's clause intact."""
    region = (*REGION, _p("Endringen i nr. 2 trer i kraft 1. mars 2011."))
    timings = _timings(_evidence(_outer(region)))
    assert timings[("§3", TARGET)].status == "unresolved"
    assert timings[("§3", TARGET)].reason.startswith(NESTED)
    assert (timings[("§5", TARGET)].status, timings[("§5", TARGET)].effective) == (
        "dated",
        date(2010, 1, 5),
    )


def test_an_unattributed_operation_inside_the_region_is_dated_by_the_host_too() -> None:
    """D7: an unknown-target operation counts from its own date; that date is the host's."""
    region = (
        *REGION,
        _p("3. I lov 4. april 2003 om ukjent skal § 2 lyde:"),
        _future("§2", "A", "B."),
    )
    unknown = [
        e
        for e in unattributed_for(_evidence(_outer(region)))
        if e.operation.address.section == "§2"
    ]
    assert len(unknown) == 1
    assert (unknown[0].timing.status, unknown[0].timing.effective) == ("dated", date(2010, 7, 1))


# ------------------------------------------------------------------- no shared timing


SECOND_HOST = "lov/2000-06-06-6"
SECOND_HOST_ACT = _act(SECOND_HOST, "lti/2000/nl-20000606-006.xml", ("§7", "Gammel."))
SECOND_DECISION = _doc(
    "lti/2012/sf-20120101-0001.xml",
    _header("forskrift/2012-01-01-1", "2012-09-01", "2012-01-01 12:00",
            "Ikraftsetting av lov 6. juni 2000 nr. 6 om annen verge", (SECOND_HOST,)),
    _p("Loven trer i kraft 1. september 2012."),
)  # fmt: skip


def _second_outer() -> Document:
    main = _part(
        "I",
        _p("I lov 6. juni 2000 nr. 6 om annen verge gjøres følgende endringer:"),
        _p("§ 7 skal lyde:"),
        _future("§7", "Endringer i andre lover", HOST_LIST),
        _p("1. I lov 2. februar 2001 nr. 2 om mål skal § 5 lyde:"),
        _future("§5", "Femte", "Endret femte."),
    ) + _part("II", _p("Loven trer i kraft straks."))
    header = _header("lov/2012-01-01-9", "2012-01-01", "2012-01-01 14:00", "Lov om endringer",
                     (SECOND_HOST, TARGET))  # fmt: skip
    return _doc("lti/2012/nl-20120101-009.xml", header, main)


def test_two_hosts_in_two_members_never_share_a_date() -> None:
    evidence = Evidence(
        act=TARGET, archives=(), baseline=TARGET_ACT, amenders=(_outer(REGION), _second_outer()),
        decisions=(), hosts=(HOST_ACT, SECOND_HOST_ACT), host_decisions=(FULL, SECOND_DECISION),
    )  # fmt: skip
    dated = {(e.law, e.operation.address.section): e.timing.effective for e in events_for(evidence)}
    assert dated[(OUTER, "§3")] == date(2010, 7, 1)  # first host's item
    assert dated[("lov/2012-01-01-9", "§5")] == date(2012, 9, 1)  # second host's item
    assert dated[(OUTER, "§5")] == date(2010, 1, 5)  # OUTER's own part II, its own date


def test_two_target_acts_in_one_region_share_only_the_host_date() -> None:
    timings = _timings(_evidence(_outer(REGION)))
    other = _timings(
        _evidence(
            _outer(REGION),
            act=_act(
                OTHER, "lti/2002/nl-20020303-003.xml", ("§4", "Gammel."), in_force="2002-04-01"
            ),
        )
    )
    assert timings[("§3", TARGET)].effective == other[("§4", OTHER)].effective == date(2010, 7, 1)


# ------------------------------------------------------------------- history changes


def test_falsely_dating_the_item_would_change_the_historical_text() -> None:
    """TARGET is in force from 2001-03-01. Between OUTER's date and the host's, § 3 is old text."""
    evidence = _evidence(_outer(REGION))
    events = [*events_for(evidence), *unattributed_for(evidence)]
    commencement = commencement_for(evidence, events_for(evidence))

    def section_3(at: date) -> tuple[str, ...]:
        state = replay(evidence.baseline, commencement, events, at)
        provision = next(p for p in state.provisions if p.key == "§3")
        return tuple(provision.applied)

    nested_id = next(e.operation.id for e in events if e.operation.address.section == "§3")
    assert nested_id not in section_3(date(2010, 3, 1))  # replay-6 had applied it here
    assert nested_id in section_3(date(2010, 7, 1))


# ------------------------------------------------------------- review round 1 (Codex)


def test_a_region_that_does_not_open_with_a_numbered_item_is_unproven() -> None:
    """After the host, an unnumbered instruction may be the outer law's own: never guessed."""
    later = (_p("2. I lov 2. februar 2001 nr. 2 om mål skal § 6 lyde:"),)  # a numbered item after
    outer = _outer((_p("§ 3 skal lyde:"), _future("§3", "Ny", "Ny tekst."), *later))
    (nest,) = nesting(outer, interpret(outer)).nests
    assert nest.problem == "nested amendment: region does not open with a numbered item"


def test_a_host_with_nothing_after_it_in_its_part_is_an_unproven_region() -> None:
    outer = _outer(())
    (nest,) = nesting(outer, interpret(outer)).nests
    assert (nest.members, nest.problem) == (
        (),
        "nested amendment: region does not open with a numbered item",
    )


def test_an_item_naming_the_host_act_itself_leaves_the_region_unproven() -> None:
    region = (
        *REGION,
        _p("3. I lov 2. januar 2000 nr. 1 om verge skal § 1 lyde:"),
        _future("§1", "E", "F."),
    )
    timing = _nested(_evidence(_outer(region)))
    assert (timing.status, timing.effective) == ("unresolved", None)


def test_a_payload_sentence_equal_to_the_outer_clause_neither_moves_nor_refuses_it() -> None:
    """An item's replacement may read "Loven trer i kraft straks."; it is text, not a clause."""
    item = (_p("3. I lov 3. mars 2002 nr. 3 om annet skal § 6 lyde:"),
            _future("§6", "Ikraft", "Loven trer i kraft straks."))  # fmt: skip
    timings = _timings(_evidence(_outer((*REGION, *item))))
    assert (timings[("§3", TARGET)].status, timings[("§3", TARGET)].effective) == (
        "dated",
        date(2010, 7, 1),
    )
    assert timings[("§5", TARGET)].effective == date(2010, 1, 5)


def test_a_clause_read_both_inside_and_outside_stays_the_outer_laws_and_refuses_the_region() -> (
    None
):
    region = (*REGION, _p("Loven trer i kraft straks."))
    timings = _timings(_evidence(_outer(region)))
    assert timings[("§3", TARGET)].status == "unresolved"
    assert (timings[("§5", TARGET)].status, timings[("§5", TARGET)].effective) == (
        "dated",
        date(2010, 1, 5),
    )


@pytest.mark.parametrize(
    ("op_id", "place"),
    [
        ("lov/2013-04-05-12#1:I:253.0", (1, 253)),
        ("lov/2021-06-18-97#18:Kapittel 18. Avsluttende bestemmelser:101.0", (18, 101)),
        ("lov/2008-03-07-4#0:I:18.0-40", (0, 18)),  # a structure operation's provision suffix
        ("lov/2010-01-05-5#0:I:7.1.0", (0, 7)),  # a substitution item's offset
        ("lov/2010-01-05-5#2:A: B:9.0", (2, 9)),  # a part label holding a colon
        ("lov/2010-01-05-5#2:A#B:9.0", (2, 9)),  # a part label holding a hash
        ("lov/2010-01-05-5#3:A#B#C:4.1", (3, 4)),  # and two
    ],
)
def test_every_operation_id_form_is_placed_on_its_part_and_node(
    op_id: str, place: tuple[int, int]
) -> None:
    assert _place(op_id) == place


# ------------------------------------------------- mutation evidence (targeted run, #893)

CHAPTERED_HOST = _doc(
    "lti/2000/nl-20000102-001.xml",
    _header(HOST, "Kongen bestemmer.", "2000-01-01 12:00", "Lov om verge", ()),
    '<section class="section" data-name="kap2"><h3>Kapittel 2.</h3>'
    '<article class="legalArticle" data-name="§9"><span class="legalArticleHeader">§ 9.</span>'
    + _ledd("A.")
    + _ledd("B.")
    + "</article></section>",
)


def _partial(body: str, published: str = "2010-03-01 12:00") -> Document:
    return _decision(
        "lti/2010/sf-20100301-0009.xml", "forskrift/2010-03-01-9", "2010-07-01", published,
        "Delvis ikraftsetting av lov 2. januar 2000 nr. 1 om verge", body,
    )  # fmt: skip


def _chaptered(body: str) -> Timing:
    return _nested(
        _evidence(_outer(REGION), hosts=(CHAPTERED_HOST,), host_decisions=(_partial(body),))
    )


def test_a_decision_dating_the_host_sections_chapter_proves_the_section() -> None:
    timing = _chaptered("Loven kapittel 2 trer i kraft 1. juli 2010.")
    assert (timing.status, timing.effective) == ("dated", date(2010, 7, 1))


@pytest.mark.parametrize(
    "split",
    [
        "Loven § 9 annet ledd trer i kraft 1. august 2010.",  # a ledd dated apart
        "Loven § 9 første ledd annet punktum trer i kraft 1. august 2010.",  # below ledd
        "Lovvedtaket del I kapittel 2 trer i kraft 1. juli 2010.",  # a part-qualified unit
    ],
)
def test_a_host_section_dated_whole_but_also_split_is_refused_with_its_proofs(split: str) -> None:
    timing = _chaptered(f"Loven § 9 trer i kraft 1. juli 2010. {split}")
    assert (timing.status, timing.effective) == ("unresolved", None)
    assert (
        timing.reason
        == "nested amendment: host section's commencement is split below the section or by part"
    )
    assert OUTER in {p.refid for p in timing.proofs}
    assert timing.known_from == date(2010, 1, 5)


def test_an_unproven_host_names_the_host_state_and_keeps_both_proofs() -> None:
    timing = _nested(_evidence(_outer(REGION), host_decisions=(CONDITIONAL,)))
    assert timing.reason.startswith("nested amendment: host provision's commencement not proven (")
    assert {OUTER, "forskrift/2010-03-01-4"} <= {p.refid for p in timing.proofs}
    pending = _nested(_evidence(_outer(REGION), host_decisions=()))
    assert pending.reason == "nested amendment: host provision's commencement not proven (pending)"


def test_a_replacement_on_the_host_commencement_day_is_in_time() -> None:
    timing = _nested(_evidence(_outer(REGION, in_force="2010-07-01")))
    assert (timing.status, timing.effective) == ("dated", date(2010, 7, 1))


def test_a_late_replacement_keeps_both_proofs() -> None:
    timing = _nested(_evidence(_outer(REGION, in_force="2011-01-01")))
    assert timing.reason == "nested amendment: host provision replaced after it commenced"
    assert {OUTER, "forskrift/2010-03-01-1"} <= {p.refid for p in timing.proofs}


@pytest.mark.parametrize(
    ("published", "retroactive"),
    [("2010-07-01 09:00", False), ("2010-08-01 09:00", True)],
)
def test_retroactive_only_when_the_host_date_was_published_after_it(
    published: str, retroactive: bool
) -> None:
    decision = _decision(
        "lti/2010/sf-20100701-0001.xml", "forskrift/2010-07-01-1", "2010-07-01", published,
        "Ikraftsetting av lov 2. januar 2000 nr. 1 om verge", "Loven trer i kraft 1. juli 2010.",
    )  # fmt: skip
    timing = _nested(_evidence(_outer(REGION), host_decisions=(decision,)))
    assert (timing.effective, timing.retroactive) == (date(2010, 7, 1), retroactive)
    assert timing.known_from == date.fromisoformat(published[:10])


def test_no_known_publication_on_either_side_leaves_known_from_empty() -> None:
    def unpublished(document: Document) -> Document:
        header = document.header.model_copy(update={"published_at": None})
        return document.model_copy(update={"header": header})

    evidence = _evidence(unpublished(_outer(REGION)), host_decisions=(unpublished(FULL),))
    timing = _nested(evidence)
    assert (timing.status, timing.effective, timing.known_from) == ("dated", date(2010, 7, 1), None)
    assert timing.retroactive is False


def test_a_later_host_after_a_refused_inner_host_still_opens_its_own_region() -> None:
    """A host inside an earlier host's list is skipped; hosts after it are still read."""
    host_list = ("Endringer i andre lover", HOST_LIST)
    main = _part(
        "I", _p("I lov 2. januar 2000 nr. 1 om verge gjøres følgende endringer:"),
        _p("§ 9 skal lyde:"), _future("§9", *host_list), *ITEM_1,
        _p("§ 7 skal lyde:"), _future("§7", *host_list),
    ) + _part(
        "II", _p("I lov 2. januar 2000 nr. 1 om verge gjøres følgende endringer:"),
        _p("§ 8 skal lyde:"), _future("§8", *host_list), *ITEM_2,
    )  # fmt: skip
    header = _header(OUTER, "2010-01-05", "2010-01-05 14:00", "Lov", (HOST, TARGET, OTHER))
    outer = _doc("lti/2010/nl-20100105-005.xml", header, main)
    nests = nesting(outer, interpret(outer)).nests
    assert [(n.host_section, n.problem) for n in nests] == [
        ("§9", "nested amendment: region holds a second host provision"),
        ("§8", None),
    ]


def test_a_shared_clause_in_a_later_part_stays_the_outer_laws() -> None:
    """The outside copy sits in part II after other nodes; only the inside copy is the host's."""
    clause = "Loven trer i kraft 1. februar 2010."
    outer = _outer((*REGION, _p(clause)), tail=(_p(clause),), closing=())
    timings = _timings(_evidence(outer))
    assert timings[("§3", TARGET)].status == "unresolved"
    assert (timings[("§5", TARGET)].status, timings[("§5", TARGET)].effective) == (
        "dated",
        date(2010, 2, 1),
    )


def test_wording_of_a_missing_provision_is_refused() -> None:
    assert _wording(None) == "nested amendment: host provision's wording outside the grammar"


def test_collect_gathers_each_host_and_its_decisions_apart_from_the_acts_own(
    tmp_path: Path,
) -> None:
    outer = _outer(REGION)
    unrelated = _doc(
        "lti/2010/sf-20100301-0005.xml",
        _header("forskrift/2010-03-01-5", "2010-07-01", "2010-03-01 12:00",
                "Ikraftsetting av lov 3. mars 2002 nr. 3 om annet", (OTHER,)),
        _p("Loven trer i kraft 1. juli 2010."),
    )  # fmt: skip
    members = {d.member: d for d in (TARGET_ACT, outer, HOST_ACT, FULL, unrelated)}
    archive = make_archive(
        tmp_path / "lovtidend-avd1-2001-2025.tar.bz2",
        layout={},
        extra={name: _bytes_of(doc) for name, doc in members.items()},
    )
    evidence = collect(TARGET, [archive], cache_dir=tmp_path)
    assert [d.header.refid for d in evidence.amenders] == [OUTER]
    assert [d.header.refid for d in evidence.hosts] == [HOST]
    assert [d.header.refid for d in evidence.host_decisions] == ["forskrift/2010-03-01-1"]
    assert "forskrift/2010-03-01-1" not in {d.header.refid for d in evidence.decisions}
    timing = _nested(evidence)
    assert (timing.status, timing.effective) == ("dated", date(2010, 7, 1))


def test_collect_reads_no_host_evidence_without_a_host(tmp_path: Path) -> None:
    plain = _doc(
        "lti/2010/nl-20100105-005.xml",
        _header(OUTER, "2010-01-05", "2010-01-05 14:00", "Lov", (TARGET,)),
        _part("I", _p("I lov 2. februar 2001 nr. 2 om mål gjøres følgende endringer:"),
              _p("§ 5 oppheves."), _p("Loven trer i kraft straks.")),
    )  # fmt: skip
    members = {d.member: d for d in (TARGET_ACT, plain, HOST_ACT, FULL)}
    archive = make_archive(
        tmp_path / "lovtidend-avd1-2001-2025.tar.bz2",
        layout={},
        extra={name: _bytes_of(doc) for name, doc in members.items()},
    )
    evidence = collect(TARGET, [archive], cache_dir=tmp_path)
    assert (evidence.hosts, evidence.host_decisions) == ((), ())


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_host_dates_are_unresolved_in_either_document_order(reverse: bool) -> None:
    """R4/D6: conflicting instruments cannot prove a host commencement by precedence."""
    conflicting = _decision(
        "lti/2010/sf-20100401-0002.xml", "forskrift/2010-04-01-2", "2010-08-01",
        "2010-04-01 12:00", "Ikraftsetting av lov 2. januar 2000 nr. 1 om verge",
        "Loven trer i kraft 1. august 2010.",
    )  # fmt: skip
    decisions = (conflicting, FULL) if reverse else (FULL, conflicting)
    timings = _timings(_evidence(_outer(REGION), host_decisions=decisions))
    nested = timings[("§3", TARGET)]
    assert (nested.status, nested.effective) == ("unresolved", None)
    assert nested.reason.startswith(NESTED)
    assert {OUTER, FULL.header.refid, conflicting.header.refid} <= {
        proof.refid for proof in nested.proofs
    }
    assert timings[("§5", TARGET)].effective == date(2010, 1, 5)


def test_known_from_uses_the_outer_publication_when_it_is_later_than_the_hosts() -> None:
    """The two-instrument proof is known only after both instruments were published."""
    outer = _outer(REGION)
    outer = outer.model_copy(
        update={"header": outer.header.model_copy(update={"published_at": datetime(2010, 8, 1)})}
    )
    timing = _nested(_evidence(outer))
    assert (timing.status, timing.effective) == ("dated", date(2010, 7, 1))
    assert timing.known_from == date(2010, 8, 1)
    assert timing.retroactive is True


def test_nested_date_stays_the_hosts_while_replay_waits_for_the_target_act() -> None:
    """R1/R4: target commencement gates application without redating the nested item."""
    target = _act(
        TARGET, TARGET_ACT.member, ("§3", "Gammel tekst."), ("§5", "Femte."),
        in_force="2011-01-01",
    )  # fmt: skip
    evidence = _evidence(_outer(REGION), act=target)
    events = events_for(evidence)
    nested = next(event for event in events if event.operation.address.section == "§3")
    assert nested.timing.effective == date(2010, 7, 1)
    commencement = commencement_for(evidence, events)
    before = replay(target, commencement, events, date(2010, 12, 31))
    assert before.status == "not_yet_in_force"
    assert nested.operation.id in before.withheld
    assert nested.operation.id not in before.applied
    assert before.provisions == ()  # replay's existing pre-commencement contract
    after = replay(target, commencement, events, date(2011, 1, 1))
    assert after.status == "replayed"
    assert nested.operation.id in after.applied
    assert next(p for p in after.provisions if p.key == "§3").ledd[0].text == "Ny tekst."
