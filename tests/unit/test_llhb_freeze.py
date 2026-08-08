"""Freeze artifacts: cited-document pins, lock shape, checksum stability."""

import hashlib

import pytest

from lovspor.errors import LovsporError
from lovspor.llhb.corpus_pin import CorpusPin
from lovspor.llhb.freeze import build_lock, cited_slugs
from lovspor.llhb.schema import canonical_jsonl
from lovspor.storage.manifest import Manifest
from tests.unit.llhb_fixtures import GENERATED_AT, record_for

_PIN = CorpusPin(lovverk_commit="c" * 40, manifest_generated_at=GENERATED_AT)


def _manifest(*slugs: str) -> Manifest:
    records = {f"nl-{i}": record_for(slug, f"Lov om {slug}") for i, slug in enumerate(slugs)}
    return Manifest(generated_at=GENERATED_AT, documents=records)


def _cases() -> list[dict]:
    return [
        {
            "case_id": "llhb-v1-C1-101",
            "category": "C1",
            "expected_act_slug": "alfaloven",
            "claimed_act_slug": None,
            "quote_ref": None,
        },
        {
            "case_id": "llhb-v1-C4-101",
            "category": "C4",
            "expected_act_slug": "betaloven",
            "claimed_act_slug": "gammaloven",
            "quote_ref": None,
        },
        {
            "case_id": "llhb-v1-C7-101",
            "category": "C7",
            "expected_act_slug": "deltaloven",
            "claimed_act_slug": None,
            "quote_ref": {"slug": "deltaloven", "section_id": "1"},
        },
    ]


def test_cited_slugs_covers_expected_claimed_and_quote_refs() -> None:
    assert cited_slugs(_cases()) == {"alfaloven", "betaloven", "gammaloven", "deltaloven"}


def test_build_lock_pins_every_cited_document() -> None:
    manifest = _manifest("alfaloven", "betaloven", "gammaloven", "deltaloven")
    lock = build_lock(
        _cases(),
        manifest,
        _PIN,
        lovspor_commit="d" * 40,
        selection_rule="benchmarks/llhb/SELECTION.md@" + "d" * 40,
        timestamp="2026-08-08T12:00:00+00:00",
    )
    assert lock["corpus_pin"]["lovverk_commit"] == "c" * 40
    assert lock["case_count"] == 3
    docs = lock["documents"]
    assert set(docs) == {"alfaloven", "betaloven", "gammaloven", "deltaloven"}
    entry = docs["alfaloven"]
    assert entry == {
        "xml_hash": "a" * 64,
        "renderer_version": 8,
        "embedding_space_id": "test-space",
        "embedding_hash": "a" * 64,
    }
    expected_checksum = hashlib.sha256(canonical_jsonl(_cases())).hexdigest()
    assert lock["dataset_sha256"] == expected_checksum
    assert lock["selection_rule"].startswith("benchmarks/llhb/SELECTION.md@")


def test_build_lock_fails_closed_on_uncited_or_missing_document() -> None:
    manifest = _manifest("alfaloven")  # betaloven/gammaloven/deltaloven absent
    with pytest.raises(LovsporError, match="betaloven"):
        build_lock(
            _cases(),
            manifest,
            _PIN,
            lovspor_commit="d" * 40,
            selection_rule="ref",
            timestamp="2026-08-08T12:00:00+00:00",
        )


def test_build_lock_checksum_is_order_independent() -> None:
    manifest = _manifest("alfaloven", "betaloven", "gammaloven", "deltaloven")
    kwargs = {
        "lovspor_commit": "d" * 40,
        "selection_rule": "ref",
        "timestamp": "2026-08-08T12:00:00+00:00",
    }
    lock_a = build_lock(_cases(), manifest, _PIN, **kwargs)
    lock_b = build_lock(list(reversed(_cases())), manifest, _PIN, **kwargs)
    assert lock_a["dataset_sha256"] == lock_b["dataset_sha256"]
