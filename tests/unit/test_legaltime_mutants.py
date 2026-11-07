"""Exact shapes and messages of the legal-time replay, pinned against the mutation gate.

Each assertion here fixes a value the first mutation run could move without a
test noticing: a report key, a refusal message, a boundary, a default. They
are real contracts (the evidence files are read by their keys; an operator
reads the messages), not decoration.
"""

import io
import json
import tarfile
import tempfile
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path

import pytest
import typer
from lxml import etree
from pytest_httpx import HTTPXMock

from lovspor.legaltime import commencement, instructions, replay, run, verify
from lovspor.legaltime.archive import (
    INDEX_VERSION,
    _cached_index,
    _ordered,
    _record,
    _title_document,
    collect,
    dump_json,
    index_archive,
    load_oracle,
)
from lovspor.legaltime.commands import ReplayRequest, _summary, fetch_impl, replay_impl
from lovspor.legaltime.document import (
    Document,
    Ledd,
    Node,
    _ledd_of,
    _node,
    _part,
    inner_xml,
    parse_document,
    parse_header,
)
from lovspor.legaltime.errors import LegalTimeDocumentError, LegalTimeInputError
from lovspor.legaltime.instructions import Address, Operation, interpret
from lovspor.legaltime.replay import Event, ProvisionState, ReplayState, _Working
from lovspor.legaltime.text import _ends_with_abbreviation, section_key_of, section_sort_key
from lovspor.parsing.xml_normalizer import safe_parser
from lovspor.sources.lovdata import LovdataClient
from tests.unit.test_legaltime_archive import LAYOUT, make_archive

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legaltime"
ACT = "lov/2019-06-14-99"


def load(name: str) -> Document:
    return parse_document((FIXTURES / name).read_bytes(), f"lti/x/{name}")


def _law(refid: str, date_in_force: str, clause: str, title: str = "Lov om endringer") -> Document:
    body = (
        f'<html><body><header><dl><dd class="refid">{refid}</dd>'
        f'<dd class="dateInForce">{date_in_force}</dd>'
        f'<dd class="dateOfPublication">2020-12-18 12:10</dd><dd class="title">{title}</dd>'
        '</dl></header><main><section class="section"><h2>I</h2><article class="defaultP">'
        "I lov 14. juni 2019 nr. 99 om prøving gjøres følgende endringer:</article>"
        '<article class="defaultP">§ 1 skal lyde:</article>'
        '<article class="futureLegalArticle" data-name="§1"><article class="legalP">A.</article>'
        '</article></section><section class="section"><h2>II</h2>'
        f'<article class="legalP">{clause}</article></section></main></body></html>'
    )
    return parse_document(body.encode(), "lti/x/law.xml")


def _op(
    kind: str, address: Address, ledd: tuple[str, ...] = (), instruction: str = "i", **extra: object
) -> Operation:
    return Operation(
        id="x#I:0.0", kind=kind, act=ACT, address=address, instruction=instruction, part="I",  # type: ignore[arg-type]
        ledd=tuple(Ledd(xml=t, text=t) for t in ledd), **extra,  # type: ignore[arg-type]
    )  # fmt: skip


def _event(op: Operation, timing: commencement.Timing | None = None) -> Event:
    when = timing or commencement.Timing(status="dated", effective=date(2022, 1, 1))
    return Event(sequence=0, law="x", member="m", operation=op, timing=when, published_at=None)


ENACTED = commencement.Timing(status="dated", effective=date(2021, 1, 1))


def _run(events: list[Event], at: date = date(2023, 1, 1)) -> ReplayState:
    return replay.replay(load("enacting.xml"), ENACTED, events, at)


def _working() -> _Working:
    return _Working(load("enacting.xml"))


# ---------------------------------------------------------------- run.py records


def test_operation_record_has_exactly_these_keys_and_values() -> None:
    document = load("amending.xml")
    op = next(o for o in interpret(document).operations if o.kind == "insert_section")
    assert run._operation_record(op) == {
        "id": "lov/2020-12-18-999#1:I:5.0",
        "kind": "insert_section",
        "part": "I",
        "instruction": "Ny § 2 a skal lyde:",
        "address": {"section": "§2a"},
        "destination": None,
        "change_part": [],
        "payload": {"provision": "§2a", "ledd": 0},
    }
    bare = _op("renumber", Address())
    assert run._operation_record(bare)["payload"] == {"provision": None, "ledd": 0}
    assert run._operation_record(bare)["address"] == {}


def test_member_record_has_exactly_these_keys_and_counts_unattributed_only_for_amenders() -> None:
    document = load("amending.xml")
    assert run._member_record(document, "amending") == {
        "role": "amending",
        "member": "lti/x/amending.xml",
        "sha256": document.sha256,
        "refid": "lov/2020-12-18-999",
        "title": "Lov om endringer i prøveloven (prøving)",
        "date_in_force": "2020-12-18, Kongen bestemmer",
        "published_at": "2020-12-18T12:10:00",
        "unattributed_operations": 0,
    }
    assert run._member_record(document, "decision")["unattributed_operations"] == 0
    orphan = parse_document(
        b'<html><body><header><dl><dd class="refid">lov/2026-06-19-48</dd></dl></header><main>'
        b'<section class="section"><h2>V</h2><article class="defaultP">I lov 14. juni 2019 nr. 21 '
        b'om arv skal \xc2\xa7 1 lyde:</article><article class="legalP">T.</article>'
        b'<article class="defaultP">\xc2\xa7 2 oppheves.</article></section></main></body></html>',
        "x",
    )
    assert run._unattributed(orphan) == 0
    unsupported = parse_document(
        b'<html><body><header><dl><dd class="refid">lov/2026-06-19-48</dd></dl></header><main>'
        b'<section class="section"><h2>V</h2><article class="defaultP">Lovens tittel skal lyde:'
        b"</article></section></main></body></html>",
        "x",
    )
    assert run._unattributed(unsupported) == 0
    assert run._member_record(unsupported, "amending")["published_at"] is None


