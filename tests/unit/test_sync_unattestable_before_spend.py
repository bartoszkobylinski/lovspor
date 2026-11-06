"""A keyed sync that will be refused by attestation pays nothing first (#804).

A changed document that cannot render keeps its prior record and old
`xml_hash`, so the ADR-0012 attestation refuses the run after the commit and
before the push. Every other changed document had been embedded by then, so
each night paid for vectors it threw away. The refusal now comes before the
first provider call.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from lovspor.sync.orchestrator import run_sync
from lovspor.temporal_attestation import AttestationError
from tests.unit.test_sync_change_reembed_guard import (
    _corpus,
    _CountingEmbedder,
    _doc,
    _state,
    _upstream,
)


def test_an_unrenderable_changed_document_stops_the_sync_before_any_embedding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _corpus(tmp_path, 3)
    before = _state(settings.lovverk_repo_path)
    embedder = _CountingEmbedder()
    broken = replace(_doc(1), xml_bytes=b"<html", xml_hash="f" * 64)
    _upstream(monkeypatch, [_doc(0, "Body. Amended."), broken, _doc(2, "Body. Amended.")], embedder)

    with pytest.raises(AttestationError, match="lov-1") as refused:
        run_sync(settings)

    assert embedder.texts == 0
    assert _state(settings.lovverk_repo_path) == before
    assert "before any embedding" in str(refused.value)


def test_an_unrenderable_new_document_is_still_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _corpus(tmp_path, 2)
    embedder = _CountingEmbedder()
    broken_new = replace(_doc(5), xml_bytes=b"<html")
    _upstream(monkeypatch, [_doc(0, "Body. Amended."), _doc(1), broken_new], embedder)

    report = run_sync(settings)

    assert report.changed_count == 1
    assert embedder.texts == 1
