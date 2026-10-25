"""Tests for lovspor.parsing.xml_normalizer.

The two key invariants under test:
1. Canonicalization is deterministic — same input → identical bytes.
2. Semantically equivalent XMLs produce the same hash.
"""

import re
from io import BytesIO
from pathlib import Path

import pytest
from lxml import etree

from lovspor.errors import ParseError
from lovspor.parsing import xml_normalizer
from lovspor.parsing.xml_normalizer import (
    canonicalize_xml,
    hash_normalized_xml,
    safe_parser,
)

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


@pytest.mark.parametrize("xml", [b"<dd/>", b'<dd class=""/>', b'<dd class=" \t\n"/>'])
def test_classes_returns_no_tokens_without_class_names(xml: bytes) -> None:
    element = etree.fromstring(xml)

    assert xml_normalizer._classes(element) == []


def test_identity_removal_preserves_nested_content_and_content_hash() -> None:
    xml = (
        b'<lov><dl class="data-document-key-info">'
        b'<dt class="eli"><span>ELI</span></dt>'
        b'<dd class="eli"><a href="/eli/lov/2000/1">identifier</a></dd>'
        b'<dd class="title"><strong>Law</strong></dd></dl>'
        b"<p>Content <em>remains</em>.</p></lov>"
    )
    expected = (
        b'<lov><dl class="data-document-key-info">'
        b'<dd class="title"><strong>Law</strong></dd></dl>'
        b"<p>Content <em>remains</em>.</p></lov>"
    )
    assert canonicalize_xml(xml) == expected
    assert hash_normalized_xml(xml) == hash_normalized_xml(expected)


def test_canonicalize_is_deterministic_across_calls() -> None:
    xml = b"<lov><paragraph>x</paragraph></lov>"
    assert canonicalize_xml(xml) == canonicalize_xml(xml)


def test_canonicalize_normalizes_attribute_order() -> None:
    a = b'<element b="2" a="1"/>'
    b = b'<element a="1" b="2"/>'
    assert canonicalize_xml(a) == canonicalize_xml(b)


def test_canonicalize_normalizes_self_closing_tags() -> None:
    a = b"<element/>"
    b = b"<element></element>"
    assert canonicalize_xml(a) == canonicalize_xml(b)


def test_safe_parser_preserves_blank_tail_when_disabled() -> None:
    """The renderer parses with remove_blank_text=False so the significant
    whitespace-only tail between two inline elements survives; the default
    (hashing) parser strips it. This strip-vs-preserve mismatch is the
    accepted change-detection gap documented in decisions.md §14 — a
    whitespace-only inline change moves the render but not the hash."""
    xml = b"<r><b>x</b> <b>y</b></r>"

    preserved = etree.parse(BytesIO(xml), parser=safe_parser(remove_blank_text=False)).getroot()
    stripped = etree.parse(BytesIO(xml), parser=safe_parser()).getroot()

    assert preserved[0].tail == " "
    assert stripped[0].tail is None


def test_canonicalize_normalizes_inter_element_whitespace() -> None:
    """HIGH regression guard: pretty-printed vs compact XML with
    identical content must produce identical canonical bytes. Codex
    review of PR #8 flagged this as a broken change-detection contract."""
    compact = b"<root><a>1</a><b>2</b></root>"
    pretty = b"<root>\n  <a>1</a>\n  <b>2</b>\n</root>"
    assert canonicalize_xml(compact) == canonicalize_xml(pretty)


def test_canonicalize_normalizes_tabs_and_newlines() -> None:
    compact = b"<r><x/><y/></r>"
    tabbed = b"<r>\n\t<x/>\n\t<y/>\n</r>"
    assert canonicalize_xml(compact) == canonicalize_xml(tabbed)


def test_canonicalize_preserves_significant_whitespace_in_text() -> None:
    """Whitespace inside element text IS content, not formatting. Only
    whitespace-only nodes *between* elements is stripped."""
    xml = b"<p>hello   world</p>"
    result = canonicalize_xml(xml)
    assert b"hello   world" in result


def test_canonicalize_strips_comments() -> None:
    """Comments are stripped at parse time to protect against hash churn
    from volatile upstream comments (timestamps, build metadata, etc.).
    Documents with and without comments hash identically."""
    with_comment = b"<root><!-- generated 2026-04-22 --><a>1</a></root>"
    without = b"<root><a>1</a></root>"
    assert canonicalize_xml(with_comment) == canonicalize_xml(without)


