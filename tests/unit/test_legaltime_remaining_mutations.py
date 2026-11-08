"""Real-input checks for the remaining legal-time mutation diffs."""

from hashlib import sha256
from pathlib import Path

import pytest
from lxml import etree

from lovspor.legaltime import archive, commencement, document, instructions, replay, scopes
from lovspor.legaltime.text import section_key_of
from tests.unit.test_legaltime_archive import make_archive
from tests.unit.test_legaltime_mutants import ACT, _law, _op, _working
from tests.unit.test_legaltime_remediation import xml_doc


def test_file_digest_bytes(tmp_path: Path) -> None:
    path = tmp_path / "bytes"
    path.write_bytes(bytes(range(256)))
    assert archive.file_sha256(path) == sha256(path.read_bytes()).hexdigest()


def test_evidence_baseline_and_amender(tmp_path: Path) -> None:
    path = make_archive(tmp_path / "evidence.tar.bz2")
    evidence = archive.collect(ACT, [path])
    assert evidence.baseline.header.refid == ACT
    assert evidence.amenders


@pytest.mark.parametrize("rest", ["", " § 1 og § 2"])
def test_partial_scope_without_unique_section(rest: str) -> None:
    match = scopes._DELT_SENTENCE.fullmatch(
        f"Endringsloven del I, endringene i lov om prøving{rest}, trer i kraft 1. januar 2022"
    )
    assert match is not None
    assert scopes._named_section(match) is None


def test_pending_baseline_is_unsettled_by_an_undated_decision() -> None:
    law = _law(ACT, "Kongen bestemmer", "")
    decision = xml_doc(
        "",
        '<dd class="dateInForce">ukjent</dd>'
        '<dd class="title">Ikraftsetting av lov 14. juni 2019 nr. 99</dd>',
    )
    out = commencement.baseline_timing(law, (decision,))
    assert out.status == "unresolved" and out.reason == "dateInForce outside the grammar"


@pytest.mark.parametrize("old,new", [("«a» og «b»", "«c»"), ("«a»", "«c» og «d»")])
def test_unbalanced_substitution_refused(old: str, new: str) -> None:
    doc = xml_doc(
        '<section><article class="defaultP">'
        f"I følgende bestemmelser skal {old} endres til {new}:</article>"
        '<article class="legalP">lov 14. juni 2019 nr. 99 § 1</article></section>'
    )
    assert instructions.interpret(doc).operations == ()


def test_continuation_copies_nested_markup_without_tail() -> None:
    root = etree.fromstring(
        '<article><article class="legalP"><ul><li>A</li></ul></article>'
        '<article class="defaultP"><b>B</b> C</article>outside</article>'
    )
    before = etree.tostring(root)
    result = document._ledd_of(root)
    assert result[0].xml.endswith('<article class="defaultP"><b>B</b> C</article>')
    assert result[0].text == "A B C"
    assert etree.tostring(root) == before


def test_inline_whitespace_preserved() -> None:
    doc = xml_doc(
        '<article class="legalArticle" data-name="§1">'
        '<article class="legalP"><b>A</b> <i>B</i></article></article>'
    )
    assert doc.provisions[0].ledd[0].text == "A B"


def test_invalid_section_reports_input() -> None:
    with pytest.raises(ValueError, match="not a section label: 'invalid'"):
        section_key_of("invalid")


@pytest.mark.parametrize("kind", ["replace_ledd", "insert_ledd"])
def test_missing_ledd_address_refused_without_changes(kind: str) -> None:
    working = _working()
    before = working.map["§1"].freeze("§1")
    assert getattr(working, f"apply_{kind}")(_op(kind, instructions.Address(section="§1")))
    assert working.map["§1"].freeze("§1") == before


def test_empty_substitution_locations_have_no_targets() -> None:
    assert replay._targets(_op("substitute_term", instructions.Address()), _working()) == []
