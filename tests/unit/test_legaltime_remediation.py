"""Behavioral regression cases for the listed legal-time mutation surface."""

import io
import json
import locale
import re
import tarfile
from datetime import date
from hashlib import sha256
from pathlib import Path

import pytest
import typer
from lxml import etree
from pytest_httpx import HTTPXMock

from lovspor.legaltime import archive, commencement, document, instructions, replay, run, verify
from lovspor.legaltime.commands import ReplayRequest, fetch_impl, replay_impl
from lovspor.legaltime.errors import LegalTimeDocumentError, LegalTimeInputError
from lovspor.legaltime.text import split_sentences
from lovspor.sources.lovdata import LovdataClient
from tests.unit.test_legaltime_archive import FIXTURES, make_archive
from tests.unit.test_legaltime_mutants import ACT, _event, _law, _op, _working, load


def xml_doc(main: str, header: str = "") -> document.Document:
    return document.parse_document(
        (
            '<html><body><header><dl><dd class="refid">lov/2020-01-01-1</dd>'
            + header
            + "</dl></header><main>"
            + main
            + "</main></body></html>"
        ).encode(),
        "input.xml",
    )


def test_missing_publication_and_archive_error(tmp_path: Path) -> None:
    record = archive._record(
        "input.xml",
        b'<html><body><header><dl><dd class="refid">lov/1</dd></dl></header><main/></body></html>',
    )
    assert record is not None and record.published_at is None
    missing = tmp_path / "absent.tar.bz2"
    with pytest.raises(LegalTimeInputError) as exc:
        archive.index_archive(missing)
    assert str(exc.value) == f"archive not found: {missing}"


def test_nested_cache_and_cache_hit_preserve_file(tmp_path: Path) -> None:
    path = make_archive(tmp_path / "a.tar.bz2")
    cache_dir = tmp_path / "nested" / "index"
    first = archive.index_archive(path, cache_dir)
    cache = next(cache_dir.iterdir())
    # A cache hit must not rewrite a valid cache. Whitespace is valid JSON.
    body = cache.read_bytes() + b"\n\n"
    cache.write_bytes(body)
    assert archive.index_archive(path, cache_dir) == first
    assert cache.read_bytes() == body
    evidence = archive.collect(ACT, [path], tmp_path / "collection" / "cache")
    assert evidence.baseline.header.refid == ACT
    assert list((tmp_path / "collection" / "cache").iterdir())
    assert not list(tmp_path.glob("*.json"))


def test_oracle_continues_after_foreign_xml(tmp_path: Path) -> None:
    path = make_archive(tmp_path / "oracle.tar.bz2", {"nl/a.xml": "consolidated.xml"})
    # Build archive with the invalid member first.

    with tarfile.open(path, "w:bz2") as tar:
        for name, body in [
            ("nl/0.xml", b"<html/>"),
            ("nl/a.xml", (FIXTURES / "consolidated.xml").read_bytes()),
        ]:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    assert archive.load_oracle(path, ACT).member == "nl/a.xml"


def test_header_fragments_and_duplicate_fields_are_concatenated() -> None:
    doc = xml_doc(
        "",
        "".join(
            f'<dd class="{key}">A<b>B</b>C</dd><dd class="{key}">D</dd>'
            for key in ["legacyID", "dokid", "title", "dateInForce"]
        )
        + '<dd class="changesToDocuments"><ul><li>lov/<b>1</b></li></ul></dd>',
    )
    assert (doc.header.legacy_id, doc.header.dokid, doc.header.title, doc.header.date_in_force) == (
        "ABCD",
    ) * 4
    assert doc.header.changes_to_documents == ("lov/1",)
    with pytest.raises(LegalTimeDocumentError) as exc:
        document.parse_header(etree.fromstring("<html/>"))
    assert str(exc.value) == "document header carries no single refid"