def test_hash_normalized_xml_same_for_pretty_vs_compact() -> None:
    compact = b"<root><a>1</a><b>2</b></root>"
    pretty = b"<root>\n  <a>1</a>\n  <b>2</b>\n</root>"
    assert hash_normalized_xml(compact) == hash_normalized_xml(pretty)


def test_hash_normalized_xml_unaffected_by_comments() -> None:
    with_c = b"<root><!-- volatile 2026-04-22T10:00 --><a>1</a></root>"
    without_c = b"<root><a>1</a></root>"
    assert hash_normalized_xml(with_c) == hash_normalized_xml(without_c)


def test_canonicalize_preserves_norwegian_utf8() -> None:
    xml = "<lov>æøå Æ Ø Å</lov>".encode()
    result = canonicalize_xml(xml)
    assert "æøå Æ Ø Å".encode() in result


def test_canonicalize_strips_xml_declaration() -> None:
    """C14N output never includes the <?xml ?> declaration."""
    xml = b'<?xml version="1.0" encoding="UTF-8"?><root/>'
    result = canonicalize_xml(xml)
    assert not result.startswith(b"<?xml")


def test_canonicalize_raises_parse_error_on_malformed_xml() -> None:
    with pytest.raises(ParseError) as exc_info:
        canonicalize_xml(b"not <xml")
    assert str(exc_info.value).startswith("malformed XML: ")


def test_canonicalize_parse_error_preserves_cause() -> None:
    with pytest.raises(ParseError) as exc_info:
        canonicalize_xml(b"<unclosed>")
    assert exc_info.value.__cause__ is not None


def test_canonicalize_rejects_documents_with_external_entities(
    tmp_path: Path,
) -> None:
    """XXE defense: a SYSTEM entity must not be fetched and inlined.

    The parser refuses to resolve custom entities and C14N refuses to
    serialize unresolved entity references, so canonicalize_xml raises
    ParseError. The secret file is never read either way — verified by
    checking it was not touched (Python file.stat().st_atime would be
    updated if file:// was fetched, but this is platform-dependent, so
    we just assert ParseError)."""
    secret = tmp_path / "secret.txt"
    secret.write_text("LEAKED_SECRET_CONTENT")
    xml = (
        f'<?xml version="1.0"?>\n'
        f"<!DOCTYPE root [\n"
        f'<!ENTITY xxe SYSTEM "file://{secret.absolute()}">\n'
        f"]>\n"
        f"<root>&xxe;</root>"
    ).encode()
    with pytest.raises(ParseError) as exc_info:
        canonicalize_xml(xml)
    assert "LEAKED_SECRET_CONTENT" not in str(exc_info.value)


