"""run_sync when a document cannot be rendered (issue #229).

One unrenderable document must never cost the sync any other document: the new,
changed and renamed loops each skip the failure and carry on with the next one.
A changed or renamed failure keeps its prior record so it is retried next sync —
unless another document's action took over the file that record points at, in
which case keeping it would leave two current records at one file, so it is
dropped with a warning and re-added once it renders. Rendering, the manifest and
git are real here; only the upstream download is replaced, and the sync is
keyless. The unrenderable document is well-formed XML carrying block-level text
the renderer refuses to drop, so the attestation gate can still read it.
"""

import hashlib
import logging
from dataclasses import replace
from pathlib import Path

import pytest

from lovspor.settings import Settings
from lovspor.sync.orchestrator import _UpstreamDoc, run_sync
from lovspor.temporal_attestation import AttestationError
from tests.unit.test_sync_orchestrator_actions import (
    _ALFA,
    _BETA,
    _body,
    _corpus,
    _doc,
    _serve,
    _stored,
    _tracked,
)

_GAMMA = _doc("lov-3", "gamma", "Gamma text.")
_UNRENDERABLE_XML = (
    b'<!DOCTYPE html><html lang="nb"><head><title>Lov</title></head>'
    b'<body><header class="documentHeader"><dl>'
    b'<dt class="title">Tittel</dt><dd class="title">Lov</dd>'
    b'<dt class="refid">RefID</dt><dd class="refid">lov/x</dd>'
    b'</dl></header><main id="dokument"><h1>Lov</h1>Stray block-level text.'
    b'<article class="legalP" id="ledd-1">Body.</article></main></body></html>'
)


_DROPPED = (
    "dropping {doc_id} this sync: its file {path} was taken over by another "
    "document; it will be re-added once it renders on a future sync"
)


def _unrenderable(doc: _UpstreamDoc, *, slug: str, xml_hash: str | None = None) -> _UpstreamDoc:
    """``doc`` served under ``slug`` as XML the renderer refuses."""
    return replace(
        doc,
        slug=slug,
        xml_bytes=_UNRENDERABLE_XML,
        xml_hash=xml_hash or doc.xml_hash,
    )


def _changed_hash(doc: _UpstreamDoc) -> str:
    return hashlib.sha256(doc.xml_hash.encode()).hexdigest()


def _markdown_path(settings: Settings, doc_id: str) -> str:
    return _stored(settings)[doc_id].markdown_path


def test_an_unrenderable_new_doc_does_not_stop_the_next_new_doc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path, [_ALFA])
    newcomer = _doc("lov-3", "gamma", "Newcomer text.")
    _serve(monkeypatch, [_ALFA, _unrenderable(_BETA, slug="beta"), newcomer])

    report = run_sync(settings)

    assert report.new_count == 2
    stored = _stored(settings)
    assert "lov-2" not in stored
    assert stored["lov-3"].markdown_path == "lover/gamma.md"
    assert "Newcomer text." in _body(settings, "gamma")
    assert "lover/gamma.md" in _tracked(settings.lovverk_repo_path)
    assert not (settings.lovverk_repo_path / "lover" / "beta.md").exists()


def test_an_unrenderable_changed_doc_does_not_stop_the_next_changed_doc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path, [_ALFA, _BETA])
    broken = _unrenderable(_ALFA, slug="alfa", xml_hash=_changed_hash(_ALFA))
    _serve(monkeypatch, [broken, _doc("lov-2", "beta", "Changed beta.")])

    # The failed doc is carried forward against a newer source, so the run
    # commits and then refuses to attest — the commit is what is checked here.
    with pytest.raises(AttestationError, match="carried-forward document"):
        run_sync(settings)

    stored = _stored(settings)
    assert stored["lov-1"].xml_hash == _ALFA.xml_hash
    assert stored["lov-2"].xml_hash == _doc("lov-2", "beta", "Changed beta.").xml_hash
    assert "Changed beta." in _body(settings, "beta")
    assert "Alfa text." in _body(settings, "alfa")