def test_members_roles_in_order() -> None:
    archive = load("enacting.xml")
    evidence = collect_fixture()
    roles = [m["role"] for m in run._members(evidence)]
    assert roles == ["enacting", "amending", "decision", "decision"]
    assert run._members(evidence)[0]["member"] != archive.member


def collect_fixture(tmp: Path | None = None) -> object:
    base = Path(tempfile.mkdtemp()) if tmp is None else tmp
    archive = make_archive(base / "lovtidend-avd1-2001-2025.tar.bz2")
    return collect(ACT, [archive])


def test_event_state_and_archive_records_are_exact() -> None:
    evidence = collect_fixture()
    events = run.events_for(evidence)
    first = run._event_record(events[0])
    assert set(first) == {"law", "member", "published_at", "operation", "timing"}
    assert first["law"] == "lov/2020-12-18-999"
    assert first["published_at"] == "2020-12-18T12:10:00"
    assert first["timing"]["status"] == "dated"
    assert (
        first["timing"]["proofs"][0]["quote_sha256"]
        == sha256(b"Loven trer i kraft straks.").hexdigest()
    )
    state = run.state_at(evidence, events, date(2020, 12, 18))
    record = run._state_record(state)
    assert set(record) == {
        "at", "status", "applied", "future", "pending", "unresolved", "act_level_problems", "withheld",  # noqa: E501
        "provisions",
    }  # fmt: skip
    assert record["status"] == "not_yet_in_force"
    assert len(record["withheld"]) == 4
    later = run._state_record(run.state_at(evidence, events, date(2023, 1, 1)))
    assert later["provisions"][0] == {
        "key": "§1", "status": "replayed", "repealed": False, "ledd": 3,
        "applied": ["lov/2020-12-18-999#1:I:1.0", "lov/2020-12-18-999#1:I:3.0"], "problems": [],
    }  # fmt: skip
    assert later["unresolved"] == []
    archives = run._archive_records(evidence)
    assert set(archives[0]) == {"archive", "sha256", "members", "horizon", "skipped_members"}
    assert archives[0]["members"] == 4
    assert archives[0]["horizon"] == "2021-05-31T16:10:00"


def test_build_report_pins_oracle_sorted_inputs_and_json_mode(tmp_path: Path) -> None:
    first = make_archive(tmp_path / "lovtidend-avd1-2001-2025.tar.bz2")
    second = make_archive(
        tmp_path / "a-lovtidend-avd1-2026.tar.bz2",
        {"lti/2026/sf-20260101-0001.xml": "resolution-partial.xml"},
    )
    oracle_archive = make_archive(
        tmp_path / "gjeldende-lover.tar.bz2", {"nl/x.xml": "consolidated.xml"}
    )
    evidence = collect(ACT, [first, second])
    oracle = load_oracle(oracle_archive, ACT)
    report = run.build_report(evidence, [date(2023, 1, 1)], oracle, [first, second])
    assert [p["filename"] for p in report["inputs"]] == [second.name, first.name]
    assert report["oracle"] == {"member": "nl/x.xml", "sha256": oracle.sha256}
    assert report["replay_version"] == "legaltime-replay-2"
    assert report["verification"][0]["at"] == "2023-01-01"
    assert isinstance(report["verification"][0]["counts"], dict)
    json.dumps(report)
    assert run._stamp(None) is None
    assert run._stamp(datetime(2020, 1, 2, 3, 4)) == "2020-01-02T03:04:00"
    assert [e.sequence for e in run.events_for(evidence)] == list(
        range(len(run.events_for(evidence)))
    )


# ------------------------------------------------------------ commencement


def test_exception_timing_branches_and_messages() -> None:
    law = _law("lov/2021-01-01-1", "2021-01-01", "Loven trer i kraft straks.")
    pending = commencement._exception_timing(
        law, "Ny § 2 trer likevel i kraft fra den tid Kongen bestemmer."
    )
    assert pending.status == "pending"
    assert pending.known_from == date(2020, 12, 18)
    straks = commencement._exception_timing(law, "Ny § 2 trer likevel i kraft straks.")
    assert straks.effective == date(2021, 1, 1)
    assert straks.proofs[0].refid == "lov/2021-01-01-1"
    assert (
        straks.proofs[0].quote_sha256
        == sha256("Ny § 2 trer likevel i kraft straks.".encode()).hexdigest()
    )
    dated = commencement._exception_timing(law, "Ny § 2 trer likevel i kraft 1. mars 2021.")
    assert dated.effective == date(2021, 3, 1)
    unresolved = commencement._exception_timing(law, "Ny § 2 trer likevel i kraft seinare.")
    assert unresolved.status == "unresolved"
    assert unresolved.reason == "exception outside the grammar"
    assert unresolved.proofs[0].member == "lti/x/law.xml"
    assert (
        unresolved.proofs[0].quote_sha256
        == sha256("Ny § 2 trer likevel i kraft seinare.".encode()).hexdigest()
    )
    assert unresolved.effective is None and unresolved.known_from is None


def test_dated_or_pending_and_header_messages() -> None:
    law = _law("lov/2021-01-01-1", "Kongen bestemmer", "Loven trer i kraft straks.")
    out = commencement._dated_or_pending(law, "Loven trer i kraft straks.")
    assert out.status == "unresolved"
    assert out.reason == "commencement clause outside the grammar"
    assert out.proofs[0].quote_sha256 == sha256(b"Loven trer i kraft straks.").hexdigest()
    header = commencement.header_timing(_law("lov/2021-01-01-1", "neste år", "x"))
    assert header.reason == "dateInForce outside the grammar"
    assert header.proofs[0].quote_sha256 == sha256("dateInForce: neste år".encode()).hexdigest()
    pending = commencement.header_timing(law)
    assert pending.status == "pending" and pending.known_from == date(2020, 12, 18)
    assert pending.proofs[0].quote_sha256 == sha256(b"dateInForce: Kongen bestemmer").hexdigest()
    dated = commencement._dated_or_pending(
        _law("lov/2021-01-01-1", "2021-01-01", "x"), "Loven trer i kraft 2. mars 2021."
    )
    assert dated.effective == date(2021, 3, 2) and not dated.retroactive
    retro = commencement._dated(law, date(2020, 12, 18), "q")
    assert retro.retroactive is False
    assert commencement._dated(law, date(2020, 12, 17), "q").retroactive is True
    assert commencement._header_date(_law("lov/1", "x", "y")) is None
    assert (
        commencement._known_from(
            parse_document(
                b'<html><body><header><dl><dd class="refid">lov/1</dd></dl></header><main/></body></html>',  # noqa: E501
                "x",
            )
        )
        is None
    )