def test_safe_parser_disables_network_access(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_parser(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(xml_normalizer.etree, "XMLParser", fake_parser)

    safe_parser()

    assert captured["no_network"] is True


def test_canonicalize_rejects_excessively_deep_xml() -> None:
    """Mutation hardening: huge_tree=False protects against XML bombs
    that exploit deep nesting rather than entity expansion. With our
    safe_parser config (huge_tree=False), lxml caps nesting at ~256
    levels. A mutation flipping huge_tree to True would let the same
    payload through. Codex PR #13 reproducer at depth 256."""
    depth = 300
    deep_xml = (b"<r>" * depth) + (b"</r>" * depth)
    with pytest.raises(ParseError, match="malformed XML"):
        canonicalize_xml(deep_xml)


def test_canonicalize_rejects_billion_laughs_bomb() -> None:
    """Entity expansion bomb does not expand: &lol4; is never resolved
    into its 10000-character payload. ParseError is raised instead."""
    bomb = (
        b'<?xml version="1.0"?>\n'
        b"<!DOCTYPE lolz [\n"
        b'<!ENTITY lol "lol">\n'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
        b'<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">\n'
        b'<!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">\n'
        b"]>\n"
        b"<lolz>&lol4;</lolz>"
    )
    with pytest.raises(ParseError) as exc_info:
        canonicalize_xml(bomb)
    assert str(exc_info.value).startswith("cannot canonicalize XML: ")


def test_canonicalize_preserves_predefined_entity_escape() -> None:
    """Legitimate XML with &amp; (predefined entity, not DTD-defined)
    must still work — XML spec guarantees these five entities without
    requiring a DTD. Regression guard against over-broad entity rejection."""
    xml = b"<p>A &amp; B</p>"
    result = canonicalize_xml(xml)
    assert b"&amp;" in result


def test_canonicalize_handles_predefined_entities_lt_gt() -> None:
    xml = b"<p>&lt;a&gt; &lt;b&gt;</p>"
    result = canonicalize_xml(xml)
    assert b"&lt;" in result
    assert b"&gt;" in result


def test_hash_normalized_xml_is_deterministic() -> None:
    xml = b"<lov><paragraph>content</paragraph></lov>"
    assert hash_normalized_xml(xml) == hash_normalized_xml(xml)


def test_hash_normalized_xml_returns_64_char_hex() -> None:
    digest = hash_normalized_xml(b"<root/>")
    assert _HEX_64.fullmatch(digest) is not None


def test_hash_normalized_xml_same_for_equivalent_inputs() -> None:
    a = b'<element b="2" a="1"/>'
    b = b'<element a="1" b="2"></element>'
    assert hash_normalized_xml(a) == hash_normalized_xml(b)


def test_hash_normalized_xml_differs_for_different_content() -> None:
    a = b"<lov><paragraph>first</paragraph></lov>"
    b = b"<lov><paragraph>second</paragraph></lov>"
    assert hash_normalized_xml(a) != hash_normalized_xml(b)


def test_hash_normalized_xml_propagates_parse_error() -> None:
    with pytest.raises(ParseError):
        hash_normalized_xml(b"not xml")


_HEADER = (
    b'<html><head><title>T</title></head><body><header class="documentHeader">'
    b'<dl class="data-document-key-info">'
    b'<dt class="refid">RefID</dt><dd class="refid">lov/1949-07-08-13</dd>'
    b"%s"
    b'</dl></header><main class="documentBody"><p>%s</p></main></body></html>'
)
_ELI = (
    b'<dt class="eli">European Legislative Identifier</dt>'
    b'<dd class="eli">/eli/lov/1949/07/08/13</dd>'
)


def test_hash_ignores_the_eli_header_field_lovdata_added() -> None:
    """Issue #679: Lovdata added the ELI row to every header on 2026-10-20;
    a hash that saw it called all 5,886 documents changed and the sync timed out."""
    before = _HEADER % (b"", b"Teksten.")
    after = _HEADER % (_ELI, b"Teksten.")

    assert hash_normalized_xml(after) == hash_normalized_xml(before)


def test_hash_still_sees_a_content_change_next_to_the_eli_field() -> None:
    before = _HEADER % (_ELI, b"Teksten.")
    after = _HEADER % (_ELI, b"Endret tekst.")

    assert hash_normalized_xml(after) != hash_normalized_xml(before)


def test_hash_ignores_changes_to_existing_eli_row_text_and_link() -> None:
    """The documented identity exclusion covers row updates as well as insertion."""
    updated_eli = (
        b'<dt class="eli">ELI</dt>'
        b'<dd class="eli"><a href="https://lovdata.no/eli/lov/1949/07/08/13">'
        b"European Legislative Identifier</a></dd>"
    )
    before = _HEADER % (_ELI, b"Teksten.")
    after = _HEADER % (updated_eli, b"Teksten.")

    assert canonicalize_xml(after) == canonicalize_xml(before)
    assert hash_normalized_xml(after) == hash_normalized_xml(before)


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        (b"Teksten.", b"Endret tekst."),
        (b"lov/1949-07-08-13</dd>", b"lov/1949-07-08-14</dd>"),
    ],
    ids=["body-amendment", "other-header-field"],
)
def test_eli_rollout_does_not_mask_simultaneous_content_changes(
    original: bytes, replacement: bytes
) -> None:
    """A real amendment during the #679 rollout must still trigger change detection."""
    before = _HEADER % (b"", b"Teksten.")
    amended_without_eli = before.replace(original, replacement)
    amended_with_eli = (_HEADER % (_ELI, b"Teksten.")).replace(original, replacement)

    assert canonicalize_xml(amended_with_eli) == canonicalize_xml(amended_without_eli)
    assert hash_normalized_xml(amended_with_eli) == hash_normalized_xml(amended_without_eli)
    assert hash_normalized_xml(amended_with_eli) != hash_normalized_xml(before)


