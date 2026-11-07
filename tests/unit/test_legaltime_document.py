"""Document shape of the replay: headers, provision trees, amending parts.

Fixtures are synthetic copies of the Lovtidend and gjeldende-lover document
shape; raw Lovdata XML never enters the repository.
"""

from datetime import datetime
from hashlib import sha256
from pathlib import Path

import pytest
from lxml import etree

from lovspor.legaltime.document import Document, element_text, inner_xml, parse_document
from lovspor.legaltime.errors import LegalTimeDocumentError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legaltime"


def load(name: str) -> Document:
    return parse_document((FIXTURES / name).read_bytes(), f"lti/2020/{name}")


def test_header_fields_are_read_from_the_key_info_list() -> None:
    header = load("amending.xml").header
    assert header.refid == "lov/2020-12-18-999"
    assert header.legacy_id == "LOV-2020-12-18-999"
    assert header.dokid == "LTI/lov/2020-12-18-999"
    assert header.date_in_force == "2020-12-18, Kongen bestemmer"
    assert header.published_at == datetime(2020, 12, 18, 12, 10)
    assert header.changes_to_documents == ("lov/2019-06-14-99", "lov/1967-02-10")


def test_consolidated_publication_date_without_time_is_read() -> None:
    assert load("consolidated.xml").header.published_at == datetime(2019, 6, 14)


def test_member_sha256_is_the_digest_of_the_bytes() -> None:
    document = load("enacting.xml")
    assert len(document.sha256) == 64
    assert document.member == "lti/2020/enacting.xml"


def test_enacting_law_yields_an_ordered_provision_tree() -> None:
    provisions = load("enacting.xml").provisions
    assert [p.key for p in provisions] == ["§1", "§2", "§3", "§4"]
    first = provisions[0]
    assert first.label == "§ 1"
    assert first.heading == "Virkeområde"
    assert [ledd.text for ledd in first.ledd] == [
        "Loven gjelder prøving av fylkesmannen. Den gjelder også retting.",
        "Annet ledd første punktum. Annet ledd annet punktum.",
    ]
    assert first.ledd[0].xml == first.ledd[0].text


def test_ledd_text_keeps_list_items_as_running_text() -> None:
    provisions = {p.key: p for p in load("enacting.xml").provisions}
    assert provisions["§2"].ledd[0].text == (
        "Følgende gjelder: bokstav a bokstav b Bokstav b gjelder ikke private."
    )
    assert "<ul" in provisions["§2"].ledd[0].xml
    assert provisions["§2"].ledd[0].xml.endswith("Bokstav b gjelder ikke private.</article>")
    assert len(provisions["§2"].ledd) == 1


def test_list_continuation_text_matches_the_consolidated_ledd() -> None:
    enacted = {p.key: p for p in load("enacting.xml").provisions}["§2"]
    consolidated = {p.key: p for p in load("consolidated.xml").provisions}["§2"]
    assert enacted.ledd[0].text == consolidated.ledd[0].text


def test_consolidated_text_drops_links_notes_and_footnote_marks() -> None:
    provisions = {p.key: p for p in load("consolidated.xml").provisions}
    assert provisions["§1"].ledd[0].text == (
        "Loven gjelder prøving av statsforvalteren. Den gjelder også retting."
    )
    assert "<a href" in provisions["§1"].ledd[0].xml
    assert provisions["§3"].ledd[0].text == "Loven gjelder fra den tid Kongen bestemmer."
    assert provisions["§4"].heading == "(Opphevet)"
    assert provisions["§4"].ledd == ()
    assert len(provisions["§1"].ledd) == 3


def test_amending_law_parts_carry_instructions_payloads_and_clauses() -> None:
    document = load("amending.xml")
    assert document.provisions == ()
    assert [part.label for part in document.parts] == ["", "I", "II", "III"]
    first = document.parts[1].nodes
    assert first[0].kind == "paragraph"
    assert first[0].text.startswith("I lov 14. juni 2019 nr. 99")
    assert first[1].text == "§ 1 annet ledd annet punktum skal lyde:"
    assert first[2].kind == "ledd"
    assert first[2].ledd is not None
    assert first[2].ledd.text == "Annet ledd nytt annet punktum."
    provision_nodes = [node for node in first if node.kind == "provision"]
    assert [node.provision.key for node in provision_nodes if node.provision] == ["§2a", "§3"]


def test_structured_change_addresses_propagate_to_their_nodes() -> None:
    nodes = load("amending.xml").parts[2].nodes
    assert [node.change_part for node in nodes] == [("lov/1967-02-10/§5/ledd/1",)] * 2
    assert nodes[1].kind == "ledd"


def test_commencement_clause_inside_a_list_is_a_ledd_node() -> None:
    nodes = load("amending.xml").parts[3].nodes
    assert len(nodes) == 1
    assert nodes[0].kind == "ledd"
    assert nodes[0].text.startswith("Loven trer i kraft straks.")


