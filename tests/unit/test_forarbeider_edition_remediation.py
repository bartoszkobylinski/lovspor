"""Observable refusal details and evidence layout for the listed edition mutants."""

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from lxml import html

from lovspor.forarbeider.edition import _require_landing_header, read_landing
from lovspor.forarbeider.edition_commands import _record, build_evidence, edition_impl
from lovspor.forarbeider.edition_models import EditionEvidence, evidence_text, read_edition_evidence
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.identity import parse_source_url
from lovspor.forarbeider.models import read_metadata
from tests.unit.test_forarbeider_edition import CORRECTED, LS_URL, NOTICE, _corrected
from tests.unit.test_forarbeider_edition_cli import CAPTURED, L_LANDING, PLAIN, _metadata
from tests.unit.test_forarbeider_edition_models import _valid


@pytest.mark.parametrize(
    ("old", "new", "reason", "detail"),
    [
        ('class="article-header"', 'class="other"', "dom_changed", "article-header"),
        (
            '<h2 class="subheader">',
            '<h2 class="subheader"><button data-show-doctitle="1"></button>',
            "browser_rendered_page",
            "subheader",
        ),
        ('property="og:title"', 'property="other"', "dom_changed", "og:title"),
        ('name="DC.Title"', 'name="other"', "title_missing", "DC.Title"),
        ("<h1>Prop. 900 LS", "<h1>Prop. 902 LS", "conflicting_identity", "h1"),
        (
            'property="og:title" content="Prop. 900 LS',
            'property="og:title" content="Prop. 902 LS',
            "conflicting_identity",
            "og:title",
        ),
        (
            'name="DC.Title" content="Prop. 900 LS',
            'name="DC.Title" content="Prop. 902 LS',
            "conflicting_identity",
            "DC.Title",
        ),
        (NOTICE, NOTICE + NOTICE, "edition_conflicting", "edition notice"),
        (NOTICE, "", "edition_conflicting", "edition notice"),
        ("13.5.2100", "31.2.2100", "edition_unrecognized", "corrected-edition-date"),
        (
            "Opprinnelig utgave og rettebrev</h3>",
            "Other</h3>",
            "edition_unrecognized",
            "edition heading",
        ),
        # An unfamiliar edition list is unrecognised markup, not a list missing
        # its links (GPT-6 review of PR #791: the list is read whole or refused).
        ('class="link-list"', 'class="other"', "edition_unrecognized", "edition block"),
        (
            "/no/opri/prp209921000900000dddopri.pdf",
            "/no/opri/a.docx",
            "edition_unrecognized",
            "edition link",
        ),
        (">Rettebrev 5. mai", ">Other 5. mai", "edition_conflicting", "edition link label"),
        (
            "<footer",
            '<a href="/no/rett/extra.pdf">extra</a><footer',
            "edition_unrecognized",
            "edition link",
        ),
        ("<footer", "<p>Korrigert utgave</p><footer", "edition_unrecognized", "edition wording"),
        (
            'href="/contentassets/00000000000000000000000000000002/no/opri/prp209921000900000dddopri.pdf"',
            "",
            "edition_unrecognized",
            "edition link",
        ),
    ],
)
def test_landing_refusals_identify_the_failed_field(
    old: str, new: str, reason: str, detail: str
) -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_landing(_corrected(old, new), LS_URL)
    assert caught.value.reason.value == reason
    assert caught.value.detail == detail


def test_chapter_refusal_preserves_the_supplied_url() -> None:
    url = f"{LS_URL}?ch=1"
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_landing(CORRECTED.read_bytes(), url)
    assert caught.value.detail == url


def test_dc_title_accepts_a_reference_without_a_subtitle() -> None:
    payload = _corrected(
        " - Strukturell testproposisjon om et oppdiktet lovforslag og samtykke", ""
    )
    assert read_landing(payload, LS_URL)[1].kind == "corrected"


