"""Skipped renders do not create paid work in the change-path guard (#790)."""

from dataclasses import replace
from pathlib import Path

import pytest

from lovspor.storage.manifest import Manifest
from lovspor.sync.orchestrator import _ChangePlan, _guard_change_reembed, _write_one
from tests.unit.test_embedding_input_staleness import _settings
from tests.unit.test_sync_change_reembed_guard import _OBSERVED, _doc


@pytest.mark.parametrize("action", ["new", "changed", "renamed"])
@pytest.mark.parametrize("allow", [False, True])
def test_entirely_unrenderable_workload_is_empty_and_silent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], action: str, allow: bool
) -> None:
    """Enforce the guard's documented render-skip and empty-scope contracts."""
    settings = _settings(tmp_path, reembed_guard_max_tokens=1)
    original = _doc(0)
    record, _ = _write_one(settings, original, _OBSERVED, None)
    prior = Manifest(generated_at=_OBSERVED, documents={original.doc_id: record})
    broken = replace(
        original,
        xml_bytes=b"<html",
        xml_hash=original.xml_hash if action == "renamed" else "f" * 64,
        slug="renamed" if action == "renamed" else original.slug,
    )
    plan = _ChangePlan(
        [broken.doc_id] if action == "new" else [],
        [broken.doc_id] if action == "changed" else [],
        [broken.doc_id] if action == "renamed" else [],
        {broken.doc_id: broken},
        Manifest(generated_at=_OBSERVED, documents={}) if action == "new" else prior,
        _OBSERVED,
    )
    repo = settings.lovverk_repo_path
    before = {
        path.relative_to(repo): path.read_bytes() for path in repo.rglob("*") if path.is_file()
    }
    capsys.readouterr()

    _guard_change_reembed(settings, plan, allow=allow)

    assert "override active" not in capsys.readouterr().err
    assert {
        path.relative_to(repo): path.read_bytes() for path in repo.rglob("*") if path.is_file()
    } == before