def test_provision_tags_defaults_and_inline_notes() -> None:
    doc = xml_doc(
        '<div class="legalArticle" data-name="wrong"/>'
        '<article class="legalArticle"><b class="legalArticleValue">wrong</b>'
        '<b class="legalArticleTitle">wrong</b>'
        '<span class="changesToParent">A<b>B</b>C</span></article>'
    )
    assert len(doc.provisions) == 1
    p = doc.provisions[0]
    assert (p.key, p.label, p.heading, p.notes) == ("", "", "", ("ABC",))
    future = document._node(etree.fromstring('<article class="futureLegalArticle"/>'))
    assert future.text == ""


def test_nested_sections_and_change_addresses() -> None:
    doc = xml_doc(
        "<h1>Document title</h1><section><h2>I</h2>"
        '<section><h3>Inner</h3><article class="defaultP">nested</article></section>'
        '<div class="change" data-change-part="lov/1/§1">'
        '<div class="document-change"><ul><li><article class="defaultP">addressed'
        "</article></li></ul></div></div>"
        '<div class="change"><article class="defaultP">unaddressed</article></div>'
        "</section>"
    )
    assert len(doc.parts) == 1
    assert [(n.text, n.change_part) for n in doc.parts[0].nodes] == [
        ("nested", ()),
        ("addressed", ("lov/1/§1",)),
        ("unaddressed", ()),
    ]


def test_noncontinuation_paragraph_without_preceding_list() -> None:
    doc = xml_doc(
        '<article class="legalArticle" data-name="§1">'
        '<article class="defaultP">not a continuation</article>'
        '<article class="legalP">first</article>'
        '<article class="defaultP">instruction</article></article>'
    )
    assert [p.text for p in doc.provisions[0].ledd] == ["first"]


def test_disjoint_sentence_addresses_are_refused() -> None:
    kind, groups = instructions.classify("§ 1 første ledd første og tredje punktum oppheves.")
    assert kind == "repeal_punktum"
    address = instructions._address(groups)
    assert address.punktum is None and address.punktum_end is None


def test_reader_continues_after_commencement_and_unaddressed_prose() -> None:
    doc = xml_doc(
        '<section><h2>I</h2><article class="defaultP">'
        "Loven trer i kraft straks. § 1 oppheves.</article></section>"
    )
    result = instructions.interpret(doc)
    assert result.clauses == ("Loven trer i kraft straks.",)
    assert [o.kind for o in result.operations] == ["repeal_section"]
    doc = xml_doc(
        '<section><h2>I</h2><article class="defaultP">'
        "Dette er prose. § 1 oppheves.</article></section>"
    )
    assert [o.kind for o in instructions.interpret(doc).operations] == ["repeal_section"]


def test_reader_payload_not_reused_and_loose_text_not_a_clause() -> None:
    doc = xml_doc(
        '<section><h2>I</h2><article class="defaultP">§ 1 oppheves. '
        '§ 2 første ledd skal lyde:</article><article class="legalP">Nytt.</article>'
        '<article class="defaultP">§ 3 oppheves.</article>'
        '<article class="legalP">Ettertekst.</article></section>'
    )
    result = instructions.interpret(doc)
    assert result.clauses == ()
    assert [len(o.ledd) for o in result.operations] == [0, 1, 1]


def test_report_keys_and_filename_order(tmp_path: Path) -> None:
    first = make_archive(tmp_path / "z.tar.bz2")
    sub = tmp_path / "zdir"
    sub.mkdir()
    second = make_archive(sub / "a.tar.bz2", {"lti/x.xml": "resolution-partial.xml"})
    evidence = archive.collect(ACT, [first, second])
    report = run.build_report(
        evidence, [date(2023, 1, 1)], load("consolidated.xml"), [first, second]
    )
    assert [p["filename"] for p in report["inputs"]] == ["a.tar.bz2", "z.tar.bz2"]
    assert report["members"]
    assert report["events"]
    assert report["baseline_timing"]["status"] == "dated"
    assert json.loads(json.dumps(report)) == report


def test_unattributed_operation_count_and_role() -> None:
    doc = xml_doc('<section><h2>I</h2><article class="defaultP">§ 1 oppheves.</article></section>')
    assert run._unattributed(doc) == 1
    assert run._member_record(doc, "amending")["unattributed_operations"] == 1
    assert run._member_record(doc, "decision")["unattributed_operations"] == 0


