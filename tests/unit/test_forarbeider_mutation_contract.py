"""Observable metadata formatting and refusal diagnostics."""

import json
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.forarbeider.commands import metadata_impl
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.identity import parse_reference, parse_source_url
from lovspor.forarbeider.models import ForarbeidRecord, metadata_text
from lovspor.forarbeider.page import build_record, read_proposition
from tests.unit.test_forarbeider_models import _fields
from tests.unit.test_forarbeider_page import (
    L_URL,
    RETRIEVED,
    _l_html,
)


@pytest.mark.parametrize(
    ("old", "new", "reason", "detail"),
    [
        ('content="Proposisjon"', 'content="NOU"', "unsupported_document_type", "DC.Type"),
        ('name="DC.Type"', 'name="other"', "dom_changed", "DC.Type"),
        (
            '<meta name="DC.Type" content="Proposisjon">',
            '<meta name="DC.Type" content="Proposisjon"><meta name="DC.Type" content="NOU">',
            "conflicting_identity",
            "DC.Type",
        ),
        (
            'property="og:url" content="' + L_URL,
            'property="og:url" content="' + L_URL + "other",
            "conflicting_identity",
            "og:url",
        ),
        (
            'name="DC.Identifier.URL" content="/no/',
            'name="DC.Identifier.URL" content="/other/',
            "conflicting_identity",
            "DC.Identifier.URL",
        ),
        ('class="content-header"', 'class="other"', "dom_changed", "content-header"),
        ("<h1>Prop. 901 L (2099&#x2013;2100)</h1>", "", "dom_changed", "h1"),
        ("<h1>Prop. 901 L", "<h1>Prop. 902 L", "conflicting_identity", "h1"),
        (
            'property="og:title" content="Prop. 901 L',
            'property="og:title" content="Prop. 902 L',
            "conflicting_identity",
            "og:title",
        ),
        (
            'name="DC.Title" content="Prop. 901 L',
            'name="DC.Title" content="Prop. 902 L',
            "conflicting_identity",
            "DC.Title",
        ),
        (
            " - Strukturell testproposisjon om et oppdiktet lovforslag",
            "",
            "title_missing",
            "DC.Title",
        ),
        (
            "Strukturell testproposisjon om et oppdiktet lovforslag</h2>",
            "Other</h2>",
            "conflicting_identity",
            "subheader",
        ),
        (
            'name="DC.Creator" content="Testdepartementet"',
            'name="DC.Creator" content="Other"',
            "conflicting_identity",
            "ministry",
        ),
        (
            '<div><a href="/no/dep/test/id1/">Testdepartementet</a></div>',
            "<div><a>A</a><a>B</a></div>",
            "conflicting_identity",
            "content-owner",
        ),
        (
            '<nav class="longdoc-nav">',
            '<ul data-longdoc-table-of-content></ul><nav class="longdoc-nav">',
            "dom_changed",
            "chapter list",
        ),
    ],
)
def test_page_refusal_identifies_the_failed_field(
    old: str, new: str, reason: str, detail: str
) -> None:
    source = _l_html()
    assert old in source
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_proposition(source.replace(old, new).encode(), L_URL + "?ch=1")
    assert caught.value.reason == RefusalReason(reason)
    assert caught.value.detail == detail
    assert str(caught.value) == f"{reason}: {detail}"


@pytest.mark.parametrize("payload", [b"", b"\xff", b"   "])
def test_unreadable_page_diagnostic(payload: bytes) -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_proposition(payload, L_URL + "?ch=1")
    assert str(caught.value) == "unreadable_page: saved page"


def test_invalid_record_timestamp_reports_source() -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        build_record(_l_html().encode(), L_URL + "?ch=1", datetime(2100, 1, 1))
    assert caught.value.reason is RefusalReason.SCHEMA_INVALID
    assert caught.value.detail == L_URL + "?ch=1"


def test_chapter_validation_explains_source_order() -> None:
    fields = _fields()
    fields["chapters"][0]["url"] = L_URL + "?ch=2"
    with pytest.raises(ValidationError) as caught:
        ForarbeidRecord.model_validate(fields)
    assert caught.value.errors()[0]["msg"] == (
        "Value error, chapters are not this document's, in source order"
    )


def test_metadata_uses_two_space_indentation() -> None:
    text = metadata_text(ForarbeidRecord.model_validate(_fields()))
    assert text == json.dumps(json.loads(text), sort_keys=True, indent=2, ensure_ascii=False) + "\n"


@pytest.mark.parametrize(
    ("url", "reason", "detail"),
    [
        ("https://[bad/", "invalid_source_url", "https://[bad/"),
        (
            L_URL.replace("https:", "http:").replace("prop.-901-l-20992100", "nou-2100-1"),
            "invalid_source_url",
            L_URL.replace("https:", "http:").replace("prop.-901-l-20992100", "nou-2100-1"),
        ),
        (
            L_URL.replace("/no/dokumenter/", "/no/tema/"),
            "invalid_source_url",
            L_URL.replace("/no/dokumenter/", "/no/tema/"),
        ),
        (L_URL + "\n", "invalid_source_url", L_URL + "\n"),
        (L_URL + "#x", "invalid_source_url", L_URL + "#x"),
        (
            L_URL.replace("20992100", "20992101"),
            "invalid_source_url",
            L_URL.replace("20992100", "20992101"),
        ),
        (
            L_URL.replace("prop.-901-l-20992100", "nou-2100-1"),
            "unsupported_document_type",
            "nou-2100-1",
        ),
        (L_URL + "?ch=x", "unexpected_query", "ch=x"),
    ],
)
def test_url_refusal_preserves_diagnostic(url: str, reason: str, detail: str) -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        parse_source_url(url)
    assert str(caught.value) == f"{reason}: {detail}"


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("NOU", "reference_missing: official reference"),
        ("Prop. 1 S (2099\u20132100)", "unsupported_document_type: S"),
        ("Prop. 1 L (2099\u20132101)", "conflicting_identity: 2099-2101"),
    ],
)
def test_reference_refusal_preserves_diagnostic(reference: str, expected: str) -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        parse_reference(reference)
    assert str(caught.value) == expected


@pytest.mark.parametrize("timestamp", ["invalid", "2100-01-01T00:00:00"])
def test_timestamp_refusal_preserves_input(tmp_path: Path, timestamp: str) -> None:
    output = tmp_path / "record.json"
    with pytest.raises(ForarbeiderRefusedError) as caught:
        metadata_impl(tmp_path / "absent.html", L_URL + "?ch=1", timestamp, output)
    assert str(caught.value) == f"invalid_timestamp: {timestamp}"
    assert not output.exists()


def test_external_entities_do_not_supply_metadata(tmp_path: Path) -> None:
    secret = tmp_path / "entity.txt"
    secret.write_text("Proposisjon", encoding="utf-8")
    for location in [secret.as_uri(), "http://127.0.0.1:9/entity.dtd"]:
        source = '<!DOCTYPE html SYSTEM "' + location + '">' + _l_html()
        facts = read_proposition(source.encode(), L_URL + "?ch=1")
        assert facts.title == build_record(_l_html().encode(), L_URL + "?ch=1", RETRIEVED).title
        source = (
            '<!DOCTYPE html [<!ENTITY external SYSTEM "'
            + location
            + '">]>'
            + _l_html().replace('content="Proposisjon"', 'content="&external;"')
        )
        with pytest.raises(ForarbeiderRefusedError):
            read_proposition(source.encode(), L_URL + "?ch=1")
