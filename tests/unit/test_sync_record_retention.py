"""run_sync never drops a manifest record (#871, ADR-0023 draft points 1 and 5a).

Rendering, the manifest and git are real; only the upstream download is
replaced. The first case is a tombstone whose document reappears upstream but
cannot be rendered: before #871 the tombstone was not carried (the re-add was
expected to replace it), so a failed re-add lost the record and freed its slug.
"""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.errors import ManifestRecordLossError
from lovspor.storage.manifest import ManifestRecord
from lovspor.sync import orchestrator as orchestrator_module
from lovspor.sync.orchestrator import run_sync
from tests.unit.test_sync_orchestrator_actions import _ALFA, _BETA, _corpus, _serve, _stored
from tests.unit.test_sync_orchestrator_render_failures import _UNRENDERABLE_XML


def test_a_failed_re_add_keeps_the_tombstone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path, [_ALFA, _BETA])
    _serve(monkeypatch, [_BETA])
    run_sync(settings)
    tombstone = _stored(settings)["lov-1"]
    _serve(monkeypatch, [replace(_ALFA, xml_bytes=_UNRENDERABLE_XML, xml_hash="f" * 64), _BETA])

    run_sync(settings)

    assert _stored(settings)["lov-1"] == tombstone


def test_a_successful_re_add_replaces_the_tombstone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path, [_ALFA, _BETA])
    _serve(monkeypatch, [_BETA])
    run_sync(settings)
    _serve(monkeypatch, [_ALFA, _BETA])

    run_sync(settings)

    assert _stored(settings)["lov-1"].status == "current"


def test_a_commit_that_would_drop_a_record_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _corpus(tmp_path, [_ALFA, _BETA])
    repo = settings.lovverk_repo_path
    manifest_path = repo / "manifest.json"
    before = manifest_path.read_bytes()
    kept: dict[str, ManifestRecord] = {"lov-2": _stored(settings)["lov-2"]}

    with pytest.raises(ManifestRecordLossError, match="lov-1"):
        orchestrator_module._commit_with_history(
            settings,
            repo=repo,
            manifest_path=manifest_path,
            actions=[],
            new_records=kept,
            now=datetime(2026, 11, 1, tzinfo=UTC),
            is_sprint4_migration=False,
        )

    assert manifest_path.read_bytes() == before