def test_an_unrenderable_rename_keeps_its_prior_record_and_the_next_rename_moves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path, [_ALFA, _BETA])
    repo = settings.lovverk_repo_path
    _serve(monkeypatch, [_unrenderable(_ALFA, slug="gamma"), replace(_BETA, slug="delta")])

    run_sync(settings)

    assert _markdown_path(settings, "lov-1") == "lover/alfa.md"
    assert _markdown_path(settings, "lov-2") == "lover/delta.md"
    assert "Alfa text." in _body(settings, "alfa")
    assert "Beta text." in _body(settings, "delta")
    assert not (repo / "lover" / "gamma.md").exists()
    assert not (repo / "lover" / "beta.md").exists()


def test_a_failed_doc_whose_file_another_doc_took_is_dropped_and_the_next_is_kept(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _corpus(tmp_path, [_ALFA, _BETA, _GAMMA])
    _serve(
        monkeypatch,
        [
            _unrenderable(_ALFA, slug="alfa", xml_hash=_changed_hash(_ALFA)),
            replace(_BETA, slug="alfa"),
            _unrenderable(_GAMMA, slug="gamma", xml_hash=_changed_hash(_GAMMA)),
        ],
    )

    with (
        caplog.at_level(logging.WARNING, logger="lovspor.sync.orchestrator"),
        pytest.raises(AttestationError, match="carried-forward document"),
    ):
        run_sync(settings)

    stored = _stored(settings)
    assert "lov-1" not in stored
    assert stored["lov-2"].markdown_path == "lover/alfa.md"
    assert stored["lov-3"].xml_hash == _GAMMA.xml_hash
    assert "Beta text." in _body(settings, "alfa")
    assert _DROPPED.format(doc_id="lov-1", path="lover/alfa.md") in caplog.messages


def test_a_failed_rename_whose_file_another_doc_took_is_dropped_from_the_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A rename is found among unchanged docs, so its prior record is already
    # carried before the loops run; announcing the drop is not enough (#421).
    # lov-3's failed change is reconciled first and kept, before lov-1's drop.
    settings = _corpus(tmp_path, [_ALFA, _BETA, _GAMMA])
    _serve(
        monkeypatch,
        [
            _unrenderable(_ALFA, slug="delta"),
            replace(_BETA, slug="alfa"),
            _unrenderable(_GAMMA, slug="gamma", xml_hash=_changed_hash(_GAMMA)),
        ],
    )

    with (
        caplog.at_level(logging.WARNING, logger="lovspor.sync.orchestrator"),
        pytest.raises(AttestationError, match="carried-forward document"),
    ):
        run_sync(settings)

    stored = _stored(settings)
    assert "lov-1" not in stored
    assert stored["lov-3"].xml_hash == _GAMMA.xml_hash
    assert stored["lov-2"].markdown_path == "lover/alfa.md"
    assert "Beta text." in _body(settings, "alfa")
    assert _DROPPED.format(doc_id="lov-1", path="lover/alfa.md") in caplog.messages


def test_a_failed_rename_whose_file_a_new_doc_took_is_dropped_from_the_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # New-document writes happen before the failed rename is reconciled.  The
    # rename's record was already carried as unchanged, so it must be removed
    # when the new document claims its old path just as it is for a takeover by
    # another rename.
    settings = _corpus(tmp_path, [_ALFA])
    newcomer = _doc("lov-2", "alfa", "Newcomer text.")
    _serve(monkeypatch, [_unrenderable(_ALFA, slug="delta"), newcomer])

    run_sync(settings)

    stored = _stored(settings)
    assert "lov-1" not in stored
    assert stored["lov-2"].markdown_path == "lover/alfa.md"
    assert "Newcomer text." in _body(settings, "alfa")
    assert _DROPPED.format(doc_id="lov-1", path="lover/alfa.md") in caplog.messages