def test_hash_still_sees_a_change_in_another_header_field() -> None:
    before = _HEADER % (_ELI, b"Teksten.")
    after = before.replace(b"lov/1949-07-08-13</dd>", b"lov/1949-07-08-14</dd>")

    assert hash_normalized_xml(after) != hash_normalized_xml(before)


def test_hash_ignores_only_the_eli_class_not_classes_containing_it() -> None:
    before = _HEADER % (b"", b"Teksten.")
    after = _HEADER % (b'<dd class="delivery">x</dd>', b"Teksten.")

    assert hash_normalized_xml(after) != hash_normalized_xml(before)


@pytest.mark.parametrize(
    "row_class",
    [b"eli", b"extra eli", b"eli extra", b" extra\teli\nother "],
)
def test_canonicalize_drops_all_eli_rows_with_class_tokens(row_class: bytes) -> None:
    """The identity exclusion applies to both dt/dd and every matching row."""
    rows = (
        b'<dt class="%s">ELI</dt><dd class="%s"><a href="/eli/old">old</a></dd>'
        b'<dt class="eli">ELI again</dt><dd class="eli">/eli/new</dd>'
    ) % (row_class, row_class)
    before = (_HEADER % (b"", b"Teksten.")).replace(
        b'class="data-document-key-info"', b'class="extra data-document-key-info other"'
    )
    after = (_HEADER % (rows, b"Teksten.")).replace(
        b'class="data-document-key-info"', b'class="extra data-document-key-info other"'
    )

    expected = canonicalize_xml(before)
    assert canonicalize_xml(after) == expected
    assert canonicalize_xml(after) == expected
    assert canonicalize_xml(expected) == expected


@pytest.mark.parametrize(
    ("xml", "expected"),
    [
        (b"<root/>", b"<root></root>"),
        (
            b'<root><dl class="data-document-key-info"/></root>',
            b'<root><dl class="data-document-key-info"></dl></root>',
        ),
        (
            b'<root><dl class="data-document-key-info">'
            b'<dt class="eli">ELI</dt><dd class="eli">identifier</dd>'
            b'<dd class="title">Retained</dd></dl></root>',
            b'<root><dl class="data-document-key-info"><dd class="title">Retained</dd></dl></root>',
        ),
    ],
)
def test_identity_row_selection_handles_empty_and_populated_results(
    xml: bytes, expected: bytes
) -> None:
    """Real XPath results may be empty or contain multiple identity rows."""
    assert canonicalize_xml(xml) == expected


def test_hash_ignores_pretty_printed_eli_rows() -> None:
    before = _HEADER % (b"", b"Teksten.")
    after = _HEADER % (b"\n  " + _ELI.replace(b"><", b">\n  <") + b"\n", b"Teksten.")

    assert canonicalize_xml(after) == canonicalize_xml(before)
    assert hash_normalized_xml(after) == hash_normalized_xml(before)


@pytest.mark.parametrize(
    "content",
    [
        b'<dl><dt class="eli">ELI</dt><dd class="eli">%s</dd></dl>',
        b'<dl class="other-data-document-key-info"><dd class="eli">%s</dd></dl>',
        b'<p class="eli">%s</p>',
    ],
)
def test_hash_preserves_eli_content_outside_key_info_rows(content: bytes) -> None:
    """Only the key-info identity rows are excluded, not arbitrary ELI content."""
    before = _HEADER % (_ELI, content % b"original")
    after = _HEADER % (_ELI, content % b"changed")

    assert hash_normalized_xml(after) != hash_normalized_xml(before)


@pytest.mark.parametrize(
    "row",
    [
        b'<div class="eli">%s</div>',
        b'<dd class="refid"><span class="eli">%s</span></dd>',
        b'<dd class="eli-other">%s</dd>',
    ],
)
def test_hash_preserves_non_identity_content_in_key_info(row: bytes) -> None:
    before = _HEADER % (row % b"original", b"Teksten.")
    after = _HEADER % (row % b"changed", b"Teksten.")

    assert hash_normalized_xml(after) != hash_normalized_xml(before)