def test_pending_event_classification() -> None:
    event = _event(
        _op("repeal_section", instructions.Address(section="§1")),
        commencement.Timing(status="pending"),
    )
    assert replay._classify(event, date(2023, 1, 1)) == "pending"


@pytest.mark.parametrize("method", ["apply_replace_section", "apply_insert_section"])
def test_section_operations_refuse_absent_payload(method: str) -> None:
    working = _working()
    kind = "replace_section" if method == "apply_replace_section" else "insert_section"
    key = "§1" if kind == "replace_section" else "§99"
    assert getattr(working, method)(_op(kind, instructions.Address(section=key))) is not None


def test_replacing_section_restores_repealed_flag() -> None:
    working = _working()
    assert (
        working.apply_repeal_section(_op("repeal_section", instructions.Address(section="§1")))
        is None
    )
    assert (
        working.apply_replace_section(
            _op(
                "replace_section",
                instructions.Address(section="§1"),
                new_provision=load("enacting.xml").provisions[0],
            )
        )
        is None
    )
    assert working.map["§1"].repealed is False


def test_location_scopes_do_not_leak_between_sections() -> None:
    assert replay._locations("§ 1 første ledd og § 2 annet ledd") == [
        ("§1", frozenset({1})),
        ("§2", frozenset({2})),
    ]


def test_omitted_amendment_tail_without_marker() -> None:
    p = load("enacting.xml").provisions[0].model_copy(update={"heading": "Endringer i andre lover"})
    state = replay.ProvisionState(
        key=p.key, label=p.label, heading=p.heading, ledd=p.ledd, status="replayed"
    )
    oracle = p.model_copy(update={"ledd": ()})
    assert verify._omitted(state, oracle)


def test_unknown_commencement_message_and_unrelated_decision() -> None:
    law = _law("lov/2021-01-01-1", "Kongen bestemmer", "x")
    op = _op("repeal_section", instructions.Address(section="§1"))
    timing = commencement.clause_timings(law, ("Loven får virkning fra 1. juli 2022.",), (op,))[
        op.id
    ]
    assert timing.reason == "commencement sentence outside the grammar"
    assert commencement.baseline_timing(
        load("enacting.xml"), (load("resolution-partial.xml"), load("resolution-full.xml"))
    ).effective == date(2021, 1, 1)