def test_baseline_timing_partial_and_undated_decisions() -> None:
    act = load("enacting.xml")
    partial = load("resolution-partial.xml").model_copy(
        update={
            "header": load("resolution-partial.xml").header.model_copy(
                update={"title": "Delt ikraftsetjing av lov 14. juni 2019 nr. 99 om prøving"}
            )
        }
    )
    out = commencement.baseline_timing(act, (partial,))
    assert out.status == "unresolved"
    assert out.reason == "partial commencement of the act itself"
    assert out.proofs[0].refid == "forskrift/2021-05-28-9998"
    assert (
        out.proofs[0].quote_sha256
        == sha256("Delt ikraftsetjing av lov 14. juni 2019 nr. 99 om prøving".encode()).hexdigest()
    )
    undated = load("resolution-full.xml").model_copy(
        update={
            "header": load("resolution-full.xml").header.model_copy(
                update={"date_in_force": "Kongen bestemmer"}
            )
        }
    )
    assert commencement.baseline_timing(act, (undated,)).status == "pending"
    assert commencement.baseline_timing(
        act, (undated, load("resolution-full.xml"))
    ).effective == date(2021, 1, 1)
    assert commencement.baseline_timing(_law("lov/1", "2021-02-02", "x"), ()).effective == date(
        2021, 2, 2
    )


def test_partial_scope_and_full_decision_edges() -> None:
    law = _law(
        "lov/2020-12-18-999", "Kongen bestemmer", "Loven gjelder fra den tid Kongen bestemmer."
    )
    result = interpret(law)
    ops = result.operations
    timings = commencement.resolve_timings(
        law, result.clauses, ops, (load("resolution-partial.xml"),)
    )
    assert {t.status for t in timings.values()} == {"pending"}  # part I, no § 2a: nothing covered
    proof_title = "Delt ikrafttredelse av lov 18. desember 2020 nr. 999 om endringer i prøveloven (prøving): del I"  # noqa: E501
    insert = parse_document((FIXTURES / "amending.xml").read_bytes(), "a")
    reading = interpret(insert)
    timings = commencement.resolve_timings(
        insert, reading.clauses, reading.operations, (load("resolution-partial.xml"),)
    )
    dated = next(t for t in timings.values() if t.effective == date(2022, 1, 1))
    assert dated.proofs[0].quote_sha256 == sha256(proof_title.encode()).hexdigest()
    undated_full = load("resolution-full.xml").model_copy(
        update={
            "header": load("resolution-full.xml").header.model_copy(
                update={
                    "date_in_force": "snart",
                    "title": "Ikraftsetting av lov 18. desember 2020 nr. 999 om endringer",
                }
            )
        }
    )
    timings = commencement.resolve_timings(law, result.clauses, ops, (undated_full,))
    assert {t.status for t in timings.values()} == {"pending"}


def test_informational_and_reservation_rules() -> None:
    assert commencement._informational("Departementet kan gi overgangsregler.")
    assert commencement._informational("Reglene gjelder når dødsfallet finner sted.")
    assert not commencement._informational("Loven får virkning fra 1. juli 2022.")
    assert not commencement._informational("Loven trer i kraft straks.")
    law = _law("lov/2021-01-01-1", "2021-01-01", "x")
    reserved = commencement._default_timing(
        law, "Loven trer i kraft 1. januar 2021 med unntak av § 3."
    )
    assert reserved is not None and reserved.reason == "default clause carries a reservation"
    assert commencement._default_timing(law, "Departementet kan gi forskrift.") is None


# ----------------------------------------------------------------- document


def test_node_kinds_and_classes() -> None:
    title = etree.fromstring('<span class="futuretitle">Kap. 1</span>')
    node = _node(title)
    assert node == Node(kind="title", css_class="futuretitle", text="Kap. 1")
    other = _node(etree.fromstring("<h5>Heading</h5>"))
    assert other == Node(kind="other", css_class="h5", text="Heading")
    classed = _node(etree.fromstring('<p class="gazettenote">N</p>'))
    assert classed.css_class == "gazettenote" and classed.kind == "other"
    paragraph = _node(etree.fromstring('<article class="defaultP">P</article>'))
    assert paragraph == Node(kind="paragraph", css_class="defaultP", text="P")


def test_part_uses_h2_or_h3_and_ledd_continuation_drops_the_tail() -> None:
    section = etree.fromstring(
        '<section class="section"><h3>Del A</h3><article class="legalP">L</article></section>'
    )
    part = _part(section)
    assert part.label == "Del A" and [n.kind for n in part.nodes] == ["ledd"]
    assert (
        _part(
            etree.fromstring(
                '<section class="section"><article class="legalP">L</article></section>'
            )
        ).label
        == ""
    )
    article = etree.fromstring(
        '<article class="legalArticle"><article class="legalP">A<ul><li>x</li></ul></article>'
        '<article class="defaultP">B</article>tail-text<article class="legalP">C</article></article>'  # noqa: E501
    )
    ledd = _ledd_of(article)
    assert [item.text for item in ledd] == ["A x B", "C"]
    assert ledd[0].xml.endswith("B</article>")
    assert "tail-text" not in ledd[0].xml