@pytest.mark.parametrize("tag", [b"dt", b"dd"])
def test_hash_ignores_an_individual_eli_row(tag: bytes) -> None:
    """Each identity row is excluded independently of a matching partner."""
    before = _HEADER % (b"", b"Teksten.")
    row = b'<%s class="eli">identifier</%s>' % (tag, tag)

    assert canonicalize_xml(_HEADER % (row, b"Teksten.")) == canonicalize_xml(before)
    assert hash_normalized_xml(_HEADER % (row, b"Teksten.")) == hash_normalized_xml(before)


def test_canonicalize_removes_eli_rows_from_every_key_info_list() -> None:
    """Selection must not stop after the first matching metadata list."""
    before = (
        b'<root><dl class="data-document-key-info"><dd class="title">First</dd></dl>'
        b'<dl class="data-document-key-info"><dd class="title">Second</dd></dl></root>'
    )
    after = before.replace(b"</dl>", _ELI + b"</dl>")

    assert canonicalize_xml(after) == canonicalize_xml(before)
    assert hash_normalized_xml(after) == hash_normalized_xml(before)


@pytest.mark.parametrize("tag", [b"div", b"section"])
def test_hash_preserves_eli_rows_under_non_dl_key_info_container(tag: bytes) -> None:
    """A matching class alone does not make an element a key-info dl."""
    template = (
        b'<%s class="data-document-key-info"><dt class="eli">ELI</dt><dd class="eli">%%s</dd></%s>'
    ) % (tag, tag)
    before = _HEADER % (_ELI, template % b"original")
    after = _HEADER % (_ELI, template % b"changed")

    assert hash_normalized_xml(after) != hash_normalized_xml(before)


def test_hash_finds_the_key_info_header_after_another_definition_list() -> None:
    other = b'<dl class="glossary"><dt>Ord</dt><dd>Forklaring</dd></dl>'
    before = (_HEADER % (b"", b"Teksten.")).replace(b"<header", other + b"<header")
    after = (_HEADER % (_ELI, b"Teksten.")).replace(b"<header", other + b"<header")

    assert hash_normalized_xml(after) == hash_normalized_xml(before)


def test_an_eli_row_outside_the_key_info_header_still_counts() -> None:
    body_row = b'<dl class="glossary"><dt class="eli">ELI</dt><dd class="eli">/eli/x</dd></dl>'
    before = _HEADER % (b"", b"Teksten.")
    after = before.replace(b"<p>Teksten.</p>", body_row + b"<p>Teksten.</p>")

    assert hash_normalized_xml(after) != hash_normalized_xml(before)


@pytest.mark.parametrize("class_attribute", [b"", b' class=""'])
@pytest.mark.parametrize("unclassified_element", ["container", "row"])
def test_hash_preserves_unclassified_metadata(
    class_attribute: bytes, unclassified_element: str
) -> None:
    container_class = b' class="data-document-key-info"'
    row_class = b' class="eli"'
    if unclassified_element == "container":
        container_class = class_attribute
    else:
        row_class = class_attribute
    template = b"<root><dl%s><dd%s>%%s</dd></dl></root>" % (container_class, row_class)

    before = template % b"original"
    after = template % b"changed"

    assert b"original" in canonicalize_xml(before)
    assert hash_normalized_xml(before) != hash_normalized_xml(after)


def test_hash_preserves_nested_eli_rows_inside_non_identity_metadata() -> None:
    """The exclusion selects direct dt/dd children, not arbitrary descendants."""
    row = b'<dd class="notes"><dl><dd class="eli">%s</dd></dl></dd>'
    before = _HEADER % (_ELI + row % b"original", b"Teksten.")
    after = _HEADER % (_ELI + row % b"changed", b"Teksten.")

    assert b'<dd class="eli">original</dd>' in canonicalize_xml(before)
    assert hash_normalized_xml(before) != hash_normalized_xml(after)


@pytest.mark.parametrize("invalid_content", [b"<unclosed>", b"\xff", b"&undefined;"])
def test_identity_row_exclusion_does_not_hide_invalid_xml(invalid_content: bytes) -> None:
    """Excluded metadata still goes through the normal well-formedness check."""
    xml = _HEADER % (b'<dd class="eli">' + invalid_content + b"</dd>", b"Teksten.")

    with pytest.raises(ParseError, match="malformed XML:"):
        canonicalize_xml(xml)
    with pytest.raises(ParseError, match="malformed XML:"):
        hash_normalized_xml(xml)