def test_fetch_pins_source_summary_and_nested_destination(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:

    names = ["lovtidend-avd1-2001-2025.tar.bz2", "gjeldende-lover.tar.bz2"]
    httpx_mock.add_response(
        url="https://api.lovdata.no/v1/publicData/list",
        json=[
            {
                "filename": n,
                "description": "x",
                "sizeBytes": 1,
                "lastModified": "2026-11-03T02:31:00Z",
            }
            for n in names
        ],
    )
    for name in names:
        httpx_mock.add_response(
            url=f"https://api.lovdata.no/v1/publicData/get/{name}", content=b"a"
        )
    dest = tmp_path / "nested" / "downloads"
    with LovdataClient() as client:
        summary = fetch_impl(dest, client)
    record = json.loads((dest / "pins.json").read_bytes())
    assert record["source"] == "https://api.lovdata.no/v1/publicData/"

    assert summary == "\n".join(f"{name} sha256={sha256(b'a').hexdigest()}" for name in names)


def test_fetch_empty_catalogue_exact_refusal(tmp_path: Path, httpx_mock: HTTPXMock) -> None:

    httpx_mock.add_response(url="https://api.lovdata.no/v1/publicData/list", json=[])
    with LovdataClient() as client, pytest.raises(LegalTimeInputError) as exc:
        fetch_impl(tmp_path, client)
    assert str(exc.value) == "publicData /list names no pilot archive"


def test_replay_requires_explicit_date() -> None:

    with pytest.raises(typer.BadParameter) as exc:
        replay_impl(ReplayRequest(act=ACT, archives=(), dates=(), oracle=None))
    assert str(exc.value) == "at least one --at date is required; no clock is consulted"


@pytest.mark.parametrize("kind", ["replace_punktum", "insert_punktum", "repeal_punktum"])
def test_sentence_ranges_and_multiple_payload_paragraphs(kind: str) -> None:
    baseline = xml_doc(
        '<article class="legalArticle" data-name="§1">'
        '<article class="legalP">First. Second. Third.</article>'
        '<article class="legalP">Other.</article></article>'
    )
    working = replay._Working(baseline)
    op = _op(
        kind,
        instructions.Address(section="§1", ledd=1, punktum=1, punktum_end=2),
        ("New first.", "New second."),
    )
    assert getattr(working, f"apply_{kind}")(op) is None
    assert (
        working.map["§1"].ledd[0].text
        == {
            "replace_punktum": "New first. New second. Third.",
            "insert_punktum": "New first. New second. First. Second. Third.",
            "repeal_punktum": "Third.",
        }[kind]
    )


def test_bokstav_replacement_preserves_other_ledd_and_handles_newlines() -> None:
    baseline = xml_doc(
        '<article class="legalArticle" data-name="§1">'
        '<article class="legalP">Untouched.</article>'
        '<article class="legalP"><ul><li data-name="a)">'
        '<article class="legalP">Old\ntext.</article></li></ul></article></article>'
    )
    working = replay._Working(baseline)
    op = _op("replace_bokstav", instructions.Address(section="§1", ledd=2, bokstav="a"), ("New.",))
    assert working.apply_replace_bokstav(op) is None
    assert [p.text for p in working.map["§1"].ledd] == ["Untouched.", "New."]


def test_substitution_changes_only_addressed_ledd() -> None:
    baseline = xml_doc(
        '<article class="legalArticle" data-name="§1">'
        '<article class="legalP">old</article>'
        '<article class="legalP">old</article></article>'
    )
    working = replay._Working(baseline)
    op = _op(
        "substitute_term",
        instructions.Address(),
        locations="§ 1 annet ledd",
        term_old="old",
        term_new="new",
    )
    assert working.apply_substitute_term(op) is None
    assert [p.text for p in working.map["§1"].ledd] == ["old", "new"]
    assert replay._substitute(working.map["§1"], frozenset(), ("absent", "new")) is False


def test_move_ledd_validates_destination_and_keeps_order() -> None:
    baseline = xml_doc(
        '<article class="legalArticle" data-name="§1">'
        '<article class="legalP">Move.</article></article>'
        '<article class="legalArticle" data-name="§2">'
        '<article class="legalP">First.</article>'
        '<article class="legalP">Second.</article></article>'
    )
    working = replay._Working(baseline)
    bad = _op(
        "move_ledd",
        instructions.Address(section="§1", ledd=1, destination_ledd=4),
        destination="§2",
    )
    assert working.apply_move_ledd(bad) == "destination ledd 4 does not fit 2 existing"
    good = bad.model_copy(
        update={"address": bad.address.model_copy(update={"destination_ledd": 2})}
    )
    assert working.apply_move_ledd(good) is None
    assert [p.text for p in working.map["§2"].ledd] == ["First.", "Move.", "Second."]


def test_cache_is_utf8_under_ascii_locale(tmp_path: Path) -> None:

    previous = locale.setlocale(locale.LC_CTYPE)
    path = make_archive(tmp_path / "a.tar.bz2")
    try:
        locale.setlocale(locale.LC_CTYPE, "C")
        original = archive.index_archive(path)
        cache = next(tmp_path.glob("*.json"))
        assert "prøving" in cache.read_text(encoding="utf-8")
        assert archive._cached_index(cache, original.sha256) == original
    finally:
        locale.setlocale(locale.LC_CTYPE, previous)


def test_substitution_items_keep_ids_parts_and_pending_act() -> None:
    doc = xml_doc(
        '<section><h2>VI</h2><article class="defaultP">'
        "I følgende bestemmelser skal «old» endres til «new»:</article>"
        '<article class="defaultP">lov 14. juni 2019 nr. 99 om prøving</article>'
        '<article class="legalP">§ 1 første ledd</article>'
        '<article class="defaultP">lov 30. juni 1916 nr. 1 § 2</article>'
        '<article class="defaultP">§ 3 oppheves.</article></section>'
    )
    result = instructions.interpret(doc)
    assert [(o.id, o.part, o.act, o.kind) for o in result.operations] == [
        ("lov/2020-01-01-1#0:VI:2.0", "VI", ACT, "substitute_term"),
        ("lov/2020-01-01-1#0:VI:3.0", "VI", "lov/1916-06-30-1", "substitute_term"),
        ("lov/2020-01-01-1#0:VI:4.0", "VI", None, "repeal_section"),
    ]


def test_events_continue_after_another_acts_operation() -> None:
    doc = xml_doc(
        "<section><h2>I</h2>"
        '<div class="change" data-change-part="lov/other/§1">'
        '<article class="defaultP">§ 1 oppheves.</article></div>'
        '<div class="change" data-change-part="lov/2019-06-14-99/§2">'
        '<article class="defaultP">§ 2 oppheves.</article></div></section>'
    )
    events = run._events_of(doc, ACT, (), 0)
    assert len(events) == 1
    assert events[0].operation.address.section == "§2"


def test_unresolved_pairs_are_serializable() -> None:
    baseline = load("enacting.xml")
    event = _event(_op("unsupported", instructions.Address(section="§1")))
    state = replay.replay(
        baseline,
        commencement.Timing(status="dated", effective=date(2020, 1, 1)),
        [event],
        date(2023, 1, 1),
    )
    record = run._state_record(state)
    assert record["unresolved"] == [[event.operation.id, "unsupported instruction: i"]]


def test_introductory_loose_ledd_is_not_a_commencement_clause() -> None:
    doc = xml_doc(
        '<section><h2>I</h2><article class="defaultP">'
        "I lov 14. juni 2019 nr. 99 gjøres følgende endringer:</article>"
        '<article class="legalP">Unattached text.</article></section>'
    )
    assert instructions.interpret(doc).clauses == ()


def test_explicit_substitution_act_does_not_steal_pending_act() -> None:
    doc = xml_doc(
        '<section><h2>VI</h2><article class="defaultP">'
        "I følgende bestemmelser skal «old» endres til «new»:</article>"
        '<article class="defaultP">lov 14. juni 2019 nr. 99</article>'
        '<article class="legalP">lov 30. juni 1916 nr. 1 § 2</article>'
        '<article class="legalP">§ 3</article></section>'
    )
    ops = instructions.interpret(doc).operations
    assert [(o.act, o.locations) for o in ops] == [("lov/1916-06-30-1", "§ 2"), (ACT, "§ 3")]


def test_renumber_cannot_convert_a_later_replacement() -> None:
    doc = xml_doc(
        '<section><h2>I</h2><article class="defaultP">'
        "Nåværende første ledd blir nytt annet ledd.</article>"
        '<article class="defaultP">§ 1 første ledd skal lyde:</article>'
        '<article class="legalP">New.</article></section>'
    )
    assert [o.kind for o in instructions.interpret(doc).operations] == ["renumber", "replace_ledd"]


def test_renumber_conversion_continues_past_unrelated_operations() -> None:
    doc = xml_doc(
        '<section><h2>I</h2><article class="defaultP">§ 2 oppheves.</article>'
        '<article class="defaultP">§ 1 første ledd skal lyde:</article>'
        '<article class="legalP">New.</article>'
        '<article class="defaultP">Nåværende første ledd blir nytt annet ledd.</article>'
        "</section>"
    )
    assert [o.kind for o in instructions.interpret(doc).operations] == [
        "repeal_section",
        "insert_ledd",
        "renumber",
    ]


def test_partial_decision_ignores_informational_text_and_refuses_undated_scope() -> None:
    decision = xml_doc(
        '<section><article class="legalP">Departementet kan gi forskrift.</article>'
        '<article class="legalP">Endringsloven del I trer i kraft 1. januar 2022.'
        "</article></section>"
    )
    assert commencement._partial_scopes(decision) == [
        commencement.Scope(
            part="I",
            section=None,
            when=date(2022, 1, 1),
            quote="Endringsloven del I trer i kraft 1. januar 2022.",
        )
    ]
    undated = xml_doc(
        '<section><article class="legalP">Endringsloven del I trer i kraft seinare.'
        "</article></section>"
    )
    assert commencement._partial_scopes(undated) is None


@pytest.mark.parametrize("kind", ["replace_ledd", "insert_ledd"])
def test_ledd_range_requires_a_start(kind: str) -> None:
    working = _working()
    op = _op(kind, instructions.Address(section="§1", ledd_end=1), ("New.",))
    assert getattr(working, f"apply_{kind}")(op) is not None


def test_evidence_reads_the_baseline_from_its_own_archive_only(tmp_path: Path) -> None:
    """The baseline archive is always one of the indexed archives; anything else is a bug."""
    with pytest.raises(KeyError):
        archive._read_evidence({}, (tmp_path / "a.tar.bz2", "missing"))


def test_text_parser_removes_formatting_blank_nodes() -> None:
    assert replay._text_of_xml("<b>A</b> <i>B</i>") == "AB"


def test_sentence_operation_checks_the_addressed_ledd_for_lists() -> None:
    baseline = xml_doc(
        '<article class="legalArticle" data-name="§1">'
        '<article class="legalP">Plain.</article>'
        '<article class="legalP"><ul><li>List.</li></ul></article></article>'
    )
    working = replay._Working(baseline)
    op = _op("replace_punktum", instructions.Address(section="§1", ledd=1, punktum=1), ("New.",))
    assert working.apply_replace_punktum(op) is None
    assert working.map["§1"].ledd[0].text == "New."


def test_substitution_targets_include_lettered_sections() -> None:
    baseline = xml_doc(
        '<article class="legalArticle" data-name="§2a">'
        '<article class="legalP">old</article></article>'
    )
    working = replay._Working(baseline)
    op = _op(
        "substitute_term", instructions.Address(), locations="§ 2 a", term_old="old", term_new="new"
    )
    assert replay._targets(op, working) == [working.map["§2a"]]


def test_location_scopes_stop_before_next_section_even_without_own_ledd() -> None:
    assert replay._locations("§ 1 og § 2 annet ledd") == [
        ("§1", frozenset()),
        ("§2", frozenset({2})),
    ]


def test_unicode_year_archive_pins_are_utf8(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    name = "lovtidend-avd1-\uff12\uff10\uff12\uff16.tar.bz2"
    httpx_mock.add_response(
        url="https://api.lovdata.no/v1/publicData/list",
        json=[
            {
                "filename": name,
                "description": "x",
                "sizeBytes": 1,
                "lastModified": "2026-11-03T02:31:00Z",
            }
        ],
    )
    httpx_mock.add_response(url=f"https://api.lovdata.no/v1/publicData/get/{name}", content=b"a")
    previous = locale.setlocale(locale.LC_CTYPE)
    try:
        locale.setlocale(locale.LC_CTYPE, "C")
        with LovdataClient() as client:
            fetch_impl(tmp_path, client)
        assert json.loads((tmp_path / "pins.json").read_bytes())["archives"][0]["filename"] == name
    finally:
        locale.setlocale(locale.LC_CTYPE, previous)


def test_missing_section_address_never_targets_an_unnamed_provision() -> None:
    working = replay._Working(
        xml_doc(
            '<article class="legalArticle"><article class="legalP">Unnamed.</article></article>'
        )
    )
    assert working._target("") is None


def test_consumed_instruction_payload_is_not_reinterpreted_as_substitution() -> None:
    doc = xml_doc(
        '<section><h2>VI</h2><article class="defaultP">'
        "I følgende bestemmelser skal «old» endres til «new»:</article>"
        '<article class="defaultP">Uklar instruks.</article>'
        '<article class="legalP">lov 14. juni 2019 nr. 99 § 1</article></section>'
    )
    assert instructions.interpret(doc).operations == ()


def test_digest_across_multiple_read_chunks_and_json_unicode(tmp_path: Path) -> None:
    path = tmp_path / "large"
    content = b"abc" * 800000
    path.write_bytes(content)
    assert archive.file_sha256(path) == sha256(content).hexdigest()
    assert archive.dump_json({"å": "ø"}) == '{\n "å": "ø"\n}'


def test_internal_grammar_contracts() -> None:
    header = instructions._SUBSTITUTE.match
    paired = header(
        "I følgende bestemmelser skal ordene «a» og «b» endres til henholdsvis «c» og «d»:"
    )
    single = header("I følgende bestemmelser skal ordet «a» endres til «b»:")
    assert paired is not None and single is not None
    assert instructions._pairs(paired) == (("a", "c"), ("b", "d"))
    assert instructions._pairs(single) == (("a", "b"),)
    assert instructions._shift("Nåværende første ledd blir nytt annet ledd.") == instructions.Shift(
        section=None, unit="ledd", pairs=((1, 2),)
    )
    assert commencement._exception_scope("Ny § 2 trer likevel i kraft straks.") == ("section", "§2")
    assert commencement._informational("Se lov 14. juni 2019 nr. 99.")


def test_unknown_abbreviation_suffix_does_not_merge_sentences() -> None:

    assert split_sentences("Se nrX. Neste setning.") == ["Se nrX.", "Neste setning."]


def test_three_section_scopes_remain_independent() -> None:
    assert replay._locations("§ 1 første ledd, § 2 annet ledd og § 3 tredje ledd") == [
        ("§1", frozenset({1})),
        ("§2", frozenset({2})),
        ("§3", frozenset({3})),
    ]


def test_next_instruction_search_never_revisits_an_earlier_node() -> None:
    nodes = [
        document.Node(kind="paragraph", css_class="defaultP", text="§ 1 oppheves."),
        document.Node(kind="other", css_class="span", text="payload"),
    ]
    assert instructions._next_instruction(nodes, 1) == 2


def test_single_introductory_paragraph_finishes_without_revisiting_it() -> None:
    doc = xml_doc(
        '<section><h2>I</h2><article class="defaultP">'
        "I lov 14. juni 2019 nr. 99 gjøres følgende endringer:</article></section>"
    )
    assert instructions.interpret(doc).operations == ()


def test_citation_removal_does_not_hide_an_adjacent_uncited_date() -> None:
    # Even malformed citation prose with an extra digit must not silently become
    # informational when removing the cited act exposes a separate date.
    sentence = "Se lov 1. januar 20201. juli 2022."
    law = _law("lov/2020-01-01-1", "2020-01-01", sentence)
    op = instructions.interpret(law).operations[0]
    timing = commencement.clause_timings(law, (sentence,), (op,))[op.id]
    assert timing.status == "unresolved"
    assert timing.reason == "commencement sentence outside the grammar"


def _single_substitution() -> "re.Match[str]":
    match = instructions._SUBSTITUTE.match("I følgende bestemmelser skal ordet «a» endres til «b»:")
    assert match is not None
    return match


def test_absent_addresses_and_substitution_terms() -> None:
    assert instructions._address({}) == instructions.Address()
    assert instructions._pairs(_single_substitution()) == (("a", "b"),)
    doc = xml_doc(
        '<section><h2>I</h2><article class="defaultP">'
        "Nåværende første ledd blir nytt annet ledd.</article></section>"
    )
    op = instructions.interpret(doc).operations[0]
    assert op.address.section is None
    assert op.destination is None


def test_substitution_without_locations_leaves_corpus_unchanged() -> None:
    working = _working()
    before = [(key, list(p.ledd)) for key, p in working.map.items()]
    op = _op("substitute_term", instructions.Address(), term_old="old", term_new="new")
    assert working.apply_substitute_term(op) == "substitution locations unresolved: none listed"
    assert replay._targets(op, working) == []
    assert [(key, list(p.ledd)) for key, p in working.map.items()] == before
