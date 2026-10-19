"""A version whose primary source is a received document (ADR-0016 A5, slice I2).

The observed rendering is pinned in ``test_promotion_render.py``; these tests
pin the received one: ``observed_at_first`` and ``source_url`` are replaced in
place by the received axis, and nothing in the front matter points at a URL.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
import yaml
from pydantic import ValidationError

from lovspor.promotion import (
    Authority,
    AuthorityType,
    ExtractedDocument,
    LocalDocument,
    MintedIdentity,
    ReceivedSource,
    mint_identity,
)
from lovspor.promotion.extract import extract_regulation
from lovspor.promotion.models import ReceivedForm
from lovspor.promotion.render import render_local_regulation
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page

FJALER = Authority(id="4646", type=AuthorityType.KOMMUNE, name="Fjaler", klass_version="2024")
REQUEST = "4646-20261005T074425Z"
RECEIVED_FRONT_MATTER_KEYS = (
    "id",
    "slug",
    "type",
    "ref_id",
    "title",
    "authority",
    "hjemmel",
    "vedtatt",
    "vedtatt_av",
    "ikraft",
    "ikraft_text",
    "version",
    "content_hash",
    "received_at",
    "innsyn_request",
    "innsyn_saksnummer",
    "received_form",
    "source_sha256",
    "source_provider",
    "source_license",
    "basis",
    "asserted",
    "language",
)


def _source(saksnummer: str | None = None) -> ReceivedSource:
    return ReceivedSource(
        received_at=datetime(2026, 10, 7, 14, 45, 6, tzinfo=timezone(timedelta(hours=2))),
        innsyn_request=REQUEST,
        innsyn_saksnummer=saksnummer,
        source_sha256="cd" * 32,
        received_form=ReceivedForm.LOVDATA_PRINT,
    )


def _document(source: ReceivedSource) -> LocalDocument:
    extracted = extract_regulation(html_page(REGULATION_LINES), "text/html; charset=utf-8")
    assert isinstance(extracted, ExtractedDocument), extracted
    identity = mint_identity(extracted.regulation, FJALER)
    assert isinstance(identity, MintedIdentity), identity
    return LocalDocument(
        identity=identity, extracted=extracted, slug="renovasjon", version=1, source=source
    )


def _front_matter(markdown: str) -> tuple[tuple[str, ...], dict[str, object]]:
    head = markdown.split("---\n")[1]
    keys = tuple(line.split(":", 1)[0] for line in head.splitlines())
    return keys, yaml.safe_load(head)


def test_received_rendering_is_byte_identical_across_runs() -> None:
    first = render_local_regulation(_document(_source()))
    assert render_local_regulation(_document(_source())).encode() == first.encode()


def test_the_received_axis_replaces_the_observed_keys_in_place() -> None:
    keys, _ = _front_matter(render_local_regulation(_document(_source())))
    assert keys == RECEIVED_FRONT_MATTER_KEYS


def test_received_provenance_values() -> None:
    _, values = _front_matter(render_local_regulation(_document(_source("2026/2348"))))
    provenance = {key: values[key] for key in RECEIVED_FRONT_MATTER_KEYS[13:]}
    assert provenance == {
        "received_at": "2026-10-07T12:45:06Z",
        "innsyn_request": REQUEST,
        "innsyn_saksnummer": "2026/2348",
        "received_form": "lovdata_print",
        "source_sha256": "cd" * 32,
        "source_provider": "Fjaler kommune (received under offentleglova)",
        "source_license": "åndsverkloven § 14",
        "basis": "received",
        "asserted": False,
        "language": "no",
    }


def test_a_request_without_a_saksnummer_writes_null() -> None:
    markdown = render_local_regulation(_document(_source()))
    assert "\ninnsyn_saksnummer: null\n" in markdown


def test_no_observed_field_and_no_url_on_a_received_version() -> None:
    head = render_local_regulation(_document(_source())).split("---\n")[1]
    assert "observed_at_first" not in head
    assert "source_url" not in head
    assert "http" not in head
    assert "(observed)" not in head


def test_received_at_is_written_in_utc() -> None:
    assert _source().received_at_utc == "2026-10-07T12:45:06Z"
    utc = _source().model_copy(update={"received_at": datetime(2026, 10, 7, 12, 45, 6, tzinfo=UTC)})
    assert utc.received_at_utc == "2026-10-07T12:45:06Z"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("innsyn_request", "4646-2026-10-05"),
        ("innsyn_saksnummer", "2026/2348 KJN"),
        ("innsyn_saksnummer", "Ola Nordmann"),
        ("source_sha256", "CD" * 32),
        ("received_at", datetime(2026, 10, 7, 12, 45, 6)),
        ("received_form", "lovdata"),
        ("received_form", None),
    ],
)
def test_a_received_source_refuses_a_malformed_field(field: str, value: object) -> None:
    fields = _source().model_dump() | {field: value}
    with pytest.raises(ValidationError):
        ReceivedSource.model_validate(fields)


def test_a_received_source_refuses_observed_fields() -> None:
    fields = _source().model_dump() | {"source_url": "https://www.fjaler.kommune.no/"}
    with pytest.raises(ValidationError):
        ReceivedSource.model_validate(fields)


def test_a_received_source_needs_its_form() -> None:
    fields = _source().model_dump(exclude={"received_form"})
    with pytest.raises(ValidationError):
        ReceivedSource.model_validate(fields)


@pytest.mark.parametrize("form", list(ReceivedForm))
def test_the_received_form_follows_the_saksnummer(form: ReceivedForm) -> None:
    source = _source().model_copy(update={"received_form": form})
    markdown = render_local_regulation(_document(source))
    assert f'\ninnsyn_saksnummer: null\nreceived_form: "{form.value}"\nsource_sha256: ' in markdown