def test_lead_part_holds_the_body_outside_any_section() -> None:
    document = load("resolution-partial.xml")
    assert [part.label for part in document.parts] == [""]
    assert document.parts[0].nodes[0].text.startswith("Delt ikrafttredelse av lov")


def test_foreign_shapes_are_refused() -> None:
    with pytest.raises(LegalTimeDocumentError):
        parse_document(b"<html><body><p>nope</p></body></html>", "x")
    with pytest.raises(LegalTimeDocumentError):
        parse_document(b"<html><body><main/></body></html>", "x")
    with pytest.raises(LegalTimeDocumentError):
        parse_document(b"<not xml", "x")


def test_element_text_skips_editorial_notes_only() -> None:
    element = etree.fromstring(
        '<a>one <b class="changesToParent">note</b> two<sup class="footnotereference">1</sup></a>'
    )
    assert element_text(element) == "one two"


def test_paragraph_after_a_plain_ledd_is_not_a_continuation() -> None:
    provisions = {p.key: p for p in load("enacting-embedded.xml").provisions}
    closing = provisions["§25"]
    assert len(closing.ledd) == 1
    assert closing.ledd[0].text.endswith("i andre lover:")


def test_document_digest_pins_raw_bytes_including_line_endings() -> None:
    body = (FIXTURES / "enacting.xml").read_bytes()
    noisy = body.replace(b"><", b">\r\n<")
    original = parse_document(body, "original.xml")
    changed = parse_document(noisy, "changed.xml")
    assert original.sha256 == sha256(body).hexdigest()
    assert changed.sha256 == sha256(noisy).hexdigest()
    assert original.sha256 != changed.sha256
    assert [p.key for p in original.provisions] == [p.key for p in changed.provisions]


def test_external_entity_cannot_read_a_local_file(tmp_path: Path) -> None:
    secret = tmp_path / "private.txt"
    secret.write_text("PRIVATE_ENTITY_SENTINEL", encoding="utf-8")
    body = (FIXTURES / "enacting.xml").read_bytes()
    declaration = f'<!DOCTYPE html [<!ENTITY leak SYSTEM "{secret.as_uri()}">]>'
    body = body.replace(b"<!DOCTYPE html>", declaration.encode(), 1)
    assert b"Denne paragrafen" in body
    body = body.replace(b"Denne paragrafen", b"&leak; Denne paragrafen", 1)
    document = parse_document(body, "entity.xml")
    assert "PRIVATE_ENTITY_SENTINEL" not in document.model_dump_json()


@pytest.mark.parametrize("body", [b"<html>", b"<html>\xff</html>"])
def test_invalid_xml_names_the_member_and_preserves_the_parse_error(body: bytes) -> None:
    with pytest.raises(LegalTimeDocumentError, match="invalid XML in broken.xml") as error:
        parse_document(body, "broken.xml")
    assert isinstance(error.value.__cause__, etree.XMLSyntaxError)


def test_inner_xml_escapes_text_so_it_stays_well_formed() -> None:
    element = etree.fromstring("<a>Fish &amp; chips &lt; 5<b>x</b></a>")
    assert inner_xml(element) == "Fish &amp; chips &lt; 5<b>x</b>"
    etree.fromstring(f"<article>{inner_xml(element)}</article>")


def test_entity_amplification_is_refused_with_the_member_name() -> None:
    # A small input declares exponentially growing replacements. Never construct
    # the expanded value: the shared hardened parser must reject it first.
    entities = ['<!ENTITY e0 "sentinel">']
    for level in range(1, 8):
        references = f"&e{level - 1};" * 10
        entities.append(f'<!ENTITY e{level} "{references}">')
    declaration = f"<!DOCTYPE html [{' '.join(entities)}]>".encode()
    body = (FIXTURES / "enacting.xml").read_bytes()
    body = body.replace(b"<!DOCTYPE html>", declaration, 1)
    body = body.replace(b"Denne paragrafen", b"&e7; Denne paragrafen", 1)

    with pytest.raises(LegalTimeDocumentError, match="invalid XML in amplification.xml") as error:
        parse_document(body, "amplification.xml")
    assert isinstance(error.value.__cause__, etree.XMLSyntaxError)


def test_excessive_xml_depth_is_refused_before_reading_provisions() -> None:
    body = (FIXTURES / "enacting.xml").read_bytes()
    nested = b"<span>" * 300 + b"sentinel" + b"</span>" * 300
    body = body.replace(b"Denne paragrafen", nested, 1)

    with pytest.raises(LegalTimeDocumentError, match="invalid XML in deep.xml") as error:
        parse_document(body, "deep.xml")
    assert isinstance(error.value.__cause__, etree.XMLSyntaxError)