def test_parse_errors_and_inner_xml_and_blank_text() -> None:
    with pytest.raises(LegalTimeDocumentError, match="document header carries no single refid"):
        parse_header(etree.fromstring("<html><body><header/></body></html>"))
    with pytest.raises(LegalTimeDocumentError, match="no document body in m"):
        parse_document(
            b'<html><body><header><dl><dd class="refid">lov/1</dd></dl></header></body></html>', "m"
        )
    element = etree.fromstring(
        "<a><b>x</b> <i>y</i></a>", parser=safe_parser(remove_blank_text=False)
    )
    assert inner_xml(element) == "<b>x</b> <i>y</i>"
    document = parse_document(
        b'<html><body><header><dl><dd class="refid">lov/1</dd></dl></header><main><section class="section">'  # noqa: E501
        b'<article class="legalArticle" data-name="\xc2\xa71"><article class="legalP"><strong>a</strong> <em>b</em>'  # noqa: E501
        b"</article></article></section></main></body></html>",
        "m",
    )
    assert document.provisions[0].ledd[0].text == "a b"
    assert document.provisions[0].ledd[0].xml == "<strong>a</strong> <em>b</em>"
    assert [p.key for p in document.provisions] == ["§1"]


def test_provision_label_heading_and_notes_are_read_from_their_spans() -> None:
    provision = {p.key: p for p in load("consolidated.xml").provisions}["§2a"]
    assert provision.label == "§ 2 a"
    assert provision.heading == "Innskutt"
    assert provision.notes == (
        "Tilføyd ved lov 18 des 2020 nr. 999 (ikr. 1 jan 2022 iflg. res. 28 mai 2021 nr. 9998).",
    )


# -------------------------------------------------------------- instructions


def test_addressed_act_rules() -> None:
    assert (
        instructions._addressed_act(("lov/2019-06-14-21/§178/ledd/3", "lov/2019-06-14-21/kap15"))
        == "lov/2019-06-14-21"
    )
    assert instructions._addressed_act(("lov/1984-06-08-58/del1",)) == "lov/1984-06-08-58"
    assert instructions._addressed_act(("lov/1/§1", "lov/2/§1")) is None
    assert instructions._addressed_act(("forskrift/2020-01-01-1/§1",)) is None
    assert instructions._addressed_act(()) is None


def test_pairs_shift_and_init_defaults() -> None:
    header = instructions._SUBSTITUTE.match
    paired = header(
        "I følgende bestemmelser skal ordene «a» og «b» endres til henholdsvis «c» og «d»:"
    )
    single = header("I følgende bestemmelser skal ordet «a» endres til «b»:")
    assert paired is not None and single is not None
    assert instructions._pairs(paired) == (("a", "c"), ("b", "d"))
    assert instructions._pairs(single) == (("a", "b"),)
    assert instructions._shift("Nåværende tredje ledd blir nytt fjerde ledd.") == (None, 3, 4)
    assert instructions._shift("Nåværende tredje ledd blir nytt ledd.") is None
    reader = instructions._Reader(load("enacting.xml"))
    assert reader.act is None and reader.substitution is None and reader.pending_item_act is None


def test_operation_title_destination_and_inline_payload_class() -> None:
    body = (
        '<html><body><header><dl><dd class="refid">lov/1</dd></dl></header><main><section class="section">'  # noqa: E501
        '<h2>I</h2><article class="defaultP">I lov 14. juni 2019 nr. 99 om x gjøres følgende endringer:'  # noqa: E501
        '</article><article class="defaultP">§ 1 overskriften skal lyde:</article>'
        '<span class="futuretitle">Ny tittel</span><article class="legalP">ikke tittel</article>'
        "</section></main></body></html>"
    )
    op = interpret(parse_document(body.encode(), "x")).operations[0]
    assert op.title == "Ny tittel" and op.destination is None and len(op.ledd) == 1
    text, payload = instructions._inline_payload("§ 3 bokstav d lyde: Stortinget", ())
    assert (
        text == "§ 3 bokstav d lyde:"
        and payload[0].css_class == "inline"
        and payload[0].kind == "ledd"
    )
    assert instructions._inline_payload("§ 3 skal lyde:", ()) == ("§ 3 skal lyde:", ())


def test_reader_loose_and_substitution_item_branches() -> None:
    body = (
        '<html><body><header><dl><dd class="refid">lov/2021-05-07-34</dd></dl></header><main>'
        '<section class="section"><h2>I</h2>'
        '<article class="defaultP">I følgjande lovføresegner vert «a» endra til «b»:</article>'
        '<article class="legalP">uten paragraf</article>'
        '<article class="legalP">lov 14. juni 2019 nr. 99 om x</article>'
        '<article class="legalP">§ 1</article>'
        '<article class="legalP">§ 2 uten lov</article>'
        "</section></main></body></html>"
    )
    result = interpret(parse_document(body.encode(), "x"))
    assert [(op.act, op.locations) for op in result.operations] == [("lov/2019-06-14-99", "§ 1")]
    assert result.clauses == ()
    consecutive = (
        '<html><body><header><dl><dd class="refid">lov/1</dd></dl></header><main>'
        '<section class="section"><h2>I</h2><article class="legalP">Loven trer i kraft straks.</article>'  # noqa: E501
        '<article class="legalP">Departementet kan gi overgangsregler.</article>'
        '<article class="legalP">Kongen kan gi forskrift.</article></section></main></body></html>'
    )
    assert len(interpret(parse_document(consecutive.encode(), "x")).clauses) == 3


