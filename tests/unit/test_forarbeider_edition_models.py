"""The edition-evidence sidecar refuses every claim its parser could not have made (#784)."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider.edition_models import (
    MAX_EVIDENCE_BYTES,
    EditionEvidence,
    evidence_text,
    read_edition_evidence,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason

CANONICAL = "https://www.regjeringen.no/no/dokumenter/prop.-900-ls-20992100/id9000002/"
ASSETS = "https://www.regjeringen.no/contentassets/00000000000000000000000000000002/no"


def _valid() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "record_type": "forarbeid-edition-evidence",
        "id": "regjeringen-9000002",
        "document_type": "prop-ls",
        "session": "2099-2100",
        "proposition_number": 900,
        "source_document_id": 9000002,
        "source_url": CANONICAL,
        "landing_page_url": CANONICAL,
        "landing_retrieved_at": "2100-05-20T10:15:00+02:00",
        "landing_sha256": "a" * 64,
        "landing_provenance": "operator_saved_offline",
        "parser_version": 1,
        "edition": {
            "kind": "corrected",
            "corrected_on": "2100-05-13",
            "original_edition_url": f"{ASSETS}/opri/a.pdf",
            "rettebrev_urls": [f"{ASSETS}/rett/rettebrev.pdf"],
        },
        "chapter_record": {
            "source_page_url": f"{CANONICAL}?ch=1",
            "source_sha256": "b" * 64,
            "retrieved_at": "2100-04-01T12:00:00Z",
        },
        "chapter_edition": "unknown",
    }


def _with(path: str, value: object) -> dict[str, Any]:
    payload = _valid()
    target = payload
    *parents, last = path.split(".")
    for key in parents:
        target = target[key]
    target[last] = value
    return payload


def test_the_valid_evidence_round_trips_byte_for_byte(tmp_path: Path) -> None:
    evidence = EditionEvidence.model_validate(_valid())
    path = tmp_path / "e.json"
    path.write_text(evidence_text(evidence), encoding="utf-8")
    path.chmod(0o600)

    assert evidence_text(read_edition_evidence(path)) == path.read_text(encoding="utf-8")
    assert read_edition_evidence(path).landing_retrieved_at.utcoffset() is not None


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("edition.kind", "original"),
        ("edition.corrected_on", None),
        ("edition.original_edition_url", None),
        ("edition.rettebrev_urls", []),
        ("edition.original_edition_url", "https://example.org/opri/a.pdf"),
        ("edition.rettebrev_urls", [f"{ASSETS}/opri/a.pdf"]),
        ("chapter_edition", "corrected"),
        ("landing_page_url", f"{CANONICAL}?ch=1"),
        ("source_url", "https://www.regjeringen.no/no/dokumenter/prop.-900-ls-20992100/id9000003/"),
        ("id", "regjeringen-9000003"),
        ("session", "2098-2099"),
        ("document_type", "prop-l"),
        ("proposition_number", 901),
        ("source_document_id", 9000003),
        ("chapter_record.source_page_url", CANONICAL),
        (
            "chapter_record.source_page_url",
            "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=1",
        ),
        ("landing_retrieved_at", "2100-05-20T10:15:00"),
        ("landing_sha256", "A" * 64),
        ("landing_provenance", "fetched"),
        ("schema_version", 2),
        ("published_at", None),
    ],
    ids=[
        "original-is-never-claimed",
        "corrected-without-date",
        "corrected-without-original",
        "corrected-without-rettebrev",
        "original-off-site",
        "rettebrev-pointing-at-original",
        "chapter-edition-asserted",
        "landing-is-a-chapter",
        "source-url-of-another-document",
        "id-of-another-document",
        "session-of-another-document",
        "type-of-another-document",
        "number-of-another-document",
        "source-id-of-another-document",
        "chapter-is-the-landing-page",
        "chapter-of-another-document",
        "naive-capture-time",
        "upper-case-sha",
        "provenance-changed",
        "schema-v2",
        "extra-key",
    ],
)
def test_a_claim_the_parser_could_not_make_is_refused(path: str, value: object) -> None:
    with pytest.raises(ValidationError):
        EditionEvidence.model_validate(_with(path, value))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("edition.corrected_on", "2100-05-13"),
        ("edition.original_edition_url", f"{ASSETS}/opri/a.pdf"),
        ("edition.rettebrev_urls", [f"{ASSETS}/rett/rettebrev.pdf"]),
    ],
)
def test_unknown_carries_no_edition_fact(path: str, value: object) -> None:
    payload = _with(
        "edition",
        {
            "kind": "unknown",
            "corrected_on": None,
            "original_edition_url": None,
            "rettebrev_urls": [],
        },
    )
    payload = {**payload, "edition": {**payload["edition"], path.split(".")[1]: value}}

    with pytest.raises(ValidationError):
        EditionEvidence.model_validate(payload)


def test_a_shared_evidence_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "e.json"
    path.write_text(json.dumps(_valid()), encoding="utf-8")
    path.chmod(0o644)

    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_edition_evidence(path)

    assert caught.value.reason is RefusalReason.OBSERVATION_UNSAFE


def test_a_hand_edited_evidence_file_is_refused_on_read(tmp_path: Path) -> None:
    path = tmp_path / "e.json"
    path.write_text(json.dumps(_with("edition.kind", "original")), encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_edition_evidence(path)

    assert caught.value.reason is RefusalReason.SCHEMA_INVALID


@pytest.mark.parametrize("payload", [b"", b'{"schema_version":', b"\xff", b"[]"])
def test_malformed_evidence_is_a_typed_schema_refusal(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "e.json"
    path.write_bytes(payload)
    path.chmod(0o600)

    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_edition_evidence(path)

    assert caught.value.reason is RefusalReason.SCHEMA_INVALID
    assert path.read_bytes() == payload


@pytest.mark.parametrize("extra", [0, 1], ids=["at-limit", "over-limit"])
def test_evidence_read_size_boundary(tmp_path: Path, extra: int) -> None:
    path = tmp_path / "e.json"
    payload = evidence_text(EditionEvidence.model_validate(_valid())).encode("utf-8")
    path.write_bytes(payload + b" " * (MAX_EVIDENCE_BYTES + extra - len(payload)))
    path.chmod(0o600)

    if extra:
        with pytest.raises(ForarbeiderRefusedError) as caught:
            read_edition_evidence(path)
        assert caught.value.reason is RefusalReason.INPUT_TOO_LARGE
    else:
        assert read_edition_evidence(path) == EditionEvidence.model_validate(_valid())