@pytest.mark.parametrize("reader", [_record, read_edition_evidence])
def test_invalid_private_json_names_its_path(
    tmp_path: Path, reader: Callable[[Path], object]
) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        reader(path)
    assert caught.value.reason.value == "schema_invalid"
    assert caught.value.detail == str(path)


def test_association_refusal_names_the_record(tmp_path: Path) -> None:
    record = read_metadata(_metadata(tmp_path, "l"))
    with pytest.raises(ForarbeiderRefusedError) as caught:
        build_evidence(CORRECTED.read_bytes(), LS_URL, datetime.fromisoformat(CAPTURED), record)
    assert caught.value.reason.value == "edition_identity_mismatch"
    assert caught.value.detail == record.id


def test_invalid_evidence_capture_names_the_landing_url(tmp_path: Path) -> None:
    record = read_metadata(_metadata(tmp_path))
    with pytest.raises(ForarbeiderRefusedError) as caught:
        build_evidence(CORRECTED.read_bytes(), LS_URL, datetime(2100, 5, 20), record)
    assert caught.value.reason.value == "schema_invalid"
    assert caught.value.detail == LS_URL


def test_unknown_edition_status_has_no_stated_date(tmp_path: Path) -> None:
    metadata = _metadata(tmp_path, "l")
    status = edition_impl(PLAIN, L_LANDING, CAPTURED, metadata)
    assert status == (
        "wrote regjeringen-9000001: edition unknown, chapter edition unknown, "
        f"to {metadata.with_suffix('.edition.json')}"
    )


def test_evidence_serialization_uses_the_documented_layout() -> None:
    evidence = EditionEvidence.model_validate(_valid())
    expected = (
        json.dumps(evidence.model_dump(mode="json"), sort_keys=True, indent=2, ensure_ascii=False)
        + "\n"
    )
    assert evidence_text(evidence) == expected


@pytest.mark.parametrize("kind", ["ls", "l"])
def test_evidence_preserves_every_document_identity_field(tmp_path: Path, kind: str) -> None:
    record = read_metadata(_metadata(tmp_path, kind))
    payload, url = (
        (CORRECTED.read_bytes(), LS_URL) if kind == "ls" else (PLAIN.read_bytes(), L_LANDING)
    )
    evidence, _ = build_evidence(payload, url, datetime.fromisoformat(CAPTURED), record)
    for field in (
        "document_type",
        "session",
        "proposition_number",
        "source_document_id",
        "source_url",
        "id",
    ):
        assert getattr(evidence, field) == getattr(record, field)


@pytest.mark.parametrize("attribute", [None, "", "XXXX"])
def test_missing_or_invalid_edition_href_has_the_same_typed_refusal(attribute: str | None) -> None:
    original = (
        'href="/contentassets/00000000000000000000000000000002/no/opri/'
        'prp209921000900000dddopri.pdf"'
    )
    replacement = "" if attribute is None else f'href="{attribute}"'
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_landing(_corrected(original, replacement), LS_URL)
    assert caught.value.reason.value == "edition_unrecognized"
    assert caught.value.detail == "edition link"


@pytest.mark.parametrize("field", ["h1", "og:title", "DC.Title"])
def test_landing_header_checks_each_of_its_three_identity_statements(field: str) -> None:
    root = html.fromstring(CORRECTED.read_bytes())
    if field == "h1":
        root.xpath("//h1")[0].text = "Prop. 902 LS (2099\u20132100)"
    else:
        attribute = "property" if field == "og:title" else "name"
        root.xpath(f'//meta[@{attribute}="{field}"]')[0].set(
            "content", "Prop. 902 LS (2099\u20132100)"
        )
    with pytest.raises(ForarbeiderRefusedError) as caught:
        _require_landing_header(root, parse_source_url(LS_URL))
    assert caught.value.reason.value == "conflicting_identity"
    assert caught.value.detail == field