def test_renumber_insertion_requires_adjacent_kinds() -> None:
    body = (
        '<html><body><header><dl><dd class="refid">lov/1</dd></dl></header><main><section class="section">'  # noqa: E501
        '<h2>I</h2><article class="defaultP">I lov 14. juni 2019 nr. 99 om x gjøres følgende endringer:'  # noqa: E501
        '</article><article class="defaultP">§ 1 fjerde ledd skal lyde:</article><article class="legalP">N.'  # noqa: E501
        '</article><article class="defaultP">§ 2 oppheves.</article>'
        '<article class="defaultP">Nåværende fjerde ledd blir nytt femte ledd.</article>'
        '<article class="defaultP">§ 3 fjerde ledd skal lyde:</article><article class="legalP">M.</article>'  # noqa: E501
        '<article class="defaultP">Nåværende femte ledd blir nytt sjette ledd.</article>'
        "</section></main></body></html>"
    )
    ops = interpret(parse_document(body.encode(), "x")).operations
    assert [op.kind for op in ops] == [
        "replace_ledd",
        "repeal_section",
        "renumber",
        "replace_ledd",
        "renumber",
    ]


# -------------------------------------------------------------------- replay


def test_apply_boundaries_exact() -> None:
    w = _working()
    assert w.apply_insert_ledd(_op("insert_ledd", Address(section="§1", ledd=3), ("X.",))) is None
    assert (
        w.apply_insert_ledd(_op("insert_ledd", Address(section="§1", ledd=5), ("X.",)))
        == "insertion point does not exist"
    )
    assert (
        w.apply_insert_ledd(_op("insert_ledd", Address(section="§1", ledd=1, ledd_end=2), ("X.",)))
        == "1 ledd supplied for 2 addressed"
    )
    assert (
        w.apply_replace_ledd(_op("replace_ledd", Address(section="§1"), ("X.",)))
        == "target ledd range does not exist"
    )
    assert (
        w.apply_insert_punktum(
            _op("insert_punktum", Address(section="§1", ledd=1, punktum=3), ("Ny.",))
        )
        is None
    )
    assert (
        w.apply_insert_punktum(
            _op("insert_punktum", Address(section="§1", ledd=1, punktum=5), ("Ny.",))
        )
        == "punktum 5 insertion: 3 existing, 1 supplied"
    )
    assert (
        w.apply_replace_punktum(
            _op("replace_punktum", Address(section="§1", ledd=1, punktum=1), ("Første.",))
        )
        is None
    )
    assert (
        w.apply_repeal_punktum(_op("repeal_punktum", Address(section="§1", ledd=1, punktum=1)))
        is None
    )
    assert (
        w.apply_repeal_punktum(_op("repeal_punktum", Address(section="§1", ledd=1, punktum=9)))
        == "punktum 9-9 does not exist among 2"
    )
    assert (
        w.apply_replace_section(_op("replace_section", Address(section="§9")))
        == "target provision missing or no replacement text"
    )
    assert (
        w.apply_repeal_section(_op("repeal_section", Address(section="§9")))
        == "target provision missing"
    )
    assert (
        w.apply_insert_section(_op("insert_section", Address(section="§1")))
        == "new provision without key or text, or key already present"
    )
    assert (
        w.apply_unsupported(_op("unsupported", Address(), instruction="x" * 100))
        == "unsupported instruction: " + "x" * 80
    )
    assert (
        w._ledd_at(_op("repeal_ledd", Address(section="§9", ledd=1)))
        == "target provision missing or repealed"
    )
    assert (
        w.apply_replace_bokstav(_op("replace_bokstav", Address(section="§2", ledd=1), ("t",)))
        == "bokstav target or replacement text missing"
    )
    assert (
        w.apply_move_ledd(
            _op("move_ledd", Address(section="§1", ledd=1, destination_ledd=2), destination="§3")
        )
        is None
    )
    assert (
        w.apply_substitute_term(
            _op("substitute_term", Address(), locations="§ 9", term_old="a", term_new="b")
        )
        == "substitution locations unresolved: ['§9']"
    )
    assert (
        w.apply_substitute_term(
            _op("substitute_term", Address(), locations="", term_old="a", term_new="b")
        )
        == "substitution locations unresolved: none listed"
    )
    assert (
        w.apply_substitute_term(
            _op("substitute_term", Address(), locations="§ 1", term_old=None, term_new="b")
        )
        == "substitution locations unresolved: none listed"
    )


def test_replace_words_capitalisation_and_markup() -> None:
    assert replay._replace_words(
        "fylkesmannen og Fylkesmannen, fylkesmannens", "fylkesmannen", "statsforvalteren"
    ) == ("statsforvalteren og Statsforvalteren, fylkesmannens")
    assert (
        replay._replace_words('<a href="fylkesmannen">fylkesmannen</a>', "fylkesmannen", "x")
        == '<a href="fylkesmannen">x</a>'
    )
    assert replay._ledd_numbers("andre til femte") == [2, 3, 4, 5]
    assert replay._ledd_numbers("andre til femte og sjette") == [2, 5, 6]
    assert replay._sentence_slice(None, None) == (1, 1)
    assert replay._insert_position(["§1", "§2", "§3"], "§2") == 1
    assert replay._insert_position(["§5"], "§1") == 0
    assert (
        replay._baseline_status(commencement.Timing(status="dated"), date(2020, 1, 1))
        == "unresolved_baseline"
    )
    assert (
        replay._classify(
            _event(_op("renumber", Address()), commencement.Timing(status="dated")),
            date(2020, 1, 1),
        )
        == "unresolved"
    )


def test_targets_and_replay_buckets() -> None:
    w = _working()
    sub = _op(
        "substitute_term", Address(), locations="§ 1 annet ledd, § 9", term_old="a", term_new="b"
    )
    assert [p.label for p in replay._targets(sub, w)] == ["§ 1"]
    move = _op("move_ledd", Address(section="§1", ledd=1), destination="§3")
    assert [p.label for p in replay._targets(move, w)] == ["§ 1", "§ 3"]
    state = _run([_event(_op("renumber", Address()))])
    assert state.applied == ("x#I:0.0",) and state.withheld == () and state.unresolved == ()
    early = _event(
        _op("renumber", Address()), commencement.Timing(status="dated", effective=date(2020, 6, 1))
    )
    before = replay.replay(load("enacting.xml"), ENACTED, [early], date(2020, 12, 31))
    assert before.withheld == ("x#I:0.0",) and before.applied == () and before.future == ()
    pending_only = _run([_event(_op("renumber", Address()), commencement.Timing(status="pending"))])
    assert pending_only.pending == ("x#I:0.0",)


