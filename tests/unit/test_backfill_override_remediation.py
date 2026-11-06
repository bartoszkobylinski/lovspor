"""The explicit spending override also applies to embedding backfill."""

from pathlib import Path

import pytest

from lovspor.errors import MassReembedError
from lovspor.storage.manifest import read_manifest
from lovspor.sync.orchestrator import _run_sprint9_embeddings_migration
from tests.unit.test_sync_change_reembed_guard import (
    _OBSERVED,
    _corpus,
    _CountingEmbedder,
    _state,
)
from tests.unit.test_sync_orchestrator import _git


@pytest.mark.parametrize("allow", [False, True])
def test_backfill_override_repairs_a_sidecar_above_the_token_budget(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], allow: bool
) -> None:
    settings = _corpus(tmp_path, 1).model_copy(update={"reembed_guard_max_tokens": 1})
    repo = settings.lovverk_repo_path
    sidecar = repo / "lover" / "embeddings" / "lov-0.bin"
    expected_sidecar = sidecar.read_bytes()
    sidecar.unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "sidecar lost")
    before = _state(repo)
    manifest_path = repo / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    prior = read_manifest(manifest_path)
    embedder = _CountingEmbedder()

    if not allow:
        with pytest.raises(MassReembedError, match="token workload"):
            _run_sprint9_embeddings_migration(
                settings, prior, embedder, _OBSERVED, allow_mass_reembed=allow
            )
        assert embedder.texts == 0
        assert not sidecar.exists()
        assert manifest_path.read_bytes() == manifest_bytes
        assert _state(repo) == before
    else:
        repaired = _run_sprint9_embeddings_migration(
            settings, prior, embedder, _OBSERVED, allow_mass_reembed=allow
        )
        assert embedder.texts == 1
        assert sidecar.read_bytes() == expected_sidecar
        assert read_manifest(manifest_path) == repaired
        record = repaired.documents["lov-0"]
        assert record.embedding_hash == record.xml_hash
        assert record.embedding_input_hash
        head, status = _state(repo)
        assert head != before[0]
        assert status == ""
        assert (
            "mass-reembed override active: 1 document(s) selected for re-embedding"
            in capsys.readouterr().err
        )
