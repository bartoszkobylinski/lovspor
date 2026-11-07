"""Behavioral checks for the ten-survivor mutation artifact."""

from hashlib import sha256

from lovspor.legaltime import replay
from lovspor.legaltime.archive import MemberRecord, _title_document
from lovspor.legaltime.document import Ledd
from tests.unit.test_legaltime_phase2_instructions import _ops, _t


def test_standin_hash_matches_header_only_xml_without_inter_item_text() -> None:
    record = MemberRecord(
        name="decision.xml",
        sha256="0" * 64,
        refid="forskrift/2022-01-01-1",
        title="Ikraftsetting",
        date_in_force="",
        published_at=None,
        changes_to_documents=("lov/2019-06-14-99", "lov/2020-01-01-1"),
    )
    expected = (
        '<html><body><header><dl><dd class="refid">forskrift/2022-01-01-1</dd>'
        '<dd class="title">Ikraftsetting</dd><dd class="changesToDocuments"><ul>'
        "<li>lov/2019-06-14-99</li><li>lov/2020-01-01-1</li></ul></dd>"
        "</dl></header><main/></body></html>"
    )
    document = _title_document(record)
    assert document.header.changes_to_documents == record.changes_to_documents
    assert document.sha256 == sha256(expected.encode()).hexdigest()


def test_numbered_ledd_joiner_does_not_override_punktum_range() -> None:
    ops = _ops(_t("§ 1 (1) og (2) første til tredje punktum skal lyde:"), _t("Ny tekst."))
    assert len(ops) == 1
    assert (ops[0].address.ledd, ops[0].address.ledd_end) == (1, 2)
    assert (ops[0].address.punktum, ops[0].address.punktum_end) == (1, 3)


def test_inline_intro_consumes_payload_once() -> None:
    ops = _ops(
        _t("Ny tekst."),
        _t("I lov 14. juni 2019 nr. 99 § 2 gjelder dette."),
        intro="I lov 14. juni 2019 nr. 99 skal § 1 første ledd lyde:",
    )
    assert len(ops) == 1
    assert ops[0].kind == "replace_ledd"
    assert [ledd.text for ledd in ops[0].ledd] == [
        "Ny tekst.",
        "I lov 14. juni 2019 nr. 99 § 2 gjelder dette.",
    ]


def test_inserted_item_drops_comments_but_keeps_their_tail() -> None:
    edited = replay._insert_item(
        '<ol><li data-name="b.">Bee</li></ol>', "a", "Ay<!--editorial--> tail"
    )
    assert isinstance(edited, Ledd)
    assert "<!--" not in edited.xml
    assert edited.text == "Ay tail Bee"


def test_existing_ledd_drops_comments_during_item_removal() -> None:
    edited = replay._remove_item(
        '<ol><!--editorial--><li data-name="a.">Ay</li><li data-name="b.">Bee</li></ol>', "a"
    )
    assert isinstance(edited, Ledd)
    assert "<!--" not in edited.xml
    assert edited.text == "Bee"