# -------------------------------------------------------------------- verify


def test_difference_messages_and_verdict_detail() -> None:
    oracle = {p.key: p for p in load("consolidated.xml").provisions}["§1"]
    base = ProvisionState(key="§1", label="§ 1", heading=oracle.heading, ledd=oracle.ledd)
    assert verify._difference(base.model_copy(update={"heading": "x"}), oracle) == "heading differs"
    assert (
        verify._difference(base.model_copy(update={"ledd": oracle.ledd[:1]}), oracle)
        == "ledd count 1 vs 3"
    )
    changed = (oracle.ledd[0], Ledd(xml="y", text="y"), Ledd(xml="z", text="z"))
    assert (
        verify._difference(base.model_copy(update={"ledd": changed}), oracle) == "ledd 2, 3 differ"
    )
    exact = verify._verdict(base, oracle)
    assert exact.verdict == "exact_raw" and exact.detail == "" and exact.oracle_notes == 1
    differing = verify._verdict(
        base.model_copy(
            update={"ledd": changed, "pending": ("p",), "applied": ("a",), "problems": ()}
        ),
        oracle,
    )
    assert (
        differing.detail == "ledd 2, 3 differ"
        and differing.pending == ("p",)
        and differing.applied == ("a",)
    )
    unresolved = verify._verdict(
        base.model_copy(update={"status": "unresolved", "problems": ("x",)}), oracle
    )
    assert unresolved.verdict == "unresolved" and unresolved.problems == ("x",)
    assert unresolved.detail == "text identical"
    noted = verify._note_checks({"§1": base.model_copy(update={"pending": ("p",)})}, {"§1": oracle})
    assert noted == ((), ())


# ------------------------------------------------------------------- archive


def test_archive_cache_contents_and_title_document(tmp_path: Path) -> None:
    archive = make_archive(tmp_path / "a.tar.bz2")
    index = index_archive(archive, tmp_path / "idx")
    cache = tmp_path / "idx" / f"a.tar.bz2.{INDEX_VERSION}.json"
    assert cache.read_text(encoding="utf-8") == index.model_dump_json(indent=1)
    assert _cached_index(cache, index.sha256) == index
    assert _cached_index(cache, "0" * 64) is None
    assert _cached_index(tmp_path / "missing.json", index.sha256) is None
    record = index.members[0].model_copy(update={"title": "Lov om <a> & b"})
    stand_in = _title_document(record)
    assert stand_in.header.title == "Lov om <a> & b"
    assert _record("x", b"<html><body><main/></body></html>") is None
    assert _record("x", b"not xml") is None
    undated = load("enacting.xml").model_copy(
        update={"header": load("enacting.xml").header.model_copy(update={"published_at": None})}
    )
    assert [d.member for d in _ordered([load("amending.xml"), undated])] == [
        "lti/x/enacting.xml",
        "lti/x/amending.xml",
    ]
    assert dump_json({"æ": "ø"}) == '{\n "æ": "ø"\n}'
    with pytest.raises(LegalTimeInputError, match="unreadable archive"):
        index_archive(tmp_path / "a.tar.bz2" if False else _corrupt(tmp_path), tmp_path / "idx2")


def _corrupt(tmp_path: Path) -> Path:
    path = tmp_path / "corrupt.tar.bz2"
    path.write_bytes(b"BZh9 not a tarball")
    return path


def test_collect_reads_decisions_citing_amenders_too(tmp_path: Path) -> None:
    layout = dict(LAYOUT)
    archive = make_archive(tmp_path / "a.tar.bz2", layout)
    evidence = collect(ACT, [archive])
    assert [d.header.refid for d in evidence.decisions] == [
        "forskrift/2020-02-28-9999",
        "forskrift/2021-05-28-9998",
    ]
    assert evidence.archives[0].skipped == ()
    with pytest.raises(LegalTimeInputError, match="no consolidated member for lov/9 in a.tar.bz2"):
        load_oracle(archive, "lov/9")


# ------------------------------------------------------------------ commands


def test_summary_lines_and_refusal_goes_to_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = {
        "states": [
            {
                "at": "2021-01-01",
                "status": "replayed",
                "applied": [1],
                "future": [],
                "pending": [2, 3],
                "unresolved": [],
            }
        ],
        "verification": [{"at": "2021-01-01", "counts": {"exact_raw": 1}}],
    }
    assert (
        _summary(report)
        == "2021-01-01: replayed, applied 1, future 0, pending 2, unresolved 0\n2021-01-01: {'exact_raw': 1}"  # noqa: E501
    )
    assert _summary({"states": []}) == ""
    archive = make_archive(tmp_path / "a.tar.bz2")
    with pytest.raises(typer.Exit):
        replay_impl(
            ReplayRequest(
                act="lov/1999-01-01-1", archives=(archive,), dates=(date(2021, 1, 1),), oracle=None
            )
        )
    captured = capsys.readouterr()
    assert "legal-time replay refused: no enacting member" in captured.err
    assert captured.out == ""


# ---------------------------------------------------------------------- text


def test_sort_key_and_abbreviation_edges() -> None:
    assert section_sort_key("kap1") == (10**9, 0, "kap1")
    assert section_sort_key("§8-7") == (8, 7, "")
    assert section_sort_key("§8") == (8, 0, "")
    assert section_sort_key("§8") < section_sort_key("§8-1") < section_sort_key("kap1")
    assert _ends_with_abbreviation("se  jf.")
    assert not _ends_with_abbreviation("se nr 21.")
    assert _ends_with_abbreviation("a b jf")


# ------------------------------------------------- second round: remaining survivors


def _tar(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:bz2") as tar:
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return path


def test_fetch_writes_pins_with_the_exact_source_into_a_nested_dest(
    tmp_path: Path, httpx_mock: object
) -> None:
    assert isinstance(httpx_mock, HTTPXMock)
    body = b"bytes"
    httpx_mock.add_response(
        url="https://api.lovdata.no/v1/publicData/list",
        json=[
            {
                "filename": "gjeldende-lover.tar.bz2",
                "description": "x",
                "sizeBytes": 5,
                "lastModified": "2026-11-04T02:32:00Z",
            }
        ],
    )
    httpx_mock.add_response(
        url="https://api.lovdata.no/v1/publicData/get/gjeldende-lover.tar.bz2", content=body
    )
    dest = tmp_path / "deep" / "er"
    with LovdataClient() as client:
        fetch_impl(dest, client)
    pins = json.loads((dest / "pins.json").read_text(encoding="utf-8"))
    assert pins["source"] == "https://api.lovdata.no/v1/publicData/"
    assert (dest / "pins.json").read_text(encoding="utf-8").endswith("}\n")
    with (
        LovdataClient() as client,
        pytest.raises(LegalTimeInputError, match="publicData /list names no pilot archive"),
    ):
        httpx_mock.add_response(url="https://api.lovdata.no/v1/publicData/list", json=[])
        fetch_impl(dest, client)


def test_index_cache_lives_in_a_nested_cache_dir_and_is_trusted_under_its_digest(
    tmp_path: Path,
) -> None:
    archive = make_archive(tmp_path / "a.tar.bz2")
    cache_dir = tmp_path / "idx" / "deep"
    index = index_archive(archive, cache_dir)
    cache = cache_dir / f"a.tar.bz2.{INDEX_VERSION}.json"
    assert cache.exists()
    tampered = index.model_copy(update={"skipped": ("lti/2020/marker.xml",)})
    cache.write_text(tampered.model_dump_json(indent=1), encoding="utf-8")
    assert index_archive(archive, cache_dir).skipped == ("lti/2020/marker.xml",)
    with pytest.raises(LegalTimeInputError, match="archive not found"):
        index_archive(tmp_path / "none.tar.bz2", cache_dir)
    evidence = collect(ACT, [archive], cache_dir / "via-collect")
    assert (cache_dir / "via-collect" / f"a.tar.bz2.{INDEX_VERSION}.json").exists()
    assert evidence.act == ACT


def test_inputs_sort_by_filename_not_by_path(tmp_path: Path) -> None:
    first = make_archive(tmp_path / "z" / "a-lovtidend.tar.bz2") if False else None
    (tmp_path / "z").mkdir()
    (tmp_path / "a").mkdir()
    one = make_archive(tmp_path / "z" / "lovtidend-avd1-2001-2025.tar.bz2")
    two = make_archive(
        tmp_path / "a" / "lovtidend-avd1-2026.tar.bz2", {"lti/2026/sf-1.xml": "resolution-full.xml"}
    )
    evidence = collect(ACT, [two, one])
    report = run.build_report(evidence, [date(2023, 1, 1)], None, [two, one])
    assert [p["filename"] for p in report["inputs"]] == [one.name, two.name]
    assert first is None


def test_member_without_publication_date_indexes_with_none() -> None:
    body = b'<html><body><header><dl><dd class="refid">lov/2020-01-01-1</dd></dl></header><main/></body></html>'  # noqa: E501
    record = _record("m.xml", body)
    assert record is not None and record.published_at is None and record.title == ""


def test_load_oracle_keeps_scanning_past_other_members(tmp_path: Path) -> None:
    archive = _tar(
        tmp_path / "o.tar.bz2",
        {
            "nl/a.xml": (FIXTURES / "amending.xml").read_bytes(),
            "nl/b.xml": (FIXTURES / "consolidated.xml").read_bytes(),
        },
    )
    assert load_oracle(archive, ACT).member == "nl/b.xml"


def test_partial_decision_with_an_undated_sentence_is_unresolved() -> None:
    law = _law(
        "lov/2020-12-18-999", "Kongen bestemmer", "Loven gjelder fra den tid Kongen bestemmer."
    )
    decision = parse_document(
        (
            b'<html><body><header><dl><dd class="refid">forskrift/2021-05-28-1</dd>'
            b'<dd class="dateInForce">2022-01-01</dd><dd class="dateOfPublication">2021-05-31 16:10</dd>'  # noqa: E501
            b'<dd class="title">Delt ikrafttredelse av lov 18. desember 2020 nr. 999 om endringer</dd>'  # noqa: E501
            b'</dl></header><main><article class="legalP">Endringsloven del I trer i kraft seinare.'
            b"</article></main></body></html>"
        ),
        "x",
    )
    reading = interpret(law)
    timings = commencement.resolve_timings(law, reading.clauses, reading.operations, (decision,))
    assert {t.reason for t in timings.values()} == {"partial decision outside the grammar"}


def test_ledd_after_a_list_ledd_stays_a_separate_ledd() -> None:
    article = etree.fromstring(
        '<article class="legalArticle"><article class="legalP">A<ul><li>x</li></ul></article>'
        '<article class="legalP">B</article></article>'
    )
    assert [item.text for item in _ledd_of(article)] == ["A x", "B"]


def test_nested_sections_and_lists_inside_a_change_keep_their_address() -> None:
    body = (
        '<html><body><header><dl><dd class="refid">lov/1</dd></dl></header><main>'
        '<section class="section"><h2>Del I</h2><section class="section"><h3>Kap 1</h3>'
        '<article class="defaultP">inner</article></section><article class="defaultP">outer</article>'  # noqa: E501
        '</section><section class="section"><h2>II</h2><article class="change" data-change-part="lov/1/§5">'  # noqa: E501
        '<ul><li><article class="listArticle"><article class="legalP">item</article></article></li></ul>'  # noqa: E501
        "</article></section></main></body></html>"
    )
    document = parse_document(body.encode(), "x")
    assert [(n.text, n.change_part) for n in document.parts[0].nodes] == [
        ("inner", ()),
        ("outer", ()),
    ]
    assert [(n.text, n.change_part) for n in document.parts[1].nodes] == [("item", ("lov/1/§5",))]


def test_non_adjacent_punktum_pair_has_no_address() -> None:
    kind, groups = instructions.classify("§ 1 første og tredje punktum skal lyde:")
    assert kind == "replace_punktum"
    assert instructions._address(groups) == Address(section="§1")


def test_renumber_shift_only_converts_the_operation_right_before_it() -> None:
    body = (
        '<html><body><header><dl><dd class="refid">lov/1</dd></dl></header><main><section class="section">'  # noqa: E501
        '<h2>I</h2><article class="defaultP">I lov 14. juni 2019 nr. 99 om x gjøres følgende endringer:'  # noqa: E501
        '</article><article class="defaultP">Nåværende annet ledd blir nytt tredje ledd.</article>'
        '<article class="defaultP">§ 1 annet ledd skal lyde:</article><article class="legalP">N.</article>'  # noqa: E501
        "</section></main></body></html>"
    )
    ops = interpret(parse_document(body.encode(), "x")).operations
    assert [op.kind for op in ops] == ["renumber", "replace_ledd"]


def test_intro_followed_by_prose_records_neither_an_operation_nor_a_clause() -> None:
    body = (
        '<html><body><header><dl><dd class="refid">lov/1</dd></dl></header><main><section class="section">'  # noqa: E501
        '<h2>I</h2><article class="defaultP">I lov 14. juni 2019 nr. 99 om x gjøres følgende endringer:'  # noqa: E501
        '</article><article class="legalP">Departementet kan gi forskrift.</article>'
        "</section></main></body></html>"
    )
    result = interpret(parse_document(body.encode(), "x"))
    assert result.operations == () and result.clauses == ()


def test_locations_with_three_sections_and_apply_on_the_second_ledd() -> None:
    assert replay._locations("§ 1 annet ledd, § 2 tredje ledd, § 3") == [
        ("§1", frozenset({2})),
        ("§2", frozenset({3})),
        ("§3", frozenset()),
    ]
    body = (
        '<html><body><header><dl><dd class="refid">lov/9</dd></dl></header><main><section class="section">'  # noqa: E501
        '<article class="legalArticle" data-name="§9"><article class="legalP">Første. Andre.</article>'  # noqa: E501
        '<article class="legalP">Liste:<ul class="defaultList"><li data-name="a)"><article class="listArticle">'  # noqa: E501
        '<article class="legalP">alfa</article></article></li></ul></article></article>'
        "</section></main></body></html>"
    )
    working = _Working(parse_document(body.encode(), "x"))
    punktum = _op("replace_punktum", Address(section="§9", ledd=2, punktum=1), ("X.",))
    assert (
        working.apply_replace_punktum(punktum) == "punktum operation on a ledd that carries a list"
    )
    bokstav = _op("replace_bokstav", Address(section="§9", ledd=2, bokstav="a"), ("beta",))
    assert working.apply_replace_bokstav(bokstav) is None
    assert working.map["§9"].ledd[1].text == "Liste: beta"
    assert working.map["§9"].ledd[0].text == "Første. Andre."


def test_move_destination_at_the_end_and_insert_section_without_key() -> None:
    w = _working()
    assert (
        w.apply_move_ledd(
            _op("move_ledd", Address(section="§1", ledd=1, destination_ledd=2), destination="§3")
        )
        is None
    )
    assert [item.text for item in w.map["§3"].ledd][-1] == ENACTED_TEXT_FIRST
    w = _working()
    assert (
        w.apply_move_ledd(
            _op("move_ledd", Address(section="§1", ledd=1, destination_ledd=3), destination="§3")
        )
        == "destination ledd 3 does not fit 1 existing"
    )
    provision = load("amending.xml")
    new = next(o for o in interpret(provision).operations if o.kind == "insert_section")
    assert (
        w.apply_insert_section(new.model_copy(update={"address": Address()}))
        == "new provision without key or text, or key already present"
    )
    assert (
        w.apply_replace_section(_op("replace_section", Address(section="§1")))
        == "target provision missing or no replacement text"
    )


ENACTED_TEXT_FIRST = "Loven gjelder prøving av fylkesmannen. Den gjelder også retting."


def test_unattributed_operations_are_counted_for_amenders_only_and_events_do_not_stop_early() -> (
    None
):
    body = (
        '<html><body><header><dl><dd class="refid">lov/2020-01-01-1</dd></dl></header><main>'
        '<section class="section"><h2>I</h2><article class="defaultP">§ 1 skal lyde:</article>'
        '<article class="futureLegalArticle" data-name="§1"><article class="legalP">T.</article></article>'  # noqa: E501
        "</section></main></body></html>"
    )
    document = parse_document(body.encode(), "x")
    assert run._unattributed(document) == 1
    assert run._member_record(document, "amending")["unattributed_operations"] == 1
    assert run._member_record(document, "decision")["unattributed_operations"] == 0
    evidence = collect_fixture()
    assert len(run.events_for(evidence)) == 5
    state = _run([_event(_op("repeal_section", Address(section="§9")))])
    assert run._state_record(state)["unresolved"] == [["x#I:0.0", "target provision missing"]]


def test_named_section_needs_exactly_one_section_and_pairs_need_both_sides() -> None:
    match = commencement._DELT_SENTENCE.match(
        "Endringsloven del I, endringene i lov 14. juni 2019 nr. 99 om prøving § 1 og § 2, "
        "trer i kraft 1. juli 2021."
    )
    assert match is not None and commencement._named_section(match) is None
    lopsided = instructions._SUBSTITUTE.match(
        "I følgende bestemmelser skal ordene «a» og «b» endres til «c»:"
    )
    assert lopsided is not None and instructions._pairs(lopsided) == ()
    with pytest.raises(ValueError, match="not a section label: 'kap 1'"):
        section_key_of("kap 1")
